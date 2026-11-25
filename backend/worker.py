from __future__ import annotations

import hashlib
import threading
import time
import uuid
from pathlib import Path

from .errors import FlowError
from .recovery import classify


class Worker:
    def __init__(self, repository, assets, artifacts, adapter, data_dir: Path) -> None:
        self.repository = repository
        self.assets = assets
        self.artifacts = artifacts
        self.adapter = adapter
        self.data_dir = Path(data_dir).resolve()
        self.staging = self.data_dir / "staging"
        self.staging.mkdir(parents=True, exist_ok=True)
        self.owner = "worker-" + uuid.uuid4().hex
        self.wakeup = threading.Event()
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="flowlocal-worker", daemon=True)
        self.thread.start()

    def poke(self) -> None:
        self.wakeup.set()

    def close(self) -> None:
        self.closed.set()
        self.wakeup.set()
        self.thread.join(10)

    def _loop(self) -> None:
        while not self.closed.is_set():
            execution = self.repository.claim_execution(self.owner)
            if execution is None:
                self.wakeup.wait(1.5)
                self.wakeup.clear()
                continue
            self._process(execution)

    def _account(self, expected: str) -> dict:
        session = self.adapter.inspect_session()
        identity = session.get("identity")
        if not isinstance(identity, str) or identity.casefold() != expected.casefold():
            raise FlowError("ACCOUNT_MISMATCH", f"Conta ativa ({identity or 'não identificada'}) não corresponde a {expected}.", status=403)
        return session

    def _uploads_barrier(self, context: dict, plan: dict) -> str:
        batch_id = context["batch_id"]
        project_key = "project:" + batch_id
        project_id = self.repository.get_setting(project_key)
        unique_assets = []
        for item in plan["snapshot"]["items"]:
            if item.get("asset_id") and item["asset_id"] not in unique_assets:
                unique_assets.append(item["asset_id"])
        for asset_id in unique_assets:
            asset, path = self.assets.verified_path(asset_id)
            confirmed = self.repository.upload(batch_id, asset_id, project_id) if project_id else None
            if confirmed and confirmed["sha256"] == asset["sha256"]:
                continue
            response = self.adapter.upload_reference(batch_id=batch_id, project_id=project_id, path=path)
            project_id = response.get("project_id")
            if not project_id:
                raise FlowError("UPLOAD_ROUTE_UNAVAILABLE", "Upload não confirmou o projeto.", status=422)
            self.repository.set_setting(project_key, project_id)
            self.repository.save_upload(batch_id, asset_id, project_id, response.get("media_id"), asset["sha256"], "CONFIRMED" if response.get("media_id") else "FAILED", response)
            if not response.get("media_id"):
                raise FlowError("UPLOAD_ROUTE_UNAVAILABLE", "Upload não devolveu media_id.", status=422)
        if not project_id and unique_assets:
            raise FlowError("UPLOAD_ROUTE_UNAVAILABLE", "Nenhum projeto foi confirmado.", status=422)
        return project_id or self.adapter.create_project(batch_id)

    def _process(self, execution: dict) -> None:
        execution_id = execution["id"]
        context = self.repository.execution_context(execution_id)
        item = context["item"]
        possible_effect = False
        try:
            self._account(context["expected_account"])
            plan = self.repository.get_plan(context["batch_id"], context["plan_hash"])
            project_id = self._uploads_barrier(context, plan)
            planned_item = next(x for x in plan["snapshot"]["items"] if x["id"] == item["id"])
            destination = Path(plan["snapshot"]["output_dir"]) / planned_item["output_name"]
            if destination.exists():
                raise FlowError("OUTPUT_EXISTS", "O arquivo de saída apareceu depois do plano; a geração foi bloqueada.", status=409)
            asset_path = None
            if planned_item["kind"] == "i2v":
                _, asset_path = self.assets.verified_path(planned_item["asset_id"])
            self._account(context["expected_account"])
            self.repository.set_item_state(item["id"], "SUBMITTING")
            self.repository.set_execution(execution_id, "RUNNING", result={"message": "Intenção persistida antes do envio."}, intent=True)
            possible_effect = True
            staging = self.staging / (execution_id + ".mp4")
            if staging.exists():
                raise FlowError("OUTPUT_EXISTS", "Staging já existe; reconciliação necessária.", status=409)
            response = self.adapter.generate_video(batch_id=context["batch_id"], project_id=project_id, item=planned_item, asset_path=asset_path, staging_path=staging)
            self.repository.set_item_state(item["id"], "VERIFYING")
            artifact = self.artifacts.validate_publish(execution_id, Path(response["path"]), destination, planned_item["settings"])
            self.repository.set_item_state(item["id"], "SUCCEEDED")
            self.repository.set_execution(execution_id, "SUCCEEDED", result={"message": "Vídeo validado e publicado.", "artifact_id": artifact["id"], "charged": response.get("charged", "unknown"), "provider_message": None}, provider_operation_id=response.get("provider_operation_id"))
            if context["scope"] == "TEST":
                with self.repository.transaction(immediate=True) as conn:
                    conn.execute("UPDATE batches SET state='AWAITING_REVIEW',updated_at=datetime('now') WHERE id=?", (context["batch_id"],))
            else:
                self.repository.batch_terminal_state(context["batch_id"])
        except Exception as exc:
            category = classify(exc, possible_effect=possible_effect)
            if category == "REFUSED":
                item_state, execution_state = "REFUSED", "FAILED"
            elif category == "UNCERTAIN_EFFECT":
                item_state, execution_state = "NEEDS_RECONCILIATION", "NEEDS_RECONCILIATION"
            else:
                item_state, execution_state = "FAILED", "FAILED"
            self.repository.set_item_state(item["id"], item_state)
            result = {"message": str(exc), "provider_message": exc.provider_message if isinstance(exc, FlowError) else None, "charged": "maybe" if possible_effect else "no"}
            self.repository.set_execution(execution_id, execution_state, result=result, error_code=category)
            if context["scope"] == "TEST":
                with self.repository.transaction(immediate=True) as conn:
                    conn.execute("UPDATE batches SET state=? WHERE id=?", ("NEEDS_RECONCILIATION" if execution_state == "NEEDS_RECONCILIATION" else "FAILED", context["batch_id"]))
            else:
                self.repository.batch_terminal_state(context["batch_id"])

        # Pause becomes effective only between operations.
        batch = self.repository.get_batch(context["batch_id"])
        if batch["pause_requested"]:
            with self.repository.transaction(immediate=True) as conn:
                conn.execute("UPDATE batches SET state='PAUSED' WHERE id=?", (context["batch_id"],))
