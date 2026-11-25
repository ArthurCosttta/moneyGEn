from __future__ import annotations

import uuid


class FlowError(Exception):
    """Typed local API failure. Provider text is carried separately."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        pointer: str | None = None,
        retry_safe: bool = False,
        execution_id: str | None = None,
        provider_message: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.pointer = pointer
        self.retry_safe = retry_safe
        self.execution_id = execution_id
        self.provider_message = provider_message
        self.correlation_id = correlation_id or str(uuid.uuid4())

    def payload(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "pointer": self.pointer,
                "retry_safe": self.retry_safe,
                "execution_id": self.execution_id,
                "correlation_id": self.correlation_id,
                "provider_message": self.provider_message,
            }
        }
