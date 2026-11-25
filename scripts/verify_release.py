from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import PurePosixPath


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("zip")
    args = parser.parse_args()
    with zipfile.ZipFile(args.zip) as archive:
        if archive.testzip() is not None:
            raise SystemExit("CRC inválido")
        names = archive.namelist()
        if any(PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts for name in names):
            raise SystemExit("Caminho inseguro no ZIP")
        forbidden = {"dados", ".runtime", "saida", "node_modules", "playwright-artifacts", "storageState.json"}
        hits = [name for name in names if forbidden.intersection(PurePosixPath(name).parts)]
        if hits:
            raise SystemExit("Conteúdo proibido: " + ", ".join(hits[:5]))
        manifest = json.loads(archive.read("FlowLocal_Windows_v3/RELEASE_MANIFEST.json"))
        for item in manifest["files"]:
            data = archive.read(item["path"])
            if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise SystemExit("Hash divergente: " + item["path"])
        summary = {
            "status": "VERIFIED",
            "crc": "PASSED",
            "manifest_hashes": "PASSED",
            "members_verified": len(manifest["files"]),
            "forbidden_paths": 0,
            "iniciar_bat": "FlowLocal_Windows_v3/INICIAR.bat" in names,
        }
        print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
