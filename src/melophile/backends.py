"""Where tags are read from and written to: audio files on disk, or tracks in the Music app."""
from __future__ import annotations

import sqlite3
from typing import Iterator

from . import db
from .progress import SILENT, Progress
from .scan import read_tags, refresh_file

WRITABLE_FORMATS = {"mp3", "flac", "m4a"}


class BackendError(RuntimeError):
    pass


class FileBackend:
    """Reads the tags the last scan stored, and writes changes into the file itself with mutagen.
    DRM-protected .m4p purchases and formats without standard tags (WAV, AIFF) are left alone."""
    name = "file"

    def iter_tags(self, conn: sqlite3.Connection, progress: Progress = SILENT) -> Iterator[tuple[int, dict]]:
        for f in db.get_active_files(conn):
            if f["format"] not in WRITABLE_FORMATS:
                continue
            tags = db.get_tags(conn, f["id"])
            if tags and not tags.get("read_error"):
                yield f["id"], tags

    def current_value(self, conn: sqlite3.Connection, file_id: int, field: str) -> str | None:
        f = db.get_file(conn, file_id)
        if f is None or f["status"] != "active":
            raise BackendError("This file is no longer in the library.")
        tags = read_tags(f["path"])
        if tags.get("error"):
            raise BackendError(f"Can't read {f['path']}: {tags['error']}")
        return tags.get(field)

    def write_field(self, conn: sqlite3.Connection, file_id: int, field: str, value: str) -> None:
        f = db.get_file(conn, file_id)
        path, fmt = f["path"], f["format"]
        if fmt == "mp3":
            _write_mp3(path, field, value)
        elif fmt == "flac":
            from mutagen.flac import FLAC

            audio = FLAC(path)
            key = FLAC_KEYS[field]
            if value:
                audio[key] = [value]
            elif key in audio:
                del audio[key]
            audio.save()
        elif fmt == "m4a":
            _write_mp4(path, field, value)
        else:
            raise BackendError(f"Melophile doesn't write tags to .{fmt} files.")
        refresh_file(conn, file_id, path)  # new size, time, hash and tags, so the next scan sees no change


FLAC_KEYS = {"artist": "artist", "title": "title", "album": "album", "album_artist": "albumartist",
             "track_number": "tracknumber", "disc_number": "discnumber", "year": "date"}


def number_pair(value: str | None) -> tuple[int, int] | None:
    """'3' or '3/12' -> (3, 0) or (3, 12); anything else -> None."""
    parts = str(value or "").split("/")
    try:
        n = int(parts[0])
        total = int(parts[1]) if len(parts) > 1 and parts[1].strip() else 0
    except ValueError:
        return None
    return (n, total) if n > 0 else None


def _write_mp3(path: str, field: str, value: str) -> None:
    from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, ID3NoHeaderError

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    frame = {"artist": TPE1, "title": TIT2, "album": TALB, "album_artist": TPE2, "track_number": TRCK,
             "disc_number": TPOS, "year": TDRC}[field]
    if value:
        tags.setall(frame.__name__, [frame(encoding=3, text=[value])])
    else:
        tags.delall(frame.__name__)
    tags.save(path)


def _write_mp4(path: str, field: str, value: str) -> None:
    from mutagen.mp4 import MP4

    audio = MP4(path)
    atom = {"artist": "\xa9ART", "title": "\xa9nam", "album": "\xa9alb", "album_artist": "aART",
            "track_number": "trkn", "disc_number": "disk", "year": "\xa9day"}[field]
    if not value:
        audio.pop(atom, None)
    elif atom in ("trkn", "disk"):
        pair = number_pair(value)
        if pair is None:
            raise BackendError(f"{value!r} isn't a track or disc number")
        audio[atom] = [pair]
    else:
        audio[atom] = [value]
    audio.save()


class MusicAppBackend:
    """Tracks in the Music app, through ScriptingBridge (Music's own scripting interface, as Python objects).

    Each track gets a catalog row with the path 'musicapp://<persistent id>' and no file details, so its
    changes share the same history and undo as files. Those rows are never fingerprinted or converted.
    The first use asks for permission to control Music (System Settings > Privacy & Security > Automation).
    Track details are fetched for the whole library in a few requests, not several per track, which matters
    on a library of tens of thousands of songs.
    """
    name = "musicapp"

    def __init__(self) -> None:
        self._app = None

    def _music(self):
        if self._app is None:
            try:
                from ScriptingBridge import SBApplication
            except ImportError as exc:
                raise BackendError("ScriptingBridge isn't available, so the Music app can't be reached.") from exc
            app = SBApplication.applicationWithBundleIdentifier_("com.apple.Music")
            if app is None:
                raise BackendError("Couldn't connect to the Music app.")
            self._app = app
        return self._app

    def _tracks(self):
        return self._music().tracks()

    def iter_tags(self, conn: sqlite3.Connection, progress: Progress = SILENT) -> Iterator[tuple[int, dict]]:
        progress.update(0, 0, "Reading the Music library")
        tracks = self._tracks()
        columns = {k: list(tracks.arrayByApplyingSelector_(sel) or [])
                   for k, sel in (("pid", "persistentID"), ("artist", "artist"), ("album_artist", "albumArtist"),
                                  ("title", "name"), ("album", "album"), ("genre", "genre"),
                                  ("duration", "duration"), ("track", "trackNumber"), ("tracks", "trackCount"),
                                  ("disc", "discNumber"), ("year", "year"))}
        n = len(columns["pid"])
        if n == 0 and tracks.count() > 0:
            raise BackendError("The Music app didn't share its tracks. Allow Melophile under System Settings > "
                               "Privacy & Security > Automation, then try again.")
        for i in range(n):
            def col(k):
                v = columns[k][i] if i < len(columns[k]) else None
                return str(v) if v else None
            path = db.MUSICAPP_PREFIX + str(columns["pid"][i])
            file_id, _ = db.upsert_file(conn, path, None, None, None, "musicapp")
            d = columns["duration"][i] if i < len(columns["duration"]) else None
            def num(k):
                v = columns[k][i] if i < len(columns[k]) else None
                return int(v) if v else 0
            track, tracks = num("track"), num("tracks")
            tags = {"artist": col("artist"), "album_artist": col("album_artist"), "title": col("title"),
                    "album": col("album"), "genre": col("genre"), "duration_seconds": float(d) if d else None,
                    "track_number": (f"{track}/{tracks}" if tracks else str(track)) if track else None,
                    "disc_number": str(num("disc")) if num("disc") else None,
                    "year": str(num("year")) if num("year") else None}
            db.upsert_tags(conn, file_id, tags)
            if i % 500 == 0:
                progress.update(i, n, "Reading the Music library")
            yield file_id, tags

    def _track(self, conn: sqlite3.Connection, file_id: int):
        from Foundation import NSPredicate

        f = db.get_file(conn, file_id)
        pid = f["path"].removeprefix(db.MUSICAPP_PREFIX)
        found = self._tracks().filteredArrayUsingPredicate_(NSPredicate.predicateWithFormat_("persistentID == %@", pid))
        if not found or found.count() == 0:
            raise BackendError("That track is no longer in the Music library.")
        return found.objectAtIndex_(0)

    def current_value(self, conn: sqlite3.Connection, file_id: int, field: str) -> str | None:
        t = self._track(conn, file_id)
        if field == "track_number":
            n, total = t.trackNumber(), t.trackCount()
            return (f"{n}/{total}" if total else str(n)) if n else None
        if field == "disc_number":
            return str(t.discNumber()) if t.discNumber() else None
        if field == "year":
            return str(t.year()) if t.year() else None
        v = {"artist": t.artist, "title": t.name, "album": t.album, "album_artist": t.albumArtist}[field]()
        return str(v) if v else None

    def write_field(self, conn: sqlite3.Connection, file_id: int, field: str, value: str) -> None:
        t = self._track(conn, file_id)
        if field == "track_number":
            n, total = number_pair(value) or (0, 0)
            t.setTrackNumber_(n)
            t.setTrackCount_(total)
        elif field == "disc_number":
            t.setDiscNumber_((number_pair(value) or (0, 0))[0])
        elif field == "year":
            t.setYear_(int(value[:4]) if value[:4].isdigit() else 0)
        else:
            {"artist": t.setArtist_, "title": t.setName_, "album": t.setAlbum_,
             "album_artist": t.setAlbumArtist_}[field](value)
        db.set_tag(conn, file_id, field, value)


BACKENDS = {"file": FileBackend, "musicapp": MusicAppBackend}


def get_backend(name: str):
    try:
        return BACKENDS[name]()
    except KeyError:
        raise ValueError(f"unknown backend {name!r}; expected one of {sorted(BACKENDS)}") from None
