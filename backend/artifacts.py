from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

from .errors import FlowError


class ArtifactService:
    def __init__(self, repository) -> None:
        self.repository = repository

    @staticmethod
    def _tool(name: str) -> str:
        value = shutil.which(name)
        if not value:
            raise FlowError("DEPENDENCY_UNAVAILABLE", f"{name} não está disponível; o vídeo não pode ser marcado VERIFIED.", status=503)
        return value

    def validate_publish(self, execution_id: str, source: Path, destination: Path, settings: dict) -> dict:
        ffprobe, ffmpeg = self._tool("ffprobe"), self._tool("ffmpeg")
        source = Path(source)
        if not source.is_file():
            raise FlowError("INVALID_ARTIFACT", "O provedor não entregou um arquivo.")
        probe = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=30, shell=False)
        try:
            info = json.loads(probe.stdout)
            stream = next(x for x in info.get("streams", []) if x.get("codec_type") == "video")
            width, height = int(stream["width"]), int(stream["height"])
            duration = float(stream.get("duration") or info["format"]["duration"])
            fmt = str(info["format"].get("format_name", ""))
        except Exception as exc:
            raise FlowError("INVALID_ARTIFACT", "ffprobe não confirmou um vídeo MP4 válido.") from exc
        if probe.returncode or "mp4" not in fmt and "mov" not in fmt or width <= 0 or height <= 0 or duration <= 0:
            raise FlowError("INVALID_ARTIFACT", "Contêiner ou stream de vídeo inválido.")
        expected_duration = settings.get("duration_seconds")
        if expected_duration is not None and abs(duration - expected_duration) > max(0.25, 1 / 24):
            raise FlowError("INVALID_ARTIFACT", f"Duração recebida ({duration:.3f}s) diverge da solicitada ({expected_duration}s).")
        expected_aspect = {"9:16": 9 / 16, "16:9": 16 / 9, "1:1": 1}.get(settings.get("aspect"))
        if expected_aspect and abs((width / height) - expected_aspect) / expected_aspect > 0.01:
            raise FlowError("INVALID_ARTIFACT", "A razão de aspecto do vídeo diverge do pedido.")
        decode = subprocess.run([ffmpeg, "-v", "error", "-i", str(source), "-f", "null", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=120, shell=False)
        if decode.returncode:
            raise FlowError("INVALID_ARTIFACT", "O vídeo não pôde ser decodificado integralmente.", provider_message=decode.stderr[-2000:])
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FlowError("OUTPUT_EXISTS", "O arquivo de saída já existe; nenhum byte foi sobrescrito.", status=409)
        staging = destination.parent / ("." + destination.name + ".part")
        if staging.exists():
            raise FlowError("OUTPUT_EXISTS", "Há um download parcial com o mesmo nome; revise antes de continuar.", status=409)
        digest = hashlib.sha256()
        with source.open("rb") as incoming, staging.open("xb") as outgoing:
            while chunk := incoming.read(1024 * 1024):
                digest.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        # Atomic publication without replacement: hard-link refuses an existing target.
        os.link(staging, destination)
        staging.unlink()
        value = {"id": str(uuid.uuid4()), "execution_id": execution_id, "sha256": digest.hexdigest(), "mime": "video/mp4", "bytes": destination.stat().st_size, "width": width, "height": height, "duration": duration, "path": str(destination), "validation_state": "VALID", "decoder_report": json.dumps({"ffprobe": info, "ffmpeg_exit": decode.returncode}, ensure_ascii=False)}
        return self.repository.save_artifact(value)

    def export_zip(self, batch_id: str, artifact_ids: list[str], output_dir: Path) -> dict:
        artifacts = [self.repository.artifact(x) for x in artifact_ids]
        allowed = [x for x in artifacts if x["validation_state"] == "VALID"]
        if not allowed:
            raise FlowError("ARTIFACT_NOT_FOUND", "Nenhum vídeo verificado para exportar.", status=404)
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"FlowLocal-{batch_id}.zip"
        if target.exists():
            raise FlowError("OUTPUT_EXISTS", "O ZIP já existe.", status=409)
        report = {"batch_id": batch_id, "included": len(allowed), "excluded": len(artifacts) - len(allowed), "artifacts": [{"id": x["id"], "sha256": x["sha256"], "bytes": x["bytes"], "name": Path(x["path"]).name} for x in allowed]}
        fd, temp_name = tempfile.mkstemp(prefix="flowlocal-export-", suffix=".part", dir=output_dir)
        os.close(fd)
        try:
            with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for artifact in allowed:
                    name = Path(artifact["path"]).name
                    if Path(name).name != name:
                        raise FlowError("INVALID_ARTIFACT", "Nome inseguro no ZIP.")
                    archive.write(artifact["path"], arcname=name)
                archive.writestr("relatorio.json", json.dumps(report, ensure_ascii=False, indent=2))
                lines = ["id,arquivo,sha256,bytes"] + [f"{x['id']},{Path(x['path']).name},{x['sha256']},{x['bytes']}" for x in allowed]
                archive.writestr("relatorio.csv", "\ufeff" + "\n".join(lines) + "\n")
            os.link(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return {"path": str(target), "sha256": digest, "bytes": target.stat().st_size, "included": len(allowed), "excluded": len(artifacts) - len(allowed)}
