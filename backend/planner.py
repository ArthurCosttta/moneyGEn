from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from .budget import unknown_quote
from .contracts import canonical_json, validate_output_name
from .errors import FlowError
from .repository import Repository


class Planner:
    def __init__(self, repository: Repository, assets, models) -> None:
        self.repository = repository
        self.assets = assets
        self.models = models

    def plan(self, batch_id: str, expected_revision: int) -> dict:
        batch = self.repository.get_batch(batch_id)
        if batch["revision"] != expected_revision:
            raise FlowError("REVISION_CONFLICT", "O pedido foi alterado em outra operação.", status=409)
        items = self.repository.all_items(batch_id)
        if not items:
            raise FlowError("SCHEMA_VALIDATION", "Adicione pelo menos um item.")
        output_dir = batch.get("output_dir")
        if not output_dir:
            raise FlowError("SCHEMA_VALIDATION", "Escolha a pasta de saída.", pointer="/output_dir")
        destination = Path(output_dir)
        if not destination.is_absolute():
            raise FlowError("SCHEMA_VALIDATION", "A pasta de saída precisa ser absoluta.", pointer="/output_dir")
        destination.mkdir(parents=True, exist_ok=True)
        try:
            fd, temp = tempfile.mkstemp(prefix=".flowlocal-write-", dir=destination)
            os.close(fd)
            os.unlink(temp)
        except OSError as exc:
            raise FlowError("STORAGE", "Sem permissão de escrita na pasta de saída.") from exc
        names: set[str] = set()
        planned = []
        errors = []
        for item in items:
            settings = {**batch["defaults"], **item["settings"]}
            try:
                self.models.validate(settings, item["kind"])
                asset_hash = None
                if item["kind"] == "i2v":
                    asset, _ = self.assets.verified_path(item["asset_id"])
                    asset_hash = asset["sha256"]
                output_name = item["output_name"] or f"{item['position']+1:04d}-{item['id'][:12]}.mp4"
                validate_output_name(output_name, f"/items/{item['position']}/output_name")
                if output_name.casefold() in names:
                    raise FlowError("INVALID_OUTPUT_NAME", "Nome MP4 repetido.", pointer=f"/items/{item['position']}/output_name")
                if (destination / output_name).exists():
                    raise FlowError("OUTPUT_EXISTS", "O arquivo de saída já existe: " + output_name, status=409, pointer=f"/items/{item['position']}/output_name")
                names.add(output_name.casefold())
                planned.append({"id": item["id"], "position": item["position"], "kind": item["kind"], "prompt": item["prompt"], "asset_id": item["asset_id"], "asset_sha256": asset_hash, "settings": settings, "output_name": output_name})
            except FlowError as exc:
                errors.append({"pointer": exc.pointer or f"/items/{item['position']}", "code": exc.code, "message": exc.message})
        snapshot = {"schema_version": 1, "batch_id": batch_id, "revision": expected_revision, "item_ids": [x["id"] for x in planned], "items": planned, "output_dir": str(destination.resolve()), "expected_account": batch["expected_account"]}
        plan_hash = hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()
        quote = unknown_quote(batch["expected_account"])
        validation = {"valid": not errors and len(planned) == len(items), "errors": errors, "free_bytes": shutil.disk_usage(destination).free, "reservation_policy": "Nenhuma reserva fixa; espaço é reavaliado antes de cada download."}
        saved = self.repository.save_plan(batch_id, expected_revision, plan_hash, snapshot, quote, validation)
        return {"batch_id": batch_id, "revision": expected_revision, "plan_hash": plan_hash, "valid": validation["valid"], "item_count": len(planned), "errors": errors, "quote": quote, "free_bytes": validation["free_bytes"], "plan_id": saved["id"]}
