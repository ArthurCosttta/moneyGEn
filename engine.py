"""Local state machine. No spending operation is reachable through the planner."""
from __future__ import annotations

import base64
import copy
import csv
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from manifests import ManifestError, local_path, match_asset, normalize_manifest

REPOSITORY = "https://github.com/ffroliva/gflow-cli"
VERSION = "0.103.1"
APP_VERSION = "2.0.0"
ACCOUNT = "arthurcostacopy@gmail.com"
PROFILE = "arthur"
MODELS = {"omni-1.2": "Omni 1.2 Flash", "veo-fast": "Veo 3.1 Fast", "veo-lite": "Veo 3.1 Lite"}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


class AppError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def parse_json(text):
    """Rich/structlog may surround the one CLI result with other messages."""
    decoder = json.JSONDecoder()
    clean = ANSI.sub("", text)
    results = []
    for match in re.finditer(r"\{", clean):
        try:
            value, _ = decoder.raw_decode(clean[match.start():])
            if isinstance(value, dict):
                results.append(value)
        except ValueError:
            pass
    for value in reversed(results):
        if "status" in value or "credits" in value or "succeeded" in value:
            return value
    raise AppError("O CLI não devolveu o JSON esperado. Consulte o log original.")


def image_extension(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise AppError("Use imagens PNG, JPEG, WebP ou GIF válidas.")


def mp4_exists(path):
    try:
        with path.open("rb") as f:
            header = f.read(32)
        return path.stat().st_size > 32 and header[4:8] == b"ftyp"
    except OSError:
        return False


class CLI:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.runtime = self.root / ".runtime"
        self.work = self.runtime / "work"
        self.work.mkdir(parents=True, exist_ok=True)

    @property
    def executable(self):
        return self.runtime / "bin" / ("gflow.exe" if os.name == "nt" else "gflow")

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GFLOW_", "UV_"))}
        env.update({
            "UV_TOOL_DIR": str(self.runtime / "tools"),
            "UV_TOOL_BIN_DIR": str(self.runtime / "bin"),
            "UV_PYTHON_INSTALL_DIR": str(self.runtime / "python"),
            "UV_CACHE_DIR": str(self.runtime / "cache"),
            "UV_NO_MODIFY_PATH": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
            "GFLOW_CLI_HOME": str(self.root / "dados" / "flow"),
            "GFLOW_CLI_OUTPUT_DIR": str(self.root / "saida"),
            "GFLOW_CLI_STORAGE_URI": "", "GFLOW_CLI_TELEMETRY": "0",
            "GFLOW_CLI_UPDATE_CHECK": "0", "GFLOW_CLI_HEADLESS": "false",
            "GFLOW_CLI_TRANSPORT": "ui_automation", "GFLOW_CLI_LOG_FORMAT": "json",
            "GFLOW_CLI_LOG_LEVEL": "INFO", "GFLOW_CLI_AUTH_LOGIN_TIMEOUT": "900",
            "GFLOW_CLI_CONCURRENCY": "1", "GFLOW_MCP_NO_SPEND": "1",
            "NO_COLOR": "1", "COLUMNS": "240", "DO_NOT_TRACK": "1",
        })
        return env

    def _run(self, args, timeout):
        try:
            result = subprocess.run(
                [str(x) for x in args], cwd=self.work, env=self.env(),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                shell=False,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as exc:
            def decode(value):
                return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")
            return 124, decode(exc.stdout), decode(exc.stderr) + "\nTempo limite. Resultado incerto; não repetir automaticamente."
        except OSError as exc:
            raise AppError(f"Não foi possível executar o CLI: {exc}. Use Instalar/Atualizar.") from exc

    def call(self, args, timeout=1800):
        if not self.executable.is_file():
            raise AppError("gflow-cli ainda não está instalado. Use Instalar/Atualizar.")
        return self._run([self.executable, *args], timeout)

    def install(self):
        uv = self.runtime / "uv" / ("uv.exe" if os.name == "nt" else "uv")
        if not uv.is_file():
            raise AppError("Abra INICIAR.bat para instalar o uv primeiro.")
        code, out, err = self._run([uv, "tool", "install", "--force", "--python", "3.12", f"gflow-cli=={VERSION}"], 1200)
        if code:
            raise AppError(out + err)
        python = self.runtime / "tools" / "gflow-cli" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        code, out2, err2 = self._run([python, "-m", "playwright", "install", "chromium"], 1200)
        if code:
            raise AppError(out2 + err2)
        code, version, err = self.call(["--version"], 30)
        if code or VERSION not in version:
            raise AppError("Versão do CLI incompatível: " + version + err)
        return f"gflow-cli {VERSION} instalado/atualizado a partir do pacote oficial no PyPI.\n" + out + out2

    def credits(self):
        code, out, err = self.call(["credits", "user", "--profile", PROFILE, "--json"], 180)
        if code:
            raise AppError(out + err)
        value = parse_json(out)
        if code or value.get("status") != "ok":
            raise AppError(out + err)
        email = value.get("email")
        if not isinstance(email, str) or email.lower() != ACCOUNT.lower():
            raise AppError(f"A conta ativa ({email or 'não identificada'}) não é {ACCOUNT}. Entre novamente com a conta indicada.")
        credits = value.get("credits")
        if not isinstance(credits, (int, float)) or isinstance(credits, bool):
            raise AppError("Saldo não confirmado pelo Flow. Entre novamente.")
        return {"credits": credits, "email": email, "checked_at": now()}


class Engine:
    def __init__(self, root, cli=None, no_spend=False):
        self.root = Path(root).resolve()
        self.data = self.root / "dados"
        self.output = self.root / "saida"
        config = self.data / "config.json"
        if config.exists():
            try:
                self.output = local_path(json.loads(config.read_text(encoding="utf-8"))["output_dir"]).resolve()
            except (ValueError, KeyError, OSError):
                pass
        for path in (self.data / "imagens", self.data / "lotes", self.output):
            path.mkdir(parents=True, exist_ok=True)
        self.cli = cli or CLI(self.root)
        self.no_spend = no_spend
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.worker = None
        self.busy = False
        self.state = {"batch": None, "credits": None, "message": "Tudo pronto. Adicione imagens e faça a simulação.", "task": None}
        path = self.data / "estado.json"
        if path.exists():
            try:
                self.state = json.loads(path.read_text(encoding="utf-8"))
                self.state["task"] = None
                batch = self.state.get("batch")
                if batch and "output_dir" not in batch:
                    batch.update(output_dir=str(self.root / "saida" / batch["id"]), report_dir=str(self.root / "saida" / batch["id"]), output_exact=False)
                if batch and batch["phase"] in ("uploading", "testing", "running"):
                    batch["phase"] = "interrupted"
                    for row in batch["rows"]:
                        if row["status"] in ("enviando", "gerando"):
                            row["status"] = "incerto"
                    self.state["message"] = "Execução interrompida. Verifique o Flow e os MP4s antes de criar outro lote; nenhuma geração será repetida automaticamente."
                self.save()
            except (ValueError, KeyError, OSError) as exc:
                raise AppError("Estado local ilegível; preserve dados/estado.json e procure suporte.") from exc

    def save(self):
        atomic_json(self.data / "estado.json", self.state)
        batch = self.state.get("batch")
        if batch:
            folder = self.report_folder(batch)
            folder.mkdir(parents=True, exist_ok=True)
            atomic_json(folder / "relatorio.json", batch)
            atomic_json(self.data / "lotes" / f"{batch['id']}.json", batch)
            temp = folder / "relatorio.csv.tmp"
            fields = ["index", "name", "prompt", "status", "credits_before", "credits_after", "credits_observed", "charged", "path", "error", "media_id", "project"]
            with temp.open("w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                for row in batch["rows"]:
                    # CSV formula injection protection; authoritative JSON preserves exact text.
                    safe = {k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v) for k, v in row.items()}
                    writer.writerow(safe)
            temp.replace(folder / "relatorio.csv")

    def snapshot(self):
        with self.lock:
            value = copy.deepcopy(self.state)
            value.update(busy=self.busy, no_spend=self.no_spend, repository=REPOSITORY, version=VERSION, app_version=APP_VERSION, account=ACCOUNT, output=str(self.output), installed=self.cli.executable.is_file())
            return value

    def idle(self):
        if self.busy:
            raise AppError("Aguarde a operação atual terminar.")

    def stage(self, name, content):
        with self.lock:
            self.idle()
            if not isinstance(name, str) or not name or len(name) > 240:
                raise AppError("Nome de imagem inválido.")
            try:
                data = base64.b64decode(content, validate=True)
            except (ValueError, TypeError) as exc:
                raise AppError("Imagem inválida.") from exc
            if not 0 < len(data) <= 20 * 1024 * 1024:
                raise AppError("Cada imagem deve ter até 20 MiB.")
            ext = image_extension(data)
            image_id = secrets.token_hex(16)
            path = self.data / "imagens" / (image_id + ext)
            path.write_bytes(data)
            meta = {"id": image_id, "name": name, "file": path.name, "sha256": hashlib.sha256(data).hexdigest()}
            atomic_json(path.with_suffix(".json"), meta)
            return meta

    def report_folder(self, batch):
        return Path(batch.get("report_dir", str(self.root / "saida" / batch["id"])))

    def video_folder(self, batch):
        return Path(batch.get("output_dir", str(self.root / "saida" / batch["id"])))

    def output_folder(self, value):
        path = local_path(value)
        if not path.is_absolute():
            raise AppError("A pasta de saída precisa de um caminho absoluto, como C:\\Videos\\Flow.")
        path = path.resolve()
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise AppError("O destino precisa ser uma pasta.")
        try:
            with tempfile.TemporaryFile(dir=path):
                pass
        except OSError as exc:
            raise AppError("Sem permissão de escrita na pasta de saída: " + str(path)) from exc
        return path

    @staticmethod
    def validate_settings(model, aspect="9:16", duration=6, resolution=None):
        if model not in MODELS:
            raise AppError("Modelo não permitido.")
        if aspect not in ("9:16", "16:9"):
            raise AppError("Escolha o formato 9:16 ou 16:9.")
        allowed = range(3, 16) if model == "omni-1.2" else (4, 6, 8)
        if duration is not None and (type(duration) is not int or duration not in allowed):
            raise AppError("Duração indisponível para este modelo. Escolha uma opção da lista.")
        if resolution not in (None, "360p", "720p") or (resolution and model != "omni-1.2"):
            raise AppError("A resolução explícita 360p/720p é oferecida aqui apenas para Omni 1.2. Para Veo, escolha Padrão do Flow.")

    @staticmethod
    def validate_video_name(value):
        if not isinstance(value, str) or not value.lower().endswith(".mp4") or len(value) > 140:
            raise AppError("O nome de saída deve terminar em .mp4 e ter até 140 caracteres.")
        if re.search(r'[\\/:*?"<>|\x00-\x1f]', value) or value[-1:] in (".", " ") or value.startswith("."):
            raise AppError("Use somente um nome de arquivo MP4, sem caminho ou caracteres especiais do Windows.")
        if value.split(".")[0].upper() in ("CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))):
            raise AppError("Nome de vídeo reservado pelo Windows.")
        return value

    def import_documents(self, documents, assets=None, images_dir=None):
        """Read ONLY user-specified local images. Never call Flow or create a paid plan."""
        with self.lock:
            self.idle()
            if not isinstance(documents, list) or not 1 <= len(documents) <= 20:
                raise AppError("Selecione entre 1 e 20 arquivos JSON.")
            assets = assets or []
            if not isinstance(assets, list) or len(assets) > 200:
                raise AppError("Lista de imagens inválida.")
            base_override = local_path(images_dir).resolve(strict=True) if images_dir else None
            resolved, settings, names = [], {}, []
            for doc in documents:
                rows, options = normalize_manifest(doc.get("document"))
                source_name = doc.get("name", "JSON colado")
                names.append(source_name)
                for key in ("model", "aspect", "duration", "resolution", "output_dir"):
                    if key in options:
                        if key in settings and settings[key] != options[key]:
                            raise AppError(f"Os JSONs têm configurações diferentes em '{key}'. Importe-os separadamente ou use os mesmos valores.")
                        settings[key] = options[key]
                # manifest_dir is supplied ONLY by the native-picker server route.
                base = base_override
                if base is None and options.get("images_dir"):
                    hint = local_path(options["images_dir"])
                    if not hint.is_absolute() and doc.get("manifest_dir"):
                        hint = local_path(doc["manifest_dir"]) / hint
                    if hint.is_absolute():
                        base = hint.resolve(strict=True)
                if base is None and doc.get("manifest_dir"):
                    base = local_path(doc["manifest_dir"]).resolve(strict=True)
                for row in rows:
                    asset = match_asset(row["image"], assets)
                    if asset:
                        image_id = asset.get("id", "")
                        if not isinstance(image_id, str) or not re.fullmatch(r"[a-f0-9]{32}", image_id):
                            raise AppError("Uma imagem selecionada tem referência inválida.")
                        meta_path = self.data / "imagens" / f"{image_id}.json"
                        if not meta_path.is_file():
                            raise AppError("Selecione novamente a imagem: " + row["image"])
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        resolved.append({"asset": meta, "row": row})
                    else:
                        path = local_path(row["image"])
                        if not path.is_absolute():
                            if base is None:
                                raise AppError("Falta localizar a imagem '" + row["image"] + "'. Selecione a pasta das imagens ou arraste as imagens junto com o JSON. Seus prompts continuam no JSON importado.")
                            path = base / path
                        try:
                            path = path.resolve(strict=True)
                        except OSError as exc:
                            raise AppError("Imagem não encontrada: " + row["image"]) from exc
                        if base is not None and not path.is_relative_to(base):
                            raise AppError("A imagem está fora da pasta escolhida: " + row["image"])
                        if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
                            raise AppError("Imagem inválida ou maior que 20 MiB: " + row["image"])
                        with path.open("rb") as image:
                            image_extension(image.read(16))
                        resolved.append({"path": path, "row": row})
            if len(resolved) > 50:
                raise AppError("O total dos JSONs deve ter até 50 takes.")
            self.validate_settings(settings.get("model", "omni-1.2"), settings.get("aspect", "9:16"), settings.get("duration", 6), settings.get("resolution"))
            for item in resolved:
                if item["row"]["output_name"] is not None:
                    self.validate_video_name(item["row"]["output_name"])
            results = []
            assigned_ids = set()
            for item in resolved:
                meta = item.get("asset")
                if meta is None:
                    path = item["path"]
                    meta = self.stage(path.name, base64.b64encode(path.read_bytes()).decode("ascii"))
                elif meta["id"] in assigned_ids:
                    image_path = self.staged_image_path(meta["id"])
                    meta = self.stage(meta["name"], base64.b64encode(image_path.read_bytes()).decode("ascii"))
                assigned_ids.add(meta["id"])
                results.append({"id": meta["id"], "name": meta["name"], "prompt": item["row"]["prompt"], "output_name": item["row"]["output_name"]})
            return {"items": results, "settings": settings, "sources": names}

    def plan(self, items, model, cap=None, aspect="9:16", duration=6, resolution=None, output_dir=None):
        with self.lock:
            self.idle()
            self.validate_settings(model, aspect, duration, resolution)
            if model == "veo-fast" and datetime.now(timezone.utc).date().isoformat() >= "2026-12-05":
                raise AppError("Veo 3.1 Fast está marcado como aposentado em 05/12/2026 pelo projeto. Selecione Omni 1.2 ou Veo Lite.")
            if cap is not None:
                raise AppError("Esta versão publicada do CLI não oferece um teto de créditos para i2v. A cotação não é garantida.")
            if not isinstance(items, list) or not 1 <= len(items) <= 50:
                raise AppError("Adicione entre 1 e 50 imagens.")
            rows, used = [], set()
            for index, item in enumerate(items, 1):
                image_id, prompt = item.get("id", ""), item.get("prompt")
                if not re.fullmatch(r"[a-f0-9]{32}", image_id) or image_id in used:
                    raise AppError("Referência de imagem inválida ou duplicada.")
                if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000 or "\x00" in prompt:
                    raise AppError(f"Preencha o prompt do take {index} (até 12.000 caracteres, sem NUL).")
                used.add(image_id)
                meta_paths = list((self.data / "imagens").glob(image_id + ".json"))
                if not meta_paths:
                    raise AppError("Imagem não encontrada; selecione-a novamente.")
                meta = json.loads(meta_paths[0].read_text(encoding="utf-8"))
                path = self.data / "imagens" / meta["file"]
                if hashlib.sha256(path.read_bytes()).hexdigest() != meta["sha256"]:
                    raise AppError("A imagem mudou no disco; selecione-a novamente.")
                if item.get("output_name"):
                    self.validate_video_name(item["output_name"])
                rows.append({"index": index, "id": image_id, "name": meta["name"], "file": meta["file"], "sha256": meta["sha256"], "prompt": prompt, "requested_output_name": item.get("output_name"), "output_name": item.get("output_name"), "status": "planejado", "project": None, "media_id": None, "path": None, "error": None, "credits_observed": None, "credits_before": None, "credits_after": None, "charged": "no"})
            batch_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
            exact = output_dir is not None
            destination = self.output_folder(output_dir) if exact else self.output / batch_id
            report_dir = destination / ("_flowlocal-" + batch_id) if exact else destination
            output_names = set()
            for row in rows:
                if row["output_name"] is None:
                    stem = unicodedata.normalize("NFKD", Path(row["name"]).stem).encode("ascii", "ignore").decode()
                    stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", stem).strip("-")[:45] or "take"
                    row["output_name"] = f"{row['index']:03d}-{stem}-{batch_id}.mp4"
                if row["output_name"].casefold() in output_names:
                    raise AppError("Dois takes usam o mesmo nome de MP4. Corrija 'video' no JSON.")
                if (destination / row["output_name"]).exists():
                    raise AppError("O vídeo já existe e não será sobrescrito: " + str(destination / row["output_name"]))
                output_names.add(row["output_name"].casefold())
            batch = {"id": batch_id, "phase": "planned", "created_at": now(), "model": model, "aspect": aspect, "duration": duration, "resolution": resolution, "count": 1, "max_credits": cap, "output_dir": str(destination), "report_dir": str(report_dir), "output_exact": exact, "rows": rows, "test_index": None, "uploads_completed": False, "test_approved": False, "approvals": [], "events": [{"time": now(), "message": "Dry-run local concluído. Nenhuma chamada ao Google; nenhum crédito gasto."}]}
            batch["account"] = ACCOUNT
            if exact:
                self.output = destination
                atomic_json(self.data / "config.json", {"output_dir": str(destination)})
            self.state.update(batch=batch, message="Simulação pronta. Revise o plano antes de confirmar o take de teste.")
            self.save()
            return copy.deepcopy(batch)

    def event(self, message):
        with self.lock:
            self.state["message"] = message
            batch = self.state.get("batch")
            if batch:
                batch["events"].append({"time": now(), "message": message})
            self.save()

    def launch(self, name, fn):
        with self.lock:
            self.idle()
            self.busy = True
            self.stop.clear()
            self.state["task"] = name
            self.save()

            def execute():
                try:
                    fn()
                except Exception as exc:
                    with self.lock:
                        batch = self.state.get("batch")
                        if batch and batch["phase"] in ("uploading", "testing", "running"):
                            batch["phase"] = "interrupted"
                        self.event(str(exc))
                finally:
                    with self.lock:
                        self.busy = False
                        self.state["task"] = None
                        self.save()
            self.worker = threading.Thread(target=execute, daemon=True)
            self.worker.start()

    def check_credits(self):
        value = self.cli.credits()
        with self.lock:
            self.state["credits"] = value
            self.save()
        return value["credits"]

    def preflight(self, settings):
        checks = []
        try:
            self.validate_settings(settings.get("model", "omni-1.2"), settings.get("aspect", "9:16"), settings.get("duration", 6), settings.get("resolution"))
            code, out, err = self.cli.call(["--version"], 30)
            if code:
                raise AppError(out + err)
            checks.append("CLI instalado: " + out.strip())
            code, out, err = self.cli.call(["models", "show", settings.get("model", "omni-1.2"), "--json"], 30)
            if code:
                raise AppError(out + err)
            catalog = json.loads(ANSI.sub("", out))
            if settings.get("duration", 6) is not None and settings.get("duration", 6) not in catalog.get("durations", []):
                raise AppError("A duração escolhida não é aceita pelo modelo no CLI instalado.")
            checks.append("Modelo e duração reconhecidos pelo CLI.")
            self.output_folder(settings.get("output_dir") or str(self.output))
            checks.append("Pasta de saída com permissão de escrita.")
            balance = self.check_credits()
            checks.append(f"Conta correta e saldo consultado: {balance} créditos.")
            result = {"ready": not self.no_spend, "checks": checks, "error": None, "time": now(), "note": "Verificação sem geração. A disponibilidade efetiva dos controles e modelos no Flow só é confirmada no take real."}
        except Exception as exc:
            result = {"ready": False, "checks": checks, "error": str(exc), "time": now()}
        with self.lock:
            self.state["preflight"] = result
            self.event("Verificação concluída sem gastar créditos." if result["ready"] else "Verificação encontrou um impedimento: " + (result["error"] or "trava --no-spend ativa"))

    def staged_image_path(self, image_id):
        if not isinstance(image_id, str) or not re.fullmatch(r"[a-f0-9]{32}", image_id):
            raise AppError("Imagem inválida.")
        meta_path = self.data / "imagens" / f"{image_id}.json"
        if not meta_path.is_file():
            raise AppError("Imagem não encontrada.")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        path = (self.data / "imagens" / meta["file"]).resolve()
        if not path.is_relative_to((self.data / "imagens").resolve()) or not path.is_file():
            raise AppError("Imagem não encontrada.")
        return path

    def login(self):
        code, _, _ = self.cli.call(["auth", "login", "--browser", "chrome", "--profile", PROFILE, "--account", ACCOUNT], 960)
        if code:
            raise AppError("O login no Chrome não foi concluído com a conta indicada. Tente novamente e finalize as etapas no Google Flow. A saída de autenticação não é salva nos logs.")
        self.check_credits()
        self.event("Login confirmado pelo Flow para " + ACCOUNT)

    def begin(self, mode, batch_id, confirmed, risk, reviewed=False):
        with self.lock:
            self.idle()
            if self.no_spend:
                raise AppError("Trava --no-spend ativa. Gerações pagas estão bloqueadas.")
            batch = self.state.get("batch")
            if not batch or batch["id"] != batch_id:
                raise AppError("O plano mudou. Faça a simulação novamente.")
            if confirmed is not True or risk is not True:
                raise AppError("Confirme o gasto de créditos e aceite o aviso antes de gerar.")
            if mode == "test" and batch["phase"] not in ("planned", "test_failed"):
                raise AppError("Este teste já foi iniciado. Nenhuma repetição automática será feita.")
            if mode == "test" and batch["phase"] == "test_failed" and not any(r["status"] == "enviado" for r in batch["rows"]):
                raise AppError("Não há outro take pendente para testar. Confira as recusas e os relatórios.")
            test_row = next((r for r in batch["rows"] if r["index"] == batch.get("test_index")), None)
            if mode == "batch" and (batch["phase"] != "review" or reviewed is not True or not test_row or test_row["status"] != "concluido"):
                raise AppError("Primeiro gere, assista e aprove o take de teste.")
            if mode not in ("test", "batch"):
                raise AppError("Ação inválida.")
            if mode == "batch":
                first_path = self.video_folder(batch) / test_row["path"]
                if not mp4_exists(first_path):
                    raise AppError("MP4 do teste não está disponível. Verifique a pasta de saída.")
            batch["approvals"].append({"time": now(), "mode": mode, "credit_spend_confirmed": True, "risk_accepted": True, "test_reviewed": reviewed is True})
            batch["test_approved"] = mode == "batch"
            batch["phase"] = ("testing" if batch.get("uploads_completed") else "uploading") if mode == "test" else "running"
            self.launch("Enviando imagens e gerando teste" if mode == "test" else "Gerando lote", lambda: self.run_test() if mode == "test" else self.run_rest())

    def raw_log(self, row, label, code, stdout, stderr):
        batch = self.state["batch"]
        path = self.report_folder(batch) / f"{row['index']:03d}-{label}.log"
        path.write_text(f"exit_code={code}\n--- stdout (original) ---\n{stdout}\n--- stderr (original) ---\n{stderr}", encoding="utf-8")
        return path.name

    def update_row(self, row, **fields):
        with self.lock:
            row.update(fields)
            self.save()

    def cancelled(self):
        if not self.stop.is_set():
            return False
        with self.lock:
            batch = self.state["batch"]
            batch["phase"] = "cancelled"
            for row in batch["rows"]:
                if row["status"] in ("planejado", "enviado"):
                    row["status"] = "cancelado"
            self.event("Execução parada entre operações. Nenhuma geração em andamento foi repetida.")
        return True

    def upload_all(self):
        rows = self.state["batch"]["rows"]
        for row in rows:
            if self.cancelled():
                return False
            self.update_row(row, status="enviando")
            self.event(f"Enviando imagem {row['index']}/{len(rows)}: {row['name']}")
            try:
                path = self.data / "imagens" / row["file"]
                if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                    raise AppError("A imagem foi alterada depois da simulação.")
                code, out, err = self.cli.call(["image", "upload", str(path), "--profile", PROFILE], 600)
                self.raw_log(row, "upload", code, out, err)
                clean = ANSI.sub("", out)
                media = re.search(r"Asset UUID:\s*(" + UUID + r")", clean)
                project = re.findall(r"Project:\s*([a-zA-Z0-9-]{1,128})", clean)
                if code or not media or not project:
                    raise AppError(out + err if code else "Upload não confirmado. Consulte o log original.")
                self.update_row(row, status="enviado", media_id=media.group(1), project=project[-1])
            except Exception as exc:
                self.update_row(row, status="falha_upload", error=str(exc))
        with self.lock:
            self.state["batch"]["uploads_completed"] = True
            self.event("Fase de uploads encerrada para TODAS as imagens. Iniciando a fase de vídeos.")
        return True

    def generate(self, row):
        batch = self.state["batch"]
        if row["status"] != "enviado":
            return
        target = self.video_folder(batch) / row.get("output_name", f"{row['index']:03d}-{Path(row['file']).stem[:12]}.mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.update_row(row, status="gerando", charged="no")
        self.event(f"Gerando take {row['index']}: {row['name']} (uma única tentativa).")
        try:
            if target.exists():
                raise AppError("O MP4 de destino já existe. Ele não será sobrescrito nem a geração repetida: " + str(target))
            # Recheck the exact account before every billed operation.
            before = self.check_credits()
            self.update_row(row, credits_before=before, charged="no")
            if self.stop.is_set():
                self.update_row(row, status="cancelado")
                return
            image_path = self.data / "imagens" / row["file"]
            if hashlib.sha256(image_path.read_bytes()).hexdigest() != row["sha256"]:
                raise AppError("A imagem mudou depois do upload. Nenhum vídeo foi solicitado para este take.")
            # On flow.google.com UUID i2v frames exit 36; the supported local
            # frame path can re-upload while attaching. All initial uploads
            # still finish before the first video call (docs/USAGE.md #639).
            args = ["video", "i2v", "--initial-frame", str(image_path), "--project", row["project"], "--profile", PROFILE, "--model", batch["model"], "--aspect", batch["aspect"], "--count", "1", "--out-file", str(target), "--json"]
            if batch["duration"] is not None:
                args.extend(["--duration", str(batch["duration"])])
            if batch.get("resolution") is not None:
                args.extend(["--resolution", batch["resolution"]])
            # '--' keeps prompts beginning with '-' literal, with whitespace untouched.
            args.extend(["--", row["prompt"]])
            self.update_row(row, charged="maybe", submitted_at=now())
            code, out, err = self.cli.call(args)
            self.raw_log(row, "geracao", code, out, err)
            try:
                payload = parse_json(out)
            except AppError:
                payload = {}
            error_info = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            self.update_row(row, cli_result=payload, exit_code=code, charged=payload.get("charged", error_info.get("charged", "yes" if payload.get("succeeded") is True else "maybe")))
            if code or payload.get("succeeded") is not True:
                # Keep Flow's exact reason (and original streams separately), no rewrite/retry.
                reasons = payload.get("failure_reasons") or []
                reason = payload.get("error_message") or payload.get("error") or payload.get("detail")
                if isinstance(reason, (dict, list)):
                    reason = json.dumps(reason, ensure_ascii=False)
                message = "\n".join(str(x) for x in reasons) or reason or out + err
                self.update_row(row, status="recusado_ou_falhou", error=message)
            elif not mp4_exists(target):
                self.update_row(row, status="sem_mp4", error="Flow informou sucesso, mas o MP4 local não foi confirmado. Consulte o Flow; não há repetição automática.")
            else:
                self.update_row(row, status="concluido", path=target.name, completed_at=now())
        except Exception as exc:
            self.update_row(row, status="incerto", error=str(exc))
        finally:
            try:
                after = self.check_credits()
                before = row.get("credits_before")
                difference = before - after if before is not None and before >= after else None
                self.update_row(row, credits_after=after, credits_observed=difference)
            except Exception as exc:
                self.update_row(row, credits_note="Saldo posterior indisponível: " + str(exc))
        self.event(f"Take {row['index']}: {row['status']}.")

    def run_test(self):
        self.check_credits()  # Do not upload private images under the wrong account.
        batch = self.state["batch"]
        if not batch.get("uploads_completed") and not self.upload_all():
            return
        if self.cancelled():
            return
        with self.lock:
            batch["phase"] = "testing"
            self.save()
        candidate = next((r for r in batch["rows"] if r["status"] == "enviado"), None)
        if candidate:
            self.generate(candidate)
        if self.cancelled():
            return
        with self.lock:
            success = candidate and candidate["status"] == "concluido"
            batch["phase"] = "review" if success else "test_failed"
            batch["test_index"] = candidate["index"] if success else None
            self.event("Teste pronto. Assista ao MP4 e confirme o restante do lote." if success else "Teste não concluído. Veja a recusa original; você pode autorizar UM teste com o próximo take pendente, sem repetir o anterior.")

    def run_rest(self):
        batch = self.state["batch"]
        for row in batch["rows"]:
            if self.cancelled():
                return
            if row["status"] == "enviado":
                self.generate(row)  # A refusal never stops the following takes.
        if self.cancelled():
            return
        with self.lock:
            batch["phase"] = "completed"
            self.event("Lote finalizado. Os MP4s e relatórios estão na pasta de saída; recusas e falhas constam por take.")

    def request_stop(self):
        with self.lock:
            self.stop.set()
            self.event("Parada solicitada. Aguardando a operação atual terminar para preservar seu resultado.")

    def export_path(self, batch_id, name):
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-f0-9]{8}", batch_id):
            raise AppError("Lote inválido.")
        if Path(name).name != name or re.search(r'[\\/:*?"<>|\x00-\x1f]', name) or name.startswith("."):
            raise AppError("Arquivo inválido.")
        stored = self.data / "lotes" / f"{batch_id}.json"
        if not stored.is_file():
            raise AppError("Lote não encontrado.")
        batch = json.loads(stored.read_text(encoding="utf-8"))
        videos = {r["path"] for r in batch["rows"] if r.get("path")}
        if name in videos:
            base = self.video_folder(batch).resolve()
        else:
            base = self.report_folder(batch).resolve()
        path = (base / name).resolve()
        if not path.is_relative_to(base) or not path.is_file() or path.suffix.lower() not in (".mp4", ".json", ".csv", ".log"):
            raise AppError("Arquivo não disponível.")
        return path
