"""Process-local serialization; checkpoint is the sole durable-context source."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json


def session_key(profile_id: str, session_id: str) -> str:
    encoded = json.dumps([profile_id, session_id], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "b-session-" + hashlib.sha256(encoded).hexdigest()


@dataclass
class _LockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class SessionLocks:
    """Remove idle locks; waiters keep the same lock alive, even on cancellation."""

    def __init__(self):
        self._entries: dict[str, _LockEntry] = {}

    @asynccontextmanager
    async def hold(self, key: str):
        entry = self._entries.setdefault(key, _LockEntry())
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0:
                self._entries.pop(key, None)
