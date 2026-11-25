from __future__ import annotations

import base64
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .assets import MAX_BYTES, image_info
from .errors import FlowError


class ImageGenerationService:
    """ChatGPT Web browser session plus a persistent local image library."""

    def __init__(self, data_dir: Path, assets) -> None:
        self.assets = assets
        self.root = Path(__file__).resolve().parents[1]
        self.data_dir = Path(data_dir).resolve()
        self.directory = self.data_dir / "biblioteca"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.web_dir = self.data_dir / "gpt-web"
        self.profile_dir = self.web_dir / "perfil-chrome"
        self.session_path = self.web_dir / "sessao-verificada.json"
        self.login_log = self.web_dir / "login.log"
        self.helper = self.root / "scripts" / "gpt_web.py"
        self._login_process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _local_programs_dir() -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "Programs"
        return Path.home() / "AppData/Local/Programs"

    def _python_executable(self) -> Path:
        for candidate in (self.root / ".runtime/tools/gflow-cli/Scripts/python.exe", Path(sys.executable)):
            if candidate.is_file():
                return candidate.resolve()
        raise FlowError("IMAGE_GENERATOR_UNAVAILABLE", "Python local não encontrado para executar o image-use.", status=503)

    def _image_use_tools(self) -> tuple[Path, Path, Path]:
        programs = self._local_programs_dir()
        image_use_candidates = [
            programs / "image-use/image-use",
            Path.home() / ".agents/use-family/image-use/image-use",
            Path.home() / ".local/bin/image-use",
        ]
        which_iu = shutil.which("image-use")
        if which_iu:
            image_use_candidates.append(Path(which_iu))
        image_use = next((p.resolve() for p in image_use_candidates if p.is_file() and p.suffix.lower() != ".cmd"), None)
        if not image_use:
            raise FlowError("IMAGE_GENERATOR_UNAVAILABLE", "A CLI image-use não está instalada.", status=503)

        chrome_use_candidates = [programs / "chrome-use/chrome-use.exe"]
        which_cu = shutil.which("chrome-use")
        if which_cu:
            chrome_use_candidates.append(Path(which_cu))
        chrome_use = next((p.resolve() for p in chrome_use_candidates if p.is_file()), None)
        if not chrome_use:
            raise FlowError("IMAGE_GENERATOR_UNAVAILABLE", "A CLI chrome-use não está instalada.", status=503)
        return self._python_executable(), image_use, chrome_use

    @staticmethod
    def _tool_env(image_use: Path, chrome_use: Path) -> dict[str, str]:
        env = os.environ.copy()
        extra_paths = [str(chrome_use.parent), str(image_use.parent)]
        current = env.get("PATH", "")
        env["PATH"] = os.pathsep.join(extra_paths + ([current] if current else []))
        env["PYTHONUTF8"] = "1"
        env["IMAGE_USE_BACKEND"] = "web"
        env["IMAGE_USE_NO_UPDATE_CHECK"] = "1"
        return env

    @staticmethod
    def _daemon_status(chrome_use: Path) -> dict:
        try:
            completed = subprocess.run(
                [str(chrome_use), "daemon", "status", "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if completed.returncode != 0 or not completed.stdout.strip():
                return {}
            parsed = json.loads(completed.stdout.strip().splitlines()[-1])
            data = parsed.get("data")
            return data if isinstance(data, dict) else {}
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            return {}

    @classmethod
    def _relay_connected(cls, chrome_use: Path) -> bool:
        return bool(cls._daemon_status(chrome_use).get("relay"))

    @staticmethod
    def _stop_chatgpt_session(chrome_use: Path) -> None:
        try:
            subprocess.run(
                [str(chrome_use), "session", "stop", "chatgpt-web", "--force", "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    @classmethod
    def _ensure_healthy_chatgpt_session(cls, chrome_use: Path) -> None:
        status = cls._daemon_status(chrome_use)
        sessions = status.get("sessions") if isinstance(status, dict) else None
        if not isinstance(sessions, list):
            return
        has_chatgpt = any(isinstance(s, dict) and s.get("name") == "chatgpt-web" for s in sessions)
        if not has_chatgpt:
            return
        try:
            probe = subprocess.run(
                [str(chrome_use), "tabs", "--session", "chatgpt-web", "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            text = ((probe.stdout or "") + "\n" + (probe.stderr or "")).casefold()
            if probe.returncode != 0 or any(
                marker in text for marker in ("connection_failed", "browser is gone", "carried-tabs", "os error", "closed connection")
            ):
                cls._stop_chatgpt_session(chrome_use)
        except (OSError, subprocess.SubprocessError):
            cls._stop_chatgpt_session(chrome_use)

    def _write_session_marker(self) -> dict:
        value = {
            "version": 1,
            "authenticated": True,
            "source": "chatgpt-web-browser",
            "transport": "chrome-use",
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.session_path.with_suffix(self.session_path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.session_path)
        return value

    def _marker(self) -> dict:
        try:
            marker = json.loads(self.session_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if (marker.get("version") == 1 and marker.get("authenticated") is True
                and marker.get("source") == "chatgpt-web-browser"
                and isinstance(marker.get("verified_at"), str)):
            return marker
        return {}

    def session_status(self) -> dict:
        marker = self._marker()
        process_running = self._login_process is not None and self._login_process.poll() is None
        return {"connected": bool(marker), "credential_available": bool(marker),
                "identity": "ChatGPT Web" if marker else None, "verified_at": marker.get("verified_at"),
                "login_in_progress": process_running, "source": marker.get("source")}

    def verify_session(self) -> dict:
        if not self._marker():
            try:
                _, _, chrome_use = self._image_use_tools()
                if self._relay_connected(chrome_use):
                    self._write_session_marker()
            except FlowError:
                pass
        return self.session_status()

    def start_login(self) -> dict:
        status = self.session_status()
        if status["connected"]:
            return {"started": False, "session": status, "message": "O ChatGPT Web já foi verificado neste programa."}
        _, _, chrome_use = self._image_use_tools()
        if self._relay_connected(chrome_use):
            self._write_session_marker()
            return {"started": False, "session": self.session_status(),
                    "message": "Extensão chrome-use já conectada ao seu Chrome. Deixe o chatgpt.com logado."}
        if status["login_in_progress"]:
            return {"started": False, "session": status, "message": "A configuração da extensão chrome-use já está em andamento."}
        self.session_path.unlink(missing_ok=True)
        self.login_log.parent.mkdir(parents=True, exist_ok=True)
        log = self.login_log.open("a", encoding="utf-8")
        try:
            self._login_process = subprocess.Popen(
                [str(chrome_use), "extension", "connect"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            log.close()
        except OSError as exc:
            log.close()
            raise FlowError("IMAGE_GENERATOR_UNAVAILABLE", "Não foi possível iniciar o chrome-use.", status=503) from exc
        return {"started": True, "session": self.session_status(),
                "message": "Clique em 'Usar no Chrome' na página da extensão que abriu e entre em chatgpt.com no mesmo navegador."}

    def logout(self) -> dict:
        if self._login_process is not None and self._login_process.poll() is None:
            self._login_process.terminate()
        self._login_process = None
        self.session_path.unlink(missing_ok=True)
        if self.profile_dir.is_dir():
            shutil.rmtree(self.profile_dir)
        return self.session_status()

    @staticmethod
    def _is_recoverable_session_failure(detail: str) -> bool:
        lowered = (detail or "").casefold()
        return any(
            term in lowered
            for term in (
                "connection_failed",
                "browser is gone",
                "carried-tabs",
                "os error 5",
                "acesso negado",
                "does not match the browser",
                "closed connection",
                "chrome-use open failed",
                "navigation_incomplete",
                "debugger stopped responding",
            )
        )

    @staticmethod
    def _short_detail(message: str, limit: int = 260) -> str:
        lines = [line.strip(" \t•-") for line in (message or "").splitlines() if line.strip()]
        joined = " | ".join(lines[:3])
        if len(joined) > limit:
            return joined[: limit - 3].rstrip() + "..."
        return joined

    def _helper_failure(self, completed: subprocess.CompletedProcess[str]) -> FlowError:
        detail = (completed.stderr or completed.stdout or "").strip()[-4000:]
        message = detail
        try:
            parsed = json.loads(detail.splitlines()[-1])
            message = str(parsed.get("message") or detail)
        except (ValueError, TypeError, IndexError):
            pass
        lowered = message.casefold()
        summary = self._short_detail(message)
        relay_reported_up = "the relay is connected" in lowered

        if not relay_reported_up and any(
            term in lowered
            for term in (
                "ab-connect",
                "extension not connected",
                "extension isn't connected",
                "relay not connected",
                "relay isn't connected",
                "a extensão chrome-use não",
                "extensão chrome-use desconectada",
            )
        ):
            return FlowError(
                "CHROME_EXTENSION_REQUIRED",
                "A extensão chrome-use não está conectada ao Chrome. Instale/ative a extensão e tente novamente.",
                status=409,
                provider_message=message,
            )
        if any(term in lowered for term in ("chrome_profile_busy", "exitcode=21", "profile in use", "já está em uso")):
            return FlowError(
                "CHROME_PROFILE_BUSY",
                "O perfil do navegador do GPT está em uso por outra janela do Chrome sem depuração. Feche essa janela e tente novamente.",
                status=409,
                provider_message=message,
            )
        if self._is_recoverable_session_failure(message) or any(
            term in lowered for term in ("timed out", "reload the chrome-use extension", "chrome-use")
            if "chrome-use open failed" in lowered or "timed out" in lowered or "reload the chrome-use extension" in lowered
        ):
            return FlowError(
                "CHROME_SESSION_STALE",
                f"A sessão do Chrome via chrome-use falhou ou expirou ({summary}). Tente novamente; se persistir, recarregue a extensão em chrome://extensions e ative 'Allow on all sites' no ícone do chrome-use.",
                status=409,
                provider_message=message,
            )
        if any(
            term in lowered
            for term in (
                "não está autenticada",
                "not authenticated",
                "entrar no gpt",
                "not logged in",
                "isn't signed in",
                "sign in to chatgpt.com",
                "logged-in",
                "login wall",
                "composer never appeared",
                "the relay is connected but",
                "no logged-in chatgpt browser available",
            )
        ):
            if any(term in lowered for term in ("não está autenticada", "not authenticated", "not logged in", "isn't signed in", "login wall")):
                self.session_path.unlink(missing_ok=True)
            return FlowError(
                "GPT_LOGIN_REQUIRED",
                f"O ChatGPT Web no Chrome conectado não abriu o compositor ({summary}). Confirme o login em chatgpt.com no Chrome conectado e tente novamente.",
                status=401,
                provider_message=message,
            )
        if any(term in lowered for term in ("recus", "policy", "violat", "safety", "refus")):
            return FlowError("IMAGE_PROMPT_REFUSED", "O ChatGPT recusou este pedido. Consulte os detalhes para ajustar o prompt.", status=422, provider_message=message)
        if any(term in lowered for term in ("limit", "quota", "muitas solicita", "too many requests", "rate limit")):
            return FlowError("IMAGE_LIMIT_REACHED", "O limite de geração de imagens do ChatGPT foi atingido.", status=429, provider_message=message)
        suffix = f": {summary}" if summary else "."
        return FlowError("IMAGE_GENERATION_FAILED", f"O ChatGPT Web não concluiu a imagem{suffix}", status=502, provider_message=message)

    def _request(
        self,
        prompt: str,
        reference_asset_ids: list[str] | None = None,
        *,
        size: str | None = None,
        ref_roles: list[str] | None = None,
    ) -> bytes:
        if not self.session_status()["connected"]:
            raise FlowError("GPT_LOGIN_REQUIRED", "Entre no GPT pelo navegador antes de criar uma imagem.", status=401)
        python, image_use, chrome_use = self._image_use_tools()
        if not self._relay_connected(chrome_use):
            raise FlowError("CHROME_EXTENSION_REQUIRED", "A extensão chrome-use não está conectada no Chrome. Clique em 'Usar no Chrome' e abra chatgpt.com.", status=409)
        self.web_dir.mkdir(parents=True, exist_ok=True)
        fd, output_name = tempfile.mkstemp(prefix="flowlocal-gpt-", suffix=".png", dir=self.web_dir)
        os.close(fd)
        output = Path(output_name)
        output.unlink(missing_ok=True)
        command = [
            str(python), str(image_use), prompt,
            "--backend", "web", "--profile", "relay", "--no-style",
            "--format", "png", "--quiet", "--no-progress", "-o", str(output),
        ]
        if size and size != "auto":
            command += ["--size", size]
        roles = ref_roles or []
        for idx, asset_id in enumerate(reference_asset_ids or []):
            _, path = self.assets.verified_path(asset_id)
            role = roles[idx] if idx < len(roles) else "subject"
            flag = "--style-ref" if role == "style" else "--composition-ref" if role == "composition" else "-i"
            command += [flag, str(path)]
        try:
            with self._lock:
                self._ensure_healthy_chatgpt_session(chrome_use)
                completed = subprocess.run(
                    command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=480,
                    env=self._tool_env(image_use, chrome_use),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if completed.returncode != 0 or not output.is_file():
                    detail = (completed.stderr or completed.stdout or "").strip()
                    if self._is_recoverable_session_failure(detail) and self._relay_connected(chrome_use):
                        output.unlink(missing_ok=True)
                        self._stop_chatgpt_session(chrome_use)
                        completed = subprocess.run(
                            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=480,
                            env=self._tool_env(image_use, chrome_use),
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        )
            if completed.returncode != 0 or not output.is_file():
                raise self._helper_failure(completed)
            data = output.read_bytes()
        except subprocess.TimeoutExpired as exc:
            raise FlowError("IMAGE_GENERATION_FAILED", "O ChatGPT não concluiu a imagem dentro de 8 minutos.", status=504) from exc
        finally:
            output.unlink(missing_ok=True)
        if not 0 < len(data) <= MAX_BYTES:
            raise FlowError("PAYLOAD_LIMIT", "A imagem gerada excedeu 20 MiB.", status=413)
        return data

    @staticmethod
    def _safe_stem(name: str) -> str:
        stem = Path(name or "imagem").stem
        stem = re.sub(r"[^\w.-]+", "-", stem, flags=re.UNICODE).strip(".-_")[:80]
        return stem or "imagem"

    def _store_library_copy(self, data: bytes, name: str) -> tuple[Path, str]:
        ext, _, _, _ = image_info(data)
        stem = self._safe_stem(name)
        target = self.directory / f"{stem}{ext}"
        if target.exists():
            target = self.directory / f"{stem}-{uuid.uuid4().hex[:8]}{ext}"
        fd, temporary = tempfile.mkstemp(prefix=".imagem-", suffix=".part", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target, target.name

    @staticmethod
    def _public(asset: dict, name: str) -> dict:
        return {"id": asset["id"], "name": name, "mime": asset["mime"], "bytes": asset["bytes"],
                "width": asset["width"], "height": asset["height"], "source": asset["source"],
                "preview_url": f"/media/assets/{asset['id']}"}

    def generate(
        self,
        prompt: str,
        reference_asset_ids: list[str] | None = None,
        *,
        size: str | None = None,
        ref_roles: list[str] | None = None,
    ) -> dict:
        params = inspect.signature(self._request).parameters
        if "size" in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            data = self._request(prompt, reference_asset_ids, size=size, ref_roles=ref_roles)
        else:
            data = self._request(prompt, reference_asset_ids)
        timestamp = datetime.now(timezone.utc).strftime("imagem-%Y%m%d-%H%M%S")
        _, name = self._store_library_copy(data, timestamp)
        asset = self.assets.stage_bytes(data, source="generated")
        return self._public(asset, name)

    def import_base64(self, name: str, content: str) -> dict:
        if not isinstance(name, str) or not name or len(name) > 255:
            raise FlowError("INVALID_IMAGE", "Nome de imagem inválido.")
        try:
            data = base64.b64decode(content, validate=True)
        except (ValueError, TypeError) as exc:
            raise FlowError("INVALID_IMAGE", "Conteúdo base64 inválido.") from exc
        _, stored_name = self._store_library_copy(data, name)
        asset = self.assets.stage_bytes(data, source="uploaded")
        return self._public(asset, stored_name)

    def list(self) -> dict:
        items = []
        for path in sorted(self.directory.iterdir(), key=lambda value: value.stat().st_mtime if value.is_file() else 0, reverse=True):
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                continue
            try:
                asset = self.assets.stage_file(path, allowed_root=self.directory, source="uploaded")
            except FlowError:
                continue
            items.append(self._public(asset, path.name))
        return {"folder": str(self.directory), "items": items}
