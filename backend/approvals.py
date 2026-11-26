from __future__ import annotations

from .budget import validate_budget
from .errors import FlowError


class ApprovalService:
    def __init__(self, repository, *, no_spend: bool) -> None:
        self.repository = repository
        self.no_spend = no_spend

    def start(self, payload: dict, scope: str) -> dict:
        if self.no_spend:
            raise FlowError("NO_SPEND_ACTIVE", "A trava sem gastos está ativa.", status=403)
        required = {"batch_id", "expected_revision", "plan_hash", "idempotency_key", "item_ids", "confirmed", "risk", "reviewed", "unknown_cost_accepted"}
        if not required <= payload.keys():
            raise FlowError("SPEND_NOT_CONFIRMED", "Faltam campos da confirmação.", status=403)
        if payload["confirmed"] is not True or payload["risk"] is not True:
            raise FlowError("SPEND_NOT_CONFIRMED", "Confirme o gasto e o risco na interface.", status=403)
        batch = self.repository.get_batch(payload["batch_id"])
        plan = self.repository.get_plan(batch["id"], payload["plan_hash"])
        validate_budget(batch, plan["quote"], unknown_cost_accepted=payload["unknown_cost_accepted"] is True)
        items = self.repository.all_items(batch["id"])
        item_map = {x["id"]: x for x in items}
        ids = payload["item_ids"]
        if scope == "TEST":
            if not isinstance(ids, list) or len(ids) != 1 or ids[0] not in item_map:
                raise FlowError("SCHEMA_VALIDATION", "O teste autoriza exatamente um item.")
            if any(x["state"] in ("SUBMITTING", "PROVIDER_PENDING", "SUCCEEDED") for x in items):
                raise FlowError("PLAN_STALE", "O teste já foi iniciado.", status=409)
        elif scope == "REMAINDER":
            if payload["reviewed"] is not True:
                raise FlowError("SPEND_NOT_CONFIRMED", "Assista e aprove o vídeo de teste.", status=403)
            succeeded = [x for x in items if x["state"] == "SUCCEEDED"]
            if not succeeded:
                raise FlowError("PLAN_STALE", "O vídeo de teste válido não está disponível.", status=409)
            # A aprovação dos restantes autoriza somente itens ainda não
            # enviados. Refazer uma falha/refusa exige um novo plano e consentimento.
            remaining = [x["id"] for x in items if x["state"] in ("VALIDATED", "READY")]
            if ids != remaining:
                raise FlowError("PLAN_STALE", "A lista dos restantes mudou.", status=409)
        else:
            raise FlowError("SCHEMA_VALIDATION", "Escopo de aprovação inválido.")
        return self.repository.create_approval_execution(payload, scope=scope, item_id=ids[0])

    def start_direct_queue(self, payload: dict) -> dict:
        if self.no_spend:
            raise FlowError("NO_SPEND_ACTIVE", "A trava sem gastos está ativa.", status=403)
        required = {"batch_id", "expected_revision", "plan_hash", "idempotency_key", "item_ids", "confirmed"}
        if not required <= payload.keys():
            raise FlowError("SPEND_NOT_CONFIRMED", "Faltam campos da confirmação.", status=403)
        if payload["confirmed"] is not True:
            raise FlowError("SPEND_NOT_CONFIRMED", "Confirme o envio da fila na interface.", status=403)
        batch = self.repository.get_batch(payload["batch_id"])
        plan = self.repository.get_plan(batch["id"], payload["plan_hash"])
        validate_budget(batch, plan["quote"], unknown_cost_accepted=bool(payload.get("unknown_cost_accepted", True)))
        items = self.repository.all_items(batch["id"])
        ready = [x["id"] for x in items if x["state"] in ("VALIDATED", "READY")]
        ids = payload["item_ids"]
        if not isinstance(ids, list) or not ids or ids != ready:
            raise FlowError("PLAN_STALE", "A lista de itens da fila mudou.", status=409)
        enriched = {
            **payload,
            "direct": True,
            "risk": bool(payload.get("risk", True)),
            "reviewed": True,
            "unknown_cost_accepted": bool(payload.get("unknown_cost_accepted", True)),
        }
        return self.repository.create_approval_execution(enriched, scope="REMAINDER", item_id=ids[0])
