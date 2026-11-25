from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = {
    "app.py", "flow_adapter.py", "engine.py", "dialogs.py", "manifests.py",
    "bootstrap.ps1", "atualizar.ps1", "INICIAR.bat", "SOMENTE_TESTE.bat",
    "ATUALIZAR_EXISTENTE.bat", "TESTES_LOCAIS.bat", "TESTAR_INTERFACE.bat",
    "LEIA-ME.md", "RELATORIO_DE_TESTES.md", "LIMITACOES.md", "ACEITE_NO_WINDOWS.md",
    "LICENSE.txt", "package.json", "package-lock.json", "playwright.config.ts",
}
TOP_DIRS = {"backend", "migrations", "schemas", "web", "exemplos", "referencia", "scripts", "tests", "evidencias"}
EXCLUDED_PARTS = {"__pycache__", "node_modules", ".runtime", "dados", "saida", "qa", "test-results", "playwright-artifacts", ".git"}


def allowed(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    return (len(relative.parts) == 1 and relative.name in TOP_FILES) or relative.parts[0] in TOP_DIRS


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", required=True)
    args = parser.parse_args()
    outputs = Path(args.outputs).resolve()
    outputs.mkdir(parents=True, exist_ok=True)
    members: list[tuple[str, bytes]] = []
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and allowed(path):
            arcname = "FlowLocal_Windows_v3/" + path.relative_to(ROOT).as_posix()
            members.append((arcname, path.read_bytes()))
    screenshots = sorted((ROOT / "qa" / "screenshots").rglob("*.png"))
    if len(screenshots) != 27:
        raise SystemExit(f"Esperadas 27 capturas; encontradas {len(screenshots)}.")
    for path in screenshots:
        relative = path.relative_to(ROOT / "qa" / "screenshots").as_posix()
        members.append(("FlowLocal_Windows_v3/evidencias/screenshots/" + relative, path.read_bytes()))
        metadata = path.with_suffix(".json")
        if not metadata.is_file():
            raise SystemExit("Metadado de captura ausente: " + str(metadata))
        members.append(("FlowLocal_Windows_v3/evidencias/screenshots/" + metadata.relative_to(ROOT / "qa" / "screenshots").as_posix(), metadata.read_bytes()))
    manifest = {
        "schema_version": 1,
        "application_version": "3.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root": "FlowLocal_Windows_v3",
        "user_data_included": False,
        "authenticated_traces_included": False,
        "files": [{"path": name, "bytes": len(data), "sha256": digest(data)} for name, data in members],
    }
    manifest_data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    members.append(("FlowLocal_Windows_v3/RELEASE_MANIFEST.json", manifest_data))
    target = outputs / "FlowLocal_Windows_v3.0.0.zip"
    fd, temporary = tempfile.mkstemp(prefix="FlowLocal-Windows-v3-", suffix=".zip", dir=outputs)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
            for name, data in members:
                archive.writestr(name, data)
        with zipfile.ZipFile(temporary) as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError("Falha de CRC: " + bad)
            names = set(archive.namelist())
            required = {"FlowLocal_Windows_v3/INICIAR.bat", "FlowLocal_Windows_v3/app.py", "FlowLocal_Windows_v3/RELEASE_MANIFEST.json"}
            if not required <= names:
                raise RuntimeError("Arquivos obrigatórios ausentes do ZIP.")
            forbidden = [x for x in names if any(f"/{part}/" in x for part in ("dados", ".runtime", "saida", "node_modules", "playwright-artifacts"))]
            if forbidden:
                raise RuntimeError("Conteúdo proibido no ZIP: " + ", ".join(forbidden[:5]))
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    sha = digest(target.read_bytes())
    external = {"zip": target.name, "bytes": target.stat().st_size, "sha256": sha, "members": len(members), "screenshots": len(screenshots)}
    (outputs / "FlowLocal_Windows_v3.0.0.sha256.json").write_text(json.dumps(external, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(ROOT / "RELATORIO_DE_TESTES.md", outputs / "RELATORIO_DE_TESTES.md")
    shutil.copy2(ROOT / "LIMITACOES.md", outputs / "LIMITACOES.md")
    print(json.dumps(external, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
