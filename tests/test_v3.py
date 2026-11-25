from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from backend.adapters.fake import FakeFlowAdapter
from backend.errors import FlowError
from backend.service import EXPECTED_ACCOUNT, FlowLocalService


ROOT = Path(__file__).resolve().parents[1]


class V3ServiceTests(unittest.TestCase):
    PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = Path(tempfile.mkdtemp(prefix="flowlocal-v3-fixture-"))
        cls.video = cls.fixture_dir / "valid.mp4"
        subprocess.run([
            shutil.which("ffmpeg") or "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=#10221c:s=180x320:r=24",
            "-t", "3", "-pix_fmt", "yuv420p", "-an", str(cls.video),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixture_dir, ignore_errors=True)

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="flowlocal-v3-test-"))
        self.adapter = FakeFlowAdapter(self.video, account=EXPECTED_ACCOUNT)
        self.service = FlowLocalService(ROOT, data_dir=self.temp / "dados", adapter=self.adapter)

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.temp, ignore_errors=True)

    def build(self, *, count=2):
        output = self.temp / "saida"
        batch = self.service.create_batch({
            "name": "Teste v3", "defaults": {"model_id": "omni-1.2", "aspect": "9:16", "duration_seconds": 3, "resolution": None},
            "output_dir": str(output), "max_credits": None,
        })
        items = [{"id": f"take_{i}", "kind": "t2v", "prompt": f"prompt exato {i}\nlinha 2", "asset_id": None, "output_name": f"take-{i}.mp4", "overrides": {}} for i in range(count)]
        added = self.service.add_items(batch["id"], {"expected_revision": batch["revision"], "idempotency_key": str(uuid.uuid4()), "items": items})
        batch = self.service.repository.get_batch(batch["id"])
        self.service.preflight(batch["id"])
        plan = self.service.planner.plan(batch["id"], added["revision"])
        return self.service.repository.get_batch(batch["id"]), plan, items

    def approval(self, batch, plan, item_ids, scope="TEST"):
        return {"batch_id": batch["id"], "expected_revision": batch["revision"], "plan_hash": plan["plan_hash"], "idempotency_key": str(uuid.uuid4()), "item_ids": item_ids, "confirmed": True, "risk": True, "reviewed": scope == "REMAINDER", "unknown_cost_accepted": True}

    def wait_item(self, batch_id, item_id, states, timeout=12):
        end = time.time() + timeout
        while time.time() < end:
            item = next(x for x in self.service.repository.all_items(batch_id) if x["id"] == item_id)
            if item["state"] in states:
                return item
            time.sleep(.1)
        self.fail("worker timeout")

    def test_fake_test_then_remainder_preserves_exact_prompts(self):
        batch, plan, items = self.build(count=2)
        self.service.start(self.approval(batch, plan, [items[0]["id"]]), "TEST")
        self.wait_item(batch["id"], items[0]["id"], {"SUCCEEDED"})
        batch = self.service.repository.get_batch(batch["id"])
        self.service.start(self.approval(batch, plan, [items[1]["id"]], "REMAINDER"), "REMAINDER")
        self.wait_item(batch["id"], items[1]["id"], {"SUCCEEDED"})
        generated = [x for x in self.adapter.effects if x["kind"] == "generate"]
        self.assertEqual([x["prompt"] for x in generated], [x["prompt"] for x in items])
        self.assertEqual(len(list((self.temp / "saida").glob("*.mp4"))), 2)

    def test_no_spend_blocks_even_fully_confirmed_payload(self):
        self.service.close()
        locked = FlowLocalService(ROOT, data_dir=self.temp / "locked", adapter=self.adapter, no_spend=True)
        self.service = locked
        batch, plan, items = self.build(count=1)
        with self.assertRaises(FlowError) as caught:
            locked.start(self.approval(batch, plan, [items[0]["id"]]), "TEST")
        self.assertEqual(caught.exception.code, "NO_SPEND_ACTIVE")
        self.assertFalse(any(x["kind"] == "generate" for x in self.adapter.effects))

    def test_wrong_account_blocks_before_effect(self):
        self.service.close()
        self.adapter = FakeFlowAdapter(self.video, account="wrong@example.com")
        self.service = FlowLocalService(ROOT, data_dir=self.temp / "wrong", adapter=self.adapter)
        batch = self.service.create_batch({"name": "Wrong", "defaults": {"model_id": "omni-1.2", "aspect": "9:16", "duration_seconds": 3, "resolution": None}, "output_dir": str(self.temp / "wrong-output")})
        self.service.add_items(batch["id"], {"expected_revision": 1, "idempotency_key": str(uuid.uuid4()), "items": [{"id": "take_1", "kind": "t2v", "prompt": "x", "asset_id": None, "output_name": "wrong.mp4", "overrides": {}}]})
        check = self.service.preflight(batch["id"])
        self.assertFalse(check["ready"])
        self.assertEqual(check["errors"][0]["code"], "ACCOUNT_MISMATCH")
        self.assertFalse(self.adapter.effects)

    def test_duplicate_json_keys_and_nan_are_rejected(self):
        from backend.contracts import strict_loads
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            with self.assertRaises(FlowError):
                strict_loads(raw)

    def test_login_refresh_credits_and_logout(self):
        connected = self.service.login()
        self.assertTrue(connected["session"]["identity_matches_expected"])
        self.assertEqual(connected["balance"]["credits"], 1000)
        disconnected = self.service.logout()
        self.assertFalse(disconnected["session"]["connected"])
        self.assertIsNone(disconnected["balance"]["credits"])

    def test_reconcile_recovers_existing_video_without_second_generation(self):
        batch, plan, items = self.build(count=1)
        self.adapter.uncertain_items.add(items[0]["id"])
        started = self.service.start(self.approval(batch, plan, [items[0]["id"]]), "TEST")
        self.wait_item(batch["id"], items[0]["id"], {"NEEDS_RECONCILIATION"})
        staging = self.temp / "dados" / "staging" / f"{started['execution_id']}.mp4"
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.video, staging)

        result = self.service.reconcile(started["execution_id"])

        self.assertTrue(result["recovered"])
        self.assertEqual(result["state"], "SUCCEEDED")
        self.assertEqual(len([x for x in self.adapter.effects if x["kind"] == "generate"]), 1)
        self.assertEqual(len(list((self.temp / "saida").glob("*.mp4"))), 1)

    def test_image_generation_requires_confirmation_then_attaches_to_library(self):
        calls = []
        self.service.images._request = lambda prompt, references=None: calls.append((prompt, references or [])) or (self.PNG_1X1 + b"\0")
        with self.assertRaises(FlowError) as caught:
            self.service.generate_image({"prompt": "produto em fundo branco", "confirmed": False})
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertFalse(calls)

        reference = self.service.images.import_base64("referencia.png", base64.b64encode(self.PNG_1X1).decode("ascii"))

        image = self.service.generate_image({"prompt": "produto em fundo branco", "confirmed": True, "reference_asset_ids": [reference["id"]]})

        self.assertEqual(calls, [("produto em fundo branco", [reference["id"]])])
        self.assertEqual(image["source"], "generated")
        self.assertEqual(image["preview_url"], f"/media/assets/{image['id']}")
        library = self.service.images.list()
        self.assertEqual(library["items"][0]["id"], image["id"])
        self.assertTrue(Path(library["folder"]).is_dir())

    def test_library_import_is_persistent_and_reuses_verified_asset(self):
        encoded = base64.b64encode(self.PNG_1X1).decode("ascii")
        imported = self.service.images.import_base64("referencia.png", encoded)
        listed = self.service.images.list()
        self.assertEqual([item["id"] for item in listed["items"]], [imported["id"]])
        self.assertTrue((Path(listed["folder"]) / listed["items"][0]["name"]).is_file())

    def test_gpt_session_is_separate_from_flow_session(self):
        snapshot = self.service.snapshot()
        self.assertFalse(snapshot["gpt_session"]["connected"])
        self.assertFalse(snapshot["gpt_session"]["credential_available"])
        self.assertFalse(snapshot["session"]["connected"])
        self.service.images.session_path.parent.mkdir(parents=True)
        self.service.images.session_path.write_text(json.dumps({
            "version": 1, "authenticated": True, "source": "chatgpt-web-browser",
            "verified_at": "2026-11-24T00:00:00+00:00",
        }), encoding="utf-8")
        self.assertTrue(self.service.snapshot()["gpt_session"]["connected"])
        login = self.service.login_gpt()
        self.assertFalse(login["started"])
        self.service.logout_gpt()
        self.assertFalse(self.service.snapshot()["gpt_session"]["connected"])
        self.assertFalse(self.service.images.profile_dir.exists())

    def test_chrome_profile_busy_and_extension_required_mapping(self):
        busy = subprocess.CompletedProcess(
            args=["image-use"],
            returncode=1,
            stdout="",
            stderr="CHROME_PROFILE_BUSY: perfil em uso",
        )
        error = self.service.images._helper_failure(busy)
        self.assertEqual(error.code, "CHROME_PROFILE_BUSY")
        self.assertEqual(error.status, 409)

        ext = subprocess.CompletedProcess(
            args=["image-use"],
            returncode=1,
            stdout="",
            stderr="relay not connected — load the ab-connect extension",
        )
        ext_err = self.service.images._helper_failure(ext)
        self.assertEqual(ext_err.code, "CHROME_EXTENSION_REQUIRED")
        self.assertEqual(ext_err.status, 409)

    def test_image_use_request_enforces_web_backend_and_relay_check(self):
        self.service.images._write_session_marker()
        original_relay = self.service.images._relay_connected
        try:
            self.service.images._relay_connected = lambda _cu: False
            with self.assertRaises(FlowError) as caught:
                self.service.images._request("um gato")
            self.assertEqual(caught.exception.code, "CHROME_EXTENSION_REQUIRED")
        finally:
            self.service.images._relay_connected = original_relay


if __name__ == "__main__":
    unittest.main()
