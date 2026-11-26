"""Loopback-only web UI, Python standard library. Run INICIAR.bat on Windows."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from engine import AppError, Engine, REPOSITORY
from dialogs import choose
from backend.contracts import strict_loads
from backend.errors import FlowError
from backend.process_lock import ProcessLock
from backend.security import valid_loopback_host, valid_origin
from backend.service import FlowLocalService


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, engine):
        super().__init__(address, Handler)
        self.engine = engine
        self.token = secrets.token_urlsafe(32)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "FlowLocal/2.0"

    def log_message(self, *args):
        pass  # URLs may contain a local access token. Never record them.

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: http: blob: data:; media-src 'self' https: blob:; frame-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def host_ok(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def send_video(self, file):
        size = file.stat().st_size
        start, end, status = 0, size - 1, 200
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if not match or not any(match.groups()):
                self.send(416, {"error": "Intervalo inválido."}, extra={"Content-Range": f"bytes */{size}"})
                return
            left, right = match.groups()
            if left:
                start = int(left)
                end = min(int(right), end) if right else end
            else:
                start = max(0, size - int(right))
            if start >= size or end < start:
                self.send(416, {"error": "Intervalo inválido."}, extra={"Content-Range": f"bytes */{size}"})
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(end-start+1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with file.open("rb") as stream:
                stream.seek(start)
                remaining = end-start+1
                while remaining > 0:
                    chunk = stream.read(min(1024*1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self, media=False):
        origin = self.headers.get("Origin")
        if not self.host_ok() or (origin and origin != self.server.origin):
            return False
        token = self.headers.get("X-Flow-Token", "")
        # Media elements cannot send custom headers. A scoped HttpOnly cookie
        # is minted only after the token-authenticated state request.
        if media and not token:
            cookies = self.headers.get("Cookie", "").split(";")
            token = next((c.strip().split("=", 1)[1] for c in cookies if c.strip().startswith("flowlocal=")), "")
        return secrets.compare_digest(token, self.server.token)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if not self.host_ok():
            self.send(403, {"error": "Host não permitido."})
            return
        assets = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/modelo-lote.json": "modelo-lote.json"}
        if path in assets:
            file = self.server.engine.root / "web" / assets[path]
            mime = {"/": "text/html", "/app.js": "text/javascript", "/style.css": "text/css", "/modelo-lote.json": "application/json"}[path]
            self.send(200, file.read_bytes(), mime + "; charset=utf-8", {"Content-Disposition": 'attachment; filename="modelo-lote.json"'} if path.endswith(".json") else None)
        elif path == "/api/state":
            if not self.authorized():
                self.send(403, {"error": "Acesso local expirado. Feche o aplicativo e abra INICIAR.bat novamente."})
                return
            self.send(200, self.server.engine.snapshot(), extra={"Set-Cookie": f"flowlocal={self.server.token}; HttpOnly; SameSite=Strict; Path=/media/"})
        elif path.startswith("/media/"):
            if not self.authorized(media=True):
                self.send(403, {"error": "Acesso negado."})
                return
            try:
                _, _, batch_id, name = path.split("/")
                if batch_id == "image":
                    file = self.server.engine.staged_image_path(name)
                    self.send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "image/png")
                    return
                file = self.server.engine.export_path(batch_id, name)
                if file.suffix.lower() == ".mp4":
                    self.send_video(file)
                    return
                data = file.read_bytes()
                content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                headers = {}
                if file.suffix.lower() == ".mp4":
                    content_type = "video/mp4"
                else:
                    content_type = "text/plain; charset=utf-8" if file.suffix == ".log" else content_type
                    headers["Content-Disposition"] = f'attachment; filename="{file.name}"'
                self.send(200, data, content_type, headers)
            except (AppError, ValueError, OSError) as exc:
                self.send(404, {"error": str(exc)})
        else:
            self.send(404, {"error": "Não encontrado."})

    def do_POST(self):
        if not self.authorized():
            self.send(403, {"error": "Acesso negado."})
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise AppError("Use JSON.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 29 * 1024 * 1024:
                self.send(413, {"error": "Requisição muito grande ou vazia."})
                return
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise AppError("Requisição inválida.")
            path = urlsplit(self.path).path
            engine = self.server.engine
            result = {"ok": True}
            if path == "/api/stage":
                result = engine.stage(body.get("name"), body.get("content"))
            elif path == "/api/plan":
                result = engine.plan(body.get("items"), body.get("model"), body.get("cap"), body.get("aspect", "9:16"), body.get("duration", 6), body.get("resolution"), body.get("output_dir"))
            elif path == "/api/import-manifest":
                # A manifest uploaded by a browser has no trusted parent directory.
                documents = [{"name": d.get("name", "JSON"), "document": d.get("document")} for d in body.get("documents", [])]
                result = engine.import_documents(documents, body.get("assets"), body.get("images_dir"))
            elif path == "/api/import-native":
                engine.idle()
                paths = choose("json", str(engine.root))
                documents = []
                for selected in paths:
                    file = Path(selected).resolve(strict=True)
                    if file.suffix.lower() != ".json" or file.stat().st_size > 4 * 1024 * 1024:
                        raise AppError("Use arquivos JSON de até 4 MiB.")
                    documents.append({"name": file.name, "document": json.loads(file.read_text(encoding="utf-8-sig")), "manifest_dir": str(file.parent)})
                result = engine.import_documents(documents, images_dir=body.get("images_dir")) if documents else {"cancelled": True}
            elif path == "/api/choose-folder":
                engine.idle()
                paths = choose("folder", body.get("initial", str(engine.output)))
                result = {"path": paths[0] if paths else None}
            elif path == "/api/preflight":
                engine.launch("Verificando configuração sem gastar créditos", lambda: engine.preflight(body))
            elif path in ("/api/test", "/api/batch"):
                engine.begin("test" if path.endswith("test") else "batch", body.get("batch_id"), body.get("confirmed"), body.get("risk"), body.get("reviewed"))
            elif path == "/api/login":
                engine.launch("Login no Chrome", engine.login)
            elif path == "/api/credits":
                engine.launch("Consultando saldo", lambda: (engine.check_credits(), engine.event("Saldo atualizado.")))
            elif path == "/api/install":
                if body.get("repository", "").rstrip("/").removesuffix(".git") != REPOSITORY:
                    raise AppError("Este pacote usa exclusivamente https://github.com/ffroliva/gflow-cli. Não executa código de outros repositórios.")
                def install():
                    message = engine.cli.install()
                    engine.event(message)
                engine.launch("Instalando/atualizando CLI", install)
            elif path == "/api/stop":
                engine.request_stop()
            elif path == "/api/open-output":
                current = engine.state.get("batch")
                folder = engine.output_folder(body.get("path") or (current.get("output_dir") if current else None) or str(engine.output))
                if os.name == "nt":
                    os.startfile(str(folder))
                else:
                    result = {"path": str(folder)}
            elif path == "/api/shutdown":
                with engine.lock:
                    engine.idle()
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send(404, {"error": "Ação não encontrada."})
                return
            self.send(200, result)
        except (AppError, ValueError, TypeError, KeyError, AttributeError) as exc:
            self.send(400, {"error": str(exc)})
        except Exception:
            self.send(500, {"error": "Falha local inesperada. Verifique a pasta, o espaço em disco e os relatórios; gerações não são repetidas automaticamente."})


class V3Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, service: FlowLocalService):
        super().__init__(address, V3Handler)
        self.service = service
        self.token = secrets.token_urlsafe(32)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class V3Handler(BaseHTTPRequestHandler):
    server_version = "FlowLocal/3.0"

    def log_message(self, *_):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def send_json(self, status: int, body: dict, *, cookie: bool = False):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        extra = {"Set-Cookie": f"flowlocal={self.server.token}; HttpOnly; SameSite=Strict; Path=/media/"} if cookie else {}
        self.send_bytes(status, data, "application/json; charset=utf-8", extra)

    def send_bytes(self, status: int, data: bytes, content_type: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: http: blob: data:; media-src 'self' https: blob:; frame-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def authorized(self, *, media: bool = False) -> bool:
        if not valid_loopback_host(self.headers.get("Host"), self.server.server_port):
            return False
        if not valid_origin(self.headers.get("Origin"), self.server.origin):
            return False
        token = self.headers.get("X-Flow-Token", "")
        if media and not token:
            for value in self.headers.get("Cookie", "").split(";"):
                if value.strip().startswith("flowlocal="):
                    token = value.strip().split("=", 1)[1]
                    break
        return secrets.compare_digest(token, self.server.token)

    def require_auth(self, *, media: bool = False):
        if not self.authorized(media=media):
            raise FlowError("LOCAL_SESSION_INVALID", "Acesso local expirado ou origem não permitida.", status=403)

    def body(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise FlowError("INVALID_JSON", "Use application/json.")
        length = int(self.headers.get("Content-Length", "0"))
        limit = 29 * 1024 * 1024 if urlsplit(self.path).path in {"/api/stage", "/api/library/import", "/api/editor/upload-audio"} else 5 * 1024 * 1024
        if not 1 <= length <= limit:
            raise FlowError("PAYLOAD_LIMIT", "Requisição muito grande ou vazia.", status=413)
        value = strict_loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise FlowError("INVALID_JSON", "A requisição precisa ser um objeto JSON.")
        return value

    def static(self, path: str) -> bool:
        mapping = {"/": "index.html", "/app.js": "app.js", "/state.js": "state.js", "/style.css": "style.css", "/overrides.css": "overrides.css", "/modelo-lote.json": "modelo-lote.json"}
        relative = mapping.get(path)
        if relative is None and (path.startswith("/views/") or path.startswith("/components/")) and path.endswith(".js"):
            relative = path.lstrip("/")
        if relative is None:
            return False
        file = (self.server.service.root / "web" / relative).resolve()
        web = (self.server.service.root / "web").resolve()
        if not file.is_relative_to(web) or not file.is_file():
            return False
        mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        extra = {"Content-Disposition": 'attachment; filename="modelo-lote.json"'} if file.name.endswith(".json") else None
        self.send_bytes(200, file.read_bytes(), mime + ("; charset=utf-8" if mime.startswith(("text/", "application/javascript", "application/json")) else ""), extra)
        return True

    def send_file(self, file: Path, *, attachment: bool = False):
        file = Path(file)
        size = file.stat().st_size
        start, end, status = 0, size - 1, 200
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if not match or not any(match.groups()):
                raise FlowError("INVALID_ARTIFACT", "Intervalo inválido.", status=416)
            left, right = match.groups()
            if left:
                start = int(left)
                end = min(int(right), end) if right else end
            else:
                start = max(0, size - int(right))
            if start >= size or end < start:
                raise FlowError("INVALID_ARTIFACT", "Intervalo inválido.", status=416)
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
        self.end_headers()
        try:
            with file.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def handle_error(self, exc: Exception):
        if isinstance(exc, FlowError):
            self.send_json(exc.status, exc.payload())
        else:
            correlation = str(secrets.token_hex(16))
            self.send_json(500, {"error": {"code": "LOCAL_FAILURE", "message": "Falha local inesperada. Nenhum efeito é repetido automaticamente.", "pointer": None, "retry_safe": False, "execution_id": None, "correlation_id": correlation, "provider_message": None}})

    def do_GET(self):
        try:
            split = urlsplit(self.path)
            path, query = unquote(split.path), parse_qs(split.query)
            if self.static(path):
                return
            if not valid_loopback_host(self.headers.get("Host"), self.server.server_port):
                raise FlowError("LOCAL_SESSION_INVALID", "Host não permitido.", status=403)
            service = self.server.service
            if path == "/api/health":
                self.send_json(200, service.health())
                return
            if path.startswith("/media/"):
                self.require_auth(media=True)
                if path.startswith("/media/assets/"):
                    asset, file = service.assets.verified_path(path.rsplit("/", 1)[1])
                    self.send_file(file)
                elif path.startswith("/media/artifacts/"):
                    artifact = service.repository.artifact(path.rsplit("/", 1)[1])
                    if artifact["validation_state"] != "VALID":
                        raise FlowError("ARTIFACT_NOT_FOUND", "Vídeo não validado.", status=404)
                    self.send_file(Path(artifact["path"]))
                elif path.startswith("/media/exports/"):
                    self.send_file(service.export_path(path.rsplit("/", 1)[1]), attachment=True)
                elif path.startswith("/media/editor/"):
                    rel_parts = [part for part in path[len("/media/editor/"):].split("/") if part]
                    if len(rel_parts) < 2:
                        raise FlowError("ARTIFACT_NOT_FOUND", "Mídia do editor inválida.", status=404)
                    self.send_file(service.editor.verified_media_path(rel_parts[0], *rel_parts[1:]))
                elif path.startswith("/media/spy/"):
                    rel_parts = [part for part in path[len("/media/spy/"):].split("/") if part]
                    if len(rel_parts) != 2:
                        raise FlowError("ARTIFACT_NOT_FOUND", "Mídia do Spy inválida.", status=404)
                    self.send_file(service.spy.verified_media_path(rel_parts[0], rel_parts[1]))
                elif path == "/media/local":
                    raw_p = (query.get("path") or [""])[0]
                    cand = Path(raw_p).resolve() if raw_p else Path("")
                    allowed_roots = (service.root.resolve(), service.data.resolve(), service.output.resolve())
                    allowed_ext = {".mp4", ".mov", ".webm", ".png", ".jpg", ".jpeg", ".webp"}
                    if not raw_p or not cand.is_file() or cand.suffix.lower() not in allowed_ext or not any(cand.is_relative_to(r) for r in allowed_roots):
                        raise FlowError("ARTIFACT_NOT_FOUND", "Mídia local não encontrada.", status=404)
                    self.send_file(cand)
                else:
                    parts = path.split("/")
                    if len(parts) != 4:
                        raise FlowError("ARTIFACT_NOT_FOUND", "Mídia inválida.", status=404)
                    self.send_file(service.legacy_export_path(parts[2], parts[3]), attachment=not parts[3].endswith(".mp4"))
                return
            self.require_auth()
            if path == "/api/state":
                self.send_json(200, service.snapshot(), cookie=True)
            elif path == "/api/batches":
                self.send_json(200, service.repository.list_batches(cursor=(query.get("cursor") or [None])[0], limit=int((query.get("limit") or [50])[0])))
            elif re.fullmatch(r"/api/batches/[^/]+/items", path):
                batch_id = path.split("/")[3]
                self.send_json(200, service.repository.list_items(batch_id, cursor=(query.get("cursor") or [None])[0], limit=int((query.get("limit") or [50])[0]), status=(query.get("status") or [None])[0]))
            elif path == "/api/events":
                self.send_json(200, service.repository.list_events((query.get("batch_id") or [""])[0], int((query.get("after_event_id") or [0])[0]), int((query.get("limit") or [200])[0])))
            elif path == "/api/library":
                self.send_json(200, service.images.list())
            elif path == "/api/meta-ads/status":
                self.send_json(200, service.meta_ads.status())
            elif path == "/api/meta-ads/accounts":
                self.send_json(200, service.meta_ads.list_accounts())
            elif path == "/api/meta-ads/campaigns":
                self.send_json(200, service.meta_ads.list_campaigns((query.get("account_id") or [None])[0], date_preset=(query.get("date_preset") or ["last_7d"])[0], status=(query.get("status") or [None])[0]))
            elif path == "/api/meta-ads/adsets":
                self.send_json(200, service.meta_ads.list_adsets((query.get("account_id") or [None])[0], campaign_id=(query.get("campaign_id") or [None])[0], date_preset=(query.get("date_preset") or ["last_7d"])[0]))
            elif path == "/api/meta-ads/ads":
                self.send_json(200, service.meta_ads.list_ads((query.get("account_id") or [None])[0], adset_id=(query.get("adset_id") or [None])[0], campaign_id=(query.get("campaign_id") or [None])[0], date_preset=(query.get("date_preset") or ["last_7d"])[0]))
            elif path == "/api/meta-ads/resources":
                self.send_json(200, service.meta_ads.list_resources((query.get("account_id") or [None])[0]))
            elif path == "/api/meta-ads/summary":
                self.send_json(200, service.meta_ads.account_summary((query.get("account_id") or [None])[0], date_preset=(query.get("date_preset") or ["last_7d"])[0]))
            elif path == "/api/editor/capabilities":
                self.send_json(200, service.editor.capabilities())
            elif path == "/api/editor/videos":
                self.send_json(200, service.editor.list_videos((query.get("folder") or [None])[0]))
            elif path == "/api/editor/audios":
                self.send_json(200, service.editor.list_audios())
            elif path == "/api/editor/projects":
                self.send_json(200, service.editor.list_projects())
            elif path == "/api/spy/capabilities":
                self.send_json(200, service.spy.capabilities())
            elif path == "/api/spy/downloads":
                self.send_json(200, service.spy.list_downloads())
            elif path == "/api/spy/trending":
                self.send_json(
                    200,
                    service.spy.trending_products(
                        (query.get("country") or ["BR"])[0],
                        keywords=(query.get("keywords") or [None])[0],
                        track=(query.get("track") or ["all"])[0],
                        categories=(query.get("categories") or [None])[0],
                    ),
                )
            elif path == "/api/spy/gpt-config":
                self.send_json(200, service.spy.get_gpt_config())
            elif path == "/api/calendar":
                self.send_json(
                    200,
                    service.calendar.summary(
                        (query.get("month") or [None])[0],
                        since=(query.get("since") or [None])[0],
                        until=(query.get("until") or [None])[0],
                        range_preset=(query.get("range_preset") or [None])[0],
                        account_id=(query.get("account_id") or [None])[0],
                    ),
                )
            elif path == "/api/flows":
                self.send_json(200, {"items": service.flows.list_runs()})
            elif path == "/api/flows/template":
                tpl = (service.root / "exemplos" / "exemplo_fluxo_produto.json").read_bytes()
                self.send_json(200, strict_loads(tpl))
            elif re.fullmatch(r"/api/flows/[^/]+", path):
                self.send_json(200, service.flows.get_run(path.rsplit("/", 1)[1]))
            elif re.fullmatch(r"/api/executions/[^/]+", path):
                self.send_json(200, service.repository.execution(path.rsplit("/", 1)[1]))
            elif re.fullmatch(r"/api/exports/[^/]+", path):
                self.send_json(200, service.export_status(path.rsplit("/", 1)[1]))
            else:
                raise FlowError("BATCH_NOT_FOUND", "Rota não encontrada.", status=404)
        except Exception as exc:
            self.handle_error(exc)

    def dispatch_mutation(self, method: str):
        try:
            self.require_auth()
            path, body, service = urlsplit(self.path).path, self.body(), self.server.service
            result, status = None, 200
            if method == "POST" and path == "/api/stage":
                result = service.assets.stage_base64(body.get("name"), body.get("content"))
            elif method == "POST" and path == "/api/batches":
                result = service.create_batch(body)
            elif method == "POST" and re.fullmatch(r"/api/batches/[^/]+/items", path):
                result = service.add_items(path.split("/")[3], body)
            elif method == "PATCH" and re.fullmatch(r"/api/batches/[^/]+", path):
                result = service.repository.patch_batch(path.split("/")[3], body.pop("expected_revision"), body)
            elif method == "PATCH" and re.fullmatch(r"/api/batches/[^/]+/items/[^/]+", path):
                parts = path.split("/")
                result = service.repository.patch_item(parts[3], parts[5], body.pop("expected_revision"), body)
            elif method == "DELETE" and re.fullmatch(r"/api/batches/[^/]+/items/[^/]+", path):
                parts = path.split("/")
                result = service.repository.delete_item(parts[3], parts[5], body["expected_revision"], body["idempotency_key"])
            elif method == "POST" and path == "/api/import-manifest":
                documents = [{"name": x.get("name", "JSON"), "document": x.get("document")} for x in body.get("documents", [])]
                result = service.imports.preview(documents, body.get("assets"), body.get("images_dir"), native=False)
            elif method == "POST" and path == "/api/import-native":
                paths = choose("json", str(service.root))
                documents = []
                for selected in paths:
                    file = Path(selected).resolve(strict=True)
                    documents.append({"name": file.name, "document": strict_loads(file.read_bytes()), "manifest_dir": str(file.parent)})
                result = service.imports.preview(documents, images_dir=body.get("images_dir"), native=True) if documents else {"cancelled": True}
            elif method == "POST" and re.fullmatch(r"/api/imports/[^/]+/commit", path):
                result = service.imports.commit(path.split("/")[3], body)
            elif method == "POST" and path == "/api/choose-folder":
                paths = choose("folder", body.get("initial", str(service.output)))
                result = {"path": paths[0] if paths else None}
            elif method == "POST" and path == "/api/plan":
                result = service.planner.plan(body["batch_id"], body["expected_revision"])
            elif method == "POST" and path == "/api/preflight":
                result = service.preflight(body["batch_id"], body.get("plan_hash"))
            elif method == "POST" and path == "/api/login":
                result = service.login()
            elif method == "POST" and path == "/api/logout":
                result = service.logout()
            elif method == "POST" and path == "/api/credits":
                result = service.refresh_account()
            elif method == "POST" and path == "/api/gpt-login":
                result = service.login_gpt()
            elif method == "POST" and path == "/api/gpt-logout":
                result = service.logout_gpt()
            elif method == "POST" and path == "/api/gpt-verify":
                result = service.verify_gpt()
            elif method == "POST" and path == "/api/meta-ads/login":
                result = service.meta_ads.login(body.get("token"), account_id=body.get("account_id"), app_id=body.get("app_id"), app_secret=body.get("app_secret"))
            elif method == "POST" and path == "/api/meta-ads/logout":
                result = service.meta_ads.logout()
            elif method == "POST" and path == "/api/meta-ads/defaults":
                result = service.meta_ads.save_defaults(body)
            elif method == "POST" and path == "/api/meta-ads/pixels":
                result = service.meta_ads.create_pixel(body.get("account_id"), body.get("name"))
            elif method == "POST" and path == "/api/meta-ads/status":
                result = service.meta_ads.update_status(body.get("entity_type"), body.get("entity_id"), body.get("status"), confirmed=bool(body.get("confirmed")))
            elif method == "POST" and path == "/api/meta-ads/upload":
                result = service.meta_ads.upload_ad(body)
                if not result.get("dry_run") and result.get("ad_id"):
                    from datetime import datetime
                    try:
                        service.calendar.create_item({
                            "scheduled_date": datetime.now().strftime("%Y-%m-%d"),
                            "kind": "ad",
                            "title": body.get("ad_name") or "Anúncio Meta (PAUSED)",
                            "status": "PAUSED",
                            "media_path": body.get("media_path") or "",
                            "meta_account_id": result.get("account_id") or "",
                            "meta_campaign_id": result.get("campaign_id") or "",
                            "meta_ad_id": result.get("ad_id") or "",
                            "platform": "Meta Ads",
                            "notes": body.get("primary_text") or "",
                        })
                    except Exception:
                        pass
            elif method == "POST" and path == "/api/calendar/items":
                result = service.calendar.create_item(body)
            elif method == "PATCH" and re.fullmatch(r"/api/calendar/items/[^/]+", path):
                result = service.calendar.patch_item(path.rsplit("/", 1)[1], body)
            elif method == "DELETE" and re.fullmatch(r"/api/calendar/items/[^/]+", path):
                result = service.calendar.delete_item(path.rsplit("/", 1)[1])
            elif method == "POST" and path == "/api/editor/analyze":
                result = service.editor.analyze_video(body.get("path"), run_whisper=bool(body.get("run_whisper", True)), run_scenes=bool(body.get("run_scenes", True)), extract_frames=bool(body.get("extract_frames", True)), force=bool(body.get("force", False)))
            elif method == "POST" and path == "/api/editor/render":
                result = service.editor.render_video(body)
            elif method == "POST" and path == "/api/editor/render-timeline":
                result = service.editor.render_timeline(body)
            elif method == "POST" and path == "/api/editor/upload-audio":
                result = service.editor.upload_audio(body.get("name"), body.get("content"))
            elif method == "POST" and path == "/api/editor/projects":
                result = service.editor.save_project(body)
            elif method == "POST" and path == "/api/editor/open-folder":
                folder = Path(str(body.get("folder") or service.output)).resolve()
                if not folder.is_dir():
                    raise FlowError("SCHEMA_VALIDATION", "Pasta não encontrada.")
                if os.name == "nt":
                    os.startfile(str(folder))
                    result = {"opened": True, "path": str(folder)}
                else:
                    result = {"opened": False, "path": str(folder)}
            elif method == "POST" and path == "/api/spy/search":
                result = service.spy.search(body)
            elif method == "POST" and path == "/api/spy/download":
                result = service.spy.download_media(body)
            elif method == "POST" and path == "/api/spy/download-transcribe":
                result = service.spy.download_and_transcribe(body)
            elif method == "POST" and path == "/api/spy/capture":
                result = service.spy.capture_page(body.get("url"))
            elif method == "POST" and path == "/api/spy/save-page":
                result = service.spy.save_edited_page(body.get("filename"), body.get("html"))
            elif method == "POST" and path == "/api/spy/resolve-stream":
                result = service.spy.resolve_stream(body.get("url"))
            elif method == "POST" and path == "/api/spy/gpt-config":
                result = service.spy.save_gpt_config(body)
            elif method == "POST" and path == "/api/spy/offer-prompt":
                result = service.spy.build_offer_prompt(body)
            elif method == "POST" and path == "/api/spy/offer-to-flow":
                result = service.spy.offer_to_flow(body)
            elif method == "POST" and path == "/api/test":
                result, status = service.start(body, "TEST"), 202
            elif method == "POST" and path == "/api/batch":
                result, status = service.start(body, "REMAINDER"), 202
            elif method == "POST" and path == "/api/queue-direct":
                result, status = service.start_direct_queue(body), 202
            elif method == "POST" and path == "/api/image-generate":
                result = service.generate_image(body)
            elif method == "POST" and path == "/api/gpt-ask":
                prompt = body.get("prompt")
                if not isinstance(prompt, str) or not prompt.strip():
                    raise FlowError("SCHEMA_VALIDATION", "Informe um pedido para o ChatGPT.")
                result = service.gpt_chat.ask_json(prompt.strip(), new_chat=bool(body.get("new_chat", True)), conversation=body.get("conversation"))
            elif method == "POST" and path == "/api/flows/preview":
                result = service.flows.preview(body.get("spec"))
            elif method == "POST" and path == "/api/flows/run":
                result, status = service.flows.start(body), 202
            elif method == "POST" and path == "/api/library/import":
                result = service.images.import_base64(body.get("name"), body.get("content"))
            elif method == "POST" and path == "/api/library/open":
                if os.name == "nt":
                    os.startfile(str(service.images.directory))
                    result = {"opened": True, "path": str(service.images.directory)}
                else:
                    result = {"opened": False, "path": str(service.images.directory)}
            elif method == "POST" and path == "/api/stop":
                result = service.repository.request_pause(body["batch_id"])
            elif method == "POST" and path == "/api/resume":
                result = service.repository.resume(body["batch_id"], body["plan_hash"])
                service.worker.poke()
            elif method == "POST" and path == "/api/reconcile":
                result = service.reconcile(body["execution_id"])
            elif method == "POST" and path == "/api/exports":
                result, status = service.create_export(body), 202
            elif method == "POST" and path == "/api/open-output":
                folder = Path(service.repository.get_batch(body["batch_id"])["output_dir"])
                if os.name == "nt":
                    os.startfile(str(folder))
                    result = {"opened": True, "path": str(folder)}
                else:
                    result = {"opened": False, "path": str(folder)}
            elif method == "POST" and path == "/api/shutdown":
                result = {"ok": True}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                raise FlowError("BATCH_NOT_FOUND", "Ação não encontrada.", status=404)
            self.send_json(status, result or {"ok": True})
        except Exception as exc:
            self.handle_error(exc)

    def do_POST(self):
        self.dispatch_mutation("POST")

    def do_PATCH(self):
        self.dispatch_mutation("PATCH")

    def do_DELETE(self):
        self.dispatch_mutation("DELETE")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-spend", action="store_true", help="Bloqueia toda geração paga; simulação continua disponível.")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    data_dir = Path(args.data_dir).resolve() if args.data_dir else None
    lock = ProcessLock(root, data_dir or (root / "dados"))
    try:
        lock.acquire()
    except FlowError as exc:
        print(exc.message)
        raise SystemExit(1)
    engine = FlowLocalService(root, data_dir=data_dir, no_spend=args.no_spend)
    try:
        server = V3Server(("127.0.0.1", args.port), engine)
    except OSError:
        print("A porta 8765 está ocupada. Feche a instância anterior do Flow Local (botão Encerrar) e tente novamente.")
        raise SystemExit(1)
    url = server.origin + "/#/criar?token=" + server.token
    # Local-only handoff file; never contains Google credentials.
    (engine.data / "abrir.url").write_text("[InternetShortcut]\nURL=" + url + "\n", encoding="utf-8")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    print(f"Flow Local ativo em {server.origin}. Feche pelo botão Encerrar.", flush=True)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.close()
        lock.release()


if __name__ == "__main__":
    main()
