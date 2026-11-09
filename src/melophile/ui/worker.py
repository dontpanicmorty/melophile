"""Runs long work (scanning, finding duplicates, converting) off the interface thread, so the window stays
responsive, shows progress, and can be stopped. One task at a time; each opens its own catalog connection."""
from __future__ import annotations

import threading
import traceback
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

from .. import db
from ..config import Config
from ..progress import Cancelled, Progress


class Task(QThread):
    progressed = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str, str)  # message, details

    def __init__(self, fn: Callable, config: Config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.fn = fn
        self.config = config
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:
        conn = None
        try:
            conn = db.connect(self.config.db_path)
            progress = Progress(lambda d, t, m: self.progressed.emit(d, t, m), self._stop.is_set)
            self.succeeded.emit(self.fn(conn, self.config, progress))
        except Cancelled:
            self.succeeded.emit({"stopped": True})
        except Exception as exc:
            self.failed.emit(str(exc) or type(exc).__name__, traceback.format_exc())
        finally:
            if conn is not None:
                conn.close()
