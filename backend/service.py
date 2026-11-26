from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from .adapters.gflow import GFlowAdapter
from .approvals import ApprovalService
from .artifacts import ArtifactService
from .assets import AssetService
from .calendar_service import CalendarService
from .contracts import normalize_document, validate_items
from .errors import FlowError
from .flow_runner import FlowRunner
from .gpt_chat import GptChatService
from .import_service import ImportService
from .image_generation import ImageGenerationService
from .meta_ads_service import MetaAdsService
from .model_registry import ModelRegistry
from .planner import Planner
from .repository import Repository
from .spy_service import SpyService
from .video_editor import VideoEditorService
from .worker import Worker

APP_VERSION = "3.0.0"
EXPECTED_ACCOUNT = "arthurcostacopy@gmail.com"


class FlowLocalService:
    def __init__(self, root: Path, *, data_dir: Path | None = None, adapter=None, no_spend: bool = False) -> None:
        self.root = Path(root).resolve()
        env_data = os.environ.get("FLOWLOCAL_DATA_DIR")
        self.data = Path(data_dir or env_data or (self.root / "dados")).resolve()
        self.output = self.root / "saida"
        self.output.mkdir(parents=True, exist_ok=True)
        self.repository = Repository(self.data, self.root / "migrations", EXPECTED_ACCOUNT)
        self.assets = AssetService(self.data, self.repository)
        self.adapter = adapter or GFlowAdapter(self.root, self.data, self.output, expected_account=EXPECTED_ACCOUNT)
        self.models = ModelRegistry(self.adapter)
        if getattr(self.adapter, "verification", None) == "MOCK":
            self.models.inspect()
        self.planner = Planner(self.repository, self.assets, self.models)
        self.imports = ImportService(self.repository, self.assets)
        self.images = ImageGenerationService(self.data, self.assets)
        self.approvals = ApprovalService(self.repository, no_spend=no_spend)
        self.artifacts = ArtifactService(self.repository)
        self.worker = Worker(self.repository, self.assets, self.artifacts, self.adapter, self.data)
        self.no_spend = no_spend
        self.session = {"connected": False, "identity": None, "identity_matches_expected": False, "checked_at": None}
        self.balance = {"credits": None, "checked_at": None, "source": None}
        self.exports: dict[str, dict] = {}
        self.gpt_chat = GptChatService()
        self.editor = VideoEditorService(self.root, self.data, self.repository)
        self.meta_ads = MetaAdsService(self.data, self.repository, self.assets, self.editor, no_spend=no_spend)
        self.calendar = CalendarService(self.root, self.data, self.repository, self.editor, self.meta_ads)
        self.spy = SpyService(self.root, self.data, self.repository, editor=self.editor, gpt_chat=self.gpt_chat)
        self.flows = FlowRunner(self)
        self.spy.flow_runner = self.flows

    def close(self) -> None:
        self.flows.close()
        self.worker.close()

    def preflight_account_only(self) -> bool:
        session = self.adapter.inspect_session()
        identity = session.get("identity")
        matches = isinstance(identity, str) and identity.casefold() == EXPECTED_ACCOUNT.casefold()
        self.session = {"connected": True, "identity": identity, "identity_matches_expected": matches, "checked_at": session.get("checked_at")}
        self.balance = {"credits": session.get("credits"), "checked_at": session.get("checked_at"), "source": "gflow-cli"}
        return matches

    def health(self) -> dict:
        return {"ready": True, "version": APP_VERSION, "migration_version": self.repository.migration_version()}

    def snapshot(self) -> dict:
        return {"version": APP_VERSION, "session": self.session, "gpt_session": self.images.session_status(), "meta_ads": self.meta_ads.status(), "editor": self.editor.capabilities(), "spy": self.spy.capabilities(), "balance": self.balance, "selected_batch_id": self.repository.selected_batch_id(), "no_spend": self.no_spend, "capabilities": [{"id": x["model_id"], "available": x["available"], "reason": None if x["available"] else x["evidence"], "verification": x.get("verification", "UNVERIFIED")} for x in self.models.list()], "expected_account": EXPECTED_ACCOUNT}

    def create_batch(self, body: dict) -> dict:
        name = body.get("name")
        defaults = body.get("defaults")
        if not isinstance(name, str) or not name.strip() or not isinstance(defaults, dict):
            raise FlowError("SCHEMA_VALIDATION", "Informe nome e configurações do lote.")
        out_dir = str(body.get("output_dir") or "").strip()
        if not out_dir and body.get("auto_output"):
            import re
            slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-_")[:36] or "lote"
            candidate = (self.output / f"{slug}-{uuid.uuid4().hex[:6]}").resolve()
            candidate.mkdir(parents=True, exist_ok=True)
            out_dir = str(candidate)
        batch = self.repository.create_batch(name, defaults, body.get("max_credits"), output_dir=out_dir)
        self.repository.select_batch(batch["id"])
        return batch

    def add_items(self, batch_id: str, body: dict) -> dict:
        items, issues = validate_items(body.get("items"))
        if issues:
            first = issues[0]
            raise FlowError("SCHEMA_VALIDATION", first.message, pointer=first.pointer)
        return self.repository.add_items(batch_id, body.get("expected_revision"), items, body.get("idempotency_key"))

    def preflight(self, batch_id: str, plan_hash: str | None = None) -> dict:
        capabilities = self.models.inspect()
        session = self.adapter.inspect_session()
        identity = session.get("identity")
        self.session = {"connected": True, "identity": identity, "identity_matches_expected": isinstance(identity, str) and identity.casefold() == EXPECTED_ACCOUNT.casefold(), "checked_at": session.get("checked_at")}
        self.balance = {"credits": session.get("credits"), "checked_at": session.get("checked_at"), "source": "gflow-cli"}
        errors = []
        if not self.session["identity_matches_expected"]:
            errors.append({"pointer": "/account", "code": "ACCOUNT_MISMATCH", "message": "A conta ativa não é a conta configurada."})
        if plan_hash:
            self.repository.get_plan(batch_id, plan_hash)
        return {"batch_id": batch_id, "plan_hash": plan_hash, "ready": not errors and any(x["available"] for x in capabilities), "session": self.session, "balance": self.balance, "quote": self.repository.get_plan(batch_id, plan_hash)["quote"] if plan_hash else None, "errors": errors, "capabilities": capabilities}

    def refresh_account(self) -> dict:
        session = self.adapter.inspect_session()
        identity = session.get("identity")
        matches = isinstance(identity, str) and identity.casefold() == EXPECTED_ACCOUNT.casefold()
        self.session = {"connected": True, "identity": identity, "identity_matches_expected": matches, "checked_at": session.get("checked_at")}
        self.balance = {"credits": session.get("credits"), "checked_at": session.get("checked_at"), "source": "gflow-cli"}
        return {"session": self.session, "balance": self.balance}

    def login(self) -> dict:
        self.adapter.login()
        return self.refresh_account()

    def logout(self) -> dict:
        self.adapter.logout()
        self.session = {"connected": False, "identity": None, "identity_matches_expected": False, "checked_at": None}
        self.balance = {"credits": None, "checked_at": None, "source": None}
        return {"session": self.session, "balance": self.balance}

    def login_gpt(self) -> dict:
        return self.images.start_login()

    def logout_gpt(self) -> dict:
        return {"session": self.images.logout()}

    def verify_gpt(self) -> dict:
        return {"session": self.images.verify_session()}

    def reconcile(self, execution_id: str) -> dict:
        execution = self.repository.execution(execution_id)
        if execution.get("artifact_id"):
            return {"execution_id": execution_id, "state": "SUCCEEDED", "artifact_id": execution["artifact_id"], "recovered": False}
        if execution["state"] != "NEEDS_RECONCILIATION":
            raise FlowError("PLAN_STALE", "Esta execução não requer recuperação.", status=409)
        context = self.repository.execution_context(execution_id)
        plan = self.repository.get_plan(context["batch_id"], context["plan_hash"])
        planned = next((x for x in plan["snapshot"]["items"] if x["id"] == context["item"]["id"]), None)
        if not planned:
            raise FlowError("PLAN_STALE", "O item não existe mais no plano.", status=409)
        staging = self.data / "staging" / f"{execution_id}.mp4"
        if not staging.is_file():
            raise FlowError("UNCERTAIN_EFFECT", "O arquivo local não foi encontrado. Confira o Flow antes de autorizar outra geração.", status=409)
        destination = Path(plan["snapshot"]["output_dir"]) / planned["output_name"]
        artifact = self.artifacts.validate_publish(execution_id, staging, destination, planned["settings"])
        provider_id = None
        try:
            provider_id = json.loads(execution.get("provider_message") or "{}").get("media_id")
        except (ValueError, TypeError):
            pass
        self.repository.set_item_state(context["item"]["id"], "SUCCEEDED")
        self.repository.set_execution(execution_id, "SUCCEEDED", result={"message": "Vídeo existente validado e recuperado sem nova geração.", "artifact_id": artifact["id"], "charged": execution.get("charged") or "maybe", "recovered": True}, provider_operation_id=provider_id)
        if context["scope"] == "TEST":
            with self.repository.transaction(immediate=True) as conn:
                conn.execute("UPDATE batches SET state='AWAITING_REVIEW',updated_at=datetime('now') WHERE id=?", (context["batch_id"],))
        else:
            self.repository.batch_terminal_state(context["batch_id"])
        return {"execution_id": execution_id, "state": "SUCCEEDED", "artifact_id": artifact["id"], "recovered": True}

    def start(self, body: dict, scope: str) -> dict:
        result = self.approvals.start(body, scope)
        self.worker.poke()
        return result

    def start_direct_queue(self, body: dict) -> dict:
        result = self.approvals.start_direct_queue(body)
        self.worker.poke()
        return result

    def generate_image(self, body: dict) -> dict:
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise FlowError("SCHEMA_VALIDATION", "Escreva um prompt de imagem com até 4.000 caracteres.")
        if body.get("confirmed") is not True:
            raise FlowError("APPROVAL_REQUIRED", "Confirme na interface antes de gerar a imagem.", status=409)
        if self.no_spend:
            raise FlowError("NO_SPEND_ACTIVE", "A geração de imagem está bloqueada pelo modo sem gasto.", status=409)
        references = body.get("reference_asset_ids") or []
        if not isinstance(references, list) or len(references) > 4 or any(not isinstance(value, str) or not value for value in references):
            raise FlowError("SCHEMA_VALIDATION", "Anexe no máximo quatro imagens de referência válidas.")
        return self.images.generate(prompt.strip(), references)

    def create_export(self, body: dict) -> dict:
        export_id = str(uuid.uuid4())
        self.exports[export_id] = {"export_id": export_id, "execution_id": str(uuid.uuid4()), "state": "PENDING", "download_url": None, "sha256": None, "bytes": None, "included": 0, "excluded": 0, "error_code": None}
        def work():
            try:
                result = self.artifacts.export_zip(body["batch_id"], body["artifact_ids"], Path(self.repository.get_batch(body["batch_id"])["output_dir"]))
                self.exports[export_id].update(state="SUCCEEDED", download_url=f"/media/exports/{export_id}", **{k: result[k] for k in ("sha256", "bytes", "included", "excluded")}, path=result["path"])
            except FlowError as exc:
                self.exports[export_id].update(state="FAILED", error_code=exc.code)
        threading.Thread(target=work, daemon=True).start()
        return {"export_id": export_id, "execution_id": self.exports[export_id]["execution_id"], "state": "PENDING", "correlation_id": str(uuid.uuid4())}

    def export_status(self, export_id: str) -> dict:
        if export_id not in self.exports:
            raise FlowError("ARTIFACT_NOT_FOUND", "Exportação não encontrada.", status=404)
        return {k: v for k, v in self.exports[export_id].items() if k != "path"}

    def export_path(self, export_id: str) -> Path:
        status = self.exports.get(export_id)
        if not status or status["state"] != "SUCCEEDED":
            raise FlowError("ARTIFACT_NOT_FOUND", "ZIP ainda não está pronto.", status=404)
        return Path(status["path"])

    def legacy_export_path(self, batch_id: str, name: str) -> Path:
        if Path(name).name != name:
            raise FlowError("ARTIFACT_NOT_FOUND", "Arquivo inválido.", status=404)
        stored = self.data / "lotes" / f"{batch_id}.json"
        if not stored.is_file():
            raise FlowError("BATCH_NOT_FOUND", "Lote legado não encontrado.", status=404)
        batch = json.loads(stored.read_text(encoding="utf-8"))
        allowed = {x.get("path") for x in batch.get("rows", []) if x.get("path")} | {"relatorio.json", "relatorio.csv"}
        if name not in allowed:
            raise FlowError("ARTIFACT_NOT_FOUND", "Arquivo legado não disponível.", status=404)
        base = Path(batch.get("output_dir", self.output / batch_id)) if name.endswith(".mp4") else Path(batch.get("report_dir", self.output / batch_id))
        path = (base.resolve() / name).resolve()
        if not path.is_relative_to(base.resolve()) or not path.is_file():
            raise FlowError("ARTIFACT_NOT_FOUND", "Arquivo legado não disponível.", status=404)
        return path
