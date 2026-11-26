from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import V3Server
from backend.adapters.fake import FakeFlowAdapter
from backend.errors import FlowError
from backend.service import EXPECTED_ACCOUNT, FlowLocalService

ROOT = Path(__file__).resolve().parents[1]


class EditorAndMetaAdsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="flowlocal-ed-meta-"))
        self.data = self.temp / "dados"
        self.fixture_video = self.temp / "fixture.mp4"
        self.fixture_video.write_bytes(b"")
        self.service = FlowLocalService(ROOT, data_dir=self.data, adapter=FakeFlowAdapter(self.fixture_video, account=EXPECTED_ACCOUNT))
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

    def test_editor_real_ffmpeg_analyze_and_render_with_captions(self):
        caps = self.service.editor.capabilities()
        if not caps["ffmpeg"] or not caps["ffprobe"]:
            self.skipTest("FFmpeg/FFprobe não instalados neste host")

        sample_dir = self.temp / "pasta_teste"
        sample_dir.mkdir(parents=True, exist_ok=True)
        sample_mp4 = sample_dir / "clipe_teste.mp4"
        ffmpeg = caps["ffmpeg_path"]
        subprocess.run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=0x162129:s=360x640:d=2",
                "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                str(sample_mp4),
            ],
            check=True,
        )

        code, listed = self._http("GET", f"/api/editor/videos?folder={sample_dir}")
        self.assertEqual(code, 200)
        self.assertEqual(len(listed["items"]), 1)
        self.assertEqual(listed["items"][0]["name"], "clipe_teste.mp4")

        code, analyzed = self._http(
            "POST",
            "/api/editor/analyze",
            {"path": str(sample_mp4), "run_whisper": False, "run_scenes": True, "extract_frames": True},
        )
        self.assertEqual(code, 200, msg=analyzed)
        self.assertGreaterEqual(len(analyzed["frames"]), 1)
        frame_url = analyzed["frames"][0]["url"]
        req_frame = Request(
            f"http://127.0.0.1:{self.port}{frame_url}",
            headers={"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}", "X-Flow-Token": self.server.token},
        )
        with urlopen(req_frame, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertGreater(len(resp.read()), 100)

        code, rendered = self._http(
            "POST",
            "/api/editor/render",
            {
                "source_path": str(sample_mp4),
                "cut_silence": False,
                "burn_captions": True,
                "segments": [{"start": 0.1, "end": 1.8, "text": "Teste MoneyGen"}],
                "caption_style": {"font_size": 48, "position": "Inferior", "color": "#9ee7ce"},
            },
        )
        self.assertEqual(code, 200, msg=rendered)
        self.assertTrue(rendered["captions_burned"])
        self.assertTrue(Path(rendered["output_path"]).is_file())

        req_vid = Request(
            f"http://127.0.0.1:{self.port}{rendered['preview_url']}",
            headers={"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}", "X-Flow-Token": self.server.token},
        )
        with urlopen(req_vid, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertGreater(len(resp.read()), 1000)

    def test_meta_ads_login_hierarchy_and_paused_upload(self):
        secret_token = "EAATESTTOKEN1234567890ABCDEF"
        cli_calls: list[list[str]] = []

        graph_calls: list[tuple[str, str]] = []

        def fake_graph(method, endpoint, **kwargs):
            graph_calls.append((method, endpoint))
            if endpoint == "me/adaccounts":
                return {"data": [{"id": "act_999111", "name": "Conta Teste"}]}
            if endpoint == "act_999111" and method == "GET":
                return {"id": "act_999111", "name": "Conta Teste", "account_status": 1}
            if endpoint == "act_999111/campaigns" and method == "GET":
                return {"data": [{"id": "cmp_1", "name": "Campanha Alpha", "status": "PAUSED", "daily_budget": "5000"}]}
            if endpoint.endswith("/adsets") and method == "GET":
                return {"data": [{"id": "set_1", "name": "Conjunto BR", "status": "PAUSED", "campaign_id": "cmp_1"}]}
            if endpoint.endswith("/ads") and method == "GET":
                return {"data": [{"id": "ad_1", "name": "Ad 01", "status": "PAUSED", "adset_id": "set_1"}]}
            if endpoint == "act_999111/insights":
                level = (kwargs.get("params") or {}).get("level", "campaign")
                key = f"{level}_id"
                val = {"campaign": "cmp_1", "adset": "set_1", "ad": "ad_1"}[level]
                return {"data": [{key: val, "spend": "42.50", "impressions": "1500", "clicks": "75", "actions": [{"action_type": "purchase", "value": "3"}]}]}
            if endpoint == "act_999111/campaigns" and method == "POST":
                return {"id": "cmp_new"}
            if endpoint == "act_999111/adsets" and method == "POST":
                return {"id": "set_new"}
            if endpoint.endswith("/advideos"):
                return {"id": "vid_777"}
            if endpoint.endswith("/adcreatives"):
                return {"id": "crt_888"}
            if endpoint.endswith("/ads") and method == "POST":
                return {"id": "ad_999"}
            return {"data": []}

        sample_mp4 = self.temp / "ad_video.mp4"
        sample_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")

        with mock.patch.object(self.service.meta_ads, "_write_cli_token"), \
             mock.patch.object(self.service.meta_ads, "_read_access_token", return_value=secret_token), \
             mock.patch.object(self.service.meta_ads, "_graph_request", side_effect=fake_graph):

            code, login_res = self._http("POST", "/api/meta-ads/login", {"token": secret_token})
            self.assertEqual(code, 200, msg=login_res)
            self.assertTrue(login_res["status"]["connected"])
            self.assertNotIn(secret_token, json.dumps(login_res))

            code, camps = self._http("GET", "/api/meta-ads/campaigns?account_id=act_999111")
            self.assertEqual(code, 200)
            self.assertEqual(camps["items"][0]["insights"]["purchases"], 3)

            code, sets = self._http("GET", "/api/meta-ads/adsets?account_id=act_999111&campaign_id=cmp_1")
            self.assertEqual(code, 200)
            self.assertEqual(sets["items"][0]["id"], "set_1")

            code, ads = self._http("GET", "/api/meta-ads/ads?account_id=act_999111&adset_id=set_1")
            self.assertEqual(code, 200)
            self.assertEqual(ads["items"][0]["id"], "ad_1")

            base_payload = {
                "account_id": "act_999111",
                "campaign_id": "__new__",
                "adset_id": "__new__",
                "new_campaign": {"name": "Teste Upload", "objective": "OUTCOME_SALES", "daily_budget_brl": 50},
                "new_adset": {"name": "BR Aberto", "country": "BR", "optimization_goal": "LINK_CLICKS"},
                "media_path": str(sample_mp4),
                "page_id": "11223344",
                "ad_name": "Criativo 01",
                "primary_text": "Oferta real",
                "headline": "Confira",
                "cta_type": "LEARN_MORE",
                "link_url": "https://exemplo.com/oferta",
            }
            graph_calls.clear()
            code, dry = self._http("POST", "/api/meta-ads/upload", {**base_payload, "dry_run": True})
            self.assertEqual(code, 200, msg=dry)
            self.assertTrue(dry["dry_run"])
            self.assertEqual(graph_calls, [("GET", "act_999111")])

            code, blocked = self._http("POST", "/api/meta-ads/upload", {**base_payload, "dry_run": False, "confirmed": False})
            self.assertEqual(code, 403)
            self.assertEqual(blocked["error"]["code"], "SPEND_NOT_CONFIRMED")

            graph_calls.clear()
            code, created = self._http("POST", "/api/meta-ads/upload", {**base_payload, "dry_run": False, "confirmed": True})
            self.assertEqual(code, 200, msg=created)
            self.assertEqual(created["status"], "PAUSED")
            self.assertEqual(created["ad_id"], "ad_999")
            self.assertEqual(
                [ep for _, ep in graph_calls],
                ["act_999111/campaigns", "act_999111/adsets", "act_999111/advideos", "act_999111/adcreatives", "act_999111/ads"],
            )

    def test_meta_ads_reads_nested_auth_access_token(self):
        cfg = self.temp / "meta_cfg.json"
        cfg.write_text(json.dumps({"auth": {"access_token": "EAA_nested_token_123456"}}), encoding="utf-8")
        with mock.patch.object(self.service.meta_ads, "_cli_config_path", return_value=cfg):
            with mock.patch.dict("os.environ", {"META_ADS_ACCESS_TOKEN": ""}, clear=False):
                self.assertEqual(self.service.meta_ads._read_access_token(), "EAA_nested_token_123456")

    def test_meta_ads_summary_roi_winner_and_create_pixel(self):
        def fake_graph(method, endpoint, **kwargs):
            if endpoint == "act_999111" and method == "GET":
                return {"id": "act_999111", "name": "Conta Teste", "currency": "BRL", "spend_cap": "50000", "balance": "12000"}
            if endpoint == "act_999111/insights":
                preset = (kwargs.get("params") or {}).get("date_preset")
                if preset == "today":
                    return {"data": [{"spend": "25.50", "impressions": "900", "clicks": "30"}]}
                level = (kwargs.get("params") or {}).get("level")
                if level == "campaign":
                    return {"data": [{"campaign_id": "cmp_1", "spend": "100.00", "clicks": "120", "actions": [{"action_type": "purchase", "value": "4"}]}]}
                if level == "ad":
                    return {"data": [{"ad_id": "ad_win", "spend": "60.00", "clicks": "80", "actions": [{"action_type": "purchase", "value": "4"}]}]}
                return {"data": [{"spend": "100.00", "action_values": [{"action_type": "purchase", "value": "350.00"}], "actions": [{"action_type": "purchase", "value": "4"}], "purchase_roas": [{"value": "3.5"}]}]}
            if endpoint == "act_999111/campaigns":
                return {"data": [{"id": "cmp_1", "name": "Campanha Alpha", "status": "ACTIVE", "daily_budget": "5000"}]}
            if endpoint == "act_999111/ads":
                return {"data": [{"id": "ad_win", "name": "Criativo Ganha Tudo", "status": "ACTIVE"}]}
            if endpoint == "act_999111/adspixels" and method == "POST":
                return {"id": "px_new_777"}
            return {"data": []}

        with mock.patch.object(self.service.meta_ads, "_read_access_token", return_value="EAA_fake_1234567890"), \
             mock.patch.object(self.service.meta_ads, "_graph_request", side_effect=fake_graph):
            code, summary = self._http("GET", "/api/meta-ads/summary?account_id=act_999111&date_preset=last_7d")
            self.assertEqual(code, 200, msg=summary)
            self.assertEqual(summary["today_spend"], 25.5)
            self.assertEqual(summary["active_daily_budget"], 50.0)
            self.assertEqual(summary["roas"], 3.5)
            self.assertEqual(summary["winner"]["name"], "Criativo Ganha Tudo")

            code, px = self._http("POST", "/api/meta-ads/pixels", {"account_id": "act_999111", "name": "Pixel Novo Teste"})
            self.assertEqual(code, 200, msg=px)
            self.assertEqual(px["id"], "px_new_777")


if __name__ == "__main__":
    unittest.main()
