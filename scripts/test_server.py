from __future__ import annotations

import argparse
import json
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import V3Server
from backend.adapters.fake import FakeFlowAdapter
from backend.service import EXPECTED_ACCOUNT, FlowLocalService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--info", required=True)
    args = parser.parse_args()
    root = ROOT
    data = Path(args.data_dir).resolve()
    data.mkdir(parents=True, exist_ok=True)
    fixture = data / "fixture-3s.mp4"
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=#10221c:s=180x320:r=24", "-t", "3", "-pix_fmt", "yuv420p", "-an", str(fixture)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    adapter = FakeFlowAdapter(fixture, account=EXPECTED_ACCOUNT)
    service = FlowLocalService(root, data_dir=data / "dados", adapter=adapter)
    server = V3Server(("127.0.0.1", args.port), service)
    Path(args.info).write_text(json.dumps({"url": server.origin, "token": server.token, "provider_mode": "fixture", "output": str(data / "saida")}), encoding="utf-8")
    signal.signal(signal.SIGTERM, lambda *_: server.shutdown())
    try:
        server.serve_forever(.1)
    finally:
        server.server_close()
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
