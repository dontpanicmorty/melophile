"""The window, off screen: every page opens, background work runs, and duplicates can be decided."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from conftest import make_audio  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from melophile import db, scan  # noqa: E402
from melophile.ui import style  # noqa: E402
from melophile.ui.app import PAGES, MainWindow  # noqa: E402
from melophile.ui.pages import suggested_keep  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    style.apply(a)
    return a


@pytest.fixture(autouse=True)
def quiet_dialogs(monkeypatch):
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


def _wait(app, win):
    while win.busy:
        app.processEvents()
        if win.task:
            win.task.wait(50)
    app.processEvents()


def test_welcome_then_every_page(app, config):
    config.library_roots = []
    win = MainWindow(config)
    assert win.stack.currentWidget() is win.welcome
    for cls in PAGES:
        win.show_page(cls.title)
        app.processEvents()
        assert win.stack.currentWidget() is win.pages[cls.title]
    win.close()


def test_scan_and_decide_a_duplicate_in_the_window(app, config, tmp_path, no_trash):
    lib = tmp_path / "library"
    a = make_audio(lib / "one" / "song.mp3")
    (lib / "two").mkdir()
    (lib / "two" / "song.mp3").write_bytes(a.read_bytes())
    config.save()
    win = MainWindow(config)
    win.pages["Library"].fp_box.setChecked(False)
    win.start_scan()
    _wait(app, win)
    assert "2 songs" in win.pages["Library"].results.toPlainText()
    win.show_page("Duplicates")
    page = win.pages["Duplicates"]
    page.acoustic.setChecked(False)
    page.find()
    _wait(app, win)
    win.show_page("Duplicates")
    assert page.group is not None and page.members.rowCount() == 2
    page.trash()  # confirmed by the patched dialog
    assert len(no_trash) == 1
    assert page.group is None
    win.close()


def test_only_one_task_at_a_time(app, config):
    import threading

    gate = threading.Event()
    win = MainWindow(config)
    assert win.run_task("Waiting", lambda c, cfg, p: gate.wait(5))
    assert not win.run_task("Another", lambda c, cfg, p: None)
    gate.set()
    _wait(app, win)
    win.close()


def test_stop_ends_a_task(app, config):
    import time

    def slow(c, cfg, p):
        for _ in range(200):
            p.check()
            time.sleep(0.05)

    win = MainWindow(config)
    results = []
    win.run_task("Slow", slow, results.append)
    win.stop_task()
    _wait(app, win)
    assert results == [{"stopped": True}]
    win.close()


def test_suggested_keep_prefers_quality():
    rows = [{"id": 1, "format": "mp3", "bitrate": 320000, "size_bytes": 9},
            {"id": 2, "format": "flac", "bitrate": 900000, "size_bytes": 30},
            {"id": 3, "format": "mp3", "bitrate": 128000, "size_bytes": 4}]
    assert suggested_keep(rows) == 2
    assert suggested_keep([r for r in rows if r["id"] != 2]) == 1
