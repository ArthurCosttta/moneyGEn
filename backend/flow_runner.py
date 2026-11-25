from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .contracts import canonical_json
from .errors import FlowError
from .flow_contracts import (
    INPUT_TOKEN_RE,
    NODE_TEMPLATE_RE,
    REF_INPUT_RE,
    REF_NODE_RE,
    validate_flow_spec,
)
from .repository import utc_now

SCENE_TOKEN_RE = re.compile(r"\{\{\s*scene\.([A-Za-z0-9_.-]{1,120})\s*\}\}")


def _lookup_path(obj: Any, dotted: str) -> Any:
    current = obj
    for part in dotted.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and 0 <= int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


class FlowRunner:
    """Persistent DAG runner chaining GPT JSON -> images -> Google Flow video batches."""

    def __init__(self, service) -> None:
        self.service = service
        self.repository = service.repository
        self.assets = service.assets
        self.images = service.images
        self.gpt_chat = service.gpt_chat
        self._closed = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._recover_interrupted()

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout=5)

    def _recover_interrupted(self) -> None:
        with self.repository.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE flow_node_runs SET state='FAILED', error_code='INTERRUPTED', "
                "error_message='Servidor reiniciado durante a execução do nó.', finished_at=? "
                "WHERE state='RUNNING'",
                (utc_now(),),
            )
            conn.execute(
                "UPDATE flow_runs SET state='FAILED', error_code='INTERRUPTED', "
                "error_message='Servidor reiniciado durante a execução do fluxo.', updated_at=? "
                "WHERE state IN ('PENDING','RUNNING')",
                (utc_now(),),
            )

    def preview(self, raw_spec: Any) -> dict:
        return validate_flow_spec(raw_spec)

    def _validate_inputs(self, validated: dict, body_inputs: dict) -> dict[str, Any]:
        if not isinstance(body_inputs, dict):
            raise FlowError("SCHEMA_VALIDATION", "inputs precisa ser um objeto.", pointer="/inputs")
        resolved: dict[str, Any] = {}
        for slot in validated["inputs"]:
            slot_id = slot["id"]
            val = body_inputs.get(slot_id)
            if slot["type"] == "image":
                if not val:
                    if slot["required"]:
                        raise FlowError("SCHEMA_VALIDATION", f"Anexe uma imagem para '{slot['label']}'.", pointer=f"/inputs/{slot_id}")
                    resolved[slot_id] = None
                else:
                    asset, _ = self.assets.verified_path(str(val))
                    resolved[slot_id] = asset["id"]
            else:
                text_val = val if isinstance(val, str) and val.strip() else (slot.get("default") or "")
                if slot["required"] and not str(text_val).strip():
                    raise FlowError("SCHEMA_VALIDATION", f"Preencha '{slot['label']}'.", pointer=f"/inputs/{slot_id}")
                resolved[slot_id] = str(text_val).strip()
        return resolved

    def _resolve_output_dir(self, requested_dir: str | None) -> Path:
        target = Path(requested_dir) if requested_dir else self.service.output
        if not target.is_absolute():
            raise FlowError("SCHEMA_VALIDATION", "A pasta de saída precisa ser absoluta.", pointer="/output_dir")
        target.mkdir(parents=True, exist_ok=True)
        return target.resolve()

    def start(self, body: dict) -> dict:
        if self.service.no_spend:
            raise FlowError("NO_SPEND_ACTIVE", "A trava sem gastos está ativa.", status=403)
        if body.get("confirmed") is not True or body.get("risk") is not True:
            raise FlowError("SPEND_NOT_CONFIRMED", "Confirme o gasto e os termos na interface antes de iniciar o fluxo.", status=403)
        validated = validate_flow_spec(body.get("spec"))
        spec = validated["spec"]
        resolved_inputs = self._validate_inputs(validated, body.get("inputs") or {})
        output_dir = self._resolve_output_dir(body.get("output_dir"))

        has_video = any(n["type"] == "video_batch" for n in spec["nodes"])
        if has_video and not self.service.session.get("identity_matches_expected"):
            preflight_check = self.service.preflight_account_only()
            if not preflight_check:
                raise FlowError("ACCOUNT_MISMATCH", "Conecte a conta correta do Google Flow antes de iniciar fluxos com vídeo.", status=403)

        run_id = str(uuid.uuid4())
        now = utc_now()
        with self.repository.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO flow_runs(id,name,spec_json,inputs_json,output_dir,state,active_node_id,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, spec["name"], canonical_json(spec), canonical_json(resolved_inputs), str(output_dir), "PENDING", None, now, now),
            )
            for pos, node in enumerate(spec["nodes"]):
                conn.execute(
                    "INSERT INTO flow_node_runs(id,run_id,node_id,position,node_type,label,state,depends_on_json,config_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), run_id, node["id"], pos, node["type"], node["label"], "PENDING", canonical_json(node["depends_on"]), canonical_json(node)),
                )

        thread = threading.Thread(target=self._run_loop, args=(run_id,), name=f"flow-run-{run_id[:8]}", daemon=True)
        with self._lock:
            self._threads[run_id] = thread
        thread.start()
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict:
        with self.repository.connect() as conn:
            row = conn.execute("SELECT * FROM flow_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise FlowError("BATCH_NOT_FOUND", "Execução de fluxo não encontrada.", status=404)
            node_rows = conn.execute(
                "SELECT * FROM flow_node_runs WHERE run_id=? ORDER BY position ASC", (run_id,)
            ).fetchall()
        run = dict(row)
        run["spec"] = json.loads(run.pop("spec_json"))
        run["inputs"] = json.loads(run.pop("inputs_json"))
        nodes = []
        for nr in node_rows:
            item = dict(nr)
            item["depends_on"] = json.loads(item.pop("depends_on_json"))
            item["config"] = json.loads(item.pop("config_json"))
            item["output"] = json.loads(item.pop("output_json")) if item.get("output_json") else None
            nodes.append(item)
        run["nodes"] = nodes
        return run

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self.repository.connect() as conn:
            rows = conn.execute("SELECT id,name,state,active_node_id,batch_id,error_code,error_message,created_at,updated_at FROM flow_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def _set_run(self, run_id: str, *, state: str | None = None, active_node_id: str | None = None, batch_id: str | None = None, error_code: str | None = None, error_message: str | None = None) -> None:
        fields = ["updated_at=?"]
        values: list[Any] = [utc_now()]
        if state is not None:
            fields.append("state=?"); values.append(state)
        fields.append("active_node_id=?"); values.append(active_node_id)
        if batch_id is not None:
            fields.append("batch_id=?"); values.append(batch_id)
        if error_code is not None:
            fields.append("error_code=?"); values.append(error_code)
        if error_message is not None:
            fields.append("error_message=?"); values.append(error_message)
        values.append(run_id)
        with self.repository.transaction(immediate=True) as conn:
            conn.execute(f"UPDATE flow_runs SET {', '.join(fields)} WHERE id=?", values)

    def _set_node(self, run_id: str, node_id: str, *, state: str, output: dict | None = None, error_code: str | None = None, error_message: str | None = None, started: bool = False, finished: bool = False) -> None:
        fields = ["state=?"]
        values: list[Any] = [state]
        if output is not None:
            fields.append("output_json=?"); values.append(canonical_json(output))
        if error_code is not None:
            fields.append("error_code=?"); values.append(error_code)
        if error_message is not None:
            fields.append("error_message=?"); values.append(error_message)
        if started:
            fields.append("started_at=?"); values.append(utc_now())
        if finished:
            fields.append("finished_at=?"); values.append(utc_now())
        values.extend([run_id, node_id])
        with self.repository.transaction(immediate=True) as conn:
            conn.execute(f"UPDATE flow_node_runs SET {', '.join(fields)} WHERE run_id=? AND node_id=?", values)

    @staticmethod
    def _render_text(template: str, inputs: dict[str, Any], outputs: dict[str, dict], scene: dict | None = None) -> str:
        def rep_input(match: re.Match) -> str:
            return str(inputs.get(match.group(1), ""))

        def rep_node(match: re.Match) -> str:
            nid, sub = match.group(1), match.group(2)
            out = outputs.get(nid, {})
            if sub is None:
                return str(out.get("asset_id") or "")
            val = _lookup_path(out, sub)
            return "" if val is None else str(val)

        rendered = INPUT_TOKEN_RE.sub(rep_input, template)
        rendered = NODE_TEMPLATE_RE.sub(rep_node, rendered)
        if scene is not None:
            rendered = SCENE_TOKEN_RE.sub(lambda m: str(_lookup_path(scene, m.group(1)) or ""), rendered)
        return rendered

    @classmethod
    def _render_value(cls, value: Any, inputs: dict[str, Any], outputs: dict[str, dict], scene: dict | None = None) -> Any:
        if isinstance(value, str):
            return cls._render_text(value, inputs, outputs, scene)
        if isinstance(value, list):
            return [cls._render_value(item, inputs, outputs, scene) for item in value]
        if isinstance(value, dict):
            return {k: cls._render_value(v, inputs, outputs, scene) for k, v in value.items()}
        return value

    @classmethod
    def _prompt_to_text(cls, value: Any, inputs: dict[str, Any], outputs: dict[str, dict], scene: dict | None = None, *, is_image: bool = False) -> str:
        rendered = cls._render_value(value, inputs, outputs, scene)
        if isinstance(rendered, dict):
            body = json.dumps(rendered, ensure_ascii=False, indent=2)
            if is_image:
                return (
                    "Gere uma fotografia de altíssima qualidade, foco nítido e fidelidade máxima "
                    "seguindo estritamente esta especificação JSON:\n" + body
                )
            return body
        return str(rendered)

    def _resolve_typed_refs(self, refs: list[Any], inputs: dict[str, Any], outputs: dict[str, dict], nid: str, *, strict: bool = True) -> tuple[list[str], list[str]]:
        ref_ids: list[str] = []
        ref_roles: list[str] = []
        for entry in refs or []:
            raw_ref = entry.get("ref") if isinstance(entry, dict) else str(entry)
            role = entry.get("role", "subject") if isinstance(entry, dict) else "subject"
            resolved_id = self._resolve_asset_ref(raw_ref, inputs, outputs)
            if not resolved_id:
                if strict:
                    raise FlowError("ARTIFACT_NOT_FOUND", f"Referência '{raw_ref}' não disponível para o nó '{nid}'.", status=409)
                continue
            ref_ids.append(resolved_id)
            ref_roles.append(role)
        return ref_ids, ref_roles

    @staticmethod
    def _resolve_asset_ref(ref: str, inputs: dict[str, Any], outputs: dict[str, dict]) -> str | None:
        m_in = REF_INPUT_RE.fullmatch(ref)
        if m_in:
            return inputs.get(m_in.group(1))
        m_node = REF_NODE_RE.fullmatch(ref)
        if m_node:
            nid, sub = m_node.group(1), m_node.group(2) or "asset_id"
            val = _lookup_path(outputs.get(nid, {}), sub)
            return str(val) if val else None
        return None

    def _run_video_batch(self, run_id: str, run: dict, node: dict, outputs: dict[str, dict]) -> dict:
        defaults = run["spec"]["video_defaults"]
        items_payload: list[dict] = []
        if node.get("from_fanout"):
            m_node = REF_NODE_RE.fullmatch(node["from_fanout"])
            fan_out = outputs.get(m_node.group(1), {}) if m_node else {}
            for idx, entry in enumerate(fan_out.get("items") or []):
                items_payload.append({
                    "id": f"take_{uuid.uuid4().hex[:12]}",
                    "kind": "i2v" if entry.get("asset_id") else "t2v",
                    "prompt": entry["video_prompt"],
                    "asset_id": entry.get("asset_id"),
                    "output_name": entry.get("output_name") or f"take-{idx + 1:02d}.mp4",
                    "overrides": {},
                })
        for idx, raw_item in enumerate(node.get("items") or []):
            asset_id = self._resolve_asset_ref(raw_item["asset"], run["inputs"], outputs) if raw_item.get("asset") else None
            if raw_item.get("asset") and not asset_id:
                raise FlowError("ARTIFACT_NOT_FOUND", f"Imagem referenciada não produzida para '{raw_item['output_name']}'.", status=409)
            items_payload.append({
                "id": f"take_{uuid.uuid4().hex[:12]}",
                "kind": "i2v" if asset_id else "t2v",
                "prompt": self._prompt_to_text(raw_item["prompt"], run["inputs"], outputs),
                "asset_id": asset_id,
                "output_name": raw_item["output_name"],
                "overrides": {},
            })
        if not items_payload:
            raise FlowError("SCHEMA_VALIDATION", "Nenhum vídeo encontrado para gerar no nó video_batch.")

        batch_dir = Path(run["output_dir"]) / f"fluxo-{run_id[:8]}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch = self.service.create_batch({
            "name": f"{run['name']} ({run_id[:6]})",
            "defaults": defaults,
            "output_dir": str(batch_dir),
            "max_credits": None,
        })
        self._set_run(run_id, active_node_id=node["id"], batch_id=batch["id"])
        added = self.service.add_items(batch["id"], {
            "expected_revision": batch["revision"],
            "idempotency_key": str(uuid.uuid4()),
            "items": items_payload,
        })
        preflight = self.service.preflight(batch["id"])
        if not preflight["ready"]:
            err = preflight["errors"][0] if preflight["errors"] else {"code": "ACCOUNT_MISMATCH", "message": "Pré-checagem do Flow falhou."}
            raise FlowError(err["code"], err["message"], status=403)
        plan = self.service.planner.plan(batch["id"], added["revision"])
        if not plan["valid"]:
            first_err = plan["errors"][0] if plan["errors"] else {"code": "SCHEMA_VALIDATION", "message": "Plano inválido."}
            raise FlowError(first_err["code"], first_err["message"], status=400)

        batch_latest = self.repository.get_batch(batch["id"])
        all_items = self.repository.all_items(batch["id"])
        first_id = all_items[0]["id"]
        self.service.start({
            "batch_id": batch["id"],
            "expected_revision": batch_latest["revision"],
            "plan_hash": plan["plan_hash"],
            "idempotency_key": str(uuid.uuid4()),
            "item_ids": [first_id],
            "confirmed": True,
            "risk": True,
            "reviewed": False,
            "unknown_cost_accepted": True,
        }, "TEST")

        first_item = self._wait_items(batch["id"], [first_id])
        if first_item[0]["state"] != "SUCCEEDED":
            raise FlowError("PROVIDER_FAILURE", f"Primeiro vídeo falhou com estado {first_item[0]['state']}. Os restantes não foram disparados.", status=502)

        remaining_ids = [item["id"] for item in all_items[1:]]
        if remaining_ids:
            batch_latest = self.repository.get_batch(batch["id"])
            self.service.start({
                "batch_id": batch["id"],
                "expected_revision": batch_latest["revision"],
                "plan_hash": plan["plan_hash"],
                "idempotency_key": str(uuid.uuid4()),
                "item_ids": remaining_ids,
                "confirmed": True,
                "risk": True,
                "reviewed": True,
                "unknown_cost_accepted": True,
            }, "REMAINDER")
            self._wait_items(batch["id"], remaining_ids)

        final_items = []
        cursor = None
        while True:
            page = self.repository.list_items(batch["id"], cursor=cursor, limit=50)
            final_items.extend(page["items"])
            cursor = page.get("next_cursor")
            if not cursor:
                break
        videos = []
        failed = []
        for it in final_items:
            art_id = it.get("artifact_id")
            if it["state"] == "SUCCEEDED" and art_id:
                videos.append({
                    "item_id": it["id"],
                    "output_name": it["output_name"],
                    "artifact_id": art_id,
                    "media_url": f"/media/artifacts/{art_id}",
                })
            else:
                failed.append(f"{it['output_name']} ({it['state']})")
        if failed:
            raise FlowError("PROVIDER_FAILURE", "Nem todos os vídeos foram concluídos: " + ", ".join(failed), status=502)
        return {"batch_id": batch["id"], "output_dir": str(batch_dir), "videos": videos}

    def _wait_items(self, batch_id: str, item_ids: list[str]) -> list[dict]:
        terminal = {"SUCCEEDED", "FAILED", "REFUSED", "NEEDS_RECONCILIATION"}
        target_set = set(item_ids)
        while not self._closed.is_set():
            items = [it for it in self.repository.all_items(batch_id) if it["id"] in target_set]
            if items and all(it["state"] in terminal for it in items):
                return items
            time.sleep(0.25)
        raise FlowError("INTERRUPTED", "Aplicativo encerrado durante execução de vídeo.", status=503)

    def _run_loop(self, run_id: str) -> None:
        run = self.get_run(run_id)
        outputs: dict[str, dict] = {}
        self._set_run(run_id, state="RUNNING", active_node_id=None)
        try:
            for node in run["spec"]["nodes"]:
                if self._closed.is_set():
                    raise FlowError("INTERRUPTED", "Fluxo interrompido.", status=503)
                nid = node["id"]
                self._set_run(run_id, state="RUNNING", active_node_id=nid)
                self._set_node(run_id, nid, state="RUNNING", started=True)

                if node["type"] == "gpt_json":
                    prompt = self._prompt_to_text(node["prompt"], run["inputs"], outputs)
                    res = self.gpt_chat.ask_json(prompt, new_chat=node.get("new_chat", True), conversation=node.get("conversation"))
                    outputs[nid] = res
                    self._set_node(run_id, nid, state="SUCCEEDED", output=res, finished=True)

                elif node["type"] == "image_gen":
                    prompt = self._prompt_to_text(node["prompt"], run["inputs"], outputs, is_image=True)
                    ref_ids, ref_roles = self._resolve_typed_refs(node.get("refs") or [], run["inputs"], outputs, nid, strict=True)
                    size = node.get("size")
                    if size or any(r != "subject" for r in ref_roles):
                        asset = self.images.generate(prompt, ref_ids, size=size, ref_roles=ref_roles)
                    else:
                        asset = self.images.generate(prompt, ref_ids)
                    out = {"asset_id": asset["id"], "preview_url": asset["preview_url"], "name": asset["name"]}
                    outputs[nid] = out
                    self._set_node(run_id, nid, state="SUCCEEDED", output=out, finished=True)

                elif node["type"] == "scene_fanout":
                    m_src = REF_NODE_RE.fullmatch(node["source"])
                    src_node = m_src.group(1) if m_src else ""
                    src_sub = m_src.group(2) if m_src and m_src.group(2) else "json"
                    raw_src = _lookup_path(outputs.get(src_node, {}), src_sub)
                    scenes = raw_src.get(node.get("scenes_key") or "scenes") if isinstance(raw_src, dict) else raw_src
                    if not isinstance(scenes, list) or not scenes:
                        raise FlowError("SCHEMA_VALIDATION", f"Nenhuma cena encontrada em '{node['source']}'.")
                    base_refs, base_roles = self._resolve_typed_refs(node.get("refs") or [], run["inputs"], outputs, nid, strict=False)
                    size = node.get("size")
                    generated_items = []
                    for idx, scene in enumerate(scenes):
                        if not isinstance(scene, dict):
                            raise FlowError("SCHEMA_VALIDATION", f"Cena {idx + 1} inválida no JSON.")
                        img_prompt = self._prompt_to_text(node["prompt_template"], run["inputs"], outputs, scene=scene, is_image=True)
                        vid_prompt = self._prompt_to_text(node["video_prompt_template"], run["inputs"], outputs, scene=scene)
                        if size or any(r != "subject" for r in base_roles):
                            asset = self.images.generate(img_prompt, base_refs, size=size, ref_roles=base_roles)
                        else:
                            asset = self.images.generate(img_prompt, base_refs)
                        generated_items.append({
                            "index": idx + 1,
                            "asset_id": asset["id"],
                            "preview_url": asset["preview_url"],
                            "video_prompt": vid_prompt or img_prompt,
                            "output_name": f"cena-{idx + 1:02d}.mp4",
                        })
                        self._set_node(run_id, nid, state="RUNNING", output={"items": generated_items, "total": len(scenes)})
                    out = {"items": generated_items, "total": len(scenes)}
                    outputs[nid] = out
                    self._set_node(run_id, nid, state="SUCCEEDED", output=out, finished=True)

                elif node["type"] == "video_batch":
                    out = self._run_video_batch(run_id, run, node, outputs)
                    outputs[nid] = out
                    self._set_node(run_id, nid, state="SUCCEEDED", output=out, finished=True)

            self._set_run(run_id, state="SUCCEEDED", active_node_id=None)
        except FlowError as exc:
            active = self.get_run(run_id).get("active_node_id")
            if active:
                self._set_node(run_id, active, state="FAILED", error_code=exc.code, error_message=exc.message, finished=True)
            self._set_run(run_id, state="FAILED", active_node_id=None, error_code=exc.code, error_message=exc.message)
        except Exception as exc:
            active = self.get_run(run_id).get("active_node_id")
            if active:
                self._set_node(run_id, active, state="FAILED", error_code="LOCAL_FAILURE", error_message=str(exc), finished=True)
            self._set_run(run_id, state="FAILED", active_node_id=None, error_code="LOCAL_FAILURE", error_message=str(exc))
        finally:
            with self._lock:
                self._threads.pop(run_id, None)
