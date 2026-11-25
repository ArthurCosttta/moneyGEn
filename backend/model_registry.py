from __future__ import annotations

from datetime import datetime, timezone

from .errors import FlowError


CANDIDATES = {
    "omni-1.2": {"label": "Omni 1.2 Flash", "modes": ["i2v", "t2v"], "aspects": ["9:16", "16:9", "1:1"], "durations": list(range(3, 16)), "resolutions": [None, "360p", "720p"]},
    "veo-fast": {"label": "Veo 3.1 Fast", "modes": ["i2v", "t2v"], "aspects": ["9:16", "16:9"], "durations": [4, 6, 8], "resolutions": [None]},
    "veo-lite": {"label": "Veo 3.1 Lite", "modes": ["i2v", "t2v"], "aspects": ["9:16", "16:9"], "durations": [4, 6, 8], "resolutions": [None]},
}


class ModelRegistry:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self._models: dict[str, dict] = {
            model_id: {"provider_id": "google-flow", "model_id": model_id, **facts, "available": False, "evidence": "Não inspecionado nesta execução.", "checked_at": None}
            for model_id, facts in CANDIDATES.items()
        }

    def inspect(self) -> list[dict]:
        try:
            observed = self.adapter.inspect_capabilities()
        except Exception as exc:
            observed = {"models": [], "error": str(exc)}
        enabled = set(observed.get("models", []))
        checked = datetime.now(timezone.utc).isoformat()
        for model_id, value in self._models.items():
            value["available"] = model_id in enabled
            value["evidence"] = observed.get("evidence") or observed.get("error") or "Capability discovery do adaptador."
            value["checked_at"] = checked
            value["verification"] = observed.get("verification", "UNVERIFIED")
        return self.list()

    def list(self) -> list[dict]:
        return [dict(value) for value in self._models.values()]

    def validate(self, settings: dict, kind: str) -> None:
        model_id = settings.get("model_id")
        model = self._models.get(model_id)
        if not model or not model.get("available"):
            reason = model.get("evidence") if model else "Modelo desconhecido."
            raise FlowError("MODEL_UNAVAILABLE", f"{model_id or 'Modelo'} indisponível: {reason}", status=422)
        if kind not in model["modes"]:
            raise FlowError("SETTINGS_UNSUPPORTED", "O modelo não oferece este modo.", status=422)
        if settings.get("aspect") not in model["aspects"]:
            raise FlowError("SETTINGS_UNSUPPORTED", "Formato não comprovado para o modelo.", status=422)
        duration = settings.get("duration_seconds")
        if duration is not None and duration not in model["durations"]:
            raise FlowError("SETTINGS_UNSUPPORTED", "Duração não comprovada para o modelo.", status=422)
        if settings.get("resolution") not in model["resolutions"]:
            raise FlowError("SETTINGS_UNSUPPORTED", "Resolução não comprovada para o modelo.", status=422)
