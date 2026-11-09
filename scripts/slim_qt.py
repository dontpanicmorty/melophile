"""Removes the parts of Qt that Melophile never loads from a built app's Python (about 150 MB).

Melophile uses QtCore, QtGui and QtWidgets. PySide6 also ships QML/Quick, Designer, translations, developer
tools and headers. Kept: exactly the Qt frameworks those three modules and the kept plugins link to, found
with otool. The build runs the self-test afterwards, which opens every page, so a missing piece fails it.

    python scripts/slim_qt.py <site-packages of the bundled Python>
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

KEEP_MODULES = {"QtCore", "QtGui", "QtWidgets"}
KEEP_PLUGINS = {"platforms", "styles", "imageformats", "iconengines"}


def _size(p: Path) -> int:
    if p.is_symlink():
        return 0
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())
    return p.stat().st_size


def _drop(p: Path, freed: list[int]) -> None:
    if p.exists() or p.is_symlink():
        freed[0] += _size(p)
        shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()


def _links(binary: Path) -> set[str]:
    out = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True).stdout
    return set(re.findall(r"(Qt\w+)\.framework", out))


def main(site: Path) -> int:
    ps = site / "PySide6"
    qt = ps / "Qt"
    freed = [0]
    for p in ps.iterdir():  # Designer, Linguist, Assistant and other tools
        if p.suffix == ".app" or (p.is_file() and not p.suffix and p.name != "py.typed"):
            _drop(p, freed)
    for name in ("include", "typesystems", "glue", "doc", "scripts"):
        _drop(ps / name, freed)
    for name in ("qml", "translations", "metatypes", "libexec"):
        _drop(qt / name, freed)
    for d in (qt / "plugins").iterdir():
        if d.name not in KEEP_PLUGINS:
            _drop(d, freed)
    for f in (qt / "plugins" / "imageformats").glob("*qpdf*"):
        _drop(f, freed)
    for f in ps.glob("Qt*.*"):
        if f.name.split(".")[0] not in KEEP_MODULES and f.suffix in (".so", ".pyi"):
            _drop(f, freed)
    for f in ps.glob("libpyside6qml*"):
        _drop(f, freed)

    roots = [ps / f"{m}.abi3.so" for m in KEEP_MODULES] + list(ps.glob("libpyside6.abi3.*.dylib"))
    roots += [f for d in KEEP_PLUGINS for f in (qt / "plugins" / d).glob("*.dylib")]
    need: set[str] = set()
    for r in roots:
        need |= _links(r)
    todo = set(need)
    while todo:
        name = todo.pop()
        for dep in _links(qt / "lib" / f"{name}.framework" / "Versions" / "A" / name) - need:
            need.add(dep)
            todo.add(dep)
    for fw in (qt / "lib").glob("*.framework"):
        if fw.stem not in need:
            _drop(fw, freed)
    print(f"slimmed Qt: freed {freed[0] / 1e6:.0f} MB; kept {', '.join(sorted(need))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
