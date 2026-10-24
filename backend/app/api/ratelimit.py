"""Ограничение попыток входа: защита от подбора пароля.

Счётчики неудач живут в памяти процесса app: он один, а после перезапуска счёт начинается заново —
для подбора это всё равно слишком медленно. Ограничение двойное: по логину и по адресу клиента.
"""

import math
import threading
import time
from collections import deque


class LoginLimiter:
    def __init__(
        self, max_per_login: int = 5, max_per_address: int = 20, window_seconds: float = 15 * 60
    ) -> None:
        self.max_per_login = max_per_login
        self.max_per_address = max_per_address
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        attempts = self._failures.setdefault(key, deque())
        while attempts and now - attempts[0] >= self.window:
            attempts.popleft()
        return attempts

    def retry_after(self, login: str, address: str) -> int | None:
        """Сколько секунд ждать, если попыток слишком много; None — можно пробовать."""
        now = time.monotonic()
        with self._lock:
            waits = []
            for key, limit in (
                (f"login:{login}", self.max_per_login),
                (f"ip:{address}", self.max_per_address),
            ):
                attempts = self._recent(key, now)
                if len(attempts) >= limit:
                    waits.append(self.window - (now - attempts[0]))
            return math.ceil(max(waits)) if waits else None

    def failed(self, login: str, address: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in (f"login:{login}", f"ip:{address}"):
                self._recent(key, now).append(now)

    def succeeded(self, login: str) -> None:
        with self._lock:
            self._failures.pop(f"login:{login}", None)
