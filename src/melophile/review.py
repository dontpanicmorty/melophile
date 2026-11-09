"""Acting on duplicates, and moving files aside.

Nothing is ever deleted outright: 'quarantine' moves files into the quarantine folder (keeping their original
folder structure, so it's clear where each came from), and 'trash' moves them to the macOS Trash, from which
Finder can put them back.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from . import db
from .trash import move_to_trash

ACTIONS = {
    "keep_one_quarantine_rest": "Keep the chosen file, quarantine the rest",
    "keep_one_trash_rest": "Keep the chosen file, move the rest to the Trash",
    "keep_all": "Keep them all",
    "not_duplicates": "These aren't duplicates",
}


class ReviewError(RuntimeError):
    pass


def resolve_duplicate_group(conn: sqlite3.Connection, quarantine_dir: Path, group_id: int, action: str,
                            kept_file_id: int | None = None, confirm_trash: bool = False) -> dict:
    """Applies a decision. The file to keep must be a member of the group, and moving files to the Trash
    needs confirm_trash=True as well, so neither a slip in the interface nor a script can lose every copy."""
    if action not in ACTIONS:
        raise ReviewError(f"Unknown action: {action}")
    group = db.get_group(conn, group_id)
    if group is None:
        raise ReviewError(f"There's no duplicate group {group_id}.")
    if group["status"] != "pending_review":
        raise ReviewError(f"Group {group_id} was already decided.")
    if action in ("keep_all", "not_duplicates"):
        db.record_duplicate_decision(conn, group_id, action, None)
        conn.commit()
        return {"moved": []}
    members = db.get_group_members(conn, group_id)
    if kept_file_id not in {m["id"] for m in members}:
        raise ReviewError("Choose which file to keep: it must be one of the files in this group.")
    if action == "keep_one_trash_rest" and not confirm_trash:
        raise ReviewError("Moving files to the Trash needs confirmation.")
    kept = next(m for m in members if m["id"] == kept_file_id)
    if kept["status"] != "active" or not Path(kept["path"]).exists():
        raise ReviewError(f"The file to keep isn't there any more: {kept['path']}")
    moved = []
    for m in members:
        if m["id"] == kept_file_id or m["status"] != "active":
            continue
        if action == "keep_one_quarantine_rest":
            target = quarantine_file(conn, quarantine_dir, m)
        else:
            target = trash_file(conn, m)
        if target:
            moved.append((m["path"], target))
    db.record_duplicate_decision(conn, group_id, action, kept_file_id)
    conn.commit()
    return {"moved": moved}


def unique_target(path: Path) -> Path:
    """path, or 'name (2).ext', 'name (3).ext'... if something is already there. Nothing is overwritten."""
    if not path.exists():
        return path
    for n in range(2, 10_000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise ReviewError(f"Too many files named like {path}")


def relocate(source: Path, dest_root: Path) -> Path:
    """Moves source under dest_root, keeping its full original folder path below it."""
    relative = Path(*source.parts[1:]) if source.is_absolute() else source
    target = unique_target(dest_root / relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return target


def quarantine_file(conn: sqlite3.Connection, quarantine_dir: Path, file_row: sqlite3.Row) -> str | None:
    source = Path(file_row["path"])
    if not source.exists():
        db.set_status(conn, file_row["id"], "missing")
        return None
    target = relocate(source, Path(quarantine_dir).expanduser())
    db.set_status(conn, file_row["id"], "quarantined", str(target))
    return str(target)


def trash_file(conn: sqlite3.Connection, file_row: sqlite3.Row) -> str | None:
    source = Path(file_row["path"])
    if not source.exists():
        db.set_status(conn, file_row["id"], "missing")
        return None
    move_to_trash(source)
    db.set_status(conn, file_row["id"], "trashed")
    return "Trash"


def isolate_files_by_format(conn: sqlite3.Connection, fmt: str, dest_dir: Path) -> list[tuple[str, str]]:
    """Moves every active file of one format (say .m4p purchases, which can't be fingerprinted) into a folder of
    the person's choosing, keeping their folder structure. They stay active in the catalog at their new place."""
    dest_dir = Path(dest_dir).expanduser().absolute()
    moved = []
    for f in db.get_active_files(conn):
        if f["format"] != fmt:
            continue
        source = Path(f["path"])
        if dest_dir in source.parents:
            continue  # already there
        if not source.exists():
            db.set_status(conn, f["id"], "missing")
            continue
        target = relocate(source, dest_dir)
        conn.execute("UPDATE files SET path = ? WHERE id = ?", (str(target), f["id"]))
        moved.append((str(source), str(target)))
    conn.commit()
    return moved
