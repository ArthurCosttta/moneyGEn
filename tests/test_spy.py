from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import V3Server
from backend.adapters.fake import FakeFlowAdapter
from backend.service import EXPECTED_ACCOUNT, FlowLocalService
from backend.spy_service import parse_meta_ad_library_html

ROOT = Path(__file__).resolve().parents[1]


class SpySearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="flowlocal-spy-"))
        self.fixture_video = self.temp / "fixture.mp4"
        self.fixture_video.write_bytes(b"")
        self.service = FlowLocalService(ROOT, data_dir=self.temp / "dados", adapter=FakeFlowAdapter(self.fixture_video, account=EXPECTED_ACCOUNT))
        self.server = V3Server(("127.0.0.1", 0), self.service)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.service.close()
        shutil.rmtree(self.temp, ignore_errors=True)

    def _http(self, method: str, path: str, body: dict | None = None):
        req = Request(
            f"http://127.0.0.1:{self.port}{path}",
            method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": f"http://127.0.0.1:{self.port}",
                "X-Flow-Token": self.server.token,
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_parse_meta_ad_library_html_extracts_videos_and_days(self):
        ten_days_ago = int(time.time()) - 10 * 86400
        payload = {
            "require": [
                {
                    "ad_archive_id": "9988776655",
                    "page_name": "Loja Som Brasil",
                    "start_date": ten_days_ago,
                    "snapshot": {
                        "title": "Caixa Karaoke LED",
                        "body": {"text": "Unboxing e teste de som com frete gratis hoje R$ 149"},
                        "link_url": "https://lojasom.com.br/karaoke",
                        "videos": [
                            {
                                "video_hd_url": "https://cdn.example.com/ad.mp4",
                                "video_preview_image_url": "https://cdn.example.com/ad.jpg",
                            }
                        ],
                    },
                }
            ]
        }
        html_doc = f'<html><script type="application/json" data-sjs>{json.dumps(payload)}</script></html>'
        parsed = parse_meta_ad_library_html(html_doc)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["id"], "meta_9988776655")
        self.assertEqual(parsed[0]["media_type"], "video")
        self.assertEqual(parsed[0]["domain"], "lojasom.com.br")
        self.assertGreaterEqual(parsed[0]["days_active"], 9)

    def test_cross_reference_ranks_multi_source_and_long_running_ads_first(self):
        meta_mock = {
            "items": [
                {
                    "id": "meta_1", "source": "meta_ads", "source_label": "Meta Ads",
                    "title": "Caixa Fergusom Karaoke", "author": "FergusomStore",
                    "url": "https://facebook.com/ads/library/?id=1", "landing_url": "https://fergusom.com.br/p",
                    "domain": "fergusom.com.br", "media_type": "video", "thumbnail": "", "video_url": "https://cdn/1.mp4",
                    "text": "Caixa portatil bluetooth microfone karaoke", "angle": "Teste / Review",
                    "days_active": 35, "views": None, "likes": None, "downloadable": True,
                },
                {
                    "id": "meta_2", "source": "meta_ads", "source_label": "Meta Ads",
                    "title": "Outro item aleatorio", "author": "Aleatoria",
                    "url": "https://facebook.com/ads/library/?id=2", "landing_url": "", "domain": "",
                    "media_type": "image", "thumbnail": "", "video_url": "", "text": "Outro texto",
                    "angle": "Demonstração", "days_active": 1, "views": None, "likes": None, "downloadable": False,
                },
            ]
        }
        yt_mock = {
            "items": [
                {
                    "id": "yt_1", "source": "youtube", "source_label": "YouTube",
                    "title": "Review FergusomStore caixa microfone karaoke", "author": "FergusomStore",
                    "url": "https://youtube.com/watch?v=abc", "landing_url": "", "domain": "",
                    "media_type": "video", "thumbnail": "", "video_url": "https://youtube.com/watch?v=abc",
                    "text": "Teste real", "angle": "Teste / Review", "days_active": None, "views": 80000, "likes": 3200, "downloadable": True,
                }
            ]
        }
        trends_mock = {"score": 78, "delta_pct": 42.5, "rising": [{"query": "fergusom karaoke"}], "top": []}

        with mock.patch.object(self.service.spy, "_search_meta_ads", return_value=meta_mock), \
             mock.patch.object(self.service.spy, "_search_youtube", return_value=yt_mock), \
             mock.patch.object(self.service.spy, "_search_tiktok", return_value={"items": []}), \
             mock.patch.object(self.service.spy, "_search_instagram", return_value={"items": []}), \
             mock.patch.object(self.service.spy, "_search_trends", return_value=trends_mock):
            code, res = self._http("POST", "/api/spy/search", {"query": "caixa karaoke"})

        self.assertEqual(code, 200, msg=res)
        self.assertEqual(res["total"], 3)
        top_item = res["items"][0]
        self.assertEqual(top_item["id"], "meta_1")
        self.assertGreaterEqual(top_item["cross_count"], 2)
        self.assertTrue(any("35d ativo" in r for r in top_item["reasons"]))

    def test_capture_page_and_local_media_download_serving(self):
        fake_page = "<html><head><title>Oferta Caixa LED</title></head><body><h1>Som Potente</h1><p>Hoje por R$ 149,90</p><img src='https://img.example/foto.jpg'></body></html>"

        class FakeResp:
            def __init__(self, data: bytes):
                self._data = data
            def read(self):
                return self._data
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with mock.patch.object(self.service.spy, "_single_file_cmd", return_value=None), \
             mock.patch("backend.spy_service.urllib.request.urlopen", return_value=FakeResp(fake_page.encode("utf-8"))):
            code, cap = self._http("POST", "/api/spy/capture", {"url": "https://loja.exemplo.com/oferta"})
        self.assertEqual(code, 200, msg=cap)
        self.assertEqual(cap["title"], "Oferta Caixa LED")
        self.assertIn("R$ 149,90", cap["prices"])

        with mock.patch("backend.spy_service.urllib.request.urlopen", return_value=FakeResp(b"\x00\x00\x00\x18ftypmp42")):
            code, dl = self._http("POST", "/api/spy/download", {"video_url": "https://cdn.example/clip.mp4", "title": "Clip Teste", "media_type": "video"})
        self.assertEqual(code, 200, msg=dl)
        self.assertEqual(dl["media_type"], "video")

        req_dl = Request(
            f"http://127.0.0.1:{self.port}{dl['media_url']}",
            headers={"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}", "X-Flow-Token": self.server.token},
        )
        with urlopen(req_dl, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"\x00\x00\x00\x18ftypmp42")

    def test_parse_deep_hydrated_meta_ad_library_json_and_ad_count(self):
        nested = {"ad_archive_id": "deep_99", "page_id": "1122334455", "collation_count": 7, "page_name": "Loja Top", "snapshot": {"cards": [{"link_url": "https://lojatop.com.br/p", "resized_image_url": "https://cdn.example/c.jpg", "body": "Oferta"}]}}
        for _ in range(25):
            nested = {"wrap": [nested]}
        html = f'<script type="application/json">{json.dumps(nested)}</script>'
        parsed = parse_meta_ad_library_html(html)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["ad_count"], 7)
        self.assertEqual(parsed[0]["page_id"], "1122334455")
        self.assertEqual(parsed[0]["landing_url"], "https://lojatop.com.br/p")

    def test_save_edited_page_writes_new_html_file(self):
        code, saved = self._http("POST", "/api/spy/save-page", {
            "filename": "loja-teste.html",
            "html": "<!doctype html><html><head><title>Nova Oferta</title></head><body><h1>Headline Editada</h1><p>Por R$ 97,00</p></body></html>",
        })
        self.assertEqual(code, 200, msg=saved)
        self.assertTrue(saved["filename"].endswith("-editada.html"))
        self.assertEqual(saved["title"], "Nova Oferta")
        self.assertIn("R$ 97,00", saved["prices"])
        self.assertTrue(Path(saved["html_path"]).is_file())

    def test_product_parsers_download_transcribe_and_offer_to_flow(self):
        from backend.product_spy import (
            extract_page_copy_summary,
            parse_aliexpress_html,
            parse_compact_number,
            parse_shopify_products_json,
        )

        self.assertEqual(parse_compact_number("1,2 mil"), 1200)
        self.assertEqual(parse_compact_number("1K+ vendido(s)"), 1000)
        self.assertEqual(parse_compact_number("2.5M"), 2500000)

        ali_payload = {
            "content": [
                {
                    "productId": "100500999",
                    "title": {"displayTitle": "Luminária Natal LED"},
                    "prices": {"salePrice": {"formattedPrice": "R$ 29,90", "minPrice": 29.9, "discount": 50}},
                    "trade": {"tradeDesc": "2K+ vendido(s)"},
                    "evaluation": {"starRating": 4.9},
                    "image": {"imgUrl": "//ae01.alicdn.com/kf/teste.jpg"},
                }
            ]
        }
        ali_items = parse_aliexpress_html(f'<html><script>window.x={{"itemList":{json.dumps(ali_payload)}}}</script></html>')
        self.assertEqual(len(ali_items), 1)
        self.assertEqual(ali_items[0]["sold_count"], 2000)
        self.assertEqual(ali_items[0]["thumbnail"], "https://ae01.alicdn.com/kf/teste.jpg")

        shop_items = parse_shopify_products_json(
            {"products": [{"title": "Kit Glow", "handle": "kit-glow", "variants": [{"price": "119.90"}], "images": [{"src": "https://cdn.shopify/img.jpg"}]}]},
            "https://minhaloja.com.br",
        )
        self.assertEqual(len(shop_items), 1)
        self.assertEqual(shop_items[0]["url"], "https://minhaloja.com.br/products/kit-glow")

        summary = extract_page_copy_summary("<html><style>x{color:red}</style><body><h1>Oferta Especial</h1><p>Compre hoje com 50% OFF</p></body></html>")
        self.assertIn("Oferta Especial", summary)
        self.assertNotIn("color:red", summary)

        # Download + Whisper transcribe endpoint
        fake_dl = {
            "name": "clip-123.mp4",
            "local_path": str((self.service.spy.media_dir / "clip-123.mp4").resolve()),
            "media_url": "/media/spy/media/clip-123.mp4",
            "media_type": "video",
            "size_bytes": 128,
        }
        (self.service.spy.media_dir / "clip-123.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
        with mock.patch.object(self.service.spy, "download_media", return_value=fake_dl), \
             mock.patch.object(self.service.editor, "_transcribe", return_value={"language": "pt", "segments": [{"start": 0.0, "end": 2.0, "text": "Olha esse produto incrível"}]}):
            code, tr = self._http("POST", "/api/spy/download-transcribe", {"video_url": "https://tiktok.com/@u/video/1", "title": "Clip"})
        self.assertEqual(code, 200, msg=tr)
        self.assertIn("produto incrível", tr["text"])
        self.assertTrue((self.service.spy.media_dir / "clip-123.transcript.json").is_file())

        # GPT config + offer-to-flow endpoint with trained project URL
        code, cfg = self._http("POST", "/api/spy/gpt-config", {"project_url": "https://chatgpt.com/g/g-p-demo123/project", "instructions": "Focar em UGC"})
        self.assertEqual(code, 200, msg=cfg)
        self.assertEqual(cfg["project_url"], "https://chatgpt.com/g/g-p-demo123/project")

        sample_flow = json.loads((self.service.root / "exemplos" / "exemplo_fluxo_produto.json").read_text(encoding="utf-8"))
        gpt_reply = {
            "json": {
                "offer_analysis": {"headline_melhorada": "Brilho Instantâneo em 3 Minutos", "mecanismo_unico": "Sensor térmico"},
                "flow_spec": sample_flow,
            },
            "conversation_id": "conv_1",
            "conversation_url": "https://chatgpt.com/c/conv_1",
            "raw_response": "{}",
        }
        with mock.patch.object(self.service.gpt_chat, "ask_json", return_value=gpt_reply) as mocked_gpt:
            code, flow_res = self._http("POST", "/api/spy/offer-to-flow", {"transcript": "Olha esse produto incrível", "product": "Escova"})
        self.assertEqual(code, 200, msg=flow_res)
        self.assertEqual(flow_res["offer_analysis"]["headline_melhorada"], "Brilho Instantâneo em 3 Minutos")
        self.assertEqual(mocked_gpt.call_args.kwargs.get("project"), "https://chatgpt.com/g/g-p-demo123/project")

    def test_meta_ads_login_clears_stale_account_and_classifies_200_error(self):
        from urllib.error import HTTPError
        import io
        # Stale account exists in DB
        self.service.repository.set_setting("meta_ads_account_id", "act_stale_999")
        with mock.patch.object(self.service.meta_ads, "_write_cli_token"), \
             mock.patch.object(self.service.meta_ads, "_graph_request", return_value={"data": []}):
            res = self.service.meta_ads.login("EAAB_VALID_TEST_TOKEN_1234567890")
        self.assertEqual(res["accounts"], [])
        self.assertEqual(self.service.repository.get_setting("meta_ads_account_id"), "")

        err_body = json.dumps({"error": {"message": "(#200) Ad account owner has NOT grant ads_management or ads_read permission", "code": 200}}).encode("utf-8")
        http_err = HTTPError("https://graph.facebook.com", 403, "Forbidden", {}, io.BytesIO(err_body))
        with mock.patch.object(self.service.meta_ads, "_read_access_token", return_value="EAAB_VALID_TEST_TOKEN_1234567890"), \
             mock.patch("backend.meta_ads_service.urllib.request.urlopen", side_effect=http_err):
            code, body = self._http("GET", "/api/meta-ads/campaigns?account_id=act_692423753947077")
        self.assertEqual(code, 403)
        self.assertEqual(body["error"]["code"], "META_ASSET_PERMISSION")

    def test_meta_summary_pulls_abo_budget_and_funding_balance_and_custom_calendar_range(self):
        def fake_graph(method, endpoint, params=None, **_kw):
            if endpoint == "act_123":
                return {
                    "id": "act_123",
                    "name": "Conta Real",
                    "currency": "BRL",
                    "spend_cap": "500000",
                    "amount_spent": "123450",
                    "balance": "0",
                    "funding_source_details": {"display_string": "Saldo disponível (R$ 420,50)"},
                }
            if endpoint == "act_123/insights":
                return {"data": [{"spend": "85.00", "clicks": "40", "actions": [{"action_type": "purchase", "value": "3"}], "action_values": [{"action_type": "purchase", "value": "300.00"}]}]}
            return {"data": []}

        with mock.patch.object(self.service.meta_ads, "_graph_request", side_effect=fake_graph), \
             mock.patch.object(self.service.meta_ads, "list_campaigns", return_value={"items": [{"id": "c1", "name": "CBO", "status": "ACTIVE", "daily_budget": None}]}), \
             mock.patch.object(self.service.meta_ads, "list_adsets", return_value={"items": [{"id": "a1", "campaign_id": "c1", "effective_status": "ACTIVE", "daily_budget": "15000"}]}), \
             mock.patch.object(self.service.meta_ads, "list_ads", return_value={"items": []}):
            summary = self.service.meta_ads.account_summary("act_123", date_preset="last_7d")
        self.assertEqual(summary["active_daily_budget"], 150.0)
        self.assertEqual(summary["funding_balance"], 420.5)
        self.assertEqual(summary["amount_spent"], 1234.5)

        # Custom historical calendar range pulls older batches & manual items across months
        code_c, old_ev = self._http("POST", "/api/calendar/items", {"scheduled_date": "2026-07-15", "title": "Lote Antigo Julho", "kind": "ad"})
        self.assertEqual(code_c, 200, msg=old_ev)
        code, cal = self._http("GET", "/api/calendar?month=2026-11&range_preset=custom&since=2026-07-01&until=2026-11-30")
        self.assertEqual(code, 200, msg=cal)
        self.assertEqual(cal["range_preset"], "custom")
        self.assertEqual(cal["since"], "2026-07-01")
        self.assertEqual(cal["until"], "2026-11-30")
        self.assertTrue(any(ev["id"] == old_ev["id"] for ev in cal["events"]))

    def test_gpt_routes_conversation_url_correctly_and_trending_supports_categories(self):
        captured_cmds = []

        def fake_subproc(cmd, **kw):
            captured_cmds.append(cmd)
            out_path = None
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("backend.gpt_chat.GptChatService._preferred_session", return_value="chatgpt-web"), \
             mock.patch("backend.gpt_chat.subprocess.run") as mocked_run, \
             mock.patch("backend.gpt_chat.Path.read_text", side_effect=[
                 json.dumps({"data": [{"response": '{"offer_analysis":{"headline_melhorada":"OK"},"flow_spec":{"nodes":[]}}', "conversationId": "c1", "conversationUrl": "https://chatgpt.com/c/c1"}]}),
                 "",
             ]):
            mocked_run.return_value = mock.Mock(returncode=0)
            res = self.service.gpt_chat.ask_json("teste", project="https://chatgpt.com/c/6b07900c-3404-83e9-a0e3-a9a47260ca2b")
        ran_cmd = mocked_run.call_args[0][0]
        self.assertIn("--session", ran_cmd)
        self.assertIn("chatgpt-web", ran_cmd)
        self.assertIn("--conversation", ran_cmd)
        self.assertNotIn("--project", ran_cmd)
        self.assertEqual(res["json"]["offer_analysis"]["headline_melhorada"], "OK")

        # Trending with E-commerce + YouTube Dark multi-categories
        with mock.patch("backend.spy_service.search_aliexpress", return_value={"items": [{"id": "a1", "title": "Serum", "sold_count": 500, "est_revenue_min": 5000}]}), \
             mock.patch.object(self.service.spy, "_search_amazon", return_value={"items": []}), \
             mock.patch.object(self.service.spy, "_search_youtube", return_value={"items": [{"id": "yt1", "title": "Mistério", "views": 90000, "likes": 4000, "url": "https://youtube.com/shorts/x"}]}), \
             mock.patch.object(self.service.spy, "_enrich_video_likes"):
            code, tr = self._http("GET", "/api/spy/trending?country=BR&track=all&categories=beleza,curiosidades")
        self.assertEqual(code, 200, msg=tr)
        self.assertIn("ecommerce", tr["category_catalog"])
        self.assertIn("youtube_dark", tr["category_catalog"])
        self.assertEqual(len(tr["hot_products"]), 1)
        self.assertEqual(len(tr["hot_videos"]), 1)


if __name__ == "__main__":
    unittest.main()
