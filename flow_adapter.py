"""Restricted subprocess entrypoint for the installed gflow runtime.

The request is a local JSON file. Prompts and paths are always argv/data, never shell text.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path


def emit(value: dict) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def clean_json(text: str) -> dict:
    decoder = json.JSONDecoder()
    values = []
    for match in re.finditer(r"\{", re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
            if isinstance(value, dict):
                values.append(value)
        except ValueError:
            pass
    return values[-1] if values else {}


def run_cli(request: dict, args: list[str], timeout: int) -> tuple[int, str, str]:
    executable = Path(request["gflow_executable"])
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GFLOW_", "UV_"))}
    env.update({
        "GFLOW_CLI_HOME": request["gflow_home"],
        "GFLOW_CLI_OUTPUT_DIR": request["output_dir"],
        "GFLOW_CLI_STORAGE_URI": "",
        "GFLOW_CLI_TELEMETRY": "0",
        "GFLOW_CLI_UPDATE_CHECK": "0",
        "GFLOW_CLI_HEADLESS": "false",
        "GFLOW_CLI_TRANSPORT": "ui_automation",
        "GFLOW_CLI_CONCURRENCY": "1",
        "GFLOW_MCP_NO_SPEND": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "NO_COLOR": "1",
    })
    result = subprocess.run([str(executable), *args], env=env, cwd=request["work_dir"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return result.returncode, result.stdout, result.stderr


async def private_upload(request: dict) -> dict:
    from gflow_cli.api.client import FlowApiClient
    expected = {
        "create_project": "(self, title: 'str | None' = None) -> 'ProjectInfo'",
        "upload_reference_in_place": "(self, project_id: 'str', path: 'Path') -> 'ImageRef | None'",
    }
    for name, signature in expected.items():
        method = getattr(FlowApiClient, name, None)
        if method is None or str(inspect.signature(method)) != signature:
            raise RuntimeError(f"Assinatura instalada incompatível: {name} {inspect.signature(method) if method else 'ausente'}")
    async with FlowApiClient(
        profile_dir=Path(request["profile_dir"]),
        headless=False,
        transport="ui_automation",
        out_dir=Path(request["output_dir"]),
    ) as client:
        project_id = request.get("project_id")
        if not project_id:
            project = await client.create_project(title="FlowLocal " + request["batch_id"])
            project_id = project.project_id
        ref = await client.upload_reference_in_place(project_id, Path(request["path"]))
        if ref is None:
            return {"ok": False, "code": "UPLOAD_ROUTE_UNAVAILABLE", "project_id": project_id, "message": "O transporte instalado não ofereceu upload no compositor; fallback REST foi bloqueado."}
        return {"ok": True, "project_id": project_id, "media_id": ref.name, "display_name": ref.display_name, "in_project": ref.in_project}


async def private_create_project(request: dict) -> dict:
    from gflow_cli.api.client import FlowApiClient
    method = getattr(FlowApiClient, "create_project", None)
    expected = "(self, title: 'str | None' = None) -> 'ProjectInfo'"
    if method is None or str(inspect.signature(method)) != expected:
        raise RuntimeError(f"Assinatura instalada incompatível: create_project {inspect.signature(method) if method else 'ausente'}")
    async with FlowApiClient(
        profile_dir=Path(request["profile_dir"]),
        headless=False,
        transport="ui_automation",
        out_dir=Path(request["output_dir"]),
    ) as client:
        project = await client.create_project(title="FlowLocal " + request["batch_id"])
        return {"ok": True, "project_id": project.project_id}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    path = Path(args.request).resolve(strict=True)
    if path.stat().st_size > 256 * 1024:
        raise SystemExit("request too large")
    request = json.loads(path.read_text(encoding="utf-8"))
    operation = request.get("operation")
    try:
        if operation == "inspect_session":
            code, out, err = run_cli(request, ["credits", "user", "--profile", request["profile"], "--json"], 180)
            value = clean_json(out)
            emit({"ok": code == 0 and value.get("status") == "ok", "code": "ACCOUNT_MISMATCH" if value.get("email", "").casefold() != request["expected_account"].casefold() else None, "identity": value.get("email"), "credits": value.get("credits"), "checked_at": value.get("checked_at"), "message": err if code else None})
        elif operation == "login":
            code, out, err = run_cli(request, ["auth", "login", "--browser", "chrome", "--profile", request["profile"], "--account", request["expected_account"]], 960)
            emit({"ok": code == 0, "code": "AUTHORIZATION" if code else None, "message": err or out if code else None})
        elif operation == "logout":
            code, out, err = run_cli(request, ["auth", "logout", "--profile", request["profile"], "--yes"], 120)
            emit({"ok": code == 0, "code": "AUTHORIZATION" if code else None, "message": err or out if code else None})
        elif operation == "inspect_capabilities":
            code, version, err = run_cli(request, ["--version"], 30)
            models = []
            evidence = version.strip() or err.strip()
            if code == 0:
                for model in ("omni-1.2", "veo-fast", "veo-lite"):
                    status, out, _ = run_cli(request, ["models", "show", model, "--json"], 30)
                    if status == 0:
                        models.append(model)
            emit({"ok": code == 0, "models": models, "version": version.strip(), "evidence": evidence, "verification": "LIVE" if models else "STATIC"})
        elif operation == "upload_reference":
            emit(asyncio.run(private_upload(request)))
        elif operation == "create_project":
            emit(asyncio.run(private_create_project(request)))
        elif operation == "generate_video":
            target = Path(request["staging_path"])
            args2 = ["video", request["kind"], "--project", request["project_id"], "--profile", request["profile"], "--model", request["settings"]["model_id"], "--aspect", request["settings"]["aspect"], "--count", "1", "--out-file", str(target), "--json"]
            if request["kind"] == "i2v":
                args2.extend(["--initial-frame", request["asset_path"]])
            if request["settings"].get("duration_seconds") is not None:
                args2.extend(["--duration", str(request["settings"]["duration_seconds"])])
            if request["settings"].get("resolution") is not None:
                args2.extend(["--resolution", request["settings"]["resolution"]])
            args2.extend(["--", request["prompt"]])
            code, out, err = run_cli(request, args2, 1800)
            value = clean_json(out)
            reported_path = Path(value.get("local_path", "")).resolve() if value.get("local_path") else None
            # The CLI can report provider success a moment before Windows exposes
            # the completed download. Wait briefly instead of classifying a valid
            # paid generation as uncertain and tempting the user to repeat it.
            if value.get("succeeded") is True and reported_path == target.resolve():
                for _ in range(120):
                    if target.is_file() and target.stat().st_size > 0:
                        break
                    time.sleep(.25)
            reasons = value.get("failure_reasons") or []
            message = "\n".join(str(x) for x in reasons) or value.get("error_message") or value.get("detail") or err or out
            succeeded = value.get("succeeded") is True and reported_path == target.resolve() and target.is_file() and target.stat().st_size > 0
            emit({"ok": succeeded, "code": "PROVIDER_FAILURE" if not succeeded else None, "provider_message": message if not succeeded else None, "provider_operation_id": value.get("media_id") or value.get("operation_id"), "charged": value.get("charged", "maybe"), "path": str(target) if succeeded else None, "raw": value, "cli_exit_code": code})
        else:
            emit({"ok": False, "code": "CAPABILITY_UNAVAILABLE", "message": "Operação de adaptador desconhecida."})
            return 2
        return 0
    except subprocess.TimeoutExpired:
        emit({"ok": False, "code": "UNCERTAIN_EFFECT", "message": "Tempo limite; o efeito pode ter ocorrido e não será repetido automaticamente."})
        return 3
    except Exception as exc:
        emit({"ok": False, "code": "PROVIDER_FAILURE", "message": str(exc)})
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
