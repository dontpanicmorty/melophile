"""Tag cleanup: propose changes (nothing is written), then apply, reject or revert each one.

Safety:
- a change already proposed, rejected or reverted is never proposed again;
- before applying, the tag must still hold the value the change was proposed against, and before reverting,
  the value it was changed to. If someone edited it in between, Melophile refuses rather than overwrite it;
- every applied change keeps its old value, so it can be reverted at any time.
"""
from __future__ import annotations

import sqlite3

from . import db
from .backends import get_backend
from .progress import SILENT, Progress
from .rules import RULES


class StaleChange(RuntimeError):
    pass


def propose_changes(conn: sqlite3.Connection, backend_name: str, progress: Progress = SILENT) -> int:
    backend = get_backend(backend_name)
    created = 0
    for n, (file_id, tags) in enumerate(backend.iter_tags(conn, progress), 1):
        if n % 200 == 0:
            progress.update(n, 0, "Checking tags")
            progress.check()
        tags = dict(tags)
        for rule in RULES:
            for change in rule.propose(tags):
                if change.new_value == (change.old_value or ""):
                    continue
                if not db.normalization_seen(conn, file_id, rule.rule_id, change.field,
                                             change.old_value, change.new_value):
                    db.add_normalization_log(conn, file_id, rule.rule_id, change.field, change.old_value,
                                             change.new_value, backend_name)
                    created += 1
                # later rules see this rule's result (e.g. spaces tidied after a feat. clause moved)
                tags[change.field] = change.new_value
    conn.commit()
    return created


def _same(a: str | None, b: str | None) -> bool:
    return (a or "") == (b or "")


def apply_change(conn: sqlite3.Connection, log_id: int, backend=None) -> None:
    entry = db.get_normalization(conn, log_id)
    if entry is None or entry["status"] != "proposed":
        return
    backend = backend or get_backend(entry["backend"])
    current = backend.current_value(conn, entry["file_id"], entry["field"])
    if not _same(current, entry["old_value"]):
        raise StaleChange(f"The {entry['field']} is now {current!r}, not {entry['old_value']!r} as when this "
                          "change was proposed. Find changes again to get an up-to-date proposal.")
    backend.write_field(conn, entry["file_id"], entry["field"], entry["new_value"])
    db.set_normalization_status(conn, log_id, "applied")
    conn.commit()


def reject_change(conn: sqlite3.Connection, log_id: int) -> None:
    entry = db.get_normalization(conn, log_id)
    if entry is not None and entry["status"] == "proposed":
        db.set_normalization_status(conn, log_id, "rejected")
        conn.commit()


def revert_change(conn: sqlite3.Connection, log_id: int, backend=None) -> None:
    entry = db.get_normalization(conn, log_id)
    if entry is None or entry["status"] != "applied":
        return
    backend = backend or get_backend(entry["backend"])
    current = backend.current_value(conn, entry["file_id"], entry["field"])
    if not _same(current, entry["new_value"]):
        raise StaleChange(f"The {entry['field']} has changed again since (it's now {current!r}), so reverting "
                          "would overwrite that. Revert the later change first.")
    backend.write_field(conn, entry["file_id"], entry["field"], entry["old_value"] or "")
    db.set_normalization_status(conn, log_id, "reverted")
    conn.commit()


def apply_many(conn: sqlite3.Connection, log_ids: list[int], progress: Progress = SILENT) -> dict:
    """Applies in the order proposed, so chained changes to one tag go in sequence."""
    backends: dict[str, object] = {}
    done, errors = 0, []
    ids = sorted(log_ids)
    for n, log_id in enumerate(ids, 1):
        progress.update(n, len(ids), "Applying changes")
        if progress.stopping():
            break
        entry = db.get_normalization(conn, log_id)
        if entry is None:
            continue
        b = backends.setdefault(entry["backend"], get_backend(entry["backend"]))
        try:
            apply_change(conn, log_id, b)
            done += 1
        except Exception as exc:
            errors.append(f"{entry['field']} {entry['old_value']!r}: {exc}")
    return {"applied": done, "errors": errors}


def revert_many(conn: sqlite3.Connection, log_ids: list[int], progress: Progress = SILENT) -> dict:
    """Reverts newest first, so a chain of changes unwinds in the right order."""
    backends: dict[str, object] = {}
    done, errors = 0, []
    ids = sorted(log_ids, reverse=True)
    for n, log_id in enumerate(ids, 1):
        progress.update(n, len(ids), "Reverting changes")
        if progress.stopping():
            break
        entry = db.get_normalization(conn, log_id)
        if entry is None:
            continue
        b = backends.setdefault(entry["backend"], get_backend(entry["backend"]))
        try:
            revert_change(conn, log_id, b)
            done += 1
        except Exception as exc:
            errors.append(f"{entry['field']} {entry['new_value']!r}: {exc}")
    return {"reverted": done, "errors": errors}
