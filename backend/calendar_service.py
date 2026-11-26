from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
import calendar as py_calendar
from pathlib import Path
from typing import Any

from .errors import FlowError

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
ALLOWED_KINDS = {"video", "edit", "ad", "post", "note"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date_from_iso(raw: str | None) -> str:
    txt = str(raw or "").strip()
    if len(txt) >= 10 and DATE_RE.fullmatch(txt[:10]):
        return txt[:10]
    return datetime.now().strftime("%Y-%m-%d")


class CalendarService:
    """Correlates video batches, CapCut edits, scheduled posts/ads, and Meta Ads campaigns onto a calendar."""

    def __init__(self, root: Path, data_dir: Path, repository, editor, meta_ads) -> None:
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.repository = repository
        self.editor = editor
        self.meta_ads = meta_ads

    def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        sdate = str(body.get("scheduled_date") or "").strip()
        if not DATE_RE.fullmatch(sdate):
            raise FlowError("SCHEMA_VALIDATION", "Informe uma data válida (AAAA-MM-DD).", pointer="/scheduled_date")
        kind = str(body.get("kind") or "ad").strip().lower()
        if kind not in ALLOWED_KINDS:
            raise FlowError("SCHEMA_VALIDATION", "Tipo de item inválido para o calendário.", pointer="/kind")
        title = str(body.get("title") or "").strip()[:160]
        if not title:
            raise FlowError("SCHEMA_VALIDATION", "Informe um título para o item.", pointer="/title")
        batch_id = str(body.get("batch_id") or "").strip() or None
        if batch_id:
            self.repository.get_batch(batch_id)
        item_id = f"cal_{uuid.uuid4().hex[:12]}"
        now = _utc_now()
        metrics = body.get("metrics") if isinstance(body.get("metrics"), dict) else {}
        row = {
            "id": item_id,
            "scheduled_date": sdate,
            "kind": kind,
            "title": title,
            "status": str(body.get("status") or "PLANNED").strip()[:30],
            "batch_id": batch_id,
            "media_path": str(body.get("media_path") or "").strip()[:500],
            "meta_account_id": str(body.get("meta_account_id") or "").strip()[:64],
            "meta_campaign_id": str(body.get("meta_campaign_id") or "").strip()[:64],
            "meta_ad_id": str(body.get("meta_ad_id") or "").strip()[:64],
            "platform": str(body.get("platform") or "Meta Ads").strip()[:40],
            "notes": str(body.get("notes") or "").strip()[:1000],
            "metrics": metrics,
            "created_at": now,
            "updated_at": now,
        }
        with self.repository.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO calendar_items(id,scheduled_date,kind,title,status,batch_id,media_path,meta_account_id,meta_campaign_id,meta_ad_id,platform,notes,metrics_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"], row["scheduled_date"], row["kind"], row["title"], row["status"],
                    row["batch_id"], row["media_path"], row["meta_account_id"], row["meta_campaign_id"],
                    row["meta_ad_id"], row["platform"], row["notes"], json.dumps(metrics, ensure_ascii=False),
                    now, now,
                ),
            )
        return row

    def patch_item(self, item_id: str, body: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction(immediate=True) as conn:
            cur = conn.execute("SELECT * FROM calendar_items WHERE id=?", (item_id,)).fetchone()
            if not cur:
                raise FlowError("ITEM_NOT_FOUND", "Item de calendário não encontrado.", status=404)
            sdate = str(body.get("scheduled_date") or cur["scheduled_date"]).strip()
            if not DATE_RE.fullmatch(sdate):
                raise FlowError("SCHEMA_VALIDATION", "Data inválida (AAAA-MM-DD).", pointer="/scheduled_date")
            title = str(body.get("title") if body.get("title") is not None else cur["title"]).strip()[:160] or cur["title"]
            status = str(body.get("status") if body.get("status") is not None else cur["status"]).strip()[:30]
            notes = str(body.get("notes") if body.get("notes") is not None else cur["notes"]).strip()[:1000]
            platform = str(body.get("platform") if body.get("platform") is not None else cur["platform"]).strip()[:40]
            now = _utc_now()
            conn.execute(
                "UPDATE calendar_items SET scheduled_date=?,title=?,status=?,notes=?,platform=?,updated_at=? WHERE id=?",
                (sdate, title, status, notes, platform, now, item_id),
            )
            updated = conn.execute("SELECT * FROM calendar_items WHERE id=?", (item_id,)).fetchone()
        return self._row_to_dict(updated)

    def delete_item(self, item_id: str) -> dict[str, Any]:
        with self.repository.transaction(immediate=True) as conn:
            cur = conn.execute("DELETE FROM calendar_items WHERE id=?", (item_id,))
            if cur.rowcount == 0:
                raise FlowError("ITEM_NOT_FOUND", "Item de calendário não encontrado.", status=404)
        return {"deleted": True, "id": item_id}

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        d = dict(row)
        try:
            d["metrics"] = json.loads(d.pop("metrics_json") or "{}")
        except (ValueError, TypeError):
            d["metrics"] = {}
        return d

    @staticmethod
    def _resolve_range(
        month: str | None,
        since: str | None,
        until: str | None,
        range_preset: str | None,
    ) -> tuple[str, str, str, str]:
        target_month = (month or "").strip()
        if not MONTH_RE.fullmatch(target_month):
            target_month = datetime.now().strftime("%Y-%m")
        preset = (range_preset or "month").strip().lower()
        today = datetime.now().date()
        if DATE_RE.fullmatch((since or "").strip()) and DATE_RE.fullmatch((until or "").strip()):
            s_day, u_day = str(since).strip(), str(until).strip()
            if s_day > u_day:
                s_day, u_day = u_day, s_day
            return target_month, "custom", s_day, u_day
        if preset in ("90d", "3m"):
            return target_month, "90d", (today - timedelta(days=90)).isoformat(), today.isoformat()
        if preset in ("180d", "6m"):
            return target_month, "180d", (today - timedelta(days=180)).isoformat(), today.isoformat()
        if preset in ("365d", "1y"):
            return target_month, "365d", (today - timedelta(days=365)).isoformat(), today.isoformat()
        if preset in ("maximum", "all"):
            return target_month, "maximum", "2020-01-01", (today + timedelta(days=365)).isoformat()
        yr, mo = map(int, target_month.split("-"))
        last_dom = py_calendar.monthrange(yr, mo)[1]
        return target_month, "month", f"{target_month}-01", f"{target_month}-{last_dom:02d}"

    def summary(
        self,
        month: str | None = None,
        *,
        since: str | None = None,
        until: str | None = None,
        range_preset: str | None = None,
        account_id: str | None = None,
        include_meta: bool = True,
    ) -> dict[str, Any]:
        target_month, resolved_preset, range_since, range_until = self._resolve_range(month, since, until, range_preset)

        events: list[dict[str, Any]] = []

        # 1. Batches from SQLite across the selected period
        with self.repository.connect() as conn:
            batches = conn.execute(
                "SELECT id,name,state,created_at,output_dir, "
                "(SELECT COUNT(*) FROM items WHERE items.batch_id=batches.id) AS total_items, "
                "(SELECT COUNT(*) FROM items WHERE items.batch_id=batches.id AND items.state='SUCCEEDED') AS done_items "
                "FROM batches ORDER BY created_at DESC LIMIT 1000"
            ).fetchall()
            manual_rows = conn.execute(
                "SELECT * FROM calendar_items WHERE scheduled_date BETWEEN ? AND ? ORDER BY scheduled_date DESC,created_at DESC",
                (range_since, range_until),
            ).fetchall()

        for b in batches:
            day = _date_from_iso(b["created_at"])
            if not (range_since <= day <= range_until):
                continue
            events.append({
                "id": f"batch:{b['id']}",
                "source": "batch",
                "kind": "video",
                "date": day,
                "title": b["name"],
                "status": b["state"],
                "platform": "Google Flow",
                "batch_id": b["id"],
                "media_path": "",
                "meta_campaign_id": "",
                "subtitle": f"{b['done_items']}/{b['total_items']} vídeo(s) prontos",
                "metrics": {"total_items": b["total_items"], "done_items": b["done_items"]},
            })

        # 2. Edited videos from saida/**/editados and saved CapCut projects
        saida_dir = (self.root / "saida").resolve()
        seen_paths: set[str] = set()
        if saida_dir.is_dir():
            for mp4 in saida_dir.rglob("*.mp4"):
                if "editados" not in mp4.parts:
                    continue
                st = mp4.stat()
                day = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
                if not (range_since <= day <= range_until):
                    continue
                rpath = str(mp4.resolve())
                seen_paths.add(rpath)
                events.append({
                    "id": f"edit:{mp4.name}:{day}",
                    "source": "editor",
                    "kind": "edit",
                    "date": day,
                    "title": mp4.name,
                    "status": "EDITED",
                    "platform": "CapCut Local",
                    "batch_id": None,
                    "media_path": rpath,
                    "meta_campaign_id": "",
                    "subtitle": f"{round(st.st_size / (1024 * 1024), 2)} MB",
                    "metrics": {"size_bytes": st.st_size},
                })

        spy_edit_dir = (self.data_dir / "spy" / "media" / "editados").resolve()
        if spy_edit_dir.is_dir():
            for mp4 in spy_edit_dir.glob("*.mp4"):
                rpath = str(mp4.resolve())
                if rpath in seen_paths:
                    continue
                st = mp4.stat()
                day = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
                if not (range_since <= day <= range_until):
                    continue
                events.append({
                    "id": f"edit:{mp4.name}:{day}",
                    "source": "editor",
                    "kind": "edit",
                    "date": day,
                    "title": mp4.name,
                    "status": "EDITED",
                    "platform": "CapCut Local",
                    "batch_id": None,
                    "media_path": rpath,
                    "meta_campaign_id": "",
                    "subtitle": "Edição vinda do Spy",
                    "metrics": {"size_bytes": st.st_size},
                })

        # 3. Correlated scheduled / manually linked calendar items
        for mr in manual_rows:
            d = self._row_to_dict(mr)
            events.append({
                "id": d["id"],
                "source": "manual",
                "kind": d["kind"],
                "date": d["scheduled_date"],
                "title": d["title"],
                "status": d["status"],
                "platform": d["platform"],
                "batch_id": d["batch_id"],
                "media_path": d["media_path"],
                "meta_campaign_id": d["meta_campaign_id"],
                "meta_ad_id": d["meta_ad_id"],
                "notes": d["notes"],
                "subtitle": d["notes"] or d["platform"],
                "metrics": d["metrics"],
            })

        # 4. Meta Ads live campaigns correlated across the full chosen date range
        meta_spend = 0.0
        meta_clicks = 0
        meta_purchases = 0
        meta_campaigns: list[dict[str, Any]] = []
        meta_status = self.meta_ads.status()
        if include_meta and meta_status.get("connected"):
            acc = (account_id or meta_status.get("account_id") or "").strip()
            if acc:
                try:
                    today_iso = datetime.now().strftime("%Y-%m-%d")
                    meta_until = min(range_until, today_iso)
                    if resolved_preset == "maximum":
                        res = self.meta_ads.list_campaigns(account_id=acc, date_preset="maximum")
                    else:
                        res = self.meta_ads.list_campaigns(
                            account_id=acc,
                            date_preset="maximum",
                            time_range={"since": range_since, "until": max(range_since, meta_until)},
                        )
                    for c in res.get("items") or []:
                        meta_campaigns.append({"id": c.get("id"), "name": c.get("name"), "status": c.get("status")})
                        ins = c.get("insights") or {}
                        meta_spend += float(ins.get("spend") or 0)
                        meta_clicks += int(ins.get("clicks") or 0)
                        meta_purchases += int(ins.get("purchases") or 0)
                        day = _date_from_iso(c.get("created_time"))
                        if range_since <= day <= range_until:
                            events.append({
                                "id": f"meta:{c.get('id')}",
                                "source": "meta_ads",
                                "kind": "ad",
                                "date": day,
                                "title": c.get("name") or c.get("id"),
                                "status": c.get("status") or c.get("effective_status") or "PAUSED",
                                "platform": f"Meta · {acc}",
                                "batch_id": None,
                                "media_path": "",
                                "meta_campaign_id": str(c.get("id") or ""),
                                "subtitle": f"Gasto: R$ {float(ins.get('spend') or 0):.2f} · Cliques: {int(ins.get('clicks') or 0)}",
                                "metrics": ins,
                            })
                except FlowError:
                    pass

        events.sort(key=lambda e: e.get("date") or "", reverse=True)
        return {
            "month": target_month,
            "range_preset": resolved_preset,
            "since": range_since,
            "until": range_until,
            "events": events,
            "kpis": {
                "videos": sum(1 for e in events if e["kind"] == "video"),
                "edits": sum(1 for e in events if e["kind"] == "edit"),
                "ads": sum(1 for e in events if e["kind"] == "ad"),
                "scheduled": sum(1 for e in events if e["source"] == "manual"),
                "meta_spend": round(meta_spend, 2),
                "meta_clicks": meta_clicks,
                "meta_purchases": meta_purchases,
            },
            "meta_campaigns": meta_campaigns,
        }
