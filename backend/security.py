from __future__ import annotations

import secrets


def valid_loopback_host(host: str | None, port: int) -> bool:
    return host in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


def valid_origin(origin: str | None, expected: str) -> bool:
    return origin is None or secrets.compare_digest(origin, expected)


def redact(value: str) -> tuple[str, bool]:
    if not value:
        return value, False
    redacted = value
    changed = False
    for marker in ("authorization:", "cookie:", "access_token", "refresh_token"):
        if marker.casefold() in redacted.casefold():
            redacted = "[mensagem sanitizada: credencial removida]"
            changed = True
            break
    return redacted, changed
