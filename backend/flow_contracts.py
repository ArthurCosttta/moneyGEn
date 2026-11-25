from __future__ import annotations

import json
import re
from typing import Any

from .contracts import ID_RE, MAX_PROMPT, strict_loads, validate_output_name
from .errors import FlowError

VALID_NODE_TYPES = ("gpt_json", "image_gen", "scene_fanout", "video_batch")
VALID_REF_ROLES = ("subject", "style", "composition")
INPUT_TOKEN_RE = re.compile(r"\{\{\s*input\.([A-Za-z0-9_-]{1,80})\s*\}\}")
REF_INPUT_RE = re.compile(r"^@input\.([A-Za-z0-9_-]{1,80})$")
REF_NODE_RE = re.compile(r"^@node\.([A-Za-z0-9_-]{1,80})(?:\.([A-Za-z0-9_.-]{1,120}))?$")
NODE_TEMPLATE_RE = re.compile(r"\{\{\s*node\.([A-Za-z0-9_-]{1,80})(?:\.([A-Za-z0-9_.-]{1,120}))?\s*\}\}")
SIZE_RE = re.compile(r"^(?:auto|\d{3,4}x\d{3,4})$")

DEFAULT_VIDEO_SETTINGS = {
    "model_id": "omni-1.2",
    "aspect": "9:16",
    "duration_seconds": 6,
    "resolution": None,
}

DEFAULT_IMAGE_SIZE_BY_ASPECT = {
    "9:16": "1024x1536",
    "16:9": "1536x1024",
    "1:1": "1024x1024",
}


def _check_id(value: Any, pointer: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise FlowError("SCHEMA_VALIDATION", "Identificador inválido; use letras, números, _ ou -.", pointer=pointer)
    return value


def _walk_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_strings(item))
    elif isinstance(value, dict):
        for val in value.values():
            found.extend(_walk_strings(val))
    return found


def _validate_prompt_value(
    value: Any,
    pointer: str,
    input_map: dict[str, dict],
    node_ids: set[str],
    nid: str,
    deps: list[str],
    *,
    max_len: int = 12_000,
) -> str | dict:
    if isinstance(value, str):
        if not value.strip() or len(value) > max_len:
            raise FlowError("SCHEMA_VALIDATION", f"Prompt inválido (1..{max_len} caracteres).", pointer=pointer)
        normalized: str | dict = value.strip()
    elif isinstance(value, dict):
        if not value:
            raise FlowError("SCHEMA_VALIDATION", "O JSON do prompt não pode estar vazio.", pointer=pointer)
        serialized = json.dumps(value, ensure_ascii=False)
        if len(serialized) > max_len:
            raise FlowError("SCHEMA_VALIDATION", f"O JSON do prompt excede {max_len} caracteres.", pointer=pointer)
        normalized = value
    else:
        raise FlowError("SCHEMA_VALIDATION", "Prompt precisa ser texto ou um objeto JSON estruturado.", pointer=pointer)

    for text in _walk_strings(normalized):
        for slot_id in (m.group(1) for m in INPUT_TOKEN_RE.finditer(text)):
            if slot_id not in input_map:
                raise FlowError("SCHEMA_VALIDATION", f"Slot '{slot_id}' não declarado em inputs.", pointer=pointer)
        for dep in (m.group(1) for m in NODE_TEMPLATE_RE.finditer(text)):
            if dep not in node_ids or dep == nid:
                raise FlowError("SCHEMA_VALIDATION", f"Node '{dep}' inválido no prompt.", pointer=pointer)
            if dep not in deps:
                deps.append(dep)
    return normalized


def _validate_ref_string(value: Any, pointer: str, input_map: dict[str, dict], node_ids: set[str]) -> tuple[str | None, str | None]:
    if not isinstance(value, str) or not value.strip():
        raise FlowError("SCHEMA_VALIDATION", "Referência inválida.", pointer=pointer)
    text = value.strip()
    m_in = REF_INPUT_RE.fullmatch(text)
    if m_in:
        slot_id = m_in.group(1)
        if slot_id not in input_map:
            raise FlowError("SCHEMA_VALIDATION", f"Slot de entrada '{slot_id}' não declarado em inputs.", pointer=pointer)
        if input_map[slot_id]["type"] != "image":
            raise FlowError("SCHEMA_VALIDATION", f"Slot '{slot_id}' é do tipo texto e não pode ser usado como referência de imagem.", pointer=pointer)
        return slot_id, None
    m_node = REF_NODE_RE.fullmatch(text)
    if m_node:
        ref_node = m_node.group(1)
        if ref_node not in node_ids:
            raise FlowError("SCHEMA_VALIDATION", f"Nó referenciado '{ref_node}' não existe.", pointer=pointer)
        return None, ref_node
    raise FlowError("SCHEMA_VALIDATION", "Use @input.<id> ou @node.<id>.asset_id.", pointer=pointer)


def _validate_typed_refs(
    refs: Any,
    pointer: str,
    input_map: dict[str, dict],
    node_ids: set[str],
    nid: str,
    deps: list[str],
) -> list[dict[str, str]]:
    if refs is None:
        return []
    if not isinstance(refs, list) or len(refs) > 4:
        raise FlowError("SCHEMA_VALIDATION", "Aceita no máximo 4 referências.", pointer=pointer)
    normalized: list[dict[str, str]] = []
    for idx, entry in enumerate(refs):
        rptr = f"{pointer}/{idx}"
        if isinstance(entry, str):
            raw_ref, role = entry, "subject"
        elif isinstance(entry, dict):
            raw_ref = entry.get("ref") or entry.get("asset")
            role = entry.get("role") or "subject"
            if role not in VALID_REF_ROLES:
                raise FlowError("SCHEMA_VALIDATION", f"role deve ser subject, style ou composition ({role}).", pointer=f"{rptr}/role")
        else:
            raise FlowError("SCHEMA_VALIDATION", "Referência precisa ser texto ou objeto {ref, role}.", pointer=rptr)
        _, ref_node = _validate_ref_string(raw_ref, rptr, input_map, node_ids)
        if ref_node == nid:
            raise FlowError("SCHEMA_VALIDATION", "Node não pode referenciar a si mesmo.", pointer=rptr)
        if ref_node and ref_node not in deps:
            deps.append(ref_node)
        normalized.append({"ref": str(raw_ref).strip(), "role": role})
    return normalized


def validate_flow_spec(raw: dict | bytes | str) -> dict:
    doc = strict_loads(raw) if isinstance(raw, (bytes, str)) else raw
    if not isinstance(doc, dict):
        raise FlowError("SCHEMA_VALIDATION", "O fluxo precisa ser um objeto JSON.", pointer="/")
    allowed_top = {"version", "name", "inputs", "video_defaults", "nodes"}
    unknown = set(doc.keys()) - allowed_top
    if unknown:
        first = sorted(unknown)[0]
        raise FlowError("SCHEMA_VALIDATION", f"Campo não reconhecido: {first}.", pointer=f"/{first}")
    if doc.get("version") != 1:
        raise FlowError("SCHEMA_VALIDATION", "Informe version: 1 no fluxo JSON.", pointer="/version")
    name = doc.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        raise FlowError("SCHEMA_VALIDATION", "Informe um nome de fluxo com até 200 caracteres.", pointer="/name")

    raw_inputs = doc.get("inputs") or []
    if not isinstance(raw_inputs, list) or len(raw_inputs) > 20:
        raise FlowError("SCHEMA_VALIDATION", "inputs precisa ser uma lista com até 20 slots.", pointer="/inputs")
    inputs: list[dict] = []
    input_map: dict[str, dict] = {}
    for idx, entry in enumerate(raw_inputs):
        ptr = f"/inputs/{idx}"
        if not isinstance(entry, dict):
            raise FlowError("SCHEMA_VALIDATION", "Cada slot de entrada precisa ser um objeto.", pointer=ptr)
        slot_id = _check_id(entry.get("id"), f"{ptr}/id")
        if slot_id in input_map:
            raise FlowError("SCHEMA_VALIDATION", f"Slot duplicado: {slot_id}.", pointer=f"{ptr}/id")
        slot_type = entry.get("type")
        if slot_type not in ("image", "text"):
            raise FlowError("SCHEMA_VALIDATION", "Tipo de slot deve ser 'image' ou 'text'.", pointer=f"{ptr}/type")
        label = entry.get("label")
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 120:
            raise FlowError("SCHEMA_VALIDATION", "Informe o rótulo do slot.", pointer=f"{ptr}/label")
        required = bool(entry.get("required", True))
        default = entry.get("default")
        if default is not None and (not isinstance(default, str) or len(default) > 4000):
            raise FlowError("SCHEMA_VALIDATION", "Valor padrão inválido no slot.", pointer=f"{ptr}/default")
        item = {"id": slot_id, "type": slot_type, "label": label.strip(), "required": required, "default": default}
        inputs.append(item)
        input_map[slot_id] = item

    raw_defaults = doc.get("video_defaults")
    if raw_defaults is None:
        video_defaults = dict(DEFAULT_VIDEO_SETTINGS)
    else:
        if not isinstance(raw_defaults, dict):
            raise FlowError("SCHEMA_VALIDATION", "video_defaults precisa ser um objeto.", pointer="/video_defaults")
        allowed_vd = {"model_id", "aspect", "duration_seconds", "resolution"}
        for key in set(raw_defaults.keys()) - allowed_vd:
            raise FlowError("SCHEMA_VALIDATION", f"Campo desconhecido em video_defaults: {key}.", pointer=f"/video_defaults/{key}")
        video_defaults = {**DEFAULT_VIDEO_SETTINGS, **raw_defaults}
        if video_defaults["aspect"] not in ("9:16", "16:9", "1:1"):
            raise FlowError("SCHEMA_VALIDATION", "Formato de vídeo inválido.", pointer="/video_defaults/aspect")
        dur = video_defaults.get("duration_seconds")
        if dur is not None and (type(dur) is not int or not 1 <= dur <= 60):
            raise FlowError("SCHEMA_VALIDATION", "Duração inválida (1..60).", pointer="/video_defaults/duration_seconds")

    raw_nodes = doc.get("nodes")
    if not isinstance(raw_nodes, list) or not (1 <= len(raw_nodes) <= 100):
        raise FlowError("SCHEMA_VALIDATION", "Informe de 1 a 100 nodes no fluxo.", pointer="/nodes")

    node_ids: set[str] = set()
    for idx, entry in enumerate(raw_nodes):
        if not isinstance(entry, dict):
            raise FlowError("SCHEMA_VALIDATION", "Cada node precisa ser um objeto.", pointer=f"/nodes/{idx}")
        nid = _check_id(entry.get("id"), f"/nodes/{idx}/id")
        if nid in node_ids:
            raise FlowError("SCHEMA_VALIDATION", f"Node duplicado: {nid}.", pointer=f"/nodes/{idx}/id")
        node_ids.add(nid)

    default_img_size = DEFAULT_IMAGE_SIZE_BY_ASPECT.get(video_defaults["aspect"], "1024x1536")
    nodes: list[dict] = []
    node_map: dict[str, dict] = {}
    for idx, entry in enumerate(raw_nodes):
        ptr = f"/nodes/{idx}"
        nid = entry["id"]
        ntype = entry.get("type")
        if ntype not in VALID_NODE_TYPES:
            raise FlowError("SCHEMA_VALIDATION", f"Tipo de node inválido: {ntype}.", pointer=f"{ptr}/type")
        label = entry.get("label") or nid
        if not isinstance(label, str) or len(label.strip()) > 120:
            raise FlowError("SCHEMA_VALIDATION", "Rótulo de node inválido.", pointer=f"{ptr}/label")

        deps: list[str] = []
        raw_deps = entry.get("depends_on") or []
        if not isinstance(raw_deps, list):
            raise FlowError("SCHEMA_VALIDATION", "depends_on precisa ser uma lista.", pointer=f"{ptr}/depends_on")
        for d in raw_deps:
            if not isinstance(d, str) or d not in node_ids or d == nid:
                raise FlowError("SCHEMA_VALIDATION", f"Dependência inválida em {nid}: {d}.", pointer=f"{ptr}/depends_on")
            if d not in deps:
                deps.append(d)

        normalized = {"id": nid, "type": ntype, "label": label.strip(), "depends_on": deps}

        if ntype == "gpt_json":
            normalized["prompt"] = _validate_prompt_value(entry.get("prompt"), f"{ptr}/prompt", input_map, node_ids, nid, deps, max_len=MAX_PROMPT)
            normalized["new_chat"] = bool(entry.get("new_chat", True))
            if entry.get("conversation") is not None:
                normalized["conversation"] = str(entry["conversation"])

        elif ntype == "image_gen":
            normalized["prompt"] = _validate_prompt_value(entry.get("prompt"), f"{ptr}/prompt", input_map, node_ids, nid, deps, max_len=12_000)
            size = entry.get("size") or default_img_size
            if not isinstance(size, str) or not SIZE_RE.fullmatch(size):
                raise FlowError("SCHEMA_VALIDATION", "size deve ser auto ou LARGURAxALTURA (ex.: 1024x1536).", pointer=f"{ptr}/size")
            normalized["size"] = size
            normalized["refs"] = _validate_typed_refs(entry.get("refs"), f"{ptr}/refs", input_map, node_ids, nid, deps)

        elif ntype == "scene_fanout":
            source = entry.get("source")
            if not isinstance(source, str):
                raise FlowError("SCHEMA_VALIDATION", "Node scene_fanout exige source (@node.<id>.json).", pointer=f"{ptr}/source")
            _, src_node = _validate_ref_string(source, f"{ptr}/source", input_map, node_ids)
            if not src_node or src_node == nid:
                raise FlowError("SCHEMA_VALIDATION", "source precisa apontar para outro node.", pointer=f"{ptr}/source")
            if src_node not in deps:
                deps.append(src_node)
            prompt_tpl = entry.get("prompt_template") if entry.get("prompt_template") is not None else "{{scene.image_prompt}}"
            video_tpl = entry.get("video_prompt_template") if entry.get("video_prompt_template") is not None else "{{scene.video_prompt}}"
            size = entry.get("size") or default_img_size
            if not isinstance(size, str) or not SIZE_RE.fullmatch(size):
                raise FlowError("SCHEMA_VALIDATION", "size inválido em scene_fanout.", pointer=f"{ptr}/size")
            normalized["source"] = source.strip()
            normalized["scenes_key"] = str(entry.get("scenes_key") or "scenes")
            normalized["prompt_template"] = prompt_tpl
            normalized["video_prompt_template"] = video_tpl
            normalized["size"] = size
            normalized["refs"] = _validate_typed_refs(entry.get("refs"), f"{ptr}/refs", input_map, node_ids, nid, deps)

        elif ntype == "video_batch":
            items = entry.get("items") or []
            from_fanout = entry.get("from_fanout")
            if not items and not from_fanout:
                raise FlowError("SCHEMA_VALIDATION", "video_batch exige items ou from_fanout.", pointer=ptr)
            if from_fanout is not None:
                _, fan_node = _validate_ref_string(from_fanout, f"{ptr}/from_fanout", input_map, node_ids)
                if not fan_node or fan_node == nid:
                    raise FlowError("SCHEMA_VALIDATION", "from_fanout inválido.", pointer=f"{ptr}/from_fanout")
                if fan_node not in deps:
                    deps.append(fan_node)
                normalized["from_fanout"] = from_fanout.strip()
            norm_items = []
            if items:
                if not isinstance(items, list):
                    raise FlowError("SCHEMA_VALIDATION", "items precisa ser uma lista.", pointer=f"{ptr}/items")
                for i_idx, item in enumerate(items):
                    iptr = f"{ptr}/items/{i_idx}"
                    if not isinstance(item, dict):
                        raise FlowError("SCHEMA_VALIDATION", "Cada item de vídeo precisa ser um objeto.", pointer=iptr)
                    prompt_val = _validate_prompt_value(item.get("prompt"), f"{iptr}/prompt", input_map, node_ids, nid, deps, max_len=MAX_PROMPT)
                    asset_ref = item.get("asset")
                    if asset_ref is not None:
                        _, ref_node = _validate_ref_string(asset_ref, f"{iptr}/asset", input_map, node_ids)
                        if ref_node == nid:
                            raise FlowError("SCHEMA_VALIDATION", "Auto-referência inválida.", pointer=f"{iptr}/asset")
                        if ref_node and ref_node not in deps:
                            deps.append(ref_node)
                    out_name = item.get("output_name") or f"take-{i_idx + 1:02d}.mp4"
                    validate_output_name(out_name, f"{iptr}/output_name")
                    norm_items.append({
                        "asset": str(asset_ref).strip() if asset_ref else None,
                        "prompt": prompt_val,
                        "output_name": out_name,
                    })
            normalized["items"] = norm_items

        nodes.append(normalized)
        node_map[nid] = normalized

    in_degree = {n["id"]: len(n["depends_on"]) for n in nodes}
    children: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for n in nodes:
        for dep in n["depends_on"]:
            children[dep].append(n["id"])
    queue = [n["id"] for n in nodes if in_degree[n["id"]] == 0]
    ordered: list[str] = []
    while queue:
        current = queue.pop(0)
        ordered.append(current)
        for child in children[current]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)
    if len(ordered) != len(nodes):
        raise FlowError("SCHEMA_VALIDATION", "O fluxo possui ciclo entre nodes (depends_on circular).", pointer="/nodes")

    ordered_nodes = [node_map[nid] for nid in ordered]
    return {
        "spec": {
            "version": 1,
            "name": name.strip(),
            "inputs": inputs,
            "video_defaults": video_defaults,
            "nodes": ordered_nodes,
        },
        "inputs": inputs,
        "nodes": ordered_nodes,
        "ordered_node_ids": ordered,
        "summary": {
            "total_nodes": len(ordered_nodes),
            "image_nodes": sum(1 for n in ordered_nodes if n["type"] == "image_gen"),
            "gpt_nodes": sum(1 for n in ordered_nodes if n["type"] == "gpt_json"),
            "video_batches": sum(1 for n in ordered_nodes if n["type"] == "video_batch"),
        },
    }
