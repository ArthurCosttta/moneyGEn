from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    run_dir = Path(tempfile.mkdtemp(prefix="flowlocal-e2e-"))
    info = run_dir / "server.json"
    port = free_port()
    server = subprocess.Popen([sys.executable, str(root / "scripts" / "test_server.py"), "--port", str(port), "--data-dir", str(run_dir), "--info", str(info)], cwd=root)
    try:
        deadline = time.time() + 20
        while not info.is_file() and time.time() < deadline:
            if server.poll() is not None:
                raise RuntimeError("Servidor de fixture encerrou antes de iniciar.")
            time.sleep(.1)
        if not info.is_file():
            raise RuntimeError("Servidor de fixture não iniciou em 20 s.")
        value = json.loads(info.read_text(encoding="utf-8"))
        env = os.environ.copy()
        env.update({"FLOWLOCAL_TEST_URL": value["url"], "FLOWLOCAL_TEST_TOKEN": value["token"], "FLOWLOCAL_TEST_OUTPUT": value["output"], "FLOWLOCAL_PROVIDER_MODE": "fixture"})
        command = ["npx.cmd" if os.name == "nt" else "npx", "playwright", "test", *sys.argv[1:]]
        return subprocess.run(command, cwd=root, env=env, shell=False).returncode
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(run_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
