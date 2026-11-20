"""Dedicated append-only file log; never writes to the configuration DB."""
import json
import os
from pathlib import Path
from threading import Lock

from .contracts import VerificationError


class FileAuditSink:
    def __init__(self, path=None):
        self.path = Path(path or Path(__file__).resolve().parents[3] / '.local/verification/audit.jsonl')
        self._lock = Lock()

    def record(self, value):
        try:
            data = (json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, 'ab') as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
        except Exception:
            raise VerificationError('audit-unavailable') from None
