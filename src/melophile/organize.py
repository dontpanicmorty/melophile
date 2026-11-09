"""Sorting files into Artist/Album folders.

1. Plan: work out where each song belongs, from its tags, without moving anything.
2. Review the planned moves, and apply the ones you want.
3. Undo any applied move later: the file goes back where it was.

Careful by design:
- files the Music app manages itself (inside its "Media" folder) are never moved: the Music app would lose
  track of them. Let the Music app keep those organized (Settings › Files › Keep Music Media folder organized);
- nothing is overwritten: a name that's taken gets " (2)" and so on;
- a file that changed place since the plan isn't moved;
- folders left empty by a move are removed, but never a music folder itself.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path

from . import db
from .backends import number_pair
from .config import Config
from .progress import SILENT, Progress
from .review import unique_target
from .rules import feat_unify
from .scan import root_key

PATTERNS = {
    "artist_album_track": "Artist / Album / 01 Title",
    "artist_album": "Artist / Album / original file name",
}
MANAGED_MARKERS = ("/Music/Media.localized/", "/Music/Media/", "/iTunes/iTunes Media/", "/iTunes/iTunes Music/",
                   "/iTunes Media/", "/iTunes Music/")
MAX_NAME = 120
_BAD = re.compile(r'[/:\\\x00-\x1f]')


class OrganizeError(RuntimeError):
    pass


def managed_by_music_app(path: str) -> bool:
    return any(m in path for m in MANAGED_MARKERS)


def safe_name(text: str | None, fallback: str) -> str:
    """A folder or file name that's valid on macOS: no slashes or colons, no leading dot, not too long."""
    name = _BAD.sub("-", (text or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name.lstrip(".")
    if len(name) > MAX_NAME:
        name = name[:MAX_NAME].rstrip(" .")
    return name or fallback


def target_for(path: str, tags: dict, dest_root: Path, pattern: str) -> Path:
    src = Path(path)
    artist = tags.get("album_artist") or tags.get("artist")
    if artist and not tags.get("album_artist"):  # "A feat. B" sorts under A
        main = {c.field: c.new_value for c in feat_unify({"artist": artist, "title": ""})}
        artist = main.get("artist", artist)
    folder = dest_root / safe_name(artist, "Unknown Artist") / safe_name(tags.get("album"), "Unknown Album")
    if pattern == "artist_album":
        return folder / src.name
    title = safe_name(tags.get("title"), src.stem)
    track = number_pair(tags.get("track_number"))
    disc = number_pair(tags.get("disc_number"))
    prefix = ""
    if track:
        prefix = f"{track[0]:02d} "
        if disc and (disc[0] > 1 or disc[1] > 1):
            prefix = f"{disc[0]}-{prefix}"
    return folder / f"{prefix}{title}{src.suffix.lower()}"


def plan(conn: sqlite3.Connection, config: Config, dest_root: str, pattern: str = "artist_album_track",
         progress: Progress = SILENT) -> dict:
    """Replaces any earlier unapplied plan with a new one. Only files under the music folders are planned, and
    only into one of them, so the next scan still finds every file."""
    if pattern not in PATTERNS:
        raise OrganizeError(f"Unknown pattern {pattern!r}")
    dest = Path(root_key(dest_root))
    roots = [Path(root_key(r)) for r in config.library_roots]
    if dest not in roots and not any(r in dest.parents for r in roots):
        raise OrganizeError("Choose one of your music folders (or a folder inside one) to sort into.")
    conn.execute("DELETE FROM organize_moves WHERE status = 'planned'")
    taken: set[str] = set()
    planned = skipped_managed = already = 0
    files = db.get_active_files(conn)
    for n, f in enumerate(files, 1):
        if n % 200 == 0:
            progress.update(n, len(files), "Planning")
            progress.check()
        path = f["path"]
        if not any(Path(root_key(r)) in Path(path).parents for r in config.library_roots):
            continue
        if managed_by_music_app(path):
            skipped_managed += 1
            continue
        tags = db.get_tags(conn, f["id"]) or {}
        target = target_for(path, tags, dest, pattern)
        if str(target) == path:
            already += 1
            continue
        stem, i = target, 2
        while str(target).casefold() in taken or (target.exists() and str(target) != path):
            target = stem.with_name(f"{stem.stem} ({i}){stem.suffix}")
            i += 1
        taken.add(str(target).casefold())
        conn.execute("INSERT INTO organize_moves (file_id, old_path, new_path, status, planned_at) "
                     "VALUES (?, ?, ?, 'planned', ?)", (f["id"], path, str(target), db.now()))
        planned += 1
    conn.commit()
    return {"planned": planned, "already_in_place": already, "skipped_music_app": skipped_managed}


def _prune_empty(folder: Path, stop_at: list[Path]) -> None:
    """Removes now-empty folders (a lone .DS_Store counts as empty), up to but never including a music folder."""
    while folder not in stop_at and any(r in folder.parents for r in stop_at):
        try:
            entries = list(folder.iterdir())
        except OSError:
            return
        if any(e.name != ".DS_Store" for e in entries):
            return
        for e in entries:
            e.unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def _move(conn: sqlite3.Connection, move: sqlite3.Row, src: str, dst: str, roots: list[Path]) -> str:
    f = db.get_file(conn, move["file_id"])
    if f is None or f["status"] != "active" or f["path"] != src or not Path(src).exists():
        raise OrganizeError("The file isn't where it was planned from any more")
    target = unique_target(Path(dst))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, target)
    conn.execute("UPDATE files SET path = ? WHERE id = ?", (str(target), move["file_id"]))
    _prune_empty(Path(src).parent, roots)
    return str(target)


def apply_moves(conn: sqlite3.Connection, config: Config, move_ids: list[int], progress: Progress = SILENT) -> dict:
    roots = [Path(root_key(r)) for r in config.library_roots]
    done, errors = 0, []
    for n, mid in enumerate(sorted(move_ids), 1):
        progress.update(n, len(move_ids), "Moving files")
        if progress.stopping():
            break
        m = db.get_move(conn, mid)
        if m is None or m["status"] != "planned":
            continue
        try:
            final = _move(conn, m, m["old_path"], m["new_path"], roots)
            conn.execute("UPDATE organize_moves SET status='applied', new_path=?, applied_at=? WHERE id=?",
                         (final, db.now(), mid))
            done += 1
        except Exception as exc:
            conn.execute("UPDATE organize_moves SET status='failed', error_message=? WHERE id=?", (str(exc), mid))
            errors.append(f"{Path(m['old_path']).name}: {exc}")
        conn.commit()
    return {"moved": done, "errors": errors}


def undo_moves(conn: sqlite3.Connection, config: Config, move_ids: list[int], progress: Progress = SILENT) -> dict:
    """Moves files back, newest first. A file is only moved back if its old place is still free."""
    roots = [Path(root_key(r)) for r in config.library_roots]
    done, errors = 0, []
    for n, mid in enumerate(sorted(move_ids, reverse=True), 1):
        progress.update(n, len(move_ids), "Moving files back")
        if progress.stopping():
            break
        m = db.get_move(conn, mid)
        if m is None or m["status"] != "applied":
            continue
        try:
            if Path(m["old_path"]).exists():
                raise OrganizeError("Something else is in its old place now")
            _move(conn, m, m["new_path"], m["old_path"], roots)
            conn.execute("UPDATE organize_moves SET status='reverted' WHERE id=?", (mid,))
            done += 1
        except Exception as exc:
            errors.append(f"{Path(m['new_path']).name}: {exc}")
        conn.commit()
    return {"moved_back": done, "errors": errors}
