from __future__ import annotations

from datetime import datetime, timezone

from .errors import FlowError


def unknown_quote(account: str) -> dict:
    return {"known": False, "test_credits": None, "remaining_credits": None, "total_credits": None, "source": None, "checked_at": datetime.now(timezone.utc).isoformat(), "account": account, "scope": "batch"}


def validate_budget(batch: dict, quote: dict, *, unknown_cost_accepted: bool) -> None:
    cap = batch.get("max_credits")
    if cap is not None:
        total = quote.get("total_credits")
        if total is None:
            raise FlowError("BUDGET_UNVERIFIABLE", "Há um teto de créditos, mas o custo não pôde ser cotado.", status=422)
        if total > cap:
            raise FlowError("BUDGET_UNVERIFIABLE", "A cotação excede o teto de créditos do lote.", status=422)
    if not quote.get("known") and not unknown_cost_accepted:
        raise FlowError("SPEND_NOT_CONFIRMED", "Confirme explicitamente que o custo não foi cotado.", status=403)
