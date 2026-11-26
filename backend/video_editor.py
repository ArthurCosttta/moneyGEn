from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .errors import FlowError

VIDEO_EXTS = {".mp4", ".mov", ".webm"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
ASPECT_SIZES = {
    "9:16": (720, 1280),
    "16:9": (1280, 720),
    "1:1": (1080, 1080),
}


def _ass_time(seconds: float) -> str:
    sec = max(0.0, float(seconds))
    total_cs = int(round(sec * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _hex_to_ass_color(hex_color: str) -> str:
    raw = (hex_color or "#ffffff").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", raw):
        raw = "ffffff"
    r, g, b = raw[0:2], raw[2:4], raw[4:6]
    return f"&H00{b.upper()}{g.upper()}{r.upper()}"


def _parse_fps(rate_str: str | None) -> float:
    if not rate_str or rate_str == "0/0":
        return 0.0
    if "/" in rate_str:
        num, den = rate_str.split("/", 1)
        try:
            d = float(den)
            return round(float(num) / d, 2) if d else 0.0
        except ValueError:
            return 0.0
    try:
        return round(float(rate_str), 2)
    except ValueError:
        return 0.0


class VideoEditorService:
    """Deterministic local video inspection & editing via FFmpeg, PySceneDetect, auto-editor & faster-whisper."""

    def __init__(self, root: Path, data_dir: Path, repository) -> None:
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.repository = repository
        self.editor_dir = self.data_dir / "editor"
        self.editor_dir.mkdir(parents=True, exist_ok=True)
        self.renders_dir = self.editor_dir / "renders"
        self.renders_dir.mkdir(parents=True, exist_ok=True)
        self.audios_dir = self.editor_dir / "audios"
        self.audios_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir = self.editor_dir / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _find_winget_ffmpeg(name: str) -> Path | None:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return None
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if not packages.is_dir():
            return None
        for match in packages.glob(f"Gyan.FFmpeg*/ffmpeg-*/bin/{name}.exe"):
            if match.is_file():
                return match.resolve()
        return None

    @classmethod
    def _ffmpeg_path(cls) -> Path | None:
        found = shutil.which("ffmpeg")
        if found:
            return Path(found).resolve()
        return cls._find_winget_ffmpeg("ffmpeg")

    @classmethod
    def _ffprobe_path(cls) -> Path | None:
        found = shutil.which("ffprobe")
        if found:
            return Path(found).resolve()
        return cls._find_winget_ffmpeg("ffprobe")

    @staticmethod
    def _sys_python_path() -> Path | None:
        candidates: list[Path] = []
        local = os.environ.get("LOCALAPPDATA")
        if local:
            for py_dir in sorted((Path(local) / "Programs" / "Python").glob("Python3*"), reverse=True):
                candidates.append(py_dir / "python.exe")
        which_py = shutil.which("python")
        if which_py:
            candidates.append(Path(which_py))
        candidates.append(Path(sys.executable))
        for cand in candidates:
            if cand.is_file() and ".runtime" not in cand.parts:
                return cand.resolve()
        return Path(sys.executable).resolve() if Path(sys.executable).is_file() else None

    @classmethod
    def _auto_editor_path(cls) -> Path | None:
        found = shutil.which("auto-editor")
        if found:
            return Path(found).resolve()
        sys_py = cls._sys_python_path()
        if sys_py:
            cand = sys_py.parent / "Scripts" / ("auto-editor.exe" if os.name == "nt" else "auto-editor")
            if cand.is_file():
                return cand.resolve()
        return None

    def _python_has_module(self, py: Path | None, module: str) -> bool:
        if not py or not py.is_file():
            return False
        try:
            r = subprocess.run(
                [str(py), "-c", f"import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('{module}') else 1)"],
                capture_output=True,
                timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def capabilities(self) -> dict[str, Any]:
        ffmpeg = self._ffmpeg_path()
        ffprobe = self._ffprobe_path()
        auto_ed = self._auto_editor_path()
        sys_py = self._sys_python_path()
        return {
            "ffmpeg": bool(ffmpeg),
            "ffmpeg_path": str(ffmpeg) if ffmpeg else None,
            "ffprobe": bool(ffprobe),
            "auto_editor": bool(auto_ed),
            "auto_editor_path": str(auto_ed) if auto_ed else None,
            "faster_whisper": self._python_has_module(sys_py, "faster_whisper"),
            "scenedetect": self._python_has_module(sys_py, "scenedetect"),
            "python_path": str(sys_py) if sys_py else None,
        }

    def verified_media_path(self, clip_or_bucket: str, *parts: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", clip_or_bucket):
            raise FlowError("INVALID_ARTIFACT", "Caminho de mídia inválido.", status=404)
        if parts == ("source",):
            analysis_file = (self.editor_dir / clip_or_bucket / "analysis.json").resolve()
            if analysis_file.is_relative_to(self.editor_dir) and analysis_file.is_file():
                try:
                    info = json.loads(analysis_file.read_text(encoding="utf-8"))
                    src = Path(str(info.get("path") or "")).resolve()
                    if src.is_file() and src.suffix.lower() in VIDEO_EXTS:
                        return src
                except (OSError, ValueError):
                    pass
            raise FlowError("ARTIFACT_NOT_FOUND", "Vídeo original não encontrado.", status=404)
        candidate = (self.editor_dir / clip_or_bucket).joinpath(*parts).resolve()
        if not candidate.is_relative_to(self.editor_dir) or not candidate.is_file():
            raise FlowError("ARTIFACT_NOT_FOUND", "Arquivo do editor não encontrado.", status=404)
        return candidate

    def _probe(self, path: Path) -> dict[str, Any]:
        ffprobe = self._ffprobe_path()
        if not ffprobe:
            raise FlowError("DEPENDENCY_UNAVAILABLE", "ffprobe não encontrado no sistema.", status=503)
        r = subprocess.run(
            [str(ffprobe), "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if r.returncode != 0 or not r.stdout.strip():
            raise FlowError("INVALID_ARTIFACT", f"Não foi possível ler o vídeo '{path.name}'.", status=422)
        data = json.loads(r.stdout)
        streams = data.get("streams") or []
        vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
        astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        fmt = data.get("format") or {}
        try:
            duration = round(float(fmt.get("duration") or (vstream or {}).get("duration") or 0), 3)
        except (TypeError, ValueError):
            duration = 0.0
        return {
            "duration": duration,
            "width": int((vstream or {}).get("width") or 0),
            "height": int((vstream or {}).get("height") or 0),
            "fps": _parse_fps((vstream or {}).get("r_frame_rate")),
            "has_audio": astream is not None,
        }

    @staticmethod
    def _clip_id(path: Path) -> str:
        st = path.stat()
        raw = f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8")
        return hashlib.sha1(raw).hexdigest()[:16]

    def _resolve_video_file(self, path_str: str) -> Path:
        if not isinstance(path_str, str) or not path_str.strip():
            raise FlowError("SCHEMA_VALIDATION", "Informe o caminho do vídeo.", pointer="/path")
        path = Path(path_str.strip()).resolve()
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTS:
            raise FlowError("ARTIFACT_NOT_FOUND", "Arquivo de vídeo não encontrado (.mp4, .mov ou .webm).", status=404)
        return path

    def _resolve_audio_file(self, path_str: str) -> Path:
        if not isinstance(path_str, str) or not path_str.strip():
            raise FlowError("SCHEMA_VALIDATION", "Informe o caminho do áudio.")
        path = Path(path_str.strip()).resolve()
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
            raise FlowError("ARTIFACT_NOT_FOUND", "Arquivo de áudio não encontrado (.mp3, .wav, .m4a, .aac).", status=404)
        return path

    def _resolve_overlay_file(self, path_str: str) -> tuple[Path, str]:
        if not isinstance(path_str, str) or not path_str.strip():
            raise FlowError("SCHEMA_VALIDATION", "Informe o caminho da camada (vídeo ou imagem).")
        path = Path(path_str.strip()).resolve()
        ext = path.suffix.lower()
        if not path.is_file() or ext not in (VIDEO_EXTS | IMAGE_EXTS):
            raise FlowError("ARTIFACT_NOT_FOUND", "Arquivo de overlay não encontrado (.mp4/.mov/.webm ou .png/.jpg/.webp).", status=404)
        return path, ("video" if ext in VIDEO_EXTS else "image")

    def upload_audio(self, name: str | None, content_b64: str | None) -> dict[str, Any]:
        import base64
        if not isinstance(name, str) or not name.strip() or not isinstance(content_b64, str) or not content_b64.strip():
            raise FlowError("SCHEMA_VALIDATION", "Envie nome e conteúdo base64 do áudio.")
        raw_name = Path(name.strip()).name
        ext = Path(raw_name).suffix.lower()
        if ext not in AUDIO_EXTS:
            raise FlowError("SCHEMA_VALIDATION", "Formato de áudio inválido (use .mp3, .wav, .m4a, .aac ou .ogg).")
        safe_stem = re.sub(r"[^\w.-]+", "-", Path(raw_name).stem).strip(".-_") or "audio"
        payload = content_b64.split(",", 1)[1] if "," in content_b64 else content_b64
        try:
            raw_bytes = base64.b64decode(payload, validate=True)
        except Exception as exc:
            raise FlowError("SCHEMA_VALIDATION", "Conteúdo base64 de áudio inválido.") from exc
        if not 1 <= len(raw_bytes) <= 28 * 1024 * 1024:
            raise FlowError("PAYLOAD_LIMIT", "Áudio vazio ou acima de 28 MB.", status=413)
        digest = hashlib.sha1(raw_bytes).hexdigest()[:8]
        out = self.audios_dir / f"{safe_stem[:40]}-{digest}{ext}"
        out.write_bytes(raw_bytes)
        dur = self._probe_duration(out)
        return {
            "name": out.name,
            "path": str(out),
            "url": f"/media/editor/audios/{out.name}",
            "duration": dur,
            "size_bytes": out.stat().st_size,
        }

    def _probe_duration(self, path: Path) -> float:
        ffprobe = self._ffprobe_path()
        if not ffprobe:
            return 0.0
        try:
            r = subprocess.run(
                [str(ffprobe), "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=12,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            fmt = json.loads(r.stdout or "{}").get("format") or {}
            return round(float(fmt.get("duration") or 0), 3)
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            return 0.0

    def list_audios(self) -> dict[str, Any]:
        files = sorted(
            (p for p in self.audios_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        items = []
        for p in files[:50]:
            items.append({
                "name": p.name,
                "path": str(p),
                "url": f"/media/editor/audios/{p.name}",
                "duration": self._probe_duration(p),
                "size_bytes": p.stat().st_size,
            })
        return {"items": items}

    def save_project(self, body: dict[str, Any]) -> dict[str, Any]:
        pid = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("id") or ""))[:40] or uuid.uuid4().hex[:12]
        name = str(body.get("name") or "Projeto CapCut").strip()[:120] or "Projeto CapCut"
        payload = {
            "id": pid,
            "name": name,
            "aspect": body.get("aspect") or "9:16",
            "clips": body.get("clips") or [],
            "overlays": body.get("overlays") or [],
            "audio_tracks": body.get("audio_tracks") or [],
            "captions": body.get("captions") or [],
            "caption_style": body.get("caption_style") or {},
            "last_render": body.get("last_render"),
        }
        (self.projects_dir / f"{pid}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def list_projects(self) -> dict[str, Any]:
        files = sorted(
            (p for p in self.projects_dir.glob("*.json") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        items = []
        for p in files[:30]:
            try:
                items.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return {"items": items}

    def list_videos(self, folder: str | None = None) -> dict[str, Any]:
        is_default = not (isinstance(folder, str) and folder.strip())
        target = Path(folder.strip()).resolve() if not is_default else (self.root / "saida").resolve()
        if not target.is_dir():
            if is_default:
                target.mkdir(parents=True, exist_ok=True)
            else:
                raise FlowError("SCHEMA_VALIDATION", f"Pasta não encontrada: {target}", pointer="/folder")
        files = [
            p
            for p in target.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS and "editados" not in p.parts
        ]
        if is_default:
            spy_media = (self.data_dir / "spy" / "media").resolve()
            if spy_media.is_dir():
                for p in spy_media.glob("*"):
                    if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                        files.append(p)
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        items = []
        for p in files[:60]:
            st = p.stat()
            meta = {"duration": None, "width": None, "height": None, "fps": None, "has_audio": None}
            try:
                meta = self._probe(p)
            except FlowError:
                pass
            clip_id = self._clip_id(p)
            analysis_file = self.editor_dir / clip_id / "analysis.json"
            items.append({
                "clip_id": clip_id,
                "name": p.name,
                "path": str(p),
                "folder": str(p.parent),
                "relative_label": str(p.relative_to(target)) if p.is_relative_to(target) else p.name,
                "size_bytes": st.st_size,
                "analyzed": analysis_file.is_file(),
                **meta,
            })
        return {"folder": str(target), "items": items}

    def _extract_frames(self, path: Path, clip_id: str, duration: float) -> list[dict[str, Any]]:
        ffmpeg = self._ffmpeg_path()
        if not ffmpeg:
            return []
        frames_dir = self.editor_dir / clip_id / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        for old in frames_dir.glob("frame_*.jpg"):
            old.unlink(missing_ok=True)
        count = 8 if duration >= 4 else max(3, int(round(duration)) or 3)
        dur = max(0.2, float(duration or 0.0))
        out = []
        for idx in range(count):
            ts = round(min(max(0.0, dur - 0.05), (idx * dur) / count), 2) if duration else float(idx)
            img_path = frames_dir / f"frame_{idx + 1:02d}.jpg"
            try:
                subprocess.run(
                    [
                        str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{ts:.2f}",
                        "-i", str(path),
                        "-frames:v", "1",
                        "-vf", "scale=240:-1",
                        "-q:v", "4",
                        str(img_path),
                    ],
                    capture_output=True,
                    timeout=12,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if img_path.is_file() and img_path.stat().st_size > 0:
                out.append({
                    "index": idx + 1,
                    "time": ts,
                    "url": f"/media/editor/{clip_id}/frames/{img_path.name}",
                })
        return out

    def _detect_scenes(self, path: Path, *, duration: float = 0.0) -> list[dict[str, Any]]:
        sys_py = self._sys_python_path()
        if not self._python_has_module(sys_py, "scenedetect"):
            return []
        frame_skip = 2 if duration and duration > 120 else 0
        script = (
            "import json, sys\n"
            "from scenedetect import open_video, SceneManager\n"
            "from scenedetect.detectors import AdaptiveDetector\n"
            "v = open_video(sys.argv[1])\n"
            "sm = SceneManager()\n"
            "sm.add_detector(AdaptiveDetector())\n"
            "skip = int(sys.argv[2])\n"
            "sm.detect_scenes(v, frame_skip=skip)\n"
            "scenes = [{'index': i+1, 'start': round(s[0].get_seconds(), 3), 'end': round(s[1].get_seconds(), 3)} for i, s in enumerate(sm.get_scene_list())]\n"
            "print(json.dumps(scenes))\n"
        )
        try:
            r = subprocess.run(
                [str(sys_py), "-c", script, str(path), str(frame_skip)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if r.returncode != 0 or not r.stdout.strip():
            return []
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except ValueError:
            return []

    def _transcribe(self, path: Path, model_size: str = "base") -> dict[str, Any]:
        sys_py = self._sys_python_path()
        if not self._python_has_module(sys_py, "faster_whisper"):
            return {"language": None, "segments": [], "note": "faster-whisper não disponível neste Python."}
        script = (
            "import json, sys\n"
            "from faster_whisper import WhisperModel\n"
            "model = WhisperModel(sys.argv[2], device='cpu', compute_type='int8')\n"
            "segs, info = model.transcribe(sys.argv[1], vad_filter=True, word_timestamps=True)\n"
            "out = []\n"
            "for i, s in enumerate(segs):\n"
            "    out.append({'id': i+1, 'start': round(s.start, 3), 'end': round(s.end, 3), 'text': s.text.strip(), 'words': [{'start': round(w.start, 3), 'end': round(w.end, 3), 'word': w.word} for w in (s.words or [])]})\n"
            "print(json.dumps({'language': info.language, 'segments': out}, ensure_ascii=False))\n"
        )
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        try:
            r = subprocess.run(
                [str(sys_py), "-c", script, str(path), model_size],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired:
            return {"language": None, "segments": [], "note": "Transcrição automática expirou o limite neste vídeo; você ainda pode cortar ou adicionar legendas manuais."}
        except OSError as exc:
            return {"language": None, "segments": [], "note": f"Falha ao executar Whisper: {exc}"}
        if r.returncode != 0 or not r.stdout.strip():
            return {"language": None, "segments": [], "note": (r.stderr or "Falha ao transcrever.")[-300:]}
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except ValueError:
            return {"language": None, "segments": [], "note": "Saída de transcrição inválida."}

    def analyze_video(
        self,
        path_str: str,
        *,
        run_whisper: bool = True,
        run_scenes: bool = True,
        extract_frames: bool = True,
        force: bool = False,
        model_size: str = "base",
    ) -> dict[str, Any]:
        path = self._resolve_video_file(path_str)
        clip_id = self._clip_id(path)
        clip_dir = self.editor_dir / clip_id
        clip_dir.mkdir(parents=True, exist_ok=True)
        cache_path = clip_dir / "analysis.json"
        if cache_path.is_file() and not force:
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached.get("clip_id") == clip_id:
                    cached.setdefault("source_url", f"/media/editor/{clip_id}/source")
                    return cached
            except (OSError, ValueError):
                pass

        meta = self._probe(path)
        frames = self._extract_frames(path, clip_id, meta["duration"]) if extract_frames else []
        scenes = self._detect_scenes(path, duration=meta["duration"]) if run_scenes else []
        if run_whisper and meta["has_audio"]:
            tx = self._transcribe(path, model_size=model_size)
        elif not meta["has_audio"]:
            tx = {"language": None, "segments": [], "note": "Vídeo sem faixa de áudio; use o campo de legenda para texto manual."}
        else:
            tx = {"language": None, "segments": [], "note": None}

        result = {
            "clip_id": clip_id,
            "name": path.name,
            "path": str(path),
            "source_url": f"/media/editor/{clip_id}/source",
            **meta,
            "frames": frames,
            "scenes": scenes,
            "language": tx.get("language"),
            "segments": tx.get("segments") or [],
            "note": tx.get("note"),
        }
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    @staticmethod
    def _write_ass(
        path: Path,
        segments: list[dict[str, Any]],
        *,
        width: int,
        height: int,
        font_size: int,
        position: str,
        color: str,
    ) -> None:
        play_x = width if width >= 240 else 720
        play_y = height if height >= 240 else 1280
        alignment = 8 if position == "Superior" else 5 if position == "Central" else 2
        margin_v = int(play_y * (0.08 if position in ("Superior", "Inferior") else 0.04))
        primary_col = _hex_to_ass_color(color)
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {play_x}",
            f"PlayResY: {play_y}",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,Arial,{int(font_size)},{primary_col},&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,4,1,{alignment},40,40,{margin_v},1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        for seg in segments:
            text = str(seg.get("text") or "").strip().replace("\r", " ").replace("\n", "\\N")
            if not text:
                continue
            start = _ass_time(float(seg.get("start") or 0))
            end = _ass_time(float(seg.get("end") or 0))
            if end <= start:
                continue
            lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def render_video(self, body: dict[str, Any]) -> dict[str, Any]:
        ffmpeg = self._ffmpeg_path()
        if not ffmpeg:
            raise FlowError("DEPENDENCY_UNAVAILABLE", "ffmpeg.exe não encontrado no sistema.", status=503)
        source = self._resolve_video_file(str(body.get("source_path") or ""))
        meta = self._probe(source)

        trim_start = body.get("trim_start")
        trim_end = body.get("trim_end")
        if trim_start is not None and trim_start != "":
            trim_start = float(trim_start)
            if trim_start < 0:
                raise FlowError("SCHEMA_VALIDATION", "Início do corte não pode ser negativo.")
        else:
            trim_start = None
        if trim_end is not None and trim_end != "":
            trim_end = float(trim_end)
            if trim_start is not None and trim_end <= trim_start:
                raise FlowError("SCHEMA_VALIDATION", "O fim do corte deve ser maior que o início.")
        else:
            trim_end = None

        cut_silence = bool(body.get("cut_silence"))
        burn_captions = bool(body.get("burn_captions"))
        margin = str(body.get("silence_margin") or "0.2s").strip()
        if not re.fullmatch(r"\d+(?:\.\d+)?s?", margin):
            margin = "0.2s"

        out_dir_raw = body.get("output_dir")
        out_dir = Path(str(out_dir_raw).strip()).resolve() if out_dir_raw else (source.parent / "editados")
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_name = Path(str(body.get("output_name") or f"{source.stem}-editado.mp4")).name
        safe_name = re.sub(r"[^\w.-]+", "-", raw_name).strip(".-_") or "video-editado.mp4"
        if not safe_name.lower().endswith(".mp4"):
            safe_name += ".mp4"
        target = out_dir / safe_name
        if target.resolve() == source.resolve() or target.exists():
            target = out_dir / f"{Path(safe_name).stem}-{uuid.uuid4().hex[:6]}.mp4"

        work_dir = Path(tempfile.mkdtemp(prefix="flowlocal-edit-"))
        try:
            current = source
            if cut_silence and meta["has_audio"]:
                auto_ed = self._auto_editor_path()
                if not auto_ed:
                    raise FlowError("DEPENDENCY_UNAVAILABLE", "auto-editor não encontrado. Instale com `pip install auto-editor`.", status=503)
                cut_tmp = work_dir / "cut.mp4"
                ae_res = subprocess.run(
                    [str(auto_ed), str(current), "--margin", margin, "-o", str(cut_tmp)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if ae_res.returncode == 0 and cut_tmp.is_file():
                    current = cut_tmp
                else:
                    raise FlowError("LOCAL_FAILURE", "Falha no auto-editor ao cortar silêncios.", provider_message=(ae_res.stderr or ae_res.stdout)[-1000:])

            segments = body.get("segments") if isinstance(body.get("segments"), list) else []
            if burn_captions and cut_silence and meta["has_audio"] and current != source and bool(body.get("resync_captions", True)):
                re_tx = self._transcribe(current, model_size=str(body.get("model_size") or "base"))
                if re_tx.get("segments"):
                    segments = re_tx["segments"]

            cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error"]
            if trim_start is not None:
                cmd += ["-ss", f"{trim_start:.3f}"]
            if trim_end is not None:
                cmd += ["-to", f"{trim_end:.3f}"]
            cmd += ["-i", str(current)]

            if burn_captions and segments:
                style = body.get("caption_style") or {}
                ass_path = work_dir / "captions.ass"
                self._write_ass(
                    ass_path,
                    segments,
                    width=meta["width"],
                    height=meta["height"],
                    font_size=int(style.get("font_size") or 52),
                    position=str(style.get("position") or "Inferior"),
                    color=str(style.get("color") or "#ffffff"),
                )
                cmd += ["-vf", "ass=captions.ass"]

            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
            if meta["has_audio"]:
                cmd += ["-c:a", "aac", "-b:a", "128k"]
            else:
                cmd += ["-an"]
            cmd += ["-movflags", "+faststart", str(target)]

            ff_res = subprocess.run(
                cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if ff_res.returncode != 0 or not target.is_file():
                raise FlowError(
                    "LOCAL_FAILURE",
                    "FFmpeg não conseguiu renderizar o vídeo editado.",
                    provider_message=(ff_res.stderr or ff_res.stdout)[-1200:],
                )

            out_meta = self._probe(target)
            job_id = uuid.uuid4().hex[:12]
            preview_dir = self.renders_dir / job_id
            preview_dir.mkdir(parents=True, exist_ok=True)
            preview_file = preview_dir / target.name
            shutil.copy2(target, preview_file)
            return {
                "job_id": job_id,
                "output_path": str(target),
                "output_name": target.name,
                "preview_url": f"/media/editor/renders/{job_id}/{target.name}",
                "size_bytes": target.stat().st_size,
                "cut_silence_applied": bool(cut_silence and meta["has_audio"]),
                "captions_burned": bool(burn_captions and segments),
                "segments_used": segments,
                **out_meta,
            }
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @staticmethod
    def _effect_filter(effect: str, speed: float, w: int, h: int, clip_dur: float, transition: str) -> str:
        parts = []
        if abs(speed - 1.0) > 0.01:
            parts.append(f"setpts=PTS/{speed:.3f}")
        parts.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease")
        parts.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x0b1014")
        parts.append("setsar=1")
        parts.append("fps=30")
        if effect == "bw":
            parts.append("hue=s=0")
        elif effect == "warm":
            parts.append("colorbalance=rs=.08:gs=.02:bs=-.08")
        elif effect == "contrast":
            parts.append("eq=contrast=1.18:saturation=1.15")
        elif effect == "vignette":
            parts.append("vignette=PI/4")
        elif effect == "zoom_in":
            parts.append(f"zoompan=z='min(zoom+0.0012,1.15)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps=30")
        if transition == "fade" and clip_dur >= 0.8:
            parts.append("fade=t=in:st=0:d=0.35")
            parts.append(f"fade=t=out:st={max(0.1, clip_dur - 0.35):.3f}:d=0.35")
        parts.append("format=yuv420p")
        return ",".join(parts)

    def render_timeline(self, body: dict[str, Any]) -> dict[str, Any]:
        ffmpeg = self._ffmpeg_path()
        if not ffmpeg:
            raise FlowError("DEPENDENCY_UNAVAILABLE", "ffmpeg.exe não encontrado no sistema.", status=503)

        clips_in = body.get("clips") or []
        if not isinstance(clips_in, list) or not clips_in:
            raise FlowError("SCHEMA_VALIDATION", "Adicione pelo menos 1 clipe na Timeline (V1).", pointer="/clips")

        aspect = str(body.get("aspect") or "9:16")
        w, h = ASPECT_SIZES.get(aspect, (720, 1280))
        overlays_in = body.get("overlays") or []
        audios_in = body.get("audio_tracks") or []
        captions = body.get("captions") or []
        style = body.get("caption_style") or {}

        resolved_clips: list[dict[str, Any]] = []
        for idx, c in enumerate(clips_in):
            src = self._resolve_video_file(str(c.get("path") or ""))
            meta = self._probe(src)
            dur_total = max(0.1, float(meta["duration"] or 1.0))
            s = max(0.0, float(c.get("trim_start") or 0.0))
            e_raw = c.get("trim_end")
            e = min(dur_total, float(e_raw)) if e_raw not in (None, "") else dur_total
            if e <= s:
                raise FlowError("SCHEMA_VALIDATION", f"Clipe #{idx + 1}: fim do corte deve ser maior que início.")
            speed = max(0.5, min(2.0, float(c.get("speed") or 1.0)))
            vol = 0.0 if c.get("mute") else max(0.0, min(2.0, float(c.get("volume") if c.get("volume") is not None else 1.0)))
            eff = str(c.get("effect") or "none")
            trans = str(c.get("transition") or "none")
            out_dur = round((e - s) / speed, 3)
            resolved_clips.append({
                "path": src,
                "meta": meta,
                "start": s,
                "end": e,
                "speed": speed,
                "volume": vol,
                "effect": eff,
                "transition": trans,
                "out_dur": out_dur,
            })

        total_timeline_dur = sum(c["out_dur"] for c in resolved_clips)
        first_src = resolved_clips[0]["path"]
        out_dir_raw = body.get("output_dir")
        out_dir = Path(str(out_dir_raw).strip()).resolve() if out_dir_raw else (first_src.parent / "editados")
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_name = Path(str(body.get("output_name") or f"{first_src.stem}-capcut.mp4")).name
        safe_name = re.sub(r"[^\w.-]+", "-", raw_name).strip(".-_") or "capcut-editado.mp4"
        if not safe_name.lower().endswith(".mp4"):
            safe_name += ".mp4"
        target = out_dir / safe_name
        if target.exists():
            target = out_dir / f"{Path(safe_name).stem}-{uuid.uuid4().hex[:6]}.mp4"

        work_dir = Path(tempfile.mkdtemp(prefix="flowlocal-tl-"))
        try:
            norm_files: list[Path] = []
            for idx, clip in enumerate(resolved_clips):
                seg_out = work_dir / f"v1_{idx:02d}.mp4"
                vf = self._effect_filter(clip["effect"], clip["speed"], w, h, clip["out_dur"], clip["transition"])
                cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{clip['start']:.3f}", "-to", f"{clip['end']:.3f}", "-i", str(clip["path"])]
                if clip["meta"]["has_audio"]:
                    af_parts = []
                    if abs(clip["speed"] - 1.0) > 0.01:
                        af_parts.append(f"atempo={clip['speed']:.3f}")
                    af_parts.append(f"volume={clip['volume']:.3f}")
                    af_parts.append("aformat=sample_rates=44100:channel_layouts=stereo")
                    cmd += ["-vf", vf, "-af", ",".join(af_parts)]
                else:
                    cmd += [
                        "-f", "lavfi", "-t", f"{clip['out_dur']:.3f}", "-i", "anullsrc=r=44100:cl=stereo",
                        "-filter_complex", f"[0:v]{vf}[v]",
                        "-map", "[v]", "-map", "1:a", "-shortest",
                    ]
                cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k", str(seg_out)]
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                if r.returncode != 0 or not seg_out.is_file():
                    raise FlowError("LOCAL_FAILURE", f"FFmpeg falhou ao preparar clipe #{idx + 1}.", provider_message=(r.stderr or "")[-800:])
                norm_files.append(seg_out)

            base_video = work_dir / "base_v1.mp4"
            if len(norm_files) == 1:
                shutil.copy2(norm_files[0], base_video)
            else:
                list_txt = work_dir / "concat.txt"
                list_txt.write_text("\n".join(f"file '{p.as_posix()}'" for p in norm_files), encoding="utf-8")
                r = subprocess.run(
                    [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(list_txt), "-c", "copy", str(base_video)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if r.returncode != 0 or not base_video.is_file():
                    raise FlowError("LOCAL_FAILURE", "FFmpeg falhou ao juntar clipes da timeline.", provider_message=(r.stderr or "")[-800:])

            # Composite Track V2 overlays, Track T1 captions, and Track A1/A2 audio tracks cleanly.
            cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(base_video)]
            filter_lines: list[str] = []
            cur_v = "0:v"
            inp_idx = 1

            pos_coords = {
                "top_right": "W-w-28:28",
                "top_left": "28:28",
                "bottom_right": "W-w-28:H-h-28",
                "bottom_left": "28:H-h-28",
                "center": "(W-w)/2:(H-h)/2",
            }
            for o_idx, ov in enumerate(overlays_in[:6]):
                ov_path, ov_kind = self._resolve_overlay_file(str(ov.get("path") or ""))
                tl_start = max(0.0, float(ov.get("timeline_start") or 0.0))
                ov_dur = max(0.3, float(ov.get("duration") or 3.0))
                scale_pct = max(0.15, min(0.95, float(ov.get("scale") or 0.35)))
                ov_w = max(80, int(round(w * scale_pct / 2)) * 2)
                xy = pos_coords.get(str(ov.get("pos") or "top_right"), pos_coords["top_right"])
                if ov_kind == "image":
                    cmd += ["-loop", "1", "-t", f"{ov_dur:.3f}", "-i", str(ov_path)]
                else:
                    cmd += ["-stream_loop", "-1", "-t", f"{ov_dur:.3f}", "-i", str(ov_path)]
                ov_tag = f"ov{o_idx}"
                out_tag = f"v_ov{o_idx}"
                filter_lines.append(f"[{inp_idx}:v]format=yuva420p,setpts=PTS+{tl_start:.3f}/TB,scale={ov_w}:-2[{ov_tag}]")
                filter_lines.append(f"[{cur_v}][{ov_tag}]overlay={xy}:eof_action=pass:enable='between(t,{tl_start:.3f},{tl_start + ov_dur:.3f})'[{out_tag}]")
                cur_v = out_tag
                inp_idx += 1

            preset = str(style.get("preset") or "clean")
            if captions:
                ass_path = work_dir / "captions.ass"
                font_size = int(style.get("font_size") or (56 if preset == "tiktok_bold" else 50))
                pos_txt = str(style.get("position") or "Inferior")
                col_txt = str(style.get("color") or ("#ffdf81" if preset == "tiktok_bold" else "#ffffff"))
                self._write_ass(ass_path, captions, width=w, height=h, font_size=font_size, position=pos_txt, color=col_txt)
                filter_lines.append(f"[{cur_v}]ass=captions.ass[v_cap]")
                cur_v = "v_cap"

            audio_tags = ["[0:a]"]
            for a_idx, trk in enumerate(audios_in[:6]):
                a_path = self._resolve_audio_file(str(trk.get("path") or ""))
                tl_start = max(0.0, float(trk.get("timeline_start") or 0.0))
                trim_s = max(0.0, float(trk.get("trim_start") or 0.0))
                max_len = max(0.5, total_timeline_dur - tl_start)
                a_dur = min(max_len, max(0.3, float(trk.get("duration") or max_len)))
                vol = max(0.0, min(2.0, float(trk.get("volume") if trk.get("volume") is not None else 0.6)))
                delay_ms = int(round(tl_start * 1000))
                cmd += ["-ss", f"{trim_s:.3f}", "-t", f"{a_dur:.3f}", "-i", str(a_path)]
                a_tag = f"aud{a_idx}"
                fade_out_st = max(0.0, a_dur - 0.5)
                chain = f"[{inp_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={vol:.3f}"
                if a_dur >= 1.2:
                    chain += f",afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out_st:.3f}:d=0.45"
                chain += f",adelay={delay_ms}|{delay_ms}[{a_tag}]"
                filter_lines.append(chain)
                audio_tags.append(f"[{a_tag}]")
                inp_idx += 1

            cur_a = "0:a"
            if len(audio_tags) > 1:
                filter_lines.append(f"{''.join(audio_tags)}amix=inputs={len(audio_tags)}:duration=first:dropout_transition=0:normalize=0[a_mix]")
                cur_a = "a_mix"

            if filter_lines:
                v_map = f"[{cur_v}]" if ":" not in cur_v else cur_v
                a_map = f"[{cur_a}]" if ":" not in cur_a else cur_a
                cmd += ["-filter_complex", ";".join(filter_lines), "-map", v_map, "-map", a_map]
            else:
                cmd += ["-map", "0:v", "-map", "0:a"]

            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(target)]
            ff_res = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if ff_res.returncode != 0 or not target.is_file():
                raise FlowError("LOCAL_FAILURE", "FFmpeg não conseguiu exportar a timeline CapCut.", provider_message=(ff_res.stderr or ff_res.stdout)[-1000:])

            out_meta = self._probe(target)
            job_id = uuid.uuid4().hex[:12]
            preview_dir = self.renders_dir / job_id
            preview_dir.mkdir(parents=True, exist_ok=True)
            preview_file = preview_dir / target.name
            shutil.copy2(target, preview_file)
            return {
                "job_id": job_id,
                "output_path": str(target),
                "output_name": target.name,
                "preview_url": f"/media/editor/renders/{job_id}/{target.name}",
                "size_bytes": target.stat().st_size,
                "clips_count": len(resolved_clips),
                "overlays_count": len(overlays_in),
                "audio_tracks_count": len(audios_in),
                "captions_burned": bool(captions),
                **out_meta,
            }
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
