from __future__ import annotations

import base64
import hashlib
import os
import struct
import tempfile
import uuid
from pathlib import Path

from .errors import FlowError
from .repository import Repository

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000


def image_info(data: bytes) -> tuple[str, str, int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return ".png", "image/png", width, height
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return ".gif", "image/gif", width, height
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk = data[12:16]
        if chunk == b"VP8X":
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return ".webp", "image/webp", width, height
        if chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return ".webp", "image/webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8 " and len(data) >= 30:
            return ".webp", "image/webp", int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            offset += 2
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(data):
                break
            size = int.from_bytes(data[offset:offset + 2], "big")
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and offset + 7 < len(data):
                height = int.from_bytes(data[offset + 3:offset + 5], "big")
                width = int.from_bytes(data[offset + 5:offset + 7], "big")
                return ".jpg", "image/jpeg", width, height
            offset += max(size, 2)
    raise FlowError("INVALID_IMAGE", "Use PNG, JPEG, WebP ou GIF decodificável.")


class AssetService:
    def __init__(self, data_dir: Path, repository: Repository) -> None:
        self.directory = Path(data_dir).resolve() / "assets"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.repository = repository

    def stage_base64(self, name: str, content: str) -> dict:
        if not isinstance(name, str) or not name or len(name) > 255:
            raise FlowError("INVALID_IMAGE", "Nome de imagem inválido.")
        try:
            data = base64.b64decode(content, validate=True)
        except (ValueError, TypeError) as exc:
            raise FlowError("INVALID_IMAGE", "Conteúdo base64 inválido.") from exc
        return self.stage_bytes(data, source="uploaded")

    def stage_file(self, path: Path, *, allowed_root: Path | None = None, source: str = "uploaded", source_execution_id: str | None = None) -> dict:
        path = Path(path)
        if str(path).startswith(("\\\\", "//")):
            raise FlowError("INVALID_IMAGE", "Caminhos de rede não são aceitos.")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise FlowError("INVALID_IMAGE", "Imagem não encontrada: " + str(path)) from exc
        if allowed_root is not None and not resolved.is_relative_to(Path(allowed_root).resolve(strict=True)):
            raise FlowError("INVALID_IMAGE", "A imagem está fora da pasta autorizada.")
        if not resolved.is_file() or resolved.stat().st_size > MAX_BYTES:
            raise FlowError("PAYLOAD_LIMIT", "Imagem inválida ou maior que 20 MiB.", status=413)
        with resolved.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        return self.stage_bytes(data, source=source, source_execution_id=source_execution_id)

    def stage_bytes(self, data: bytes, *, source: str, source_execution_id: str | None = None) -> dict:
        if not 0 < len(data) <= MAX_BYTES:
            raise FlowError("PAYLOAD_LIMIT", "Cada imagem deve ter até 20 MiB.", status=413)
        ext, mime, width, height = image_info(data)
        if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
            raise FlowError("INVALID_IMAGE", "Imagem excede 40 megapixels ou tem dimensões inválidas.")
        digest = hashlib.sha256(data).hexdigest()
        final = self.directory / f"{digest}{ext}"
        if not final.exists():
            fd, temp_name = tempfile.mkstemp(prefix=digest + ".", suffix=".part", dir=self.directory)
            try:
                with os.fdopen(fd, "wb") as target:
                    for offset in range(0, len(data), 1024 * 1024):
                        target.write(data[offset:offset + 1024 * 1024])
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temp_name, final)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        value = {
            "id": str(uuid.uuid4()),
            "sha256": digest,
            "path": str(final),
            "mime": mime,
            "bytes": len(data),
            "width": width,
            "height": height,
            "source": source,
            "source_execution_id": source_execution_id,
        }
        stored = self.repository.put_asset(value)
        return {**stored, "preview_url": f"/media/assets/{stored['id']}"}

    def verified_path(self, asset_id: str) -> tuple[dict, Path]:
        asset = self.repository.get_asset(asset_id)
        path = Path(asset["path"]).resolve()
        if not path.is_relative_to(self.directory) or not path.is_file():
            raise FlowError("ARTIFACT_NOT_FOUND", "Imagem registrada não está disponível.", status=404)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != asset["sha256"]:
            raise FlowError("PLAN_STALE", "A imagem mudou depois do planejamento.", status=409)
        return asset, path
