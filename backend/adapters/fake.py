from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from ..errors import FlowError


class FakeFlowAdapter:
    """Explicit fixture boundary. HTTP, SQLite, worker and filesystem remain real."""

    verification = "MOCK"

    def __init__(self, fixture_mp4: Path, *, account: str, refusal_items: set[str] | None = None, uncertain_items: set[str] | None = None) -> None:
        self.fixture_mp4 = Path(fixture_mp4)
        self.account = account
        self.refusal_items = refusal_items or set()
        self.uncertain_items = uncertain_items or set()
        self.effects: list[dict] = []
        self.balance = 1000

    def inspect_session(self) -> dict:
        return {"ok": True, "identity": self.account, "credits": self.balance, "checked_at": "2026-11-23T12:00:00+00:00"}

    def inspect_capabilities(self) -> dict:
        return {"ok": True, "models": ["omni-1.2", "veo-fast", "veo-lite"], "evidence": "FakeFlowAdapter determinístico", "verification": "MOCK"}

    def login(self) -> dict:
        return {"ok": True}

    def logout(self) -> dict:
        return {"ok": True}

    def create_project(self, batch_id: str) -> str:
        project_id = "fixture-project-" + batch_id[:8]
        self.effects.append({"kind": "create_project", "batch_id": batch_id, "project_id": project_id})
        return project_id

    def upload_reference(self, *, batch_id: str, project_id: str | None, path: Path) -> dict:
        project_id = project_id or self.create_project(batch_id)
        media_id = "fixture-media-" + uuid.uuid4().hex[:12]
        self.effects.append({"kind": "upload", "batch_id": batch_id, "project_id": project_id, "media_id": media_id, "path": str(path)})
        return {"ok": True, "project_id": project_id, "media_id": media_id, "in_project": True}

    def generate_video(self, *, batch_id: str, project_id: str, item: dict, asset_path: Path | None, staging_path: Path) -> dict:
        self.effects.append({"kind": "generate", "batch_id": batch_id, "project_id": project_id, "item_id": item["id"], "prompt": item["prompt"]})
        if item["id"] in self.uncertain_items:
            raise FlowError("UNCERTAIN_EFFECT", "Fixture simulou timeout após intent.", status=503)
        if item["id"] in self.refusal_items:
            raise FlowError("REFUSED", "PUBLIC_ERROR_UNSAFE_GENERATION: fixture refusal", status=422, provider_message="PUBLIC_ERROR_UNSAFE_GENERATION: fixture refusal")
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.fixture_mp4, staging_path)
        self.balance -= 20
        return {"ok": True, "path": str(staging_path), "provider_operation_id": "fixture-operation-" + item["id"], "charged": "yes"}
