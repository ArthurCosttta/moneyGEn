from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import V3Server
from backend.adapters.fake import FakeFlowAdapter
from backend.service import EXPECTED_ACCOUNT, FlowLocalService

ROOT = Path(__file__).resolve().parents[1]


class CapCutQueueCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="flowlocal-cc-cal-"))
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
            with urlopen(req, timeout=25) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_direct_queue_enqueues_multiple_prompts_and_respects_no_spend(self):
        code, batch = self._http("POST", "/api/batches", {
            "name": "Fila Rápida Teste",
            "defaults": {"model_id": "omni-1.2", "aspect": "9:16", "duration_seconds": 6, "resolution": None},
            "auto_output": True,
        })
        self.assertEqual(code, 200, msg=batch)

        code, added = self._http("POST", f"/api/batches/{batch['id']}/items", {
            "expected_revision": batch["revision"],
            "idempotency_key": "dir-items-1",
            "items": [
                {"id": "take_1", "kind": "t2v", "prompt": "Cena 1 produto", "asset_id": None},
                {"id": "take_2", "kind": "t2v", "prompt": "Cena 2 benefício", "asset_id": None},
            ],
        })
        self.assertEqual(code, 200, msg=added)

        code, plan = self._http("POST", "/api/plan", {"batch_id": batch["id"], "expected_revision": added["revision"]})
        self.assertEqual(code, 200, msg=plan)
        code, items_res = self._http("GET", f"/api/batches/{batch['id']}/items")
        ids = [x["id"] for x in items_res["items"]]

        code, queued = self._http("POST", "/api/queue-direct", {
            "batch_id": batch["id"],
            "expected_revision": plan["revision"],
            "plan_hash": plan["plan_hash"],
            "idempotency_key": "dir-go-1",
            "item_ids": ids,
            "confirmed": True,
        })
        self.assertEqual(code, 202, msg=queued)
        self.assertEqual(len(queued["execution_ids"]), 2)

        # Confirm --no-spend still blocks direct queue
        no_spend_svc = FlowLocalService(ROOT, data_dir=self.temp / "dados_ns", adapter=FakeFlowAdapter(self.fixture_video, account=EXPECTED_ACCOUNT), no_spend=True)
        try:
            b2 = no_spend_svc.create_batch({"name": "NS", "defaults": {"model_id": "omni-1.2", "aspect": "9:16", "duration_seconds": 6}, "auto_output": True})
            a2 = no_spend_svc.add_items(b2["id"], {"expected_revision": b2["revision"], "idempotency_key": "ns-i", "items": [{"id": "take_ns1", "kind": "t2v", "prompt": "P1", "asset_id": None}]})
            p2 = no_spend_svc.planner.plan(b2["id"], a2["revision"])
            ids2 = [x["id"] for x in no_spend_svc.repository.all_items(b2["id"])]
            with self.assertRaises(Exception) as ctx:
                no_spend_svc.start_direct_queue({"batch_id": b2["id"], "expected_revision": p2["revision"], "plan_hash": p2["plan_hash"], "idempotency_key": "ns-q", "item_ids": ids2, "confirmed": True})
            self.assertEqual(getattr(ctx.exception, "code", None), "NO_SPEND_ACTIVE")
        finally:
            no_spend_svc.close()

    def test_capcut_multitrack_timeline_renders_and_saves_project(self):
        caps = self.service.editor.capabilities()
        if not caps["ffmpeg"] or not caps["ffprobe"]:
            self.skipTest("FFmpeg/FFprobe não instalados")
        ffmpeg = caps["ffmpeg_path"]

        media_dir = self.data / "media_test"
        media_dir.mkdir(parents=True, exist_ok=True)
        vid = media_dir / "base.mp4"
        aud = media_dir / "beat.wav"
        ov = media_dir / "logo.png"

        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x162129:s=360x640:d=2", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(vid)], check=True)
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "pcm_s16le", str(aud)], check=True)
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x9ee7ce:s=120x120:d=1", "-frames:v", "1", str(ov)], check=True)

        code, up_aud = self._http("POST", "/api/editor/upload-audio", {
            "name": "beat.wav",
            "content": base64.b64encode(aud.read_bytes()).decode("ascii"),
        })
        self.assertEqual(code, 200, msg=up_aud)

        code, rendered = self._http("POST", "/api/editor/render-timeline", {
            "name": "Teste CapCut",
            "aspect": "9:16",
            "clips": [{"path": str(vid), "trim_start": 0, "trim_end": 1.6, "speed": 1.0, "volume": 1.0, "effect": "contrast", "transition": "fade"}],
            "overlays": [{"path": str(ov), "timeline_start": 0.2, "duration": 1.0, "scale": 0.3, "pos": "top_right"}],
            "audio_tracks": [{"path": up_aud["path"], "timeline_start": 0.0, "trim_start": 0.0, "duration": 1.5, "volume": 0.5}],
            "captions": [{"start": 0.1, "end": 1.4, "text": "OFERTA RELAMPAGO"}],
            "caption_style": {"preset": "tiktok_bold", "position": "Inferior", "font_size": 48, "color": "#ffdf81"},
        })
        self.assertEqual(code, 200, msg=rendered)
        self.assertTrue(Path(rendered["output_path"]).is_file())
        self.assertTrue(rendered["captions_burned"])
        self.assertEqual(rendered["audio_tracks_count"], 1)
        self.assertEqual(rendered["overlays_count"], 1)

        code, saved_p = self._http("POST", "/api/editor/projects", {"name": "Proj 1", "aspect": "9:16", "clips": [{"path": str(vid)}]})
        self.assertEqual(code, 200, msg=saved_p)
        code, projs = self._http("GET", "/api/editor/projects")
        self.assertEqual(code, 200)
        self.assertTrue(any(p["id"] == saved_p["id"] for p in projs["items"]))

        # Verify /media/local preview works for the uploaded mockup preview path inside allowed roots
        req_local = Request(
            f"http://127.0.0.1:{self.port}/media/local?path={vid}",
            headers={"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}", "X-Flow-Token": self.server.token},
        )
        with urlopen(req_local, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertGreater(len(resp.read()), 100)

    def test_calendar_correlates_batches_and_manual_items(self):
        today = datetime.now().strftime("%Y-%m-%d")
        month = today[:7]

        code, batch = self._http("POST", "/api/batches", {
            "name": "Lote Calendário",
            "defaults": {"model_id": "omni-1.2", "aspect": "9:16", "duration_seconds": 6, "resolution": None},
        })
        self.assertEqual(code, 200)

        code, item = self._http("POST", "/api/calendar/items", {
            "scheduled_date": today,
            "kind": "ad",
            "title": "Criativo 01 Hook Forte",
            "status": "READY",
            "batch_id": batch["id"],
            "platform": "Meta Ads",
            "notes": "Testar em conjunto aberto",
        })
        self.assertEqual(code, 200, msg=item)

        code, cal = self._http("GET", f"/api/calendar?month={month}")
        self.assertEqual(code, 200, msg=cal)
        ids = {e["id"] for e in cal["events"]}
        self.assertIn(f"batch:{batch['id']}", ids)
        self.assertIn(item["id"], ids)

        code, patched = self._http("PATCH", f"/api/calendar/items/{item['id']}", {"status": "PUBLISHED"})
        self.assertEqual(code, 200)
        self.assertEqual(patched["status"], "PUBLISHED")

        code, deleted = self._http("DELETE", f"/api/calendar/items/{item['id']}", {})
        self.assertEqual(code, 200)
        self.assertTrue(deleted["deleted"])


if __name__ == "__main__":
    unittest.main()
