"""Scanning: walks the music folders into the catalog, reads tags, and fingerprints what's new.

Repeat scans are quick: a file whose size and modification time haven't changed is skipped. One unreadable
file (locked, damaged, a network hiccup) is recorded and skipped, never the end of the scan, and progress is
committed as it goes.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import mutagen

from . import db, tools
from .config import Config
from .fingerprint import compute_fingerprint, lookup_acoustid
from .progress import SILENT, Progress

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".m4p", ".aac", ".wav", ".aif", ".aiff", ".alac", ".ogg", ".opus"}
COMMIT_EVERY = 200
MAX_REPORTED_FAILURES = 50
CHUNK = 1024 * 1024


class ScanError(RuntimeError):
    pass


@dataclass
class WalkStats:
    """Lets a scan tell a genuinely empty folder from one that came back empty when it shouldn't have (a
    network share can list as empty while it's reconnecting)."""
    dirs_visited: int = 0
    files_seen: int = 0
    audio_files_seen: int = 0


def root_key(root: str | Path) -> str:
    """How a music folder is spelled in the catalog: `~` expanded and made absolute, so paths always match."""
    return str(Path(root).expanduser().absolute())


def walk_audio_files(root: str | Path, stats: WalkStats | None = None,
                     skip: tuple[Path, ...] = ()) -> Iterator[Path]:
    """Every audio file under root, leaving out hidden files and folders and any folder in `skip` (the
    quarantine folder, so quarantined files aren't scanned back in)."""
    skip_keys = {root_key(s) for s in skip}
    for dirpath, dirnames, filenames in os.walk(root_key(root)):
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and os.path.join(dirpath, d) not in skip_keys)
        if stats is not None:
            stats.dirs_visited += 1
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            if stats is not None:
                stats.files_seen += 1
            path = Path(dirpath) / name
            if path.suffix.lower() in AUDIO_EXTENSIONS:
                if stats is not None:
                    stats.audio_files_seen += 1
                yield path


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_format(path: str | Path) -> str:
    return Path(path).suffix.lower().lstrip(".")


def read_tags(path: str | Path) -> dict[str, Any]:
    """Tags through mutagen's format-independent interface. Never raises: an unreadable file is still
    catalogued (with 'error' set), so it can be found as a byte-for-byte duplicate."""
    try:
        audio = mutagen.File(str(path), easy=True)
    except Exception as exc:
        return {"error": str(exc) or type(exc).__name__}
    if audio is None:
        return {"error": "not a readable audio file"}

    def first(key: str) -> str | None:
        try:
            values = audio.get(key)
        except Exception:
            return None
        return str(values[0]) if values else None

    info = getattr(audio, "info", None)
    length = getattr(info, "length", None)
    bitrate = getattr(info, "bitrate", None)
    return {
        "artist": first("artist"), "album_artist": first("albumartist"), "title": first("title"),
        "album": first("album"), "track_number": first("tracknumber"), "disc_number": first("discnumber"),
        "year": first("date"), "genre": first("genre"),
        "duration_seconds": float(length) if length else None,
        "bitrate": int(bitrate) if bitrate else None,
    }


def refresh_file(conn: sqlite3.Connection, file_id: int, path: str) -> None:
    """Re-reads one file after Melophile changed it (new tags), so the catalog matches the disk."""
    st = os.stat(path)
    db.upsert_file(conn, path, st.st_size, st.st_mtime, sha256_file(path), detect_format(path))
    db.upsert_tags(conn, file_id, read_tags(path))


def scan_root(conn: sqlite3.Connection, root: str, config: Config, progress: Progress = SILENT) -> dict:
    """Catalogs one music folder. Files under it that are gone are marked missing (never removed from the
    catalog), except when the whole folder looks empty or unreachable: then nothing is marked missing,
    because an unplugged drive or a dropped network share would otherwise mark every song as gone."""
    key = root_key(root)
    if not Path(key).is_dir():
        raise ScanError(f"The folder {key} isn't available. Is its drive connected?")
    run_id = db.start_scan_run(conn, key)
    seen: set[str] = set()
    new = updated = unchanged = 0
    failures: list[tuple[str, str]] = []
    stats = WalkStats()
    pending = 0
    for path in walk_audio_files(key, stats, skip=(config.quarantine_dir,)):
        if progress.stopping():
            conn.commit()
            break
        p = str(path)
        seen.add(p)
        progress.update(len(seen), 0, p)
        try:
            st = path.stat()
            existing = db.get_file_by_path(conn, p)
            if existing is not None and db.is_unchanged(existing, st.st_size, st.st_mtime):
                unchanged += 1
                continue
            digest = sha256_file(path)
            file_id, is_new = db.upsert_file(conn, p, st.st_size, st.st_mtime, digest, detect_format(path))
            db.upsert_tags(conn, file_id, read_tags(path))
            if not is_new:
                db.delete_fingerprint(conn, file_id)  # the audio may have changed: fingerprint it again
            new += is_new
            updated += not is_new
        except Exception as exc:
            failures.append((p, str(exc) or type(exc).__name__))
        finally:
            pending += 1
            if pending >= COMMIT_EVERY:
                conn.commit()
                pending = 0
    conn.commit()
    stopped = progress.stopping()

    warning = None
    had_files = conn.execute("SELECT COUNT(*) FROM files WHERE status='active' AND substr(path,1,?)=?",
                             (len(key) + 1, key.rstrip("/") + "/")).fetchone()[0]
    if stats.files_seen == 0:
        warning = (f"{key} looks completely empty. A network share can look like this while it reconnects. "
                   + ("Nothing was marked missing. " if had_files else "")
                   + "If you expected music here, check the drive or share and scan again.")
    missing = 0
    if not stopped and stats.files_seen > 0:
        missing = db.mark_missing_under_root(conn, key, seen)
    db.finish_scan_run(conn, run_id, len(seen), new, updated, missing)
    conn.commit()
    result = {"root": key, "files_seen": len(seen), "files_new": new, "files_updated": updated,
              "files_unchanged": unchanged, "files_missing": missing, "files_failed": len(failures),
              "failures": failures[:MAX_REPORTED_FAILURES], "stopped": stopped}
    if len(failures) > MAX_REPORTED_FAILURES:
        result["failures_truncated"] = len(failures) - MAX_REPORTED_FAILURES
    if warning:
        result["warning"] = warning
    return result


def fingerprint_pending(conn: sqlite3.Connection, config: Config, progress: Progress = SILENT) -> dict:
    """Fingerprints every active file that doesn't have one yet, MAX_PARALLEL at a time. A file fpcalc can't
    read is remembered (an empty fingerprint), so it isn't retried on every scan; it's retried once it changes."""
    fpcalc = tools.find_fpcalc(config.fpcalc_path)
    if not fpcalc:
        raise tools.ToolMissing("fpcalc isn't available. Choose it under Settings, or use the packaged app, which includes it.")
    todo = db.files_needing_fingerprint(conn)
    compressed = config.acoustid.enabled and bool(config.acoustid.api_key)
    done = ok = 0
    failures: list[tuple[str, str]] = []
    pool = ThreadPoolExecutor(max_workers=tools.MAX_PARALLEL)
    try:
        futures = {pool.submit(compute_fingerprint, f["path"], fpcalc, compressed): f for f in todo}
        for fut in as_completed(futures):
            f = futures[fut]
            done += 1
            try:
                r = fut.result()
                db.upsert_fingerprint(conn, f["id"], r["chromaprint_fp"], r["raw_fp"], r["duration_seconds"])
                ok += 1
            except Exception as exc:
                db.upsert_fingerprint(conn, f["id"], None, None, None)
                failures.append((f["path"], str(exc)))
            if done % 25 == 0:
                conn.commit()
            progress.update(done, len(todo), f["path"])
            if progress.stopping():
                for other in futures:
                    other.cancel()
                break
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        conn.commit()
    if compressed and not progress.stopping():
        _acoustid_lookups(conn, config.acoustid.api_key, progress)
    return {"fingerprinted": ok, "failed": len(failures), "failures": failures[:MAX_REPORTED_FAILURES],
            "remaining": len(todo) - done}


def _acoustid_lookups(conn: sqlite3.Connection, api_key: str, progress: Progress) -> None:
    """One request at a time, within AcoustID's limit of three a second (see net.py). A network failure leaves
    the rest for the next scan."""
    from .net import NetError

    rows = conn.execute("""SELECT fp.* FROM fingerprints fp JOIN files f ON f.id = fp.file_id
                           WHERE f.status='active' AND fp.chromaprint_fp IS NOT NULL AND fp.acoustid_id IS NULL""").fetchall()
    for i, fp in enumerate(rows, 1):
        if progress.stopping():
            break
        progress.update(i, len(rows), "Looking up AcoustID")
        try:
            aid, mbid = lookup_acoustid(api_key, fp["duration_seconds"], fp["chromaprint_fp"])
        except NetError:
            break
        if aid:
            conn.execute("UPDATE fingerprints SET acoustid_id = ?, mb_recording_id = ? WHERE file_id = ?",
                         (aid, mbid, fp["file_id"]))
            conn.commit()


def scan_all(conn: sqlite3.Connection, config: Config, fingerprints: bool = True,
             progress: Progress = SILENT) -> dict:
    """Scans every music folder in Settings, then fingerprints what's new (if fpcalc is available)."""
    results, errors = [], []
    for root in config.library_roots:
        if progress.stopping():
            break
        try:
            results.append(scan_root(conn, root, config, progress))
        except ScanError as exc:
            errors.append(str(exc))
    fp = None
    if fingerprints and not progress.stopping() and tools.find_fpcalc(config.fpcalc_path):
        fp = fingerprint_pending(conn, config, progress)
    return {"roots": results, "errors": errors, "fingerprints": fp, "stopped": progress.stopping()}
