"""Parse local batch manifests; never fetch a URL or invoke a generation."""
from pathlib import Path, PureWindowsPath
import re


class ManifestError(ValueError):
    pass


def local_path(value):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ManifestError("Informe um caminho local válido.")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value) or value.startswith(("\\\\", "//")):
        raise ManifestError("Use arquivos e pastas do PC; links e caminhos de rede não são aceitos.")
    return Path(value).expanduser()


def first(item, keys):
    for key in keys:
        if key in item:
            return item[key]
    return None


def normalize_manifest(document):
    if isinstance(document, list):
        rows, settings = document, {}
    elif isinstance(document, dict):
        rows = first(document, ("takes", "videos", "items", "prompts"))
        settings = {k: document[k] for k in ("model", "aspect", "duration", "resolution", "output_dir", "images_dir") if k in document}
    else:
        raise ManifestError("O JSON deve ser uma lista ou um objeto com a lista 'takes'.")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
        raise ManifestError("O JSON precisa conter entre 1 e 50 takes.")
    result = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ManifestError(f"Take {index}: use um objeto com image e prompt.")
        image = first(row, ("image", "imagem", "image_path", "ref", "arquivo"))
        prompt = first(row, ("prompt", "video_prompt", "prompt_video", "Prompt Video", "text"))
        if not isinstance(image, str) or not image.strip():
            raise ManifestError(f"Take {index}: falta o caminho/nome da imagem em 'image'.")
        local_path(image)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000 or "\x00" in prompt:
            raise ManifestError(f"Take {index}: prompt ausente ou inválido (até 12.000 caracteres).")
        output = first(row, ("video", "output_name", "output_filename"))
        result.append({"image": image, "prompt": prompt, "output_name": output})
    aliases = {"omni 1.2 flash": "omni-1.2", "omni-1.2-flash": "omni-1.2", "veo 3.1 fast": "veo-fast", "veo 3.1 lite": "veo-lite"}
    if isinstance(settings.get("model"), str):
        settings["model"] = aliases.get(settings["model"].lower(), settings["model"])
    if settings.get("duration") in ("auto", "default"):
        settings["duration"] = None
    if settings.get("resolution") in ("auto", "default"):
        settings["resolution"] = None
    return result, settings


def match_asset(reference, assets):
    """Prefer an exact relative path, then a unique basename; never guess a duplicate."""
    ref = reference.replace("\\", "/").removeprefix("./").casefold()
    exact = [a for a in assets if (candidate := a.get("relative_path", a["name"]).replace("\\", "/").casefold()) == ref or candidate.endswith("/" + ref)]
    if len(exact) == 1:
        return exact[0]
    name = PureWindowsPath(reference).name.casefold()
    candidates = [a for a in assets if a["name"].casefold() == name]
    if len(candidates) > 1:
        raise ManifestError(f"Há mais de uma imagem chamada '{name}'. Use o caminho relativo completo no JSON.")
    return candidates[0] if candidates else None
