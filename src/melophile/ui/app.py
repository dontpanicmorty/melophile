"""Melophile's window: a sidebar of pages, the page itself, and a task bar for work in progress."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__, db, scan
from ..config import Config
from ..paths import ASSETS_DIR
from . import style
from .pages import ConvertPage, DuplicatesPage, LibraryPage, OrganizePage, SettingsPage, TagsPage, WelcomePage
from .worker import Task

APP_NAME = "Melophile"
PAGES = (LibraryPage, DuplicatesPage, TagsPage, OrganizePage, ConvertPage, SettingsPage)


class MainWindow(QMainWindow):
    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.config = config or Config.load()
        self.config.ensure_dirs()
        self.conn = db.connect(self.config.db_path)
        db.requeue_interrupted_jobs(self.conn)
        self.task: Task | None = None
        self.settings_fp = True
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 820)
        self.setMinimumSize(900, 620)

        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(210)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 8, 0, 10)
        brand = QLabel(APP_NAME)
        brand.setObjectName("brand")
        tag = QLabel("for a tidy music library")
        tag.setObjectName("tagline")
        sv.addWidget(brand)
        sv.addWidget(tag)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        sv.addWidget(self.nav, 1)
        ver = QLabel(f"Version {__version__}")
        ver.setObjectName("muted")
        ver.setContentsMargins(18, 0, 0, 0)
        sv.addWidget(ver)

        self.stack = QStackedWidget()
        self.welcome = WelcomePage(self)
        self.stack.addWidget(self.welcome)
        self.pages = {}
        for cls in PAGES:
            page = cls(self)
            self.pages[cls.title] = page
            self.stack.addWidget(page)
            self.nav.addItem(cls.title)
        self.nav.currentRowChanged.connect(self._nav)

        # the task bar: what's running, how far along, and a way to stop it
        self.taskbar = QWidget()
        tb = QHBoxLayout(self.taskbar)
        tb.setContentsMargins(20, 8, 20, 10)
        self.task_label = QLabel("")
        self.task_detail = QLabel("")
        self.task_detail.setObjectName("muted")
        self.task_detail.setMinimumWidth(100)
        self.bar = QProgressBar()
        self.bar.setMaximumWidth(260)
        self.bar.setTextVisible(False)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_task)
        tb.addWidget(self.task_label)
        tb.addWidget(self.bar)
        tb.addWidget(self.task_detail, 1)
        tb.addWidget(self.stop_btn)
        self.taskbar.hide()

        main = QWidget()
        mv = QVBoxLayout(main)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(0)
        mv.addWidget(self.stack, 1)
        mv.addWidget(self.taskbar)
        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(side)
        h.addWidget(main, 1)
        self.setCentralWidget(root)

        for i in range(len(PAGES)):
            a = QAction(self)
            a.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            a.triggered.connect(lambda _=False, i=i: self.nav.setCurrentRow(i))
            self.addAction(a)
        if self.config.library_roots:
            self.show_page("Library")
        else:
            self.stack.setCurrentWidget(self.welcome)

    # ---- navigation
    def _nav(self, i: int) -> None:
        if 0 <= i < len(PAGES):
            page = self.pages[PAGES[i].title]
            self.stack.setCurrentWidget(page)
            page.refresh()

    def show_page(self, title: str) -> None:
        i = [c.title for c in PAGES].index(title)
        if self.nav.currentRow() == i:
            self._nav(i)
        else:
            self.nav.setCurrentRow(i)

    def refresh_all(self) -> None:
        current = self.stack.currentWidget()
        if hasattr(current, "refresh"):
            current.refresh()

    # ---- settings
    def save_config(self) -> None:
        self.config.save()
        self.refresh_all()

    def add_root(self, folder: str) -> None:
        key = scan.root_key(folder)
        if key not in self.config.library_roots:
            self.config.library_roots.append(key)
            self.save_config()

    def remove_root(self, folder: str) -> None:
        if folder in self.config.library_roots:
            self.config.library_roots.remove(folder)
            self.save_config()

    # ---- background work
    def start_scan(self) -> None:
        lib = self.pages["Library"]
        self.settings_fp = lib.fp_box.isChecked() or not lib.fp_box.isEnabled()
        fp = lib.fp_box.isChecked()
        self.run_task("Scanning", lambda c, cfg, p: scan.scan_all(c, cfg, fp, p), lib.show_results)

    def run_task(self, label: str, fn, on_done=None) -> bool:
        if self.task is not None:
            QMessageBox.information(self, APP_NAME, f"{self.task_label.text()} is still running. Stop it or "
                                    "wait for it to finish first.")
            return False
        self.task = Task(fn, self.config, self)
        self.task_label.setText(label)
        self.task_detail.setText("")
        self.bar.setRange(0, 0)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setText("Stop")
        self.taskbar.show()
        self.task.progressed.connect(self._progress)
        self.task.succeeded.connect(lambda r: self._finished(r, on_done))
        self.task.failed.connect(self._failed)
        self.task.start()
        return True

    @property
    def busy(self) -> bool:
        return self.task is not None

    def _progress(self, done: int, total: int, msg: str) -> None:
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.task_detail.setText(f"{done:,} of {total:,}  {self._short(msg)}")
        else:
            self.bar.setRange(0, 0)
            self.task_detail.setText(f"{done:,}  {self._short(msg)}" if done else self._short(msg))

    @staticmethod
    def _short(msg: str, n: int = 80) -> str:
        return msg if len(msg) <= n else "…" + msg[-n:]

    def stop_task(self) -> None:
        if self.task:
            self.task.stop()
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("Stopping…")

    def _end(self) -> None:
        if self.task:
            self.task.wait()
            self.task.deleteLater()
        self.task = None
        self.taskbar.hide()
        self.refresh_all()

    def _finished(self, result, on_done) -> None:
        self._end()
        if on_done:
            on_done(result)

    def _failed(self, message: str, details: str) -> None:
        self._end()
        box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME, message, parent=self)
        box.setDetailedText(details)
        box.exec()

    def closeEvent(self, event) -> None:
        if self.task:
            if QMessageBox.question(self, APP_NAME, "Work is still running. Stop it and quit? Everything done so "
                                    "far is kept.") != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.task.stop()
            self.task.wait(15000)
        self.conn.close()
        event.accept()


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    icon = ASSETS_DIR / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    style.apply(app)
    win = MainWindow()
    win.show()
    win.raise_()
    return app.exec()
