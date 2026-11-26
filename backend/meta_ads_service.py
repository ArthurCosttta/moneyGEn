from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .errors import FlowError

GRAPH_VERSION = "v21.0"
ACT_ID_RE = re.compile(r"^act_[A-Za-z0-9_]{1,64}$")
ENTITY_ID_RE = re.compile(r"^[A-Za-z0-9_]{3,80}$")


def _mask_token(token: str) -> str:
    cleaned = (token or "").strip()
    if len(cleaned) <= 10:
        return "***" if cleaned else ""
    return f"{cleaned[:5]}...{cleaned[-4:]}"


def _multipart_form_data(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----FlowLocalMeta{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, val in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(val).encode("utf-8"))
        chunks.append(b"\r\n")
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'.encode("utf-8")
    )
    chunks.append(f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
    chunks.append(file_path.read_bytes())
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class MetaAdsService:
    """Real Meta Ads integration combining the `meta-ads` CLI with direct Graph API media upload."""

    def __init__(self, data_dir: Path, repository, assets, editor=None, *, no_spend: bool = False) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.repository = repository
        self.assets = assets
        self.editor = editor
        self.no_spend = no_spend

    @staticmethod
    def _cli_config_path() -> Path:
        return Path.home() / ".config" / "meta-ads-cli" / "config.json"

    @staticmethod
    def _cli_invocation() -> list[str] | None:
        appdata = os.environ.get("APPDATA")
        if appdata:
            node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
            dist_js = Path(appdata) / "npm" / "node_modules" / "meta-ads" / "dist" / "index.js"
            if dist_js.is_file() and Path(node).is_file():
                return [str(node), str(dist_js)]
            cmd_shim = Path(appdata) / "npm" / "meta-ads.cmd"
            if cmd_shim.is_file():
                return [str(cmd_shim)]
        found = shutil.which("meta-ads")
        if found:
            return [found]
        return None

    def _run_cli(self, args: list[str], *, timeout: int = 35) -> Any:
        base = self._cli_invocation()
        if not base:
            raise FlowError(
                "META_CLI_UNAVAILABLE",
                "CLI `meta-ads` não encontrada. Execute `npm install -g meta-ads`.",
                status=503,
            )
        env = os.environ.copy()
        env["NO_COLOR"] = "1"
        try:
            completed = subprocess.run(
                base + args,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise FlowError("META_TIMEOUT", "A consulta à Meta Ads demorou além do limite.", status=504) from exc
        except OSError as exc:
            raise FlowError("META_CLI_UNAVAILABLE", f"Falha ao executar meta-ads: {exc}", status=503) from exc

        raw = (completed.stdout or "").strip()
        err = (completed.stderr or "").strip()
        if completed.returncode != 0:
            detail = (err or raw or "Erro desconhecido na CLI meta-ads.")[-1500:]
            code = "META_AUTH_REQUIRED" if any(t in detail.casefold() for t in ("token", "auth", "oauth", "190", "unauthorized")) else "META_API_ERROR"
            status = 401 if code == "META_AUTH_REQUIRED" else 502
            raise FlowError(code, f"Meta Ads retornou erro: {detail.splitlines()[-1][:240]}", status=status, provider_message=detail)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            for line in reversed(raw.splitlines()):
                line = line.strip()
                if line.startswith(("{", "[")):
                    try:
                        return json.loads(line)
                    except ValueError:
                        continue
            return {"raw": raw}

    def _read_access_token(self) -> str:
        env_tok = os.environ.get("META_ADS_ACCESS_TOKEN", "").strip()
        if env_tok:
            return env_tok
        cfg_path = self._cli_config_path()
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            cfg = {}
        if not isinstance(cfg, dict):
            return ""
        sources = [cfg]
        if isinstance(cfg.get("auth"), dict):
            sources.append(cfg["auth"])
        for src in sources:
            for key in ("accessToken", "access_token", "token"):
                val = src.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        return ""

    def _graph_request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        form_data: dict[str, str] | None = None,
        file_upload: tuple[str, Path] | None = None,
        timeout: int = 60,
        token_override: str | None = None,
    ) -> dict[str, Any]:
        token = (token_override or self._read_access_token()).strip()
        if not token:
            raise FlowError("META_AUTH_REQUIRED", "Conecte sua conta Meta Ads antes de executar esta ação.", status=401)
        clean_ep = endpoint.lstrip("/")
        query = dict(params or {})
        query["access_token"] = token
        url = f"https://graph.facebook.com/{GRAPH_VERSION}/{clean_ep}"
        headers: dict[str, str] = {}
        data_bytes: bytes | None = None
        if file_upload is not None:
            field_name, path = file_upload
            fields = dict(form_data or {})
            fields["access_token"] = token
            data_bytes, content_type = _multipart_form_data(fields, field_name, path)
            headers["Content-Type"] = content_type
        elif method.upper() == "POST":
            body_fields = dict(form_data or {})
            body_fields["access_token"] = token
            data_bytes = urllib.parse.urlencode(body_fields).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            url += "?" + urllib.parse.urlencode(query)

        req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw_err = exc.read().decode("utf-8", errors="replace")
            msg = raw_err[:1200]
            err_code = None
            try:
                err_obj = json.loads(raw_err).get("error", {})
                msg = str(err_obj.get("message") or msg)
                err_code = err_obj.get("code")
            except (ValueError, TypeError):
                pass
            auth_err = exc.code == 401 or err_code in (190, 102)
            perm_err = err_code == 200 or "has NOT grant" in msg or "permission" in msg.lower()
            if perm_err and ("ads_management" in msg or "ads_read" in msg or "owner" in msg):
                raise FlowError(
                    "META_ASSET_PERMISSION",
                    f"Meta Graph API (#200): este token não tem a conta de anúncios atribuída no Business Manager. Vá em Configurações do Negócio → Usuários do sistema → Atribuir ativos → Contas de anúncios → marque sua conta com Controle total (Gerenciar conta de anúncios). Detalhe: {msg}",
                    status=403,
                    provider_message=raw_err[:1500],
                ) from exc
            raise FlowError(
                "META_AUTH_REQUIRED" if auth_err else "META_API_ERROR",
                f"Meta Graph API: {msg}",
                status=401 if auth_err else 502,
                provider_message=raw_err[:1500],
            ) from exc
        except OSError as exc:
            raise FlowError("META_API_ERROR", f"Falha de conexão com a Meta Graph API: {exc}", status=502) from exc

    def _write_cli_token(self, token: str | None) -> None:
        cfg_path = self._cli_config_path()
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                cfg = {}
        except (OSError, ValueError, TypeError):
            cfg = {}
        if token:
            auth = cfg.get("auth") if isinstance(cfg.get("auth"), dict) else {}
            auth["access_token"] = token
            cfg["auth"] = auth
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        elif cfg_path.is_file():
            if isinstance(cfg.get("auth"), dict):
                cfg["auth"].pop("access_token", None)
            for k in ("accessToken", "access_token", "token"):
                cfg.pop(k, None)
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    def _defaults(self) -> dict[str, str]:
        return {
            "account_id": self.repository.get_setting("meta_ads_account_id") or "",
            "pixel_id": self.repository.get_setting("meta_ads_pixel_id") or "",
            "page_id": self.repository.get_setting("meta_ads_page_id") or "",
            "default_url": self.repository.get_setting("meta_ads_default_url") or "",
        }

    def status(self) -> dict[str, Any]:
        defaults = self._defaults()
        token = self._read_access_token()
        if not token:
            return {
                "cli_installed": True,
                "connected": False,
                "token_preview": "",
                **defaults,
                "error": None,
            }
        return {
            "cli_installed": True,
            "connected": True,
            "token_preview": _mask_token(token),
            **defaults,
            "error": None,
        }

    def login(self, token: str, *, account_id: str | None = None, app_id: str | None = None, app_secret: str | None = None) -> dict[str, Any]:
        if not isinstance(token, str) or len(token.strip()) < 16:
            raise FlowError("SCHEMA_VALIDATION", "Cole um Access Token válido da Meta Marketing API.", pointer="/token")
        clean_token = token.strip()
        if app_id and app_secret:
            ex = self._graph_request(
                "GET",
                "oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": app_id.strip(),
                    "client_secret": app_secret.strip(),
                    "fb_exchange_token": clean_token,
                },
                timeout=20,
                token_override=clean_token,
            )
            if isinstance(ex, dict) and ex.get("access_token"):
                clean_token = str(ex["access_token"]).strip()
        accounts_res = self._graph_request(
            "GET",
            "me/adaccounts",
            params={"fields": "id,name,account_id,account_status,currency,timezone_name,amount_spent", "limit": "100"},
            timeout=25,
            token_override=clean_token,
        )
        accounts = accounts_res.get("data") if isinstance(accounts_res, dict) else []
        if not isinstance(accounts, list):
            accounts = []
        self._write_cli_token(clean_token)
        chosen = (account_id or "").strip()
        if chosen:
            if not ACT_ID_RE.fullmatch(chosen):
                chosen = f"act_{chosen.removeprefix('act_')}"
            self.repository.set_setting("meta_ads_account_id", chosen)
        elif accounts:
            chosen = str(accounts[0].get("id") or "")
            self.repository.set_setting("meta_ads_account_id", chosen)
        else:
            self.repository.set_setting("meta_ads_account_id", "")
        return {"status": self.status(), "accounts": accounts}

    def logout(self) -> dict[str, Any]:
        self._write_cli_token(None)
        for key in ("meta_ads_account_id", "meta_ads_pixel_id", "meta_ads_page_id", "meta_ads_default_url"):
            self.repository.set_setting(key, "")
        return {"status": self.status()}

    def save_defaults(self, body: dict[str, Any]) -> dict[str, Any]:
        mapping = {
            "account_id": "meta_ads_account_id",
            "pixel_id": "meta_ads_pixel_id",
            "page_id": "meta_ads_page_id",
            "default_url": "meta_ads_default_url",
        }
        for key, setting_key in mapping.items():
            if key in body and body[key] is not None:
                val = str(body[key]).strip()
                if key == "account_id" and val and not val.startswith("act_"):
                    val = f"act_{val}"
                self.repository.set_setting(setting_key, val)
        return self.status()

    @staticmethod
    def _normalize_account_id(account_id: str | None, fallback: str) -> str:
        raw = (account_id or fallback or "").strip()
        if not raw:
            raise FlowError("META_AUTH_REQUIRED", "Selecione uma conta de anúncios (act_...).", status=400)
        if not raw.startswith("act_"):
            raw = f"act_{raw}"
        if not ACT_ID_RE.fullmatch(raw):
            raise FlowError("SCHEMA_VALIDATION", "ID de conta Meta inválido (use act_123456).", pointer="/account_id")
        return raw

    def list_accounts(self) -> dict[str, Any]:
        res = self._graph_request(
            "GET",
            "me/adaccounts",
            params={"fields": "id,name,account_id,account_status,currency,timezone_name,amount_spent", "limit": "100"},
            timeout=25,
        )
        items = res.get("data") if isinstance(res, dict) else []
        if not isinstance(items, list):
            items = []
        warning = None
        if not items:
            me_name = "este usuário"
            try:
                me = self._graph_request("GET", "me", params={"fields": "id,name"}, timeout=15)
                if isinstance(me, dict) and me.get("name"):
                    me_name = str(me["name"])
            except FlowError:
                pass
            warning = (
                f"O token conectado ({me_name}) está ativo, porém nenhuma Conta de Anúncios foi atribuída a ele "
                "no Meta Business Manager (/me/adaccounts retornou vazio). "
                "Em Configurações do Negócio → Usuários → Usuários do sistema → Atribuir ativos → Contas de anúncios, "
                "marque sua conta e ative 'Gerenciar conta de anúncios (Controle total)'."
            )
        return {"items": items, "warning": warning}

    @staticmethod
    def _insights_map(insights_res: Any, id_field: str) -> dict[str, dict[str, Any]]:
        rows = insights_res.get("data") if isinstance(insights_res, dict) else []
        mapped: dict[str, dict[str, Any]] = {}
        if not isinstance(rows, list):
            return mapped
        for row in rows:
            if not isinstance(row, dict):
                continue
            eid = str(row.get(id_field) or "")
            if not eid:
                continue
            actions = row.get("actions") or []
            purchases = 0
            if isinstance(actions, list):
                for act in actions:
                    if isinstance(act, dict) and act.get("action_type") in ("purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"):
                        try:
                            purchases += int(float(act.get("value") or 0))
                        except (TypeError, ValueError):
                            pass
            mapped[eid] = {
                "spend": float(row.get("spend") or 0),
                "impressions": int(float(row.get("impressions") or 0)),
                "clicks": int(float(row.get("clicks") or 0)),
                "ctr": float(row.get("ctr") or 0),
                "cpc": float(row.get("cpc") or 0),
                "purchases": purchases,
            }
        return mapped

    def _insights_for_level(
        self,
        acc: str,
        level: str,
        date_preset: str,
        *,
        time_range: dict[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        id_field = f"{level}_id"
        params: dict[str, Any] = {
            "level": level,
            "fields": f"{id_field},spend,impressions,clicks,ctr,cpc,actions",
            "limit": "150",
        }
        if time_range and time_range.get("since") and time_range.get("until"):
            params["time_range"] = json.dumps({"since": str(time_range["since"]), "until": str(time_range["until"])})
        else:
            params["date_preset"] = date_preset or "last_7d"
        try:
            ins = self._graph_request("GET", f"{acc}/insights", params=params, timeout=22)
            return self._insights_map(ins, id_field)
        except FlowError:
            return {}

    def list_campaigns(
        self,
        account_id: str | None = None,
        *,
        date_preset: str = "last_7d",
        time_range: dict[str, str] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        acc = self._normalize_account_id(account_id, self._defaults()["account_id"])
        params: dict[str, Any] = {
            "fields": "id,name,objective,status,effective_status,daily_budget,lifetime_budget,created_time",
            "limit": "150",
        }
        if status in ("ACTIVE", "PAUSED"):
            params["effective_status"] = json.dumps([status])
        res = self._graph_request("GET", f"{acc}/campaigns", params=params, timeout=25)
        items = res.get("data") if isinstance(res, dict) else []
        if not isinstance(items, list):
            items = []
        insights = self._insights_for_level(acc, "campaign", date_preset, time_range=time_range)
        for item in items:
            item["insights"] = insights.get(str(item.get("id") or ""), {"spend": 0, "impressions": 0, "clicks": 0, "ctr": 0, "cpc": 0, "purchases": 0})
        return {"account_id": acc, "items": items}

    def list_adsets(self, account_id: str | None = None, *, campaign_id: str | None = None, date_preset: str = "last_7d") -> dict[str, Any]:
        acc = self._normalize_account_id(account_id, self._defaults()["account_id"])
        endpoint = f"{acc}/adsets"
        if campaign_id:
            if not ENTITY_ID_RE.fullmatch(campaign_id):
                raise FlowError("SCHEMA_VALIDATION", "campaign_id inválido.")
            endpoint = f"{campaign_id}/adsets"
        res = self._graph_request(
            "GET",
            endpoint,
            params={"fields": "id,name,campaign_id,status,effective_status,billing_event,optimization_goal,daily_budget,created_time", "limit": "50"},
            timeout=25,
        )
        items = res.get("data") if isinstance(res, dict) else []
        if not isinstance(items, list):
            items = []
        insights = self._insights_for_level(acc, "adset", date_preset)
        for item in items:
            item["insights"] = insights.get(str(item.get("id") or ""), {"spend": 0, "impressions": 0, "clicks": 0, "ctr": 0, "cpc": 0, "purchases": 0})
        return {"account_id": acc, "campaign_id": campaign_id, "items": items}

    def list_ads(self, account_id: str | None = None, *, adset_id: str | None = None, campaign_id: str | None = None, date_preset: str = "last_7d") -> dict[str, Any]:
        acc = self._normalize_account_id(account_id, self._defaults()["account_id"])
        endpoint = f"{acc}/ads"
        if adset_id:
            if not ENTITY_ID_RE.fullmatch(adset_id):
                raise FlowError("SCHEMA_VALIDATION", "adset_id inválido.")
            endpoint = f"{adset_id}/ads"
        elif campaign_id:
            if not ENTITY_ID_RE.fullmatch(campaign_id):
                raise FlowError("SCHEMA_VALIDATION", "campaign_id inválido.")
            endpoint = f"{campaign_id}/ads"
        res = self._graph_request(
            "GET",
            endpoint,
            params={"fields": "id,name,adset_id,campaign_id,status,effective_status,creative{id,name},created_time", "limit": "50"},
            timeout=25,
        )
        items = res.get("data") if isinstance(res, dict) else []
        if not isinstance(items, list):
            items = []
        insights = self._insights_for_level(acc, "ad", date_preset)
        for item in items:
            item["insights"] = insights.get(str(item.get("id") or ""), {"spend": 0, "impressions": 0, "clicks": 0, "ctr": 0, "cpc": 0, "purchases": 0})
        return {"account_id": acc, "items": items}

    def list_resources(self, account_id: str | None = None) -> dict[str, Any]:
        acc = self._normalize_account_id(account_id, self._defaults()["account_id"])
        pixels_res = self._graph_request("GET", f"{acc}/adspixels", params={"fields": "id,name,last_fired_time,creation_time,code"})
        pages_res = self._graph_request("GET", "me/accounts", params={"fields": "id,name,instagram_business_account{id,username}"})
        return {
            "account_id": acc,
            "pixels": pixels_res.get("data") if isinstance(pixels_res.get("data"), list) else [],
            "pages": pages_res.get("data") if isinstance(pages_res.get("data"), list) else [],
        }

    def create_pixel(self, account_id: str | None, name: str | None) -> dict[str, Any]:
        if self.no_spend:
            raise FlowError("NO_SPEND_ACTIVE", "A trava sem gastos impede criar recursos na conta real.", status=403)
        acc = self._normalize_account_id(account_id, self._defaults()["account_id"])
        clean_name = str(name or "").strip()[:100]
        if not clean_name:
            raise FlowError("SCHEMA_VALIDATION", "Informe um nome para o novo Pixel.", pointer="/name")
        res = self._graph_request("POST", f"{acc}/adspixels", form_data={"name": clean_name}, timeout=20)
        pid = str(res.get("id") or "")
        if pid and not self._defaults()["pixel_id"]:
            self.repository.set_setting("meta_ads_pixel_id", pid)
        return {"account_id": acc, "id": pid, "name": clean_name, "raw": res}

    @staticmethod
    def _extract_revenue_and_roas(row: dict[str, Any]) -> tuple[float, float, int]:
        spend = float(row.get("spend") or 0)
        roas = 0.0
        pr = row.get("purchase_roas")
        if isinstance(pr, list) and pr and isinstance(pr[0], dict):
            try:
                roas = float(pr[0].get("value") or 0)
            except (TypeError, ValueError):
                roas = 0.0
        rev = 0.0
        for av in (row.get("action_values") or []):
            if isinstance(av, dict) and av.get("action_type") in ("purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"):
                try:
                    rev += float(av.get("value") or 0)
                except (TypeError, ValueError):
                    pass
        purchases = 0
        for act in (row.get("actions") or []):
            if isinstance(act, dict) and act.get("action_type") in ("purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"):
                try:
                    purchases += int(float(act.get("value") or 0))
                except (TypeError, ValueError):
                    pass
        if roas == 0.0 and spend > 0 and rev > 0:
            roas = round(rev / spend, 2)
        return round(rev, 2), round(roas, 2), purchases

    def account_summary(self, account_id: str | None = None, *, date_preset: str = "last_7d") -> dict[str, Any]:
        acc = self._normalize_account_id(account_id, self._defaults()["account_id"])
        acc_info = self._graph_request(
            "GET",
            acc,
            params={"fields": "id,name,currency,spend_cap,amount_spent,balance,account_status,funding_source_details,daily_spend_limit"},
            timeout=15,
        )
        today_res = self._graph_request(
            "GET",
            f"{acc}/insights",
            params={"date_preset": "today", "fields": "spend,impressions,clicks,actions,action_values,purchase_roas"},
            timeout=15,
        )
        period_res = self._graph_request(
            "GET",
            f"{acc}/insights",
            params={"date_preset": date_preset or "last_7d", "fields": "spend,impressions,clicks,actions,action_values,purchase_roas"},
            timeout=15,
        )
        today_row = (today_res.get("data") or [{}])[0] if isinstance(today_res.get("data"), list) else {}
        period_row = (period_res.get("data") or [{}])[0] if isinstance(period_res.get("data"), list) else {}
        today_spend = round(float(today_row.get("spend") or 0), 2)
        period_spend = round(float(period_row.get("spend") or 0), 2)
        period_rev, period_roas, period_purchases = self._extract_revenue_and_roas(period_row)
        roi_pct = round(((period_rev - period_spend) / period_spend) * 100, 1) if period_spend > 0 and period_rev > 0 else 0.0

        # Active daily budget across CBO campaigns + ABO adsets + top winner creative
        active_daily_budget = 0.0
        active_lifetime_budget = 0.0
        cbo_campaign_ids: set[str] = set()
        winner = None
        try:
            camps = self.list_campaigns(acc, date_preset=date_preset).get("items") or []
            for c in camps:
                is_act = (c.get("effective_status") or c.get("status")) == "ACTIVE"
                if is_act and c.get("daily_budget"):
                    active_daily_budget += float(c["daily_budget"]) / 100.0
                    cbo_campaign_ids.add(str(c.get("id") or ""))
                elif is_act and c.get("lifetime_budget"):
                    active_lifetime_budget += float(c["lifetime_budget"]) / 100.0
                    cbo_campaign_ids.add(str(c.get("id") or ""))
            try:
                adsets = self.list_adsets(acc, date_preset=date_preset).get("items") or []
                for aset in adsets:
                    is_act = (aset.get("effective_status") or aset.get("status")) == "ACTIVE"
                    if not is_act or str(aset.get("campaign_id") or "") in cbo_campaign_ids:
                        continue
                    if aset.get("daily_budget"):
                        active_daily_budget += float(aset["daily_budget"]) / 100.0
                    elif aset.get("lifetime_budget"):
                        active_lifetime_budget += float(aset["lifetime_budget"]) / 100.0
            except FlowError:
                pass
            ads = self.list_ads(acc, date_preset=date_preset).get("items") or []
            candidates = ads or camps
            if candidates:
                ranked = sorted(
                    candidates,
                    key=lambda x: (
                        (x.get("insights") or {}).get("purchases", 0),
                        (x.get("insights") or {}).get("clicks", 0),
                        (x.get("insights") or {}).get("spend", 0),
                    ),
                    reverse=True,
                )
                top = ranked[0]
                winner = {
                    "id": top.get("id"),
                    "name": top.get("name") or top.get("id"),
                    "kind": "ad" if ads else "campaign",
                    "status": top.get("effective_status") or top.get("status") or "PAUSED",
                    "insights": top.get("insights") or {},
                }
        except FlowError:
            pass

        spend_cap = round(float(acc_info.get("spend_cap") or 0) / 100.0, 2)
        balance = round(float(acc_info.get("balance") or 0) / 100.0, 2)
        amount_spent = round(float(acc_info.get("amount_spent") or 0) / 100.0, 2)
        fsd = acc_info.get("funding_source_details") if isinstance(acc_info.get("funding_source_details"), dict) else {}
        funding_display = str(fsd.get("display_string") or "").strip()
        funding_balance = balance
        if funding_display:
            m_bal = re.search(r"(?:saldo|balance|dispon[ií]vel)[^0-9-]*([0-9]+(?:[.,\s][0-9]{3})*(?:[.,][0-9]{2})?)", funding_display, re.IGNORECASE) or re.search(r"R\$\s*([0-9]+(?:[.,\s][0-9]{3})*(?:[.,][0-9]{2})?)", funding_display)
            if m_bal:
                raw_n = re.sub(r"\s+", "", m_bal.group(1))
                if "," in raw_n and "." in raw_n:
                    raw_n = raw_n.replace(".", "").replace(",", ".")
                elif "," in raw_n:
                    raw_n = raw_n.replace(",", ".")
                try:
                    funding_balance = round(float(raw_n), 2)
                except ValueError:
                    pass
        return {
            "account_id": acc,
            "name": acc_info.get("name") or acc,
            "currency": acc_info.get("currency") or "BRL",
            "spend_cap": spend_cap,
            "amount_spent": amount_spent,
            "balance": balance,
            "funding_balance": funding_balance,
            "funding_display": funding_display,
            "active_daily_budget": round(active_daily_budget, 2),
            "active_lifetime_budget": round(active_lifetime_budget, 2),
            "today_spend": today_spend,
            "period_spend": period_spend,
            "period_revenue": period_rev,
            "roas": period_roas,
            "roi_pct": roi_pct,
            "purchases": period_purchases,
            "winner": winner,
        }

    def update_status(self, entity_type: str, entity_id: str, new_status: str, *, confirmed: bool = False) -> dict[str, Any]:
        if self.no_spend and new_status == "ACTIVE":
            raise FlowError("NO_SPEND_ACTIVE", "A trava sem gastos impede ativar campanhas/anúncios reais.", status=403)
        if not confirmed:
            raise FlowError("SPEND_NOT_CONFIRMED", "Confirme a alteração antes de modificar a conta real.", status=403)
        if entity_type not in ("campaign", "adset", "ad"):
            raise FlowError("SCHEMA_VALIDATION", "Tipo inválido (campaign, adset ou ad).")
        if not ENTITY_ID_RE.fullmatch(entity_id or ""):
            raise FlowError("SCHEMA_VALIDATION", "ID da entidade inválido.")
        if new_status not in ("ACTIVE", "PAUSED"):
            raise FlowError("SCHEMA_VALIDATION", "Status deve ser ACTIVE ou PAUSED.")
        return self._graph_request("POST", entity_id, form_data={"status": new_status}, timeout=25)

    def _resolve_media_file(self, body: dict[str, Any]) -> tuple[Path, str]:
        media_kind = ""
        path: Path | None = None
        if body.get("artifact_id"):
            art = self.repository.artifact(str(body["artifact_id"]))
            path = Path(art["path"]).resolve(strict=True)
            media_kind = "video"
        elif body.get("asset_id"):
            _, path = self.assets.verified_path(str(body["asset_id"]))
            media_kind = "image"
        elif body.get("media_path"):
            candidate = Path(str(body["media_path"])).resolve()
            if not candidate.is_file():
                raise FlowError("ARTIFACT_NOT_FOUND", "Arquivo de mídia não encontrado no disco.", status=404)
            suffix = candidate.suffix.lower()
            if suffix in (".mp4", ".mov", ".webm"):
                media_kind = "video"
            elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
                media_kind = "image"
            else:
                raise FlowError("INVALID_IMAGE", "Use vídeo (.mp4/.mov/.webm) ou imagem (.png/.jpg/.webp).")
            path = candidate
        if path is None or not path.is_file():
            raise FlowError("SCHEMA_VALIDATION", "Selecione um vídeo ou imagem válido para o anúncio.", pointer="/media_path")
        return path, media_kind

    def upload_ad(self, body: dict[str, Any]) -> dict[str, Any]:
        dry_run = bool(body.get("dry_run"))
        if not dry_run:
            if self.no_spend:
                raise FlowError("NO_SPEND_ACTIVE", "A trava sem gastos está ativa; use Simulação (Dry-run).", status=403)
            if body.get("confirmed") is not True:
                raise FlowError("SPEND_NOT_CONFIRMED", "Confirme antes de subir o anúncio (criado como PAUSED).", status=403)

        acc = self._normalize_account_id(body.get("account_id"), self._defaults()["account_id"])
        media_path, media_kind = self._resolve_media_file(body)
        page_id = str(body.get("page_id") or self._defaults()["page_id"] or "").strip()
        if not page_id:
            raise FlowError("SCHEMA_VALIDATION", "Informe ou selecione a Página do Facebook (page_id).", pointer="/page_id")
        link_url = str(body.get("link_url") or self._defaults()["default_url"] or "").strip()
        if not link_url.startswith(("http://", "https://")):
            raise FlowError("SCHEMA_VALIDATION", "Informe uma URL de destino válida (https://...).", pointer="/link_url")

        ad_name = str(body.get("ad_name") or media_path.stem).strip()[:120]
        primary_text = str(body.get("primary_text") or "").strip()
        headline = str(body.get("headline") or "").strip()
        cta_type = str(body.get("cta_type") or "LEARN_MORE").strip()
        if not primary_text:
            raise FlowError("SCHEMA_VALIDATION", "Preencha o texto principal do anúncio.", pointer="/primary_text")

        campaign_id = str(body.get("campaign_id") or "__new__").strip()
        adset_id = str(body.get("adset_id") or "__new__").strip()
        new_campaign = body.get("new_campaign") or {}
        new_adset = body.get("new_adset") or {}

        if campaign_id == "__new__":
            cname = str(new_campaign.get("name") or f"Campanha {ad_name}").strip()
            objective = str(new_campaign.get("objective") or "OUTCOME_SALES").strip()
            cbudget_brl = float(new_campaign.get("daily_budget_brl") or 50)
            if cbudget_brl <= 0:
                raise FlowError("SCHEMA_VALIDATION", "Orçamento diário da campanha deve ser maior que zero.")
            camp_form = {
                "name": cname,
                "objective": objective,
                "daily_budget": str(int(round(cbudget_brl * 100))),
                "status": "PAUSED",
                "special_ad_categories": "[]",
            }
        else:
            if not ENTITY_ID_RE.fullmatch(campaign_id):
                raise FlowError("SCHEMA_VALIDATION", "campaign_id inválido.")
            camp_form = None

        if adset_id == "__new__":
            sname = str(new_adset.get("name") or f"Conjunto {ad_name}").strip()
            billing = str(new_adset.get("billing_event") or "IMPRESSIONS").strip()
            goal = str(new_adset.get("optimization_goal") or "LINK_CLICKS").strip()
            country = str(new_adset.get("country") or "BR").strip().upper()[:2]
            targeting = json.dumps({
                "geo_locations": {"countries": [country]},
                "age_min": int(new_adset.get("age_min") or 18),
                "age_max": int(new_adset.get("age_max") or 65),
            })
            adset_form = {
                "campaign_id": "{CAMPAIGN_ID}" if campaign_id == "__new__" else campaign_id,
                "name": sname,
                "billing_event": billing,
                "optimization_goal": goal,
                "targeting": targeting,
                "status": "PAUSED",
            }
            if new_adset.get("daily_budget_brl"):
                adset_form["daily_budget"] = str(int(round(float(new_adset["daily_budget_brl"]) * 100)))
        else:
            if not ENTITY_ID_RE.fullmatch(adset_id):
                raise FlowError("SCHEMA_VALIDATION", "adset_id inválido.")
            adset_form = None

        if dry_run:
            self._graph_request("GET", acc, params={"fields": "id,name,account_status"}, timeout=15)
            return {
                "dry_run": True,
                "account_id": acc,
                "media_file": media_path.name,
                "media_kind": media_kind,
                "desired_status": "PAUSED",
                "campaign_plan": camp_form or {"existing_campaign_id": campaign_id},
                "adset_plan": adset_form or {"existing_adset_id": adset_id},
                "creative_plan": {
                    "page_id": page_id,
                    "link_url": link_url,
                    "headline": headline,
                    "cta_type": cta_type,
                },
            }

        if camp_form:
            camp_created = self._graph_request("POST", f"{acc}/campaigns", form_data=camp_form, timeout=25)
            campaign_id = str(camp_created.get("id") or "")
            if not campaign_id:
                raise FlowError("META_API_ERROR", "Meta não retornou ID da campanha criada.", provider_message=json.dumps(camp_created))

        if adset_form:
            resolved_adset_form = {k: (campaign_id if v == "{CAMPAIGN_ID}" else v) for k, v in adset_form.items()}
            adset_created = self._graph_request("POST", f"{acc}/adsets", form_data=resolved_adset_form, timeout=25)
            adset_id = str(adset_created.get("id") or "")
            if not adset_id:
                raise FlowError("META_API_ERROR", "Meta não retornou ID do conjunto criado.", provider_message=json.dumps(adset_created))

        if media_kind == "video":
            vid_res = self._graph_request(
                "POST",
                f"{acc}/advideos",
                form_data={"title": ad_name},
                file_upload=("source", media_path),
                timeout=300,
            )
            video_id = str(vid_res.get("id") or "")
            if not video_id:
                raise FlowError("META_API_ERROR", "Meta não retornou video_id no upload.", provider_message=json.dumps(vid_res))
            story_spec: dict[str, Any] = {
                "page_id": page_id,
                "video_data": {
                    "video_id": video_id,
                    "message": primary_text,
                    "title": headline or ad_name,
                    "call_to_action": {"type": cta_type, "value": {"link": link_url}},
                },
            }
            media_ref_id = video_id
        else:
            img_res = self._graph_request(
                "POST",
                f"{acc}/adimages",
                file_upload=("filename", media_path),
                timeout=120,
            )
            images_dict = img_res.get("images") or {}
            first_img = next(iter(images_dict.values()), {}) if isinstance(images_dict, dict) else {}
            image_hash = str(first_img.get("hash") or "")
            if not image_hash:
                raise FlowError("META_API_ERROR", "Meta não retornou hash da imagem enviada.", provider_message=json.dumps(img_res))
            story_spec = {
                "page_id": page_id,
                "link_data": {
                    "link": link_url,
                    "message": primary_text,
                    "name": headline or ad_name,
                    "image_hash": image_hash,
                    "call_to_action": {"type": cta_type, "value": {"link": link_url}},
                },
            }
            media_ref_id = image_hash

        if body.get("instagram_actor_id"):
            story_spec["instagram_user_id"] = str(body["instagram_actor_id"]).strip()

        creative_res = self._graph_request(
            "POST",
            f"{acc}/adcreatives",
            form_data={
                "name": f"Criativo {ad_name}",
                "object_story_spec": json.dumps(story_spec, ensure_ascii=False),
            },
            timeout=30,
        )
        creative_id = str(creative_res.get("id") or "")
        if not creative_id:
            raise FlowError("META_API_ERROR", "Meta não retornou ID do adcreative.", provider_message=json.dumps(creative_res))

        ad_fields: dict[str, str] = {
            "name": ad_name,
            "adset_id": adset_id,
            "creative": json.dumps({"creative_id": creative_id}),
            "status": "PAUSED",
        }
        pixel_id = str(body.get("pixel_id") or self._defaults()["pixel_id"] or "").strip()
        if pixel_id:
            ad_fields["tracking_specs"] = json.dumps([{"action.type": ["offsite_conversion"], "fb_pixel": [pixel_id]}])

        ad_res = self._graph_request("POST", f"{acc}/ads", form_data=ad_fields, timeout=30)
        ad_id = str(ad_res.get("id") or "")
        if not ad_id:
            raise FlowError("META_API_ERROR", "Meta não retornou ID do anúncio criado.", provider_message=json.dumps(ad_res))

        return {
            "dry_run": False,
            "account_id": acc,
            "campaign_id": campaign_id,
            "adset_id": adset_id,
            "media_id": media_ref_id,
            "creative_id": creative_id,
            "ad_id": ad_id,
            "status": "PAUSED",
        }
