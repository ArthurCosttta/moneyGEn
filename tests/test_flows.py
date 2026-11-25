from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen

from app import V3Server
from backend.adapters.fake import FakeFlowAdapter
from backend.errors import FlowError
from backend.flow_contracts import validate_flow_spec
from backend.gpt_chat import extract_json_payload
from backend.service import EXPECTED_ACCOUNT, FlowLocalService

ROOT = Path(__file__).resolve().parents[1]
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FlowWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = Path(tempfile.mkdtemp(prefix="flowlocal-flow-fixture-"))
        cls.video = cls.fixture_dir / "valid.mp4"
        subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-y", "-f", "lavfi", "-i", "color=c=#10221c:s=180x320:r=24",
                "-t", "3", "-pix_fmt", "yuv420p", "-an", str(cls.video),
            ],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixture_dir, ignore_errors=True)

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="flowlocal-flow-test-"))
        self.adapter = FakeFlowAdapter(self.video, account=EXPECTED_ACCOUNT)
        self.service = FlowLocalService(ROOT, data_dir=self.temp / "dados", adapter=self.adapter)

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_example_template_validates_and_cycles_are_rejected(self):
        tpl = (ROOT / "exemplos" / "exemplo_fluxo_produto.json").read_bytes()
        preview = validate_flow_spec(tpl)
        self.assertEqual(preview["ordered_node_ids"], ["produto_sheet", "fundo", "cena_01", "cena_02", "videos"])

        cyclic = {
            "version": 1,
            "name": "Ciclo",
            "nodes": [
                {"id": "a", "type": "image_gen", "prompt": "a", "depends_on": ["b"]},
                {"id": "b", "type": "image_gen", "prompt": "b", "depends_on": ["a"]},
            ],
        }
        with self.assertRaises(FlowError) as caught:
            validate_flow_spec(cyclic)
        self.assertEqual(caught.exception.code, "SCHEMA_VALIDATION")

    def test_extract_json_payload_parses_fenced_json(self):
        raw = "Aqui está:\n```json\n{\"scenes\":[{\"image_prompt\":\"x\",\"video_prompt\":\"y\"}]}\n```"
        parsed = extract_json_payload(raw)
        self.assertEqual(parsed["scenes"][0]["image_prompt"], "x")

    def test_end_to_end_dag_gpt_json_fanout_images_and_videos(self):
        base_asset = self.service.assets.stage_base64("produto.png", base64.b64encode(PNG_1X1).decode("ascii"))
        image_calls = []

        def fake_request(prompt, refs=None):
            image_calls.append((prompt, list(refs or [])))
            return PNG_1X1

        self.service.images._write_session_marker()
        self.service.images._request = fake_request
        self.service.gpt_chat.ask_json = lambda prompt, **_kw: {
            "json": {
                "scenes": [
                    {"image_prompt": "cena 1 prompt {{input.direcao}}", "video_prompt": "video 1"},
                    {"image_prompt": "cena 2 prompt", "video_prompt": "video 2"},
                ]
            },
            "conversation_id": "conv-123",
            "conversation_url": "https://chatgpt.com/c/conv-123",
        }

        spec = {
            "version": 1,
            "name": "Fluxo Completo",
            "inputs": [
                {"id": "foto_produto", "type": "image", "label": "Produto", "required": True},
                {"id": "direcao", "type": "text", "label": "Direção", "required": True, "default": "minimalista"},
            ],
            "video_defaults": {"model_id": "omni-1.2", "aspect": "9:16", "duration_seconds": 3, "resolution": None},
            "nodes": [
                {
                    "id": "produto_sheet",
                    "type": "image_gen",
                    "label": "Sheet",
                    "prompt": "sheet {{input.direcao}}",
                    "refs": ["@input.foto_produto"],
                },
                {
                    "id": "fundo",
                    "type": "image_gen",
                    "label": "Fundo",
                    "prompt": "fundo {{input.direcao}}",
                    "refs": [],
                },
                {
                    "id": "roteiro",
                    "type": "gpt_json",
                    "label": "Roteiro",
                    "depends_on": ["produto_sheet", "fundo"],
                    "prompt": "gere 2 cenas para {{input.direcao}}",
                },
                {
                    "id": "cenas",
                    "type": "scene_fanout",
                    "label": "Cenas",
                    "source": "@node.roteiro.json",
                    "prompt_template": "{{scene.image_prompt}}",
                    "video_prompt_template": "{{scene.video_prompt}}",
                    "refs": ["@node.produto_sheet.asset_id", "@node.fundo.asset_id"],
                },
                {
                    "id": "videos",
                    "type": "video_batch",
                    "label": "Vídeos",
                    "from_fanout": "@node.cenas.items",
                },
            ],
        }

        started = self.service.flows.start({
            "spec": spec,
            "inputs": {"foto_produto": base_asset["id"], "direcao": "dourado"},
            "output_dir": str(self.temp / "saida"),
            "confirmed": True,
            "risk": True,
        })

        deadline = time.time() + 20
        run = started
        while time.time() < deadline:
            run = self.service.flows.get_run(started["id"])
            if run["state"] in ("SUCCEEDED", "FAILED"):
                break
            time.sleep(0.1)

        self.assertEqual(run["state"], "SUCCEEDED", msg=run.get("error_message"))
        # 1 sheet + 1 fundo + 2 cenas = 4 image_gen calls
        self.assertEqual(len(image_calls), 4)
        self.assertEqual(image_calls[0][1], [base_asset["id"]])
        self.assertEqual(image_calls[1][1], [])
        sheet_id = run["nodes"][0]["output"]["asset_id"]
        fundo_id = run["nodes"][1]["output"]["asset_id"]
        self.assertEqual(image_calls[2][1], [sheet_id, fundo_id])
        self.assertEqual(image_calls[3][1], [sheet_id, fundo_id])

        videos_out = run["nodes"][4]["output"]
        self.assertEqual(len(videos_out["videos"]), 2)
        self.assertTrue(Path(videos_out["output_dir"], "cena-01.mp4").is_file())
        self.assertTrue(Path(videos_out["output_dir"], "cena-02.mp4").is_file())

    def test_flow_no_spend_and_http_preview(self):
        server = V3Server(("127.0.0.1", 0), self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = Request(
                server.origin + "/api/flows/template",
                headers={"X-Flow-Token": server.token},
                method="GET",
            )
            with urlopen(req, timeout=5) as resp:
                tpl = json.loads(resp.read().decode("utf-8"))
            req2 = Request(
                server.origin + "/api/flows/preview",
                data=json.dumps({"spec": tpl}).encode("utf-8"),
                headers={"X-Flow-Token": server.token, "Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req2, timeout=5) as resp2:
                prev = json.loads(resp2.read().decode("utf-8"))
            self.assertEqual(prev["summary"]["total_nodes"], 5)
        finally:
            server.shutdown()
            server.server_close()

        locked = FlowLocalService(ROOT, data_dir=self.temp / "locked", adapter=self.adapter, no_spend=True)
        try:
            with self.assertRaises(FlowError) as caught:
                locked.flows.start({"spec": tpl, "inputs": {}, "confirmed": True, "risk": True})
            self.assertEqual(caught.exception.code, "NO_SPEND_ACTIVE")
        finally:
            locked.close()

    def test_structured_json_prompt_and_typed_refs_dispatch(self):
        base_asset = self.service.assets.stage_base64("prod.png", base64.b64encode(PNG_1X1).decode("ascii"))
        style_asset = self.service.assets.stage_base64("paleta.png", base64.b64encode(PNG_1X1 + b"\0").decode("ascii"))
        captured = []

        def fake_request(prompt, refs=None, *, size=None, ref_roles=None):
            captured.append({"prompt": prompt, "refs": list(refs or []), "size": size, "ref_roles": list(ref_roles or [])})
            return PNG_1X1

        self.service.images._write_session_marker()
        self.service.images._request = fake_request
        spec = {
            "version": 1,
            "name": "Structured Prompt",
            "inputs": [
                {"id": "foto", "type": "image", "label": "Foto", "required": True},
                {"id": "paleta", "type": "image", "label": "Paleta", "required": True},
                {"id": "tema", "type": "text", "label": "Tema", "required": True, "default": "neon"},
            ],
            "nodes": [
                {
                    "id": "hero",
                    "type": "image_gen",
                    "label": "Hero",
                    "size": "1024x1536",
                    "prompt": {"objetivo": "Foto hero", "clima": "{{input.tema}}", "qualidade": "8K"},
                    "refs": [
                        {"ref": "@input.foto", "role": "subject"},
                        {"ref": "@input.paleta", "role": "style"},
                    ],
                }
            ],
        }
        started = self.service.flows.start({
            "spec": spec,
            "inputs": {"foto": base_asset["id"], "paleta": style_asset["id"], "tema": "festa"},
            "confirmed": True,
            "risk": True,
        })
        deadline = time.time() + 10
        run = started
        while time.time() < deadline:
            run = self.service.flows.get_run(started["id"])
            if run["state"] in ("SUCCEEDED", "FAILED"):
                break
            time.sleep(0.05)

        self.assertEqual(run["state"], "SUCCEEDED", msg=run.get("error_message"))
        self.assertEqual(len(captured), 1)
        self.assertIn('"clima": "festa"', captured[0]["prompt"])
        self.assertEqual(captured[0]["size"], "1024x1536")
        self.assertEqual(captured[0]["refs"], [base_asset["id"], style_asset["id"]])
        self.assertEqual(captured[0]["ref_roles"], ["subject", "style"])

    def test_second_flow_run_does_not_collide_on_item_ids(self):
        spec = {
            "version": 1,
            "name": "Run Único",
            "video_defaults": {"model_id": "omni-1.2", "aspect": "9:16", "duration_seconds": 3, "resolution": None},
            "nodes": [
                {
                    "id": "videos",
                    "type": "video_batch",
                    "items": [{"prompt": "take 1", "output_name": "video-01.mp4"}],
                }
            ],
        }
        for i in range(2):
            started = self.service.flows.start({
                "spec": spec,
                "inputs": {},
                "output_dir": str(self.temp / f"saida-{i}"),
                "confirmed": True,
                "risk": True,
            })
            deadline = time.time() + 15
            run = started
            while time.time() < deadline:
                run = self.service.flows.get_run(started["id"])
                if run["state"] in ("SUCCEEDED", "FAILED"):
                    break
                time.sleep(0.05)
            self.assertEqual(run["state"], "SUCCEEDED", msg=run.get("error_message"))

    def test_helper_failure_does_not_misclassify_connected_relay_as_extension_missing(self):
        detail = (
            "no logged-in ChatGPT browser available.\n"
            "tried:\n"
            "  • current Chrome (relay) — chrome-use open failed (exit 1): "
            "connection_failed / chatgpt-web.carried-tabs.json: Acesso negado. (os error 5)\n"
            "the relay is connected but the open Chrome isn't usable — sign in to chatgpt.com in it."
        )
        completed = subprocess.CompletedProcess(args=["image-use"], returncode=1, stdout="", stderr=detail)
        err = self.service.images._helper_failure(completed)
        self.assertEqual(err.code, "CHROME_SESSION_STALE")
        self.assertIn("carried-tabs", err.message)

        login_only = subprocess.CompletedProcess(
            args=["image-use"],
            returncode=1,
            stdout="",
            stderr="no logged-in ChatGPT browser available.\n  • current Chrome (relay) — composer never appeared\nthe relay is connected but the open Chrome isn't usable — sign in to chatgpt.com in it.",
        )
        login_err = self.service.images._helper_failure(login_only)
        self.assertEqual(login_err.code, "GPT_LOGIN_REQUIRED")
        self.assertIn("composer never appeared", login_err.message)

    def test_request_auto_recovers_wedged_chatgpt_session_and_retries(self):
        self.service.images._write_session_marker()
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            if "daemon" in cmd and "status" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"success": True, "data": {"relay": True, "sessions": [{"name": "chatgpt-web"}]}}), stderr="")
            if "tabs" in cmd and "--session" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr='{"code":"connection_failed","error":"carried-tabs.json: Acesso negado"}')
            if "session" in cmd and "stop" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout='{"success":true}', stderr="")
            out_idx = cmd.index("-o") + 1
            out_path = Path(cmd[out_idx])
            image_use_runs = [c for c in calls if "-o" in c]
            if len(image_use_runs) == 1:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="chrome-use open failed (exit 1): connection_failed / browser is gone")
            out_path.write_bytes(PNG_1X1)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        fake_tools = (Path(shutil.which("python") or "python.exe"), Path("image-use"), Path("chrome-use.exe"))
        with mock.patch.object(self.service.images, "_image_use_tools", return_value=fake_tools), \
             mock.patch("backend.image_generation.subprocess.run", side_effect=fake_run):
            result = self.service.images._request("teste recuperacao")
        self.assertEqual(result, PNG_1X1)
        stop_calls = [c for c in calls if "session" in c and "stop" in c and "--force" in c]
        self.assertGreaterEqual(len(stop_calls), 2)


if __name__ == "__main__":
    unittest.main()
