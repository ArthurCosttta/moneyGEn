from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

CHATGPT_URL = "https://chatgpt.com/"
COMPOSER = ('[data-testid="prompt-textarea"], #prompt-textarea, '
            '[contenteditable="true"][data-lexical-editor="true"], '
            'form[data-chatgpt-composer] [data-composer-markdown][contenteditable="true"][role="textbox"]')
SEND = '[data-testid="send-button"], button[type="submit"]'
ASSISTANT = ('[data-testid^="conversation-turn-"][data-turn="assistant"], '
             '[data-testid^="conversation-turn-"][data-message-author-role="assistant"], '
             '[data-turn-key]:has([data-conversation-role="assistant"], [data-chatgpt-agent-turn-start])')


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def marker_value(url: str) -> dict:
    return {"version": 1, "authenticated": True, "source": "chatgpt-web-browser",
            "verified_at": datetime.now(timezone.utc).isoformat(), "surface_url": url}


def visible_composer(page, timeout_ms: int = 60_000):
    composer = page.locator(COMPOSER).filter(visible=True).first
    composer.wait_for(state="visible", timeout=timeout_ms)
    if not page.url.startswith("https://chatgpt.com/"):
        raise RuntimeError("A verificação terminou fora de chatgpt.com.")
    return composer


def cdp_endpoint_if_alive(profile: Path) -> str | None:
    port_file = profile / "DevToolsActivePort"
    try:
        port = int(port_file.read_text(encoding="utf-8").splitlines()[0].strip())
    except (OSError, ValueError, IndexError):
        return None
    endpoint = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{endpoint}/json/version", timeout=1.5) as response:
            if response.status == 200 and json.loads(response.read().decode("utf-8")).get("webSocketDebuggerUrl"):
                return endpoint
    except (OSError, urllib.error.URLError, ValueError):
        return None
    return None


def ensure_cdp_browser(chrome: Path, profile: Path, initial_url: str | None = None) -> tuple[str, bool]:
    existing = cdp_endpoint_if_alive(profile)
    if existing:
        return existing, False
    profile.mkdir(parents=True, exist_ok=True)
    port_file = profile / "DevToolsActivePort"
    port_file.unlink(missing_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        [
            str(chrome), f"--user-data-dir={profile}", "--new-window", "--disable-background-mode",
            "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0",
            initial_url or "about:blank",
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, creationflags=flags,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        endpoint = cdp_endpoint_if_alive(profile)
        if endpoint:
            return endpoint, True
        code = process.poll()
        if code is not None:
            endpoint = cdp_endpoint_if_alive(profile)
            if endpoint:
                return endpoint, False
            if code == 21:
                raise RuntimeError("CHROME_PROFILE_BUSY: O perfil do Chrome já está em uso por outra janela sem depuração. Feche-a e tente novamente.")
            raise RuntimeError(f"O Chrome dedicado terminou inesperadamente (código {code}).")
        time.sleep(0.1)
    process.terminate()
    raise RuntimeError("O Chrome abriu, mas não disponibilizou a depuração local da sessão.")


def open_or_focus_tab(context, target_url: str, *, reuse_prefix: str | None = None, navigate_if_reused: bool = False):
    if reuse_prefix:
        existing = next((item for item in context.pages if item.url.startswith(reuse_prefix)), None)
        if existing is not None:
            if navigate_if_reused and existing.url != target_url:
                existing.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
            existing.bring_to_front()
            return existing
    blank = next((item for item in context.pages if item.url in ("about:blank", "chrome://newtab/", "")), None)
    page = blank if blank is not None else context.new_page()
    page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
    page.bring_to_front()
    return page


def connect_cdp_context(playwright, chrome: Path, profile: Path, initial_url: str | None = None):
    endpoint, launched_new = ensure_cdp_browser(chrome, profile, initial_url=initial_url)
    browser = playwright.chromium.connect_over_cdp(endpoint, timeout=30_000)
    if not browser.contexts:
        browser.close()
        raise RuntimeError("O Chrome dedicado não disponibilizou o perfil aberto.")
    return browser, browser.contexts[0], launched_new


def open_ui(args) -> dict:
    if not args.url:
        raise RuntimeError("URL é obrigatória para abrir a interface.")
    parsed = urlsplit(args.url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else args.url
    with sync_playwright() as playwright:
        browser, context, launched_new = connect_cdp_context(playwright, Path(args.chrome), Path(args.profile), initial_url=args.url)
        try:
            if launched_new:
                existing = next((item for item in context.pages if item.url.startswith(origin)), None)
                if existing is not None:
                    existing.bring_to_front()
                else:
                    open_or_focus_tab(context, args.url, reuse_prefix=origin)
            else:
                open_or_focus_tab(context, args.url, reuse_prefix=origin, navigate_if_reused=True)
            return {"ok": True, "url": args.url, "reused": not launched_new}
        finally:
            browser.close()


def verify_profile(chrome: Path, profile: Path, marker: Path, *, visible: bool) -> dict:
    with sync_playwright() as playwright:
        browser, context, _ = connect_cdp_context(playwright, chrome, profile, initial_url=CHATGPT_URL)
        try:
            page = open_or_focus_tab(context, CHATGPT_URL, reuse_prefix=CHATGPT_URL)
            visible_composer(page)
            value = marker_value(page.url)
            atomic_json(marker, value)
            return value
        finally:
            browser.close()


def login(args) -> dict:
    marker = Path(args.marker)
    marker.unlink(missing_ok=True)
    with sync_playwright() as playwright:
        browser, context, _ = connect_cdp_context(playwright, Path(args.chrome), Path(args.profile), initial_url=CHATGPT_URL)
        try:
            page = open_or_focus_tab(context, CHATGPT_URL, reuse_prefix=CHATGPT_URL)
            try:
                visible_composer(page, timeout_ms=15 * 60_000)
            except PlaywrightTimeoutError as exc:
                if page.is_closed():
                    raise RuntimeError("A aba do ChatGPT foi fechada antes da confirmação do login.") from exc
                raise RuntimeError("O campo de mensagem do ChatGPT não apareceu dentro de 15 minutos.") from exc
            value = marker_value(page.url)
            atomic_json(marker, value)
            return value
        finally:
            browser.close()


def clear_session(args) -> dict:
    profile = Path(args.profile)
    endpoint = cdp_endpoint_if_alive(profile)
    if not endpoint:
        return {"ok": True, "cleared": False, "browser_running": False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(endpoint, timeout=15_000)
        try:
            for context in browser.contexts:
                for page in list(context.pages):
                    if page.url.startswith(CHATGPT_URL) or "openai.com" in page.url:
                        try:
                            page.close()
                        except Exception:
                            pass
                for domain in ("chatgpt.com", ".chatgpt.com", "openai.com", ".openai.com", "auth.openai.com"):
                    try:
                        context.clear_cookies(domain=domain)
                    except Exception:
                        pass
            return {"ok": True, "cleared": True, "browser_running": True}
        finally:
            browser.close()


def diagnostic_text(page) -> str:
    messages: list[str] = []
    for selector in ('[role="alert"]', '[role="status"]'):
        try:
            messages.extend(page.locator(selector).all_inner_texts())
        except Exception:
            pass
    cleaned = [" ".join(value.split())[:500] for value in messages if value.strip()]
    return " | ".join(dict.fromkeys(cleaned))[:2000]


def fill_composer(page, composer, form, prompt_text: str):
    composer.click()
    composer.fill(prompt_text)
    send = form.locator(SEND).filter(visible=True).first
    send.wait_for(state="visible", timeout=20_000)
    deadline = time.time() + 3
    while not send.is_enabled() and time.time() < deadline:
        time.sleep(0.1)
    if not send.is_enabled():
        composer.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.insert_text(prompt_text)
        deadline = time.time() + 60
        while not send.is_enabled() and time.time() < deadline:
            time.sleep(0.1)
    if not send.is_enabled():
        raise RuntimeError("O ChatGPT recebeu o prompt, mas não habilitou o botão de enviar.")
    return send


def generate(args) -> dict:
    chrome, profile, marker = Path(args.chrome), Path(args.profile), Path(args.marker)
    output = Path(args.output)
    references = [str(Path(value).resolve()) for value in args.reference]
    with sync_playwright() as playwright:
        browser, context, _ = connect_cdp_context(playwright, chrome, profile, initial_url=CHATGPT_URL)
        try:
            page = open_or_focus_tab(context, CHATGPT_URL)
            try:
                composer = visible_composer(page)
            except PlaywrightTimeoutError as exc:
                marker.unlink(missing_ok=True)
                raise RuntimeError("A sessão do ChatGPT Web não está autenticada; use Entrar no GPT.") from exc
            form = composer.locator("xpath=ancestor::form[1]")
            if references:
                upload = form.locator('input[data-testid="upload-photos-input"], input[type="file"][multiple]:not([accept])').first
                upload.wait_for(state="attached", timeout=20_000)
                upload.set_input_files(references)
                deadline = time.time() + 90
                while time.time() < deadline:
                    if form.locator(".composer-attachment-surface").filter(visible=True).count() >= len(references):
                        break
                    time.sleep(0.25)
            before = page.locator(f"{ASSISTANT} img").count()
            send = fill_composer(page, composer, form, "Crie uma imagem com este pedido:\n\n" + args.prompt)
            send.click()
            images = page.locator(f"{ASSISTANT} img").filter(visible=True)
            deadline = time.time() + 360
            candidate = None
            while time.time() < deadline:
                count = images.count()
                if count > before:
                    for index in range(count - 1, before - 1, -1):
                        item = images.nth(index)
                        box = item.bounding_box()
                        if box and box["width"] >= 256 and box["height"] >= 256:
                            candidate = item
                            break
                if candidate is not None:
                    break
                alerts = diagnostic_text(page).casefold()
                if any(word in alerts for word in ("violat", "policy", "unable", "can't", "cannot", "recus")):
                    raise RuntimeError("O ChatGPT recusou a imagem. " + diagnostic_text(page))
                time.sleep(1)
            if candidate is None:
                detail = diagnostic_text(page)
                raise RuntimeError("O ChatGPT não exibiu uma imagem gerada dentro de 6 minutos."
                                   + (" Mensagem visível: " + detail if detail else ""))
            output.parent.mkdir(parents=True, exist_ok=True)
            candidate.screenshot(path=str(output), type="png")
            atomic_json(marker, marker_value(page.url))
            return {"ok": True, "output": str(output), "surface_url": page.url}
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("open-ui", "login", "verify", "generate", "clear-session"))
    parser.add_argument("--chrome", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--url")
    parser.add_argument("--prompt")
    parser.add_argument("--output")
    parser.add_argument("--reference", action="append", default=[])
    args = parser.parse_args()
    try:
        if args.command == "open-ui":
            result = open_ui(args)
        elif args.command == "login":
            result = login(args)
        elif args.command == "verify":
            result = verify_profile(Path(args.chrome), Path(args.profile), Path(args.marker), visible=True)
        elif args.command == "clear-session":
            result = clear_session(args)
        else:
            if not args.prompt or not args.output:
                raise RuntimeError("Prompt e saída são obrigatórios para gerar.")
            result = generate(args)
        print(json.dumps({"status": "ok", **result}, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc), "type": type(exc).__name__}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
