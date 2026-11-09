"""`melophile selftest`: proves a copy of Melophile works end to end, on a throwaway library it makes itself.
Never touches the real settings, catalog or music, never contacts the Music app, and makes no network requests."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _tone(path: Path, freq: int, ffmpeg: str, tags: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=6"]
    for k, v in tags.items():
        cmd += ["-metadata", f"{k}={v}"]
    subprocess.run([*cmd, str(path)], check=True, timeout=60)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="melophile-selftest-"))
    os.environ["MELOPHILE_HOME"] = str(work / "home")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from . import __version__, convert, db, dedupe, normalize, organize, scan, tools
    from .config import Config
    from .ui import style
    from .ui.app import PAGES, MainWindow

    import Foundation  # noqa: F401  (the Trash)
    import ScriptingBridge  # noqa: F401  (the Music app)

    print(f"melophile   {__version__}")
    print(f"python      {sys.version.split()[0]}  ({sys.executable})")
    ffmpeg = tools.ffmpeg_path()
    print(f"ffmpeg      {ffmpeg}")
    fpcalc = tools.find_fpcalc()
    print(f"fpcalc      {fpcalc or 'not found'}")
    if os.environ.get("MELOPHILE_PACKAGED") == "1":
        assert fpcalc, "the packaged app should include fpcalc"

    lib = work / "library"
    _tone(lib / "a" / "song.mp3", 440, ffmpeg, {"artist": "Someone feat. Other", "title": "Song (Official Video)"})
    _tone(lib / "b" / "song.flac", 440, ffmpeg, {"artist": "Someone", "title": "Song"})
    (lib / "c").mkdir()
    (lib / "c" / "copy.mp3").write_bytes((lib / "a" / "song.mp3").read_bytes())
    config = Config(library_roots=[str(lib)], db_path=work / "home" / "catalog.db",
                    quarantine_dir=work / "quarantine", config_path=work / "home" / "config.toml")
    config.save()
    conn = db.connect(config.db_path)

    r = scan.scan_all(conn, config, fingerprints=bool(fpcalc))
    assert r["roots"][0]["files_new"] == 3, r
    if fpcalc:
        assert r["fingerprints"]["fingerprinted"] == 3, r["fingerprints"]
    d = dedupe.run_dedupe(conn, config)
    assert d["exact_groups"] == 1, d
    n = normalize.propose_changes(conn, "file")
    assert n >= 2, n
    ids = [e["id"] for e in db.get_normalizations(conn, "proposed")]
    assert not normalize.apply_many(conn, ids)["errors"]
    assert not normalize.revert_many(conn, [e["id"] for e in db.get_normalizations(conn, "applied")])["errors"]
    o = organize.plan(conn, config, str(lib))
    assert o["planned"] == 3, o
    moves = [m["id"] for m in db.get_moves(conn, "planned")]
    assert organize.apply_moves(conn, config, moves)["moved"] == 3
    assert organize.undo_moves(conn, config, moves)["moved_back"] == 3
    assert convert.enqueue(conn, "flac") == 1
    c = convert.run_queue(conn, config)
    assert c["succeeded"] == 1, db.recent_conversion_jobs(conn)[0]["error_message"]
    conn.close()

    app = QApplication.instance() or QApplication([])
    style.apply(app)
    win = MainWindow(Config.load(config.config_path))
    for cls in PAGES:
        win.show_page(cls.title)
        app.processEvents()
    win.close()
    print(f"scan, {'fingerprints, ' if fpcalc else ''}duplicates, tag cleanup, organizing, conversion and every page: ok")
    print("selftest ok")
    return 0
