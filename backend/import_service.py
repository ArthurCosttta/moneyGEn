from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from .contracts import canonical_json, normalize_document
from .errors import FlowError


class ImportService:
    def __init__(self, repository, assets) -> None:
        self.repository = repository
        self.assets = assets

    def preview(self, documents: list[dict], bindings: list[dict] | None = None, images_dir: str | None = None, *, native: bool = False) -> dict:
        if not isinstance(documents, list) or not 1 <= len(documents) <= 20:
            raise FlowError("SCHEMA_VALIDATION", "Selecione de 1 a 20 JSONs.")
        binding_map = {x.get("reference"): x.get("asset_id") for x in (bindings or []) if isinstance(x, dict)}
        base_override = Path(images_dir).resolve(strict=True) if images_dir else None
        valid_items, errors, unresolved, aliases, defaults = [], [], [], [], None
        name = "Lote importado"
        output_dir = None
        max_credits = None
        source_index = 0
        for doc_index, source in enumerate(documents):
            normalized, used_aliases, issues = normalize_document(source.get("document"))
            aliases.extend(used_aliases)
            errors.extend({**x.as_dict(), "pointer": f"/documents/{doc_index}" + x.pointer} for x in issues)
            if defaults is None:
                defaults = normalized.get("defaults")
                name = normalized.get("name", name)
                output_dir = normalized.get("output_dir")
                max_credits = normalized.get("max_credits")
            elif normalized.get("defaults") != defaults:
                errors.append({"pointer": f"/documents/{doc_index}/defaults", "code": "CONFLICTING_DEFAULTS", "message": "Os JSONs têm configurações globais diferentes."})
            manifest_dir = Path(source["manifest_dir"]).resolve(strict=True) if native and source.get("manifest_dir") else None
            for item in normalized.get("items", []):
                item = dict(item)
                image = item.pop("asset_id", None)
                if item["kind"] == "i2v":
                    asset_id = binding_map.get(image)
                    if not asset_id and native:
                        candidate = Path(image)
                        base = base_override or manifest_dir
                        if not candidate.is_absolute() and base:
                            candidate = base / candidate
                        try:
                            asset_id = self.assets.stage_file(candidate, allowed_root=base).get("id")
                        except (FlowError, OSError):
                            asset_id = None
                    if not asset_id:
                        unresolved.append(str(image))
                    item["asset_id"] = asset_id
                else:
                    item["asset_id"] = None
                item["source_index"] = source_index
                source_index += 1
                valid_items.append(item)
        preview_data = {"name": name, "defaults": defaults or {"model_id": "omni-1.2", "aspect": "9:16", "duration_seconds": 6, "resolution": None}, "output_dir": output_dir, "max_credits": max_credits, "items": valid_items, "errors": errors, "unresolved_images": sorted(set(unresolved)), "aliases": sorted(set(aliases))}
        preview_hash = hashlib.sha256(canonical_json(preview_data).encode()).hexdigest()
        preview_id = self.repository.save_import(preview_hash, preview_data)
        return {"preview_id": preview_id, "preview_hash": preview_hash, "valid_items": valid_items, "valid_indexes": [i for i, x in enumerate(valid_items) if x.get("kind") == "t2v" or x.get("asset_id")], "errors": errors, "unresolved_images": sorted(set(unresolved)), "aliases": sorted(set(aliases)), "cancelled": False, "defaults": preview_data["defaults"], "name": name, "output_dir": output_dir, "max_credits": max_credits}

    def commit(self, import_id: str, payload: dict) -> dict:
        preview = self.repository.get_import(import_id)
        if preview["preview_hash"] != payload.get("preview_hash"):
            raise FlowError("PLAN_STALE", "A prévia mudou; importe novamente.", status=409)
        data = preview["preview"]
        indexes = payload.get("accepted_indexes")
        if not isinstance(indexes, list) or not indexes:
            raise FlowError("SCHEMA_VALIDATION", "Selecione pelo menos um item válido.")
        chosen = []
        for index in indexes:
            if type(index) is not int or not 0 <= index < len(data["items"]):
                raise FlowError("SCHEMA_VALIDATION", "Índice de item inválido.")
            item = dict(data["items"][index])
            if item["kind"] == "i2v" and not item.get("asset_id"):
                raise FlowError("SCHEMA_VALIDATION", "Associe todas as imagens antes de importar.", pointer=f"/items/{index}/image")
            # Revalidate immutable asset bytes immediately before commit.
            if item.get("asset_id"):
                self.assets.verified_path(item["asset_id"])
            if not item.get("id"):
                item["id"] = "take_" + uuid.uuid4().hex[:12]
            chosen.append(item)
        result = self.repository.add_items(payload["batch_id"], payload["expected_revision"], chosen, payload["idempotency_key"])
        return {"batch_id": result["batch_id"], "revision": result["revision"], "imported_item_ids": result["item_ids"], "rejected_indexes": [i for i in range(len(data["items"])) if i not in indexes]}
