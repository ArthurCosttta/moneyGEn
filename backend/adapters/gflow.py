from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

from ..errors import FlowError


class GFlowAdapter:
    def __init__(self, root: Path, data_dir: Path, output_dir: Path, *, expected_account: str, profile: str = "arthur") -> None:
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.expected_account = expected_account
        self.profile = profile
        self.runtime = self.root / ".runtime"
        self.gflow_executable = self.runtime / "bin" / ("gflow.exe" if os.name == "nt" else "gflow")
        self.python = self.runtime / "tools" / "gflow-cli" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.work_dir = self.runtime / "work"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.request_dir = self.data_dir / "adapter-requests"
        self.request_dir.mkdir(parents=True, exist_ok=True)

    def _base(self) -> dict:
        return {"gflow_executable": str(self.gflow_executable), "gflow_home": str(self.data_dir / "flow"), "profile_dir": str(self.data_dir / "flow" / f"profile_{self.profile}"), "profile": self.profile, "expected_account": self.expected_account, "output_dir": str(self.output_dir), "work_dir": str(self.work_dir)}

    def _call(self, operation: str, payload: dict | None = None, timeout: int = 1900) -> dict:
        if not self.python.is_file() or not self.gflow_executable.is_file():
            raise FlowError("DEPENDENCY_UNAVAILABLE", "gflow-cli 0.103.1 não está instalado nesta pasta.", status=503)
        request = {**self._base(), **(payload or {}), "operation": operation}
        request_path = self.request_dir / f"{uuid.uuid4()}.json"
        fd, temp = tempfile.mkstemp(prefix=request_path.stem + ".", suffix=".tmp", dir=self.request_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(request, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, request_path)
            result = subprocess.run([str(self.python), str(self.root / "flow_adapter.py"), "--request", str(request_path)], cwd=self.work_dir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
            if not lines:
                raise FlowError("PROVIDER_FAILURE", "O adaptador não devolveu JSON válido: " + result.stderr[-1000:], status=503)
            value = json.loads(lines[-1])
            if not value.get("ok"):
                code = value.get("code") or "PROVIDER_FAILURE"
                status = 403 if code == "ACCOUNT_MISMATCH" else 422 if code in ("UPLOAD_ROUTE_UNAVAILABLE", "CAPABILITY_UNAVAILABLE") else 503
                raise FlowError(code, value.get("message") or value.get("provider_message") or "Falha no Flow.", status=status, provider_message=value.get("provider_message"))
            return value
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
            try:
                request_path.unlink()
            except OSError:
                pass

    def inspect_session(self) -> dict:
        return self._call("inspect_session", timeout=200)

    def inspect_capabilities(self) -> dict:
        return self._call("inspect_capabilities", timeout=180)

    def login(self) -> dict:
        return self._call("login", timeout=980)

    def logout(self) -> dict:
        return self._call("logout", timeout=140)

    def create_project(self, batch_id: str) -> str:
        return self._call("create_project", {"batch_id": batch_id}, timeout=300)["project_id"]

    def upload_reference(self, *, batch_id: str, project_id: str | None, path: Path) -> dict:
        return self._call("upload_reference", {"batch_id": batch_id, "project_id": project_id, "path": str(path)}, timeout=700)

    def generate_video(self, *, batch_id: str, project_id: str, item: dict, asset_path: Path | None, staging_path: Path) -> dict:
        return self._call("generate_video", {"batch_id": batch_id, "project_id": project_id, "kind": item["kind"], "prompt": item["prompt"], "settings": item["settings"], "asset_path": str(asset_path) if asset_path else None, "staging_path": str(staging_path)}, timeout=1900)
