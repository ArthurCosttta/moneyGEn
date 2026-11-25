from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from .errors import FlowError

MAX_JSON_BYTES = 5 * 1024 * 1024
MAX_ITEMS = 1000
MAX_PROMPT = 20_000
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
OUTPUT_RE = re.compile(r'^[^\\/:*?"<>|\x00-\x1f]+\.mp4$', re.I)
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MODEL_ALIASES = {
    "omni 1.2 flash": "omni-1.2",
    "omni-1.2-flash": "omni-1.2",
    "veo 3.1 fast": "veo-fast",
    "veo 3.1 lite": "veo-lite",
}


@dataclass(frozen=True)
class FieldIssue:
    pointer: str
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"pointer": self.pointer, "code": self.code, "message": self.message}


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FlowError("INVALID_JSON", f"Chave JSON repetida: {key}", pointer=f"/{key}")
        result[key] = value
    return result


def strict_loads(raw: bytes | str) -> Any:
    if isinstance(raw, bytes):
        if len(raw) > MAX_JSON_BYTES:
            raise FlowError("PAYLOAD_LIMIT", "JSON maior que 5 MiB.", status=413)
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FlowError("INVALID_JSON", "O JSON precisa estar em UTF-8.") from exc
    try:
        return json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                FlowError("INVALID_JSON", f"Número JSON não permitido: {value}")
            ),
        )
    except FlowError:
        raise
    except (ValueError, TypeError) as exc:
        raise FlowError("INVALID_JSON", "JSON inválido: " + str(exc)) from exc


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_output_name(value: str, pointer: str = "/output_name") -> str:
    if not isinstance(value, str) or not (5 <= len(value) <= 180) or not OUTPUT_RE.fullmatch(value):
        raise FlowError("INVALID_OUTPUT_NAME", "Use um nome de arquivo que termine em .mp4.", pointer=pointer)
    if value.endswith((".", " ")) or value.split(".", 1)[0].upper() in RESERVED:
        raise FlowError("INVALID_OUTPUT_NAME", "Nome reservado ou inválido no Windows.", pointer=pointer)
    return value


def _settings(value: Any, pointer: str, *, required: bool) -> tuple[dict, list[FieldIssue]]:
    issues: list[FieldIssue] = []
    if value is None and not required:
        return {}, issues
    if not isinstance(value, dict):
        return {}, [FieldIssue(pointer, "TYPE", "Configuração precisa ser um objeto.")]
    allowed = {"model_id", "aspect", "duration_seconds", "resolution"}
    for key in value.keys() - allowed:
        issues.append(FieldIssue(f"{pointer}/{key}", "UNKNOWN_FIELD", "Campo não reconhecido."))
    result = dict(value)
    if required:
        for key in allowed:
            if key not in result:
                issues.append(FieldIssue(f"{pointer}/{key}", "REQUIRED", "Campo obrigatório."))
    if "model_id" in result and (not isinstance(result["model_id"], str) or not result["model_id"] or len(result["model_id"]) > 128):
        issues.append(FieldIssue(f"{pointer}/model_id", "VALUE", "Modelo inválido."))
    if "aspect" in result and result["aspect"] not in ("9:16", "16:9", "1:1"):
        issues.append(FieldIssue(f"{pointer}/aspect", "VALUE", "Formato inválido."))
    duration = result.get("duration_seconds")
    if "duration_seconds" in result and duration is not None and (type(duration) is not int or not 1 <= duration <= 60):
        issues.append(FieldIssue(f"{pointer}/duration_seconds", "VALUE", "Duração precisa estar entre 1 e 60 segundos."))
    resolution = result.get("resolution")
    if "resolution" in result and resolution is not None and (not isinstance(resolution, str) or len(resolution) > 32):
        issues.append(FieldIssue(f"{pointer}/resolution", "VALUE", "Resolução inválida."))
    return result, issues


def validate_items(rows: Any, *, base_pointer: str = "/items") -> tuple[list[dict], list[FieldIssue]]:
    issues: list[FieldIssue] = []
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ITEMS:
        return [], [FieldIssue(base_pointer, "ITEM_COUNT", "Inclua de 1 a 1.000 itens.")]
    accepted: list[dict] = []
    ids: set[str] = set()
    outputs: set[str] = set()
    allowed = {"id", "kind", "prompt", "image", "asset_id", "output_name", "overrides", "title", "source_index"}
    for index, row in enumerate(rows):
        pointer = f"{base_pointer}/{index}"
        if not isinstance(row, dict):
            issues.append(FieldIssue(pointer, "TYPE", "Item precisa ser um objeto."))
            continue
        row_issues: list[FieldIssue] = []
        for key in row.keys() - allowed:
            row_issues.append(FieldIssue(f"{pointer}/{key}", "UNKNOWN_FIELD", "Campo não reconhecido."))
        item_id = row.get("id")
        if not isinstance(item_id, str) or not ID_RE.fullmatch(item_id):
            row_issues.append(FieldIssue(f"{pointer}/id", "VALUE", "ID inválido."))
        elif item_id in ids:
            row_issues.append(FieldIssue(f"{pointer}/id", "DUPLICATE_ID", "ID repetido."))
        kind = row.get("kind", "i2v")
        if kind not in ("i2v", "t2v"):
            row_issues.append(FieldIssue(f"{pointer}/kind", "VALUE", "kind precisa ser i2v ou t2v."))
        prompt = row.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT or "\x00" in (prompt or ""):
            row_issues.append(FieldIssue(f"{pointer}/prompt", "VALUE", "Prompt obrigatório, com até 20.000 caracteres."))
        image = row.get("asset_id", row.get("image"))
        if kind == "i2v" and (not isinstance(image, str) or not image):
            row_issues.append(FieldIssue(f"{pointer}/image", "REQUIRED", "i2v exige uma imagem."))
        if kind == "t2v" and image is not None:
            row_issues.append(FieldIssue(f"{pointer}/image", "VALUE", "t2v exige image=null."))
        output = row.get("output_name")
        if output is not None:
            try:
                validate_output_name(output, f"{pointer}/output_name")
                folded = output.casefold()
                if folded in outputs:
                    row_issues.append(FieldIssue(f"{pointer}/output_name", "DUPLICATE_OUTPUT", "Nome MP4 repetido."))
                outputs.add(folded)
            except FlowError as exc:
                row_issues.append(FieldIssue(exc.pointer or f"{pointer}/output_name", exc.code, exc.message))
        overrides, setting_issues = _settings(row.get("overrides"), f"{pointer}/overrides", required=False)
        row_issues.extend(setting_issues)
        if row_issues:
            issues.extend(row_issues)
            continue
        ids.add(item_id)
        accepted.append({
            "id": item_id,
            "kind": kind,
            "prompt": prompt,
            "asset_id": image,
            "output_name": output,
            "overrides": overrides,
            "title": row.get("title") if isinstance(row.get("title"), str) else None,
            "source_index": row.get("source_index", index),
        })
    return accepted, issues


def normalize_document(document: Any) -> tuple[dict, list[str], list[FieldIssue]]:
    """Normalize the canonical format or legacy field aliases without rewriting prompts."""
    aliases: list[str] = []
    if isinstance(document, list):
        rows, top = document, {}
        aliases.append("root:list")
    elif isinstance(document, dict):
        top = document
        if document.get("schema_version") == 1 and "items" in document:
            unknown = set(document) - {"schema_version", "name", "defaults", "output_dir", "max_credits", "items"}
            issues = [FieldIssue(f"/{k}", "UNKNOWN_FIELD", "Campo não reconhecido.") for k in sorted(unknown)]
            defaults, setting_issues = _settings(document.get("defaults"), "/defaults", required=True)
            items, item_issues = validate_items(document.get("items"))
            if not isinstance(document.get("name"), str) or not document["name"].strip() or len(document["name"]) > 200:
                issues.append(FieldIssue("/name", "VALUE", "Nome obrigatório, com até 200 caracteres."))
            if document.get("output_dir") is not None and not isinstance(document.get("output_dir"), str):
                issues.append(FieldIssue("/output_dir", "TYPE", "output_dir precisa ser texto ou null."))
            cap = document.get("max_credits")
            if cap is not None and (type(cap) is not int or cap < 0):
                issues.append(FieldIssue("/max_credits", "VALUE", "max_credits precisa ser inteiro ou null."))
            return {
                "schema_version": 1,
                "name": document.get("name", "Lote importado"),
                "defaults": defaults,
                "output_dir": document.get("output_dir"),
                "max_credits": cap,
                "items": items,
            }, aliases, issues + setting_issues + item_issues
        rows = next((document[k] for k in ("takes", "videos", "items", "prompts") if k in document), None)
        aliases.extend(k for k in ("takes", "videos", "prompts") if k in document)
    else:
        return {}, aliases, [FieldIssue("/", "TYPE", "O JSON precisa ser uma lista ou objeto.")]
    if not isinstance(rows, list):
        return {}, aliases, [FieldIssue("/items", "REQUIRED", "Lista de itens ausente.")]
    converted = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            converted.append(row)
            continue
        def pick(keys: tuple[str, ...], default=None):
            for key in keys:
                if key in row:
                    if key != keys[0]:
                        aliases.append(f"items/{index}/{key}")
                    return row[key]
            return default
        image = pick(("image", "imagem", "image_path", "ref", "arquivo"))
        prompt = pick(("prompt", "video_prompt", "prompt_video", "Prompt Video", "text"))
        output = pick(("output_name", "video", "output_filename"))
        converted.append({
            "id": str(pick(("id",), f"take_{index+1:04d}")),
            "kind": pick(("kind",), "i2v" if image is not None else "t2v"),
            "prompt": prompt,
            "image": image,
            "output_name": output,
            "overrides": pick(("overrides",), {}),
            "source_index": index,
        })
    model = top.get("model_id", top.get("model", "omni-1.2")) if isinstance(top, dict) else "omni-1.2"
    if isinstance(model, str):
        model = MODEL_ALIASES.get(model.casefold(), model)
    duration = top.get("duration_seconds", top.get("duration", 6)) if isinstance(top, dict) else 6
    if duration in ("auto", "default"):
        duration = None
    resolution = top.get("resolution") if isinstance(top, dict) else None
    if resolution in ("auto", "default"):
        resolution = None
    defaults = {"model_id": model, "aspect": top.get("aspect", "9:16"), "duration_seconds": duration, "resolution": resolution}
    normalized, item_issues = validate_items(converted)
    _, setting_issues = _settings(defaults, "/defaults", required=True)
    return {
        "schema_version": 1,
        "name": top.get("name", "Lote importado") if isinstance(top, dict) else "Lote importado",
        "defaults": defaults,
        "output_dir": top.get("output_dir") if isinstance(top, dict) else None,
        "max_credits": top.get("max_credits") if isinstance(top, dict) else None,
        "items": normalized,
    }, sorted(set(aliases)), setting_issues + item_issues
