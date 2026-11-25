from __future__ import annotations

from .errors import FlowError


READ_RETRY_DELAYS = (1.0, 2.0)


def classify(exc: Exception, *, possible_effect: bool = False) -> str:
    if isinstance(exc, FlowError):
        if exc.code in {"SCHEMA_VALIDATION", "INVALID_JSON", "INVALID_IMAGE", "INVALID_OUTPUT_NAME"}:
            return "VALIDATION"
        if exc.code in {"ACCOUNT_MISMATCH", "SPEND_NOT_CONFIRMED", "NO_SPEND_ACTIVE"}:
            return "AUTHORIZATION"
        if exc.code in {"REFUSED"}:
            return "REFUSED"
        if exc.code in {"OUTPUT_EXISTS", "INVALID_ARTIFACT", "STORAGE"}:
            return "STORAGE"
        if exc.code in {"UNCERTAIN_EFFECT"} or possible_effect:
            return "UNCERTAIN_EFFECT"
    return "UNCERTAIN_EFFECT" if possible_effect else "PROVIDER_FAILURE"


def reconcile_without_unique_operation() -> None:
    raise FlowError(
        "UNCERTAIN_EFFECT",
        "Não há identificador remoto unívoco. A pendência foi mantida; uma nova tentativa pode cobrar em duplicidade.",
        status=409,
    )
