from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .contracts import strict_loads
from .errors import FlowError

FENCED_JSON_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def extract_json_payload(text: str) -> Any:
    if not isinstance(text, str) or not text.strip():
        raise FlowError("GPT_INVALID_JSON", "O ChatGPT retornou uma resposta vazia.", status=502)
    cleaned = text.strip()
    candidates: list[str] = []
    for match in FENCED_JSON_RE.finditer(cleaned):
        candidates.append(match.group(1).strip())
    candidates.append(cleaned)
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            return strict_loads(candidate)
        except FlowError:
            pass
        for idx, ch in enumerate(candidate):
            if ch in "{[":
                try:
                    value, _ = decoder.raw_decode(candidate[idx:])
                    return strict_loads(json.dumps(value, ensure_ascii=False))
                except (ValueError, FlowError):
                    pass
    raise FlowError("GPT_INVALID_JSON", "A resposta do ChatGPT não contém um JSON válido.", status=502, provider_message=cleaned[:1500])


class GptChatService:
    """Runs ChatGPT Web text/JSON prompts through the connected chrome-use extension."""

    @staticmethod
    def _chrome_use_path() -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        programs = Path(local_app_data) / "Programs" if local_app_data else Path.home() / "AppData/Local/Programs"
        candidates = [programs / "chrome-use/chrome-use.exe"]
        found = shutil.which("chrome-use")
        if found:
            candidates.append(Path(found))
        path = next((p.resolve() for p in candidates if p.is_file()), None)
        if not path:
            raise FlowError("IMAGE_GENERATOR_UNAVAILABLE", "A CLI chrome-use não está instalada.", status=503)
        return path

    @staticmethod
    def _env() -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("HOME", str(Path.home()))
        env["PYTHONUTF8"] = "1"
        return env

    def ask_json(
        self,
        prompt: str,
        *,
        new_chat: bool = True,
        conversation: str | None = None,
        timeout: int = 180,
    ) -> dict[str, Any]:
        chrome_use = self._chrome_use_path()
        strict_prompt = (
            prompt.strip()
            + "\n\nResponda EXCLUSIVAMENTE com um JSON válido, sem explicações antes ou depois."
        )
        command = [str(chrome_use), "site", "chatgpt/ask", strict_prompt, "--json", "--timeout", str(timeout)]
        if conversation:
            command += ["--conversation", conversation]
        elif new_chat:
            command.append("--new")
        fd_out, out_name = tempfile.mkstemp(prefix="gpt-chat-out-", suffix=".txt")
        fd_err, err_name = tempfile.mkstemp(prefix="gpt-chat-err-", suffix=".txt")
        try:
            with os.fdopen(fd_out, "w", encoding="utf-8") as fout, os.fdopen(fd_err, "w", encoding="utf-8") as ferr:
                completed = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=fout,
                    stderr=ferr,
                    timeout=timeout + 30,
                    env=self._env(),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            raw = Path(out_name).read_text(encoding="utf-8", errors="replace").strip()
            err = Path(err_name).read_text(encoding="utf-8", errors="replace").strip()
        except subprocess.TimeoutExpired as exc:
            raise FlowError("GPT_TIMEOUT", "O ChatGPT Web não respondeu o JSON dentro do tempo limite.", status=504) from exc
        finally:
            for tmp in (out_name, err_name):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        if completed.returncode != 0:
            detail = err or raw
            lowered = detail.casefold()
            short = " | ".join(line.strip() for line in detail.splitlines() if line.strip())[:240]
            if "profile_window_unavailable" in lowered or "profile window unavailable" in lowered:
                raise FlowError("CHROME_EXTENSION_REQUIRED", "Abra uma janela visível do Chrome no perfil conectado com a extensão chrome-use e tente novamente.", status=409, provider_message=detail[:1500])
            if "the relay is connected" not in lowered and any(
                term in lowered for term in ("relay not connected", "relay isn't connected", "extension not connected", "extension isn't connected", "ab-connect")
            ):
                raise FlowError("CHROME_EXTENSION_REQUIRED", "Extensão chrome-use desconectada no Chrome.", status=409, provider_message=detail[:1500])
            if any(term in lowered for term in ("connection_failed", "browser is gone", "carried-tabs", "closed connection", "timed out", "debugger stopped responding")):
                raise FlowError("CHROME_SESSION_STALE", f"A sessão do Chrome via chrome-use falhou ou travou ({short}). Tente novamente ou recarregue a extensão em chrome://extensions.", status=409, provider_message=detail[:1500])
            if any(term in lowered for term in ("login", "sign in", "unauthorized", "401")):
                raise FlowError("GPT_LOGIN_REQUIRED", "Entre em chatgpt.com no Chrome conectado e tente novamente.", status=401, provider_message=detail[:1500])
            raise FlowError("GPT_CHAT_FAILED", f"Falha ao consultar o ChatGPT Web: {short}", status=502, provider_message=detail[:1500])

        try:
            envelope = json.loads(raw)
        except ValueError as exc:
            raise FlowError("GPT_INVALID_JSON", "Saída inválida do adaptador chatgpt/ask.", status=502, provider_message=raw[:1500]) from exc

        row = envelope.get("data") if isinstance(envelope, dict) and "data" in envelope else envelope
        if isinstance(row, list):
            row = row[0] if row else {}
        if not isinstance(row, dict):
            raise FlowError("GPT_INVALID_JSON", "Formato inesperado do adaptador chatgpt/ask.", status=502, provider_message=raw[:1500])

        response_text = str(row.get("response") or "")
        parsed_json = extract_json_payload(response_text)
        return {
            "json": parsed_json,
            "conversation_id": row.get("conversationId"),
            "conversation_url": row.get("conversationUrl"),
            "raw_response": response_text,
        }
