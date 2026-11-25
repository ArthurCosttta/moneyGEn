from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .contracts import canonical_json
from .errors import FlowError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, traceback):
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


class Repository:
    """SQLite repository with one connection per operation/thread."""

    def __init__(self, data_dir: Path, migrations_dir: Path, expected_account: str) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "flowlocal.sqlite3"
        self.migrations_dir = Path(migrations_dir).resolve()
        self.expected_account = expected_account
        self._write_lock = threading.RLock()
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._write_lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def migrate(self) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            for path in sorted(self.migrations_dir.glob("[0-9][0-9][0-9]_*.sql")):
                version = int(path.name.split("_", 1)[0])
                if version in applied:
                    continue
                script = path.read_text(encoding="utf-8")
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.executescript(script)
                    conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)", (version, utc_now()))
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            # Effects that may have reached the provider are never replayed on restart.
            conn.execute(
                "UPDATE executions SET state='NEEDS_RECONCILIATION', lease_owner=NULL, lease_until=NULL "
                "WHERE state='RUNNING' AND intent_at IS NOT NULL"
            )
            conn.execute(
                "UPDATE executions SET state='QUEUED', lease_owner=NULL, lease_until=NULL "
                "WHERE state='RUNNING' AND intent_at IS NULL"
            )

    def migration_version(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()
            return int(row[0])

    @staticmethod
    def _batch(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        value = dict(row)
        value["defaults"] = json.loads(value.pop("defaults_json"))
        value["pause_requested"] = bool(value["pause_requested"])
        return value

    @staticmethod
    def _item(row: sqlite3.Row) -> dict:
        value = dict(row)
        value["settings"] = json.loads(value.pop("settings_json"))
        value["artifact_id"] = value.get("artifact_id")
        return value

    def create_batch(self, name: str, defaults: dict, max_credits: int | None = None, *, output_dir: str = "") -> dict:
        batch_id, timestamp = str(uuid.uuid4()), utc_now()
        with self.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO batches(id,revision,state,name,defaults_json,max_credits,output_dir,expected_account,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (batch_id, 1, "DRAFT", name, _json(defaults), max_credits, output_dir, self.expected_account, timestamp, timestamp),
            )
            self._event(conn, "batch.created", batch_id, payload={"status": "DRAFT", "message": "Rascunho criado."})
        return self.get_batch(batch_id)

    def get_batch(self, batch_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if row is None:
            raise FlowError("BATCH_NOT_FOUND", "Lote não encontrado.", status=404)
        return self._batch(row)  # type: ignore[return-value]

    def selected_batch_id(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value_json FROM settings WHERE key='selected_batch_id'").fetchone()
        return json.loads(row[0]) if row else None

    def select_batch(self, batch_id: str) -> None:
        with self.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO settings(key,value_json,revision) VALUES('selected_batch_id',?,1) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,revision=settings.revision+1",
                (_json(batch_id),),
            )

    def list_batches(self, *, cursor: str | None = None, limit: int = 50) -> dict:
        limit = max(1, min(50, int(limit)))
        where, args = "", []
        if cursor:
            where, args = "WHERE created_at < (SELECT created_at FROM batches WHERE id=?)", [cursor]
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM batches {where} ORDER BY created_at DESC LIMIT ?", (*args, limit + 1)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
        more = len(rows) > limit
        rows = rows[:limit]
        return {"items": [self._batch(r) for r in rows], "next_cursor": rows[-1]["id"] if more else None, "total": total}

    def patch_batch(self, batch_id: str, expected_revision: int, changes: dict) -> dict:
        allowed = {"name", "defaults", "draft_text", "output_dir", "max_credits"}
        changes = {k: v for k, v in changes.items() if k in allowed}
        if not changes:
            return self.get_batch(batch_id)
        columns, values = [], []
        for key, value in changes.items():
            columns.append(("defaults_json" if key == "defaults" else key) + "=?")
            values.append(_json(value) if key == "defaults" else value)
        columns += ["revision=revision+1", "plan_hash=NULL", "state='DRAFT'", "updated_at=?"]
        values += [utc_now(), batch_id, expected_revision]
        with self.transaction(immediate=True) as conn:
            result = conn.execute(
                f"UPDATE batches SET {','.join(columns)} WHERE id=? AND revision=?",
                values,
            )
            if result.rowcount != 1:
                self._raise_revision(conn, batch_id)
            self._event(conn, "batch.updated", batch_id, payload={"status": "DRAFT", "message": "Rascunho atualizado."})
        return self.get_batch(batch_id)

    def _raise_revision(self, conn: sqlite3.Connection, batch_id: str) -> None:
        if conn.execute("SELECT 1 FROM batches WHERE id=?", (batch_id,)).fetchone() is None:
            raise FlowError("BATCH_NOT_FOUND", "Lote não encontrado.", status=404)
        raise FlowError("REVISION_CONFLICT", "O pedido foi alterado em outra operação.", status=409)

    def add_items(self, batch_id: str, expected_revision: int, items: list[dict], idempotency_key: str) -> dict:
        request_hash = canonical_json({"batch_id": batch_id, "revision": expected_revision, "items": items})
        with self.transaction(immediate=True) as conn:
            existing = self._idempotency(conn, idempotency_key, request_hash)
            if existing is not None:
                return existing
            batch = conn.execute("SELECT revision,state FROM batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None or batch["revision"] != expected_revision:
                self._raise_revision(conn, batch_id)
            count = conn.execute("SELECT COUNT(*) FROM items WHERE batch_id=?", (batch_id,)).fetchone()[0]
            if count + len(items) > 1000:
                raise FlowError("QUEUE_CAPACITY", "O lote aceita até 1.000 itens.", status=429)
            position = conn.execute("SELECT COALESCE(MAX(position),-1)+1 FROM items WHERE batch_id=?", (batch_id,)).fetchone()[0]
            for offset, item in enumerate(items):
                output = item.get("output_name") or f"{position+offset+1:04d}-{item['id'][:12]}.mp4"
                try:
                    conn.execute(
                        "INSERT INTO items(id,batch_id,position,kind,prompt,asset_id,settings_json,output_name,state,source_index) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (item["id"], batch_id, position + offset, item["kind"], item["prompt"], item.get("asset_id"), _json(item.get("overrides", {})), output, "DRAFT", item.get("source_index")),
                    )
                except sqlite3.IntegrityError as exc:
                    raise FlowError("SCHEMA_VALIDATION", "ID ou nome de saída repetido.", status=409) from exc
            result = conn.execute(
                "UPDATE batches SET revision=revision+1,plan_hash=NULL,state='DRAFT',updated_at=? WHERE id=? AND revision=?",
                (utc_now(), batch_id, expected_revision),
            )
            if result.rowcount != 1:
                self._raise_revision(conn, batch_id)
            response = {"batch_id": batch_id, "revision": expected_revision + 1, "item_ids": [x["id"] for x in items]}
            self._store_idempotency(conn, idempotency_key, request_hash, response)
            self._event(conn, "items.added", batch_id, payload={"status": "DRAFT", "total": count + len(items), "message": "Itens adicionados."})
            return response

    def list_items(self, batch_id: str, *, cursor: str | None = None, limit: int = 50, status: str | None = None) -> dict:
        limit = max(1, min(50, int(limit)))
        clauses, args = ["i.batch_id=?"], [batch_id]
        if status:
            clauses.append("i.state=?")
            args.append(status)
        if cursor:
            clauses.append("i.position > COALESCE((SELECT position FROM items WHERE id=? AND batch_id=?),-1)")
            args.extend([cursor, batch_id])
        query = (
            "SELECT i.*, (SELECT a.id FROM artifacts a JOIN executions e ON e.id=a.execution_id "
            "WHERE e.item_id=i.id AND a.validation_state='VALID' ORDER BY e.attempt DESC LIMIT 1) artifact_id "
            f"FROM items i WHERE {' AND '.join(clauses)} ORDER BY i.position LIMIT ?"
        )
        with self.connect() as conn:
            rows = conn.execute(query, (*args, limit + 1)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM items WHERE batch_id=?" + (" AND state=?" if status else ""), (batch_id, status) if status else (batch_id,)).fetchone()[0]
        more = len(rows) > limit
        rows = rows[:limit]
        return {"items": [self._item(r) for r in rows], "next_cursor": rows[-1]["id"] if more else None, "total": total}

    def all_items(self, batch_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT i.*,NULL artifact_id FROM items i WHERE batch_id=? ORDER BY position", (batch_id,)).fetchall()
        return [self._item(row) for row in rows]

    def patch_item(self, batch_id: str, item_id: str, expected_revision: int, changes: dict) -> dict:
        allowed = {"prompt", "asset_id", "output_name", "overrides", "position"}
        changes = {k: v for k, v in changes.items() if k in allowed}
        columns, values = [], []
        for key, value in changes.items():
            columns.append(("settings_json" if key == "overrides" else key) + "=?")
            values.append(_json(value) if key == "overrides" else value)
        with self.transaction(immediate=True) as conn:
            batch = conn.execute("SELECT revision FROM batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None or batch[0] != expected_revision:
                self._raise_revision(conn, batch_id)
            if columns:
                try:
                    result = conn.execute(f"UPDATE items SET {','.join(columns)} WHERE id=? AND batch_id=?", (*values, item_id, batch_id))
                except sqlite3.IntegrityError as exc:
                    raise FlowError("SCHEMA_VALIDATION", "Posição ou nome de saída já utilizado.", status=409) from exc
                if result.rowcount != 1:
                    raise FlowError("ITEM_NOT_FOUND", "Item não encontrado.", status=404)
            conn.execute("UPDATE batches SET revision=revision+1,plan_hash=NULL,state='DRAFT',updated_at=? WHERE id=?", (utc_now(), batch_id))
            self._event(conn, "item.updated", batch_id, item_id=item_id, payload={"status": "DRAFT", "message": "Item atualizado."})
        return {"batch_id": batch_id, "revision": expected_revision + 1, "item_ids": [item_id]}

    def delete_item(self, batch_id: str, item_id: str, expected_revision: int, key: str) -> dict:
        request_hash = canonical_json({"batch_id": batch_id, "item_id": item_id, "revision": expected_revision})
        with self.transaction(immediate=True) as conn:
            existing = self._idempotency(conn, key, request_hash)
            if existing is not None:
                return existing
            batch = conn.execute("SELECT revision,state FROM batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None or batch["revision"] != expected_revision:
                self._raise_revision(conn, batch_id)
            if batch["state"] not in ("DRAFT", "PLANNED"):
                raise FlowError("PLAN_STALE", "Somente rascunhos podem remover itens.", status=409)
            result = conn.execute("DELETE FROM items WHERE id=? AND batch_id=?", (item_id, batch_id))
            if result.rowcount != 1:
                raise FlowError("ITEM_NOT_FOUND", "Item não encontrado.", status=404)
            conn.execute("UPDATE batches SET revision=revision+1,plan_hash=NULL,state='DRAFT',updated_at=? WHERE id=?", (utc_now(), batch_id))
            response = {"batch_id": batch_id, "revision": expected_revision + 1, "removed_item_id": item_id}
            self._store_idempotency(conn, key, request_hash, response)
            return response

    def put_asset(self, value: dict) -> dict:
        with self.transaction(immediate=True) as conn:
            existing = conn.execute("SELECT * FROM assets WHERE sha256=?", (value["sha256"],)).fetchone()
            if existing:
                return dict(existing)
            conn.execute(
                "INSERT INTO assets(id,sha256,path,mime,bytes,width,height,source,source_execution_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (value["id"], value["sha256"], value["path"], value["mime"], value["bytes"], value["width"], value["height"], value["source"], value.get("source_execution_id")),
            )
        return value

    def get_asset(self, asset_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise FlowError("ARTIFACT_NOT_FOUND", "Imagem não encontrada.", status=404)
        return dict(row)

    def save_import(self, preview_hash: str, preview: dict) -> str:
        preview_id = str(uuid.uuid4())
        with self.transaction(immediate=True) as conn:
            conn.execute("INSERT INTO imports(id,preview_hash,preview_json,state,created_at) VALUES (?,?,?,?,?)", (preview_id, preview_hash, _json(preview), "PREVIEW", utc_now()))
        return preview_id

    def get_import(self, preview_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM imports WHERE id=?", (preview_id,)).fetchone()
        if not row:
            raise FlowError("BATCH_NOT_FOUND", "Prévia de importação não encontrada.", status=404)
        value = dict(row)
        value["preview"] = json.loads(value.pop("preview_json"))
        return value

    def save_plan(self, batch_id: str, revision: int, plan_hash: str, snapshot: dict, quote: dict, validation: dict) -> dict:
        plan_id = str(uuid.uuid4())
        with self.transaction(immediate=True) as conn:
            batch = conn.execute("SELECT revision FROM batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None or batch[0] != revision:
                self._raise_revision(conn, batch_id)
            conn.execute(
                "INSERT OR IGNORE INTO plans(id,batch_id,revision,hash,snapshot_json,quote_json,validation_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (plan_id, batch_id, revision, plan_hash, _json(snapshot), _json(quote), _json(validation), utc_now()),
            )
            conn.execute("UPDATE batches SET plan_hash=?,state='PLANNED',updated_at=? WHERE id=?", (plan_hash, utc_now(), batch_id))
            conn.execute("UPDATE items SET state='VALIDATED' WHERE batch_id=? AND state='DRAFT'", (batch_id,))
            self._event(conn, "plan.created", batch_id, payload={"status": "PLANNED", "total": len(snapshot["items"]), "message": "Simulação concluída sem gastar."})
        return {"id": plan_id, "batch_id": batch_id, "revision": revision, "hash": plan_hash, "snapshot": snapshot, "quote": quote, "validation": validation}

    def get_plan(self, batch_id: str, plan_hash: str | None = None) -> dict:
        with self.connect() as conn:
            if plan_hash:
                row = conn.execute("SELECT * FROM plans WHERE batch_id=? AND hash=? ORDER BY created_at DESC LIMIT 1", (batch_id, plan_hash)).fetchone()
            else:
                row = conn.execute("SELECT * FROM plans WHERE batch_id=? ORDER BY created_at DESC LIMIT 1", (batch_id,)).fetchone()
        if not row:
            raise FlowError("PLAN_STALE", "Faça a simulação novamente.", status=409)
        value = dict(row)
        value["snapshot"] = json.loads(value.pop("snapshot_json"))
        value["quote"] = json.loads(value.pop("quote_json"))
        value["validation"] = json.loads(value.pop("validation_json"))
        return value

    def create_approval_execution(self, payload: dict, *, scope: str, item_id: str, capability: str = "video.generate") -> dict:
        request_hash = canonical_json(payload)
        key = payload["idempotency_key"]
        with self.transaction(immediate=True) as conn:
            existing = self._idempotency(conn, key, request_hash)
            if existing is not None:
                return existing
            batch = conn.execute("SELECT * FROM batches WHERE id=?", (payload["batch_id"],)).fetchone()
            if not batch:
                raise FlowError("BATCH_NOT_FOUND", "Lote não encontrado.", status=404)
            if batch["revision"] != payload["expected_revision"] or batch["plan_hash"] != payload["plan_hash"]:
                raise FlowError("PLAN_STALE", "O plano mudou; simule novamente.", status=409)
            approval_id, correlation_id = str(uuid.uuid4()), str(uuid.uuid4())
            approval_payload = {**payload, "scope": scope, "item_ids": payload["item_ids"], "identity": batch["expected_account"]}
            conn.execute("INSERT INTO approvals(id,batch_id,revision,plan_hash,scope,payload_json,created_at) VALUES (?,?,?,?,?,?,?)", (approval_id, batch["id"], batch["revision"], batch["plan_hash"], scope, _json(approval_payload), utc_now()))
            execution_ids = []
            for approved_item_id in payload["item_ids"]:
                execution_id = str(uuid.uuid4())
                attempt = conn.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM executions WHERE item_id=?", (approved_item_id,)).fetchone()[0]
                conn.execute(
                    "INSERT INTO executions(id,batch_id,item_id,attempt,capability,state,correlation_id,request_hash,approval_id) VALUES (?,?,?,?,?,'QUEUED',?,?,?)",
                    (execution_id, batch["id"], approved_item_id, attempt, capability, correlation_id, request_hash, approval_id),
                )
                execution_ids.append(execution_id)
            new_state = "TEST_RUNNING" if scope == "TEST" else "QUEUED"
            conn.execute("UPDATE batches SET state=?,updated_at=? WHERE id=?", (new_state, utc_now(), batch["id"]))
            response = {"execution_id": execution_ids[0], "execution_ids": execution_ids, "state": "QUEUED", "correlation_id": correlation_id}
            self._store_idempotency(conn, key, request_hash, response, execution_id=execution_ids[0], status_code=202)
            for approved_item_id, execution_id in zip(payload["item_ids"], execution_ids):
                self._event(conn, "execution.queued", batch["id"], item_id=approved_item_id, execution_id=execution_id, correlation_id=correlation_id, payload={"status": "QUEUED", "message": "Execução autorizada e enfileirada."})
            return response

    def execution_context(self, execution_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT e.*,a.scope,a.payload_json,b.revision,b.plan_hash,b.output_dir,b.expected_account,b.pause_requested "
                "FROM executions e JOIN approvals a ON a.id=e.approval_id JOIN batches b ON b.id=e.batch_id WHERE e.id=?",
                (execution_id,),
            ).fetchone()
            if not row:
                raise FlowError("ITEM_NOT_FOUND", "Execução não encontrada.", status=404)
            item = conn.execute("SELECT i.*,NULL artifact_id FROM items i WHERE i.id=?", (row["item_id"],)).fetchone()
        value = dict(row)
        value["approval"] = json.loads(value.pop("payload_json"))
        value["item"] = self._item(item)
        return value

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO settings(key,value_json,revision) VALUES (?,?,1) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,revision=settings.revision+1",
                (key, _json(value)),
            )

    def batch_terminal_state(self, batch_id: str) -> str:
        with self.transaction(immediate=True) as conn:
            rows = conn.execute("SELECT state FROM items WHERE batch_id=?", (batch_id,)).fetchall()
            states = [row[0] for row in rows]
            running = {"SUBMITTING", "PROVIDER_PENDING", "DOWNLOADING", "VERIFYING", "READY", "VALIDATED"}
            if any(state in running for state in states):
                state = "RUNNING"
            elif states and all(state == "SUCCEEDED" for state in states):
                state = "SUCCEEDED"
            elif any(state == "SUCCEEDED" for state in states):
                state = "PARTIAL"
            else:
                state = "FAILED"
            conn.execute("UPDATE batches SET state=?,updated_at=? WHERE id=?", (state, utc_now(), batch_id))
            return state

    def claim_execution(self, owner: str) -> dict | None:
        lease_until = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT e.* FROM executions e JOIN batches b ON b.id=e.batch_id "
                "WHERE e.state='QUEUED' AND b.pause_requested=0 ORDER BY b.created_at,e.rowid LIMIT 1"
            ).fetchone()
            if not row:
                return None
            result = conn.execute("UPDATE executions SET state='RUNNING',lease_owner=?,lease_until=? WHERE id=? AND state='QUEUED'", (owner, lease_until, row["id"]))
            if result.rowcount != 1:
                return None
            return dict(conn.execute("SELECT * FROM executions WHERE id=?", (row["id"],)).fetchone())

    def execution(self, execution_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT e.*, (SELECT id FROM artifacts a WHERE a.execution_id=e.id AND a.validation_state='VALID' LIMIT 1) artifact_id "
                "FROM executions e WHERE e.id=?", (execution_id,)
            ).fetchone()
        if not row:
            raise FlowError("ITEM_NOT_FOUND", "Execução não encontrada.", status=404)
        value = dict(row)
        result = json.loads(value.pop("result_json") or "{}")
        value.update({k: result.get(k) for k in ("provider_message", "charged", "credits_observed_delta", "asset_id")})
        return value

    def set_execution(self, execution_id: str, state: str, *, result: dict | None = None, error_code: str | None = None, provider_operation_id: str | None = None, intent: bool = False) -> None:
        with self.transaction(immediate=True) as conn:
            row = conn.execute("SELECT batch_id,item_id,correlation_id FROM executions WHERE id=?", (execution_id,)).fetchone()
            if not row:
                raise FlowError("ITEM_NOT_FOUND", "Execução não encontrada.", status=404)
            conn.execute(
                "UPDATE executions SET state=?,result_json=?,error_code=?,provider_operation_id=COALESCE(?,provider_operation_id),intent_at=CASE WHEN ? THEN COALESCE(intent_at,?) ELSE intent_at END,lease_until=NULL WHERE id=?",
                (state, _json(result or {}), error_code, provider_operation_id, int(intent), utc_now(), execution_id),
            )
            self._event(conn, "execution." + state.casefold(), row["batch_id"], item_id=row["item_id"], execution_id=execution_id, correlation_id=row["correlation_id"], payload={"status": state, "error_code": error_code, "message": (result or {}).get("message")})

    def set_item_state(self, item_id: str, state: str) -> None:
        with self.transaction(immediate=True) as conn:
            conn.execute("UPDATE items SET state=? WHERE id=?", (state, item_id))

    def upload(self, batch_id: str, asset_id: str, project_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM uploads WHERE batch_id=? AND asset_id=? AND project_id=? AND state='CONFIRMED'", (batch_id, asset_id, project_id)).fetchone()
        return dict(row) if row else None

    def save_upload(self, batch_id: str, asset_id: str, project_id: str, media_id: str | None, sha256: str, state: str, result: dict) -> dict:
        upload_id = str(uuid.uuid4())
        with self.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO uploads(id,batch_id,asset_id,project_id,media_id,state,sha256,result_json) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(batch_id,asset_id,project_id) DO UPDATE SET media_id=excluded.media_id,state=excluded.state,sha256=excluded.sha256,result_json=excluded.result_json",
                (upload_id, batch_id, asset_id, project_id, media_id, state, sha256, _json(result)),
            )
        return {"id": upload_id, "batch_id": batch_id, "asset_id": asset_id, "project_id": project_id, "media_id": media_id, "state": state}

    def save_artifact(self, value: dict) -> dict:
        with self.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO artifacts(id,execution_id,sha256,mime,bytes,width,height,duration,path,validation_state,decoder_report) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (value["id"], value["execution_id"], value["sha256"], value["mime"], value["bytes"], value["width"], value["height"], value["duration"], value["path"], value["validation_state"], value["decoder_report"]),
            )
        return value

    def artifact(self, artifact_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row:
            raise FlowError("ARTIFACT_NOT_FOUND", "Vídeo não encontrado.", status=404)
        return dict(row)

    def artifacts_for_batch(self, batch_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT a.*,e.item_id FROM artifacts a JOIN executions e ON e.id=a.execution_id WHERE e.batch_id=? AND a.validation_state='VALID' ORDER BY e.rowid",
                (batch_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def request_pause(self, batch_id: str) -> dict:
        with self.transaction(immediate=True) as conn:
            result = conn.execute("UPDATE batches SET pause_requested=1,state=CASE WHEN state='RUNNING' THEN 'PAUSE_REQUESTED' ELSE 'PAUSED' END,updated_at=? WHERE id=?", (utc_now(), batch_id))
            if result.rowcount != 1:
                raise FlowError("BATCH_NOT_FOUND", "Lote não encontrado.", status=404)
            current = conn.execute("SELECT id FROM executions WHERE batch_id=? AND state='RUNNING' LIMIT 1", (batch_id,)).fetchone()
        batch = self.get_batch(batch_id)
        return {"batch_id": batch_id, "pause_requested": True, "state": batch["state"], "current_execution_id": current[0] if current else None}

    def resume(self, batch_id: str, plan_hash: str) -> dict:
        with self.transaction(immediate=True) as conn:
            result = conn.execute("UPDATE batches SET pause_requested=0,state='QUEUED',updated_at=? WHERE id=? AND plan_hash=? AND state='PAUSED'", (utc_now(), batch_id, plan_hash))
            if result.rowcount != 1:
                raise FlowError("PLAN_STALE", "Não é possível continuar: lote, plano ou estado mudou.", status=409)
        return self.get_batch(batch_id)

    def list_events(self, batch_id: str, after: int = 0, limit: int = 200) -> dict:
        limit = max(1, min(200, int(limit)))
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM events WHERE batch_id=? AND id>? ORDER BY id LIMIT ?", (batch_id, after, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        values = []
        for row in rows:
            value = dict(row)
            value["payload"] = json.loads(value.pop("payload_json"))
            values.append(value)
        return {"events": values, "last_event_id": values[-1]["id"] if values else after, "has_more": more}

    def _event(self, conn: sqlite3.Connection, event_type: str, batch_id: str | None, *, item_id: str | None = None, execution_id: str | None = None, correlation_id: str | None = None, payload: dict | None = None) -> None:
        conn.execute(
            "INSERT INTO events(event_type,batch_id,item_id,execution_id,correlation_id,created_at,payload_json) VALUES (?,?,?,?,?,?,?)",
            (event_type, batch_id, item_id, execution_id, correlation_id or str(uuid.uuid4()), utc_now(), _json(payload or {})),
        )

    def _idempotency(self, conn: sqlite3.Connection, key: str, request_hash: str) -> dict | None:
        row = conn.execute("SELECT request_hash,response_json FROM idempotency WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        if row["request_hash"] != request_hash:
            raise FlowError("IDEMPOTENCY_CONFLICT", "A chave já foi usada com outro pedido.", status=409)
        return json.loads(row["response_json"])

    def _store_idempotency(self, conn: sqlite3.Connection, key: str, request_hash: str, response: dict, *, execution_id: str | None = None, status_code: int = 200) -> None:
        conn.execute("INSERT INTO idempotency(key,request_hash,status_code,response_json,execution_id,created_at) VALUES (?,?,?,?,?,?)", (key, request_hash, status_code, _json(response), execution_id, utc_now()))
