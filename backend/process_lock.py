from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .errors import FlowError


class ProcessLock:
    def __init__(self, installation: Path, data_dir: Path) -> None:
        self.installation = installation.resolve()
        self.data_dir = data_dir.resolve()
        self.handle = None
        self.fd = None

    def acquire(self) -> None:
        digest = hashlib.sha256(str(self.installation).casefold().encode()).hexdigest()[:24]
        if os.name == "nt":
            import ctypes
            handle = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\FlowLocal-{digest}")
            if not handle or ctypes.windll.kernel32.GetLastError() == 183:
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                raise FlowError("PROFILE_BUSY", "Esta instalação do Flow Local já está aberta.", status=409)
            self.handle = handle
        else:
            import fcntl
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.fd = os.open(self.data_dir / "app.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(self.fd)
                self.fd = None
                raise FlowError("PROFILE_BUSY", "Esta instalação do Flow Local já está aberta.", status=409) from exc

    def release(self) -> None:
        if self.handle is not None:
            import ctypes
            ctypes.windll.kernel32.ReleaseMutex(self.handle)
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()
