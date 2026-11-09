"""How long-running work reports progress and is stopped. The app passes one in; scripts and tests needn't."""
from __future__ import annotations

from typing import Callable


class Cancelled(Exception):
    """Raised inside long work when the person pressed Stop. Work done so far is kept."""


class Progress:
    def __init__(self, on_update: Callable[[int, int, str], None] | None = None,
                 should_stop: Callable[[], bool] | None = None) -> None:
        self._on_update = on_update
        self._should_stop = should_stop

    def update(self, done: int, total: int, message: str = "") -> None:
        """total 0 means 'unknown' (shown as a busy bar)."""
        if self._on_update:
            self._on_update(done, total, message)

    def stopping(self) -> bool:
        return bool(self._should_stop and self._should_stop())

    def check(self) -> None:
        if self.stopping():
            raise Cancelled()


SILENT = Progress()
