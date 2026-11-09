"""The catalog: a local SQLite database of every file, its tags, fingerprints, duplicates and decisions.

A connection belongs to one thread. Background work in the app opens its own (see ui/worker.py).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 1
MUSICAPP_PREFIX = "musicapp://"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.executescript(SCHEMA_PATH.read_text())
    if conn.execute("SELECT version FROM schema_version").fetchone() is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()
    return conn


# ---------------------------------------------------------------- files ----

def get_file_by_path(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()


def get_file(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()


def upsert_file(conn: sqlite3.Connection, path: str, size_bytes: int | None, mtime: float | None,
                sha256: str | None, fmt: str) -> tuple[int, bool]:
    """Inserts or updates a file row and marks it active. Returns (file_id, is_new)."""
    existing = get_file_by_path(conn, path)
    ts = now()
    if existing is None:
        cur = conn.execute(
            """INSERT INTO files (path, size_bytes, mtime, sha256, format, first_seen_at, last_scanned_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active')""",
            (path, size_bytes, mtime, sha256, fmt, ts, ts))
        return cur.lastrowid, True
    conn.execute(
        """UPDATE files SET size_bytes=?, mtime=?, sha256=?, format=?, last_scanned_at=?, status='active'
           WHERE id=?""",
        (size_bytes, mtime, sha256, fmt, ts, existing["id"]))
    return existing["id"], False


def is_unchanged(existing: sqlite3.Row, size_bytes: int, mtime: float) -> bool:
    return existing["size_bytes"] == size_bytes and existing["mtime"] == mtime and existing["status"] == "active"


def set_status(conn: sqlite3.Connection, file_id: int, status: str, path: str | None = None) -> None:
    if path is None:
        conn.execute("UPDATE files SET status = ? WHERE id = ?", (status, file_id))
    else:
        conn.execute("UPDATE files SET status = ?, path = ? WHERE id = ?", (status, path, file_id))


def mark_missing_under_root(conn: sqlite3.Connection, root: str, seen_paths: set[str]) -> int:
    """Marks active files under `root` that this scan didn't see as missing. `root` must be spelled the way
    the scan stored paths (expanded, absolute). Compares a plain prefix: no LIKE, whose % and _ wildcards
    would also match look-alike folder names."""
    prefix = root.rstrip("/") + "/"
    rows = conn.execute("SELECT id, path FROM files WHERE status = 'active' AND substr(path, 1, ?) = ?",
                        (len(prefix), prefix)).fetchall()
    missing = [(r["id"],) for r in rows if r["path"] not in seen_paths]
    conn.executemany("UPDATE files SET status = 'missing' WHERE id = ?", missing)
    return len(missing)


def get_active_files(conn: sqlite3.Connection, include_musicapp: bool = False) -> list[sqlite3.Row]:
    """Active files on disk; Music app tracks only when asked (they have no file to hash or convert)."""
    if include_musicapp:
        return conn.execute("SELECT * FROM files WHERE status = 'active' ORDER BY id").fetchall()
    return conn.execute("SELECT * FROM files WHERE status = 'active' AND format != 'musicapp' ORDER BY id").fetchall()


def library_stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("""SELECT format, COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS size FROM files
                           WHERE status = 'active' AND format != 'musicapp' GROUP BY format ORDER BY n DESC""").fetchall()
    return {
        "files": sum(r["n"] for r in rows),
        "bytes": sum(r["size"] for r in rows),
        "by_format": [(r["format"] or "(none)", r["n"], r["size"]) for r in rows],
        "fingerprinted": conn.execute("""SELECT COUNT(*) FROM fingerprints fp JOIN files f ON f.id = fp.file_id
                                         WHERE f.status = 'active' AND fp.raw_fp IS NOT NULL""").fetchone()[0],
        "pending_groups": conn.execute(
            "SELECT COUNT(*) FROM duplicate_groups WHERE status = 'pending_review'").fetchone()[0],
        "proposed_changes": conn.execute(
            "SELECT COUNT(*) FROM normalization_log WHERE status = 'proposed'").fetchone()[0],
        "queued_jobs": conn.execute("SELECT COUNT(*) FROM conversion_jobs WHERE status = 'queued'").fetchone()[0],
    }


# ----------------------------------------------------------------- tags ----

TAG_FIELDS = ("artist", "album_artist", "title", "album", "track_number", "disc_number", "year", "genre",
              "duration_seconds", "bitrate")


def upsert_tags(conn: sqlite3.Connection, file_id: int, tags: dict[str, Any]) -> None:
    conn.execute(
        f"""INSERT INTO tags (file_id, {', '.join(TAG_FIELDS)}, read_error, tags_read_at)
            VALUES (?, {', '.join('?' for _ in TAG_FIELDS)}, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
            {', '.join(f'{f}=excluded.{f}' for f in TAG_FIELDS)},
            read_error=excluded.read_error, tags_read_at=excluded.tags_read_at""",
        (file_id, *(tags.get(f) for f in TAG_FIELDS), tags.get("error"), now()))


def get_tags(conn: sqlite3.Connection, file_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tags WHERE file_id = ?", (file_id,)).fetchone()
    return dict(row) if row else None


EDITABLE_FIELDS = ("artist", "title", "album", "album_artist", "track_number", "disc_number", "year")


def set_tag(conn: sqlite3.Connection, file_id: int, field: str, value: str | None) -> None:
    if field not in EDITABLE_FIELDS:
        raise ValueError(f"not an editable field: {field}")
    conn.execute(f"UPDATE tags SET {field} = ? WHERE file_id = ?", (value, file_id))


# ---------------------------------------------------------- fingerprints ----

def upsert_fingerprint(conn: sqlite3.Connection, file_id: int, chromaprint_fp: str | None, raw_fp: str | None,
                       duration_seconds: float | None, acoustid_id: str | None = None) -> None:
    conn.execute(
        """INSERT INTO fingerprints (file_id, chromaprint_fp, raw_fp, duration_seconds, acoustid_id, computed_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(file_id) DO UPDATE SET chromaprint_fp=excluded.chromaprint_fp, raw_fp=excluded.raw_fp,
               duration_seconds=excluded.duration_seconds, acoustid_id=excluded.acoustid_id,
               computed_at=excluded.computed_at""",
        (file_id, chromaprint_fp, raw_fp, duration_seconds, acoustid_id, now()))


def get_fingerprint(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM fingerprints WHERE file_id = ?", (file_id,)).fetchone()


def delete_fingerprint(conn: sqlite3.Connection, file_id: int) -> None:
    conn.execute("DELETE FROM fingerprints WHERE file_id = ?", (file_id,))


def files_needing_fingerprint(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Active files on disk with no fingerprint yet. DRM-protected .m4p files can never be decoded, so
    they're left out instead of failing every time."""
    return conn.execute(
        """SELECT f.* FROM files f LEFT JOIN fingerprints fp ON fp.file_id = f.id
           WHERE f.status = 'active' AND f.format NOT IN ('musicapp', 'm4p') AND fp.file_id IS NULL
           ORDER BY f.id""").fetchall()


# ---------------------------------------------------------- duplicates -----

def create_duplicate_group(conn: sqlite3.Connection, tier: str, confidence: float, member_file_ids: Iterable[int],
                           similarity_scores: dict[int, float] | None = None) -> int:
    scores = similarity_scores or {}
    cur = conn.execute("INSERT INTO duplicate_groups (tier, confidence, status, created_at) VALUES (?, ?, 'pending_review', ?)",
                       (tier, confidence, now()))
    gid = cur.lastrowid
    conn.executemany("INSERT INTO duplicate_group_members (group_id, file_id, similarity_score) VALUES (?, ?, ?)",
                     [(gid, fid, scores.get(fid)) for fid in member_file_ids])
    return gid


def get_pending_duplicate_groups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("""SELECT * FROM duplicate_groups WHERE status = 'pending_review'
                           ORDER BY CASE tier WHEN 'exact_hash' THEN 0 WHEN 'acoustic' THEN 1 ELSE 2 END,
                                    confidence DESC, id""").fetchall()


def get_group(conn: sqlite3.Connection, group_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM duplicate_groups WHERE id = ?", (group_id,)).fetchone()


def get_group_members(conn: sqlite3.Connection, group_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT f.*, m.similarity_score, t.artist, t.title, t.album, t.duration_seconds, t.bitrate
           FROM duplicate_group_members m JOIN files f ON f.id = m.file_id LEFT JOIN tags t ON t.file_id = f.id
           WHERE m.group_id = ? ORDER BY f.id""", (group_id,)).fetchall()


def files_already_grouped(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT DISTINCT file_id FROM duplicate_group_members")}


def record_duplicate_decision(conn: sqlite3.Connection, group_id: int, action: str, kept_file_id: int | None) -> None:
    conn.execute("INSERT INTO duplicate_decisions (group_id, kept_file_id, action, decided_at) VALUES (?, ?, ?, ?)",
                 (group_id, kept_file_id, action, now()))
    status = "ignored" if action == "not_duplicates" else "reviewed"
    conn.execute("UPDATE duplicate_groups SET status = ? WHERE id = ?", (status, group_id))


# ----------------------------------------------------------- normalize -----

def normalization_seen(conn: sqlite3.Connection, file_id: int, rule_id: str, field: str,
                       old_value: str | None, new_value: str) -> bool:
    """Whether this exact change was proposed before, whatever became of it. A change that was rejected or
    reverted is not proposed again."""
    return conn.execute(
        """SELECT 1 FROM normalization_log WHERE file_id = ? AND rule_id = ? AND field = ?
           AND old_value IS ? AND new_value = ? LIMIT 1""",
        (file_id, rule_id, field, old_value, new_value)).fetchone() is not None


def add_normalization_log(conn: sqlite3.Connection, file_id: int, rule_id: str, field: str,
                          old_value: str | None, new_value: str, backend: str) -> int:
    cur = conn.execute(
        """INSERT INTO normalization_log (file_id, rule_id, field, old_value, new_value, backend, status, proposed_at)
           VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?)""",
        (file_id, rule_id, field, old_value, new_value, backend, now()))
    return cur.lastrowid


def get_normalizations(conn: sqlite3.Connection, status: str, backend: str | None = None,
                       limit: int | None = None) -> list[sqlite3.Row]:
    sql = """SELECT n.*, f.path FROM normalization_log n JOIN files f ON f.id = n.file_id WHERE n.status = ?"""
    args: list[Any] = [status]
    if backend:
        sql += " AND n.backend = ?"
        args.append(backend)
    sql += " ORDER BY n.applied_at DESC, n.id DESC" if status == "applied" else " ORDER BY n.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, args).fetchall()


def get_normalization(conn: sqlite3.Connection, log_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM normalization_log WHERE id = ?", (log_id,)).fetchone()


def set_normalization_status(conn: sqlite3.Connection, log_id: int, status: str) -> None:
    if status == "applied":
        conn.execute("UPDATE normalization_log SET status = 'applied', applied_at = ? WHERE id = ?", (now(), log_id))
    else:
        conn.execute("UPDATE normalization_log SET status = ? WHERE id = ?", (status, log_id))


def record_musicbrainz_lookup(conn: sqlite3.Connection, file_id: int, recording_id: str | None, status: str) -> None:
    conn.execute("""INSERT INTO musicbrainz_lookups (file_id, recording_id, status, checked_at) VALUES (?, ?, ?, ?)
                    ON CONFLICT(file_id) DO UPDATE SET recording_id=excluded.recording_id, status=excluded.status,
                    checked_at=excluded.checked_at""", (file_id, recording_id, status, now()))


def clear_musicbrainz_lookups(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM musicbrainz_lookups")
    conn.commit()


def musicbrainz_stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT status, COUNT(*) FROM musicbrainz_lookups GROUP BY status").fetchall()
    return {r[0]: r[1] for r in rows}


# ------------------------------------------------------------- organize -----

def get_moves(conn: sqlite3.Connection, status: str, limit: int | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM organize_moves WHERE status = ? ORDER BY " + ("applied_at DESC, id DESC" if status == "applied" else "id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (status,)).fetchall()


def get_move(conn: sqlite3.Connection, move_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM organize_moves WHERE id = ?", (move_id,)).fetchone()


# ------------------------------------------------------------ conversion ----

def enqueue_conversion_job(conn: sqlite3.Connection, source_file_id: int, target_path: str,
                           source_disposition: str = "keep") -> int:
    cur = conn.execute(
        """INSERT INTO conversion_jobs (source_file_id, target_path, status, source_disposition, created_at)
           VALUES (?, ?, 'queued', ?, ?)""",
        (source_file_id, target_path, source_disposition, now()))
    return cur.lastrowid


def files_with_conversion_job(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT source_file_id FROM conversion_jobs WHERE status IN ('queued', 'running', 'succeeded')")}


def get_queued_conversion_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM conversion_jobs WHERE status = 'queued' ORDER BY id").fetchall()


def recent_conversion_jobs(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute("""SELECT j.*, f.path AS source_path FROM conversion_jobs j
                           JOIN files f ON f.id = j.source_file_id ORDER BY j.id DESC LIMIT ?""", (limit,)).fetchall()


def requeue_interrupted_jobs(conn: sqlite3.Connection) -> int:
    """A job left 'running' means the app quit mid-conversion: put it back in the queue."""
    n = conn.execute("UPDATE conversion_jobs SET status = 'queued', started_at = NULL WHERE status = 'running'").rowcount
    conn.commit()
    return n


def clear_finished_jobs(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM conversion_jobs WHERE status = 'failed'")
    conn.commit()


def start_conversion_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("UPDATE conversion_jobs SET status = 'running', started_at = ? WHERE id = ?", (now(), job_id))


def finish_conversion_job(conn: sqlite3.Connection, job_id: int, success: bool, error_message: str | None = None) -> None:
    conn.execute("UPDATE conversion_jobs SET status = ?, finished_at = ?, error_message = ? WHERE id = ?",
                 ("succeeded" if success else "failed", now(), error_message, job_id))


# ------------------------------------------------------------- scan runs ----

def start_scan_run(conn: sqlite3.Connection, root_path: str) -> int:
    return conn.execute("INSERT INTO scan_runs (started_at, root_path) VALUES (?, ?)", (now(), root_path)).lastrowid


def finish_scan_run(conn: sqlite3.Connection, run_id: int, seen: int, new: int, updated: int, missing: int) -> None:
    conn.execute("""UPDATE scan_runs SET finished_at=?, files_seen=?, files_new=?, files_updated=?, files_missing=?
                    WHERE id=?""", (now(), seen, new, updated, missing, run_id))


def last_scan(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM scan_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
