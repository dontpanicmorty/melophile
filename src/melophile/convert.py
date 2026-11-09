"""MP3/FLAC to M4A (AAC) conversion with the ffmpeg bundled in imageio-ffmpeg.

Each conversion:
- never overwrites anything: if the .m4a already exists, the job fails and says so;
- writes to a temporary file first and renames it only once it's complete and checked, so a stopped or failed
  conversion leaves no half-written file;
- keeps the album art and every tag (artist, album, track and disc numbers, year, genre...);
- is checked (the new file must be readable and as long as the original) before anything happens to the original.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from . import db, tools
from .config import Config
from .progress import SILENT, Progress
from .backends import number_pair
from .review import quarantine_file, trash_file
from .scan import read_tags

SOURCE_FORMATS = ("mp3", "flac")
DISPOSITIONS = {"keep": "Keep the original", "quarantine": "Quarantine the original",
                "trash": "Move the original to the Trash"}
_MP4_ATOMS = {"artist": "\xa9ART", "title": "\xa9nam", "album": "\xa9alb", "album_artist": "aART",
              "genre": "\xa9gen", "year": "\xa9day"}


class ConversionError(RuntimeError):
    pass


def _timeout(duration: float | None) -> float:
    return max(120.0, 2.0 * (duration or 600.0))


def transcode(source: str, target: Path, bitrate: str, duration: float | None) -> None:
    """Into `target`, with the cover art kept when the source has one. If the cover can't be carried over
    (an unusual image format), it converts again without it rather than fail."""
    base = [tools.ffmpeg_path(), "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "2",
            "-i", source, "-map_metadata", "0", "-c:a", "aac", "-b:a", bitrate, "-f", "ipod"]
    with_art = base[:base.index("-map_metadata")] + ["-map", "0:a:0", "-map", "0:v:0?", "-c:v", "copy",
                                                     "-disposition:v:0", "attached_pic"] + base[base.index("-map_metadata"):]
    try:
        tools.run([*with_art, "-y", str(target)], _timeout(duration), "ffmpeg")
    except tools.ToolError:
        tools.run([*base, "-map", "0:a:0", "-y", str(target)], _timeout(duration), "ffmpeg")


def _apply_tags(target: Path, tags: dict) -> None:
    """The catalog's tags win over ffmpeg's tag mapping, which can differ between formats."""
    from mutagen.mp4 import MP4

    audio = MP4(str(target))
    for field, atom in _MP4_ATOMS.items():
        if tags.get(field):
            audio[atom] = [str(tags[field])]
    for field, atom in (("track_number", "trkn"), ("disc_number", "disk")):
        pair = number_pair(tags.get(field))
        if pair:
            audio[atom] = [pair]
    audio.save()


def verify(source_duration: float | None, target: Path, tolerance: float = 2.0) -> None:
    info = read_tags(target)
    if info.get("error") or not info.get("duration_seconds"):
        raise ConversionError("The converted file couldn't be read back")
    if source_duration and abs(info["duration_seconds"] - source_duration) > tolerance:
        raise ConversionError(f"The converted file is {info['duration_seconds']:.0f} s long, "
                              f"the original {source_duration:.0f} s")


def enqueue(conn: sqlite3.Connection, fmt: str, disposition: str = "keep") -> int:
    """Queues every active file of one format that hasn't been converted or queued already."""
    if fmt not in SOURCE_FORMATS:
        raise ConversionError(f"Melophile converts {' and '.join(SOURCE_FORMATS)} files, not {fmt}")
    if disposition not in DISPOSITIONS:
        raise ConversionError(f"Unknown choice for the original: {disposition}")
    done = db.files_with_conversion_job(conn)
    n = 0
    for f in db.get_active_files(conn):
        if f["format"] == fmt and f["id"] not in done:
            db.enqueue_conversion_job(conn, f["id"], str(Path(f["path"]).with_suffix(".m4a")), disposition)
            n += 1
    conn.commit()
    return n


def convert_one(conn: sqlite3.Connection, config: Config, job: sqlite3.Row) -> None:
    source = db.get_file(conn, job["source_file_id"])
    if source is None or source["status"] != "active" or not Path(source["path"]).exists():
        raise ConversionError("The original file isn't there any more")
    target = Path(job["target_path"])
    if target.exists():
        raise ConversionError(f"{target.name} already exists beside the original; nothing was overwritten")
    tags = db.get_tags(conn, source["id"]) or {}
    duration = tags.get("duration_seconds")
    tmp = target.with_name(f".{target.stem}.melophile-partial.m4a")
    try:
        transcode(source["path"], tmp, config.bitrate, duration)
        _apply_tags(tmp, tags)
        verify(duration, tmp)
        if target.exists():  # appeared while converting
            raise ConversionError(f"{target.name} appeared while converting; nothing was overwritten")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    if job["source_disposition"] == "quarantine":
        quarantine_file(conn, config.quarantine_dir, source)
    elif job["source_disposition"] == "trash":
        trash_file(conn, source)
    # 'keep': the .m4a joins the catalog on the next scan, and duplicate finding pairs it with its original


def run_queue(conn: sqlite3.Connection, config: Config, progress: Progress = SILENT) -> dict:
    db.requeue_interrupted_jobs(conn)
    jobs = db.get_queued_conversion_jobs(conn)
    ok = failed = 0
    for n, job in enumerate(jobs, 1):
        if progress.stopping():
            break
        progress.update(n - 1, len(jobs), Path(job["target_path"]).name)
        db.start_conversion_job(conn, job["id"])
        conn.commit()
        try:
            convert_one(conn, config, job)
            db.finish_conversion_job(conn, job["id"], True)
            ok += 1
        except Exception as exc:
            db.finish_conversion_job(conn, job["id"], False, str(exc))
            failed += 1
        conn.commit()
    progress.update(len(jobs), len(jobs), "")
    return {"succeeded": ok, "failed": failed, "remaining": len(db.get_queued_conversion_jobs(conn))}
