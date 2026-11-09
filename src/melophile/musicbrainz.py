"""Tag fixes from MusicBrainz, the open music encyclopedia.

For each song Melophile finds its MusicBrainz recording, in the most reliable way available:
1. by acoustic fingerprint through AcoustID, when that's turned on in Settings (the sound decides, not the tags);
2. otherwise by searching MusicBrainz for the song's artist and title, accepting only a close match of about the
   same length.
From that recording it proposes the artist, title, album, album artist, track and disc numbers and year. These are
ordinary Tag Cleanup proposals: nothing is written until you apply one, and each can be reverted.

Careful by design:
- the album (with its track and disc numbers and year) is taken from the release that matches the song's current
  album. If the song has no album, the earliest official album release is used. If it has an album that matches
  none of the recording's releases, those fields are left alone, rather than moving the song to a compilation;
- a featured artist follows Melophile's own style (moved into the title as "feat. …");
- an empty value never replaces an existing one;
- each song is looked up once; a song that changes isn't looked up again unless you clear its lookup.
MusicBrainz allows one request a second, so a large library takes hours; the work can be stopped and resumed.
"""
from __future__ import annotations

import sqlite3
from typing import Callable

from rapidfuzz import fuzz

from . import db, net
from .backends import get_backend
from .config import Config
from .fingerprint import lookup_acoustid
from .progress import SILENT, Progress
from .rules import ProposedFieldChange, feat_unify

MB = "https://musicbrainz.org/ws/2"
RULE_ID = "musicbrainz"
INC = "artist-credits+releases+release-groups+media"
SEARCH_MIN_SCORE = 95       # MusicBrainz's own search score, out of 100
NAME_MIN_SIMILARITY = 90    # our check that artist and title really agree
LENGTH_TOLERANCE = 3.0      # seconds
ALBUM_MIN_SIMILARITY = 85
FIELDS = ("artist", "title", "album", "album_artist", "track_number", "disc_number", "year")

Fetch = Callable[..., dict]


def _credit(credits: list | None) -> str:
    return "".join(c.get("name", "") + c.get("joinphrase", "") for c in credits or []).strip()


def _length_ok(rec: dict, duration: float | None) -> bool:
    length = rec.get("length")
    return not duration or not length or abs(length / 1000 - duration) <= LENGTH_TOLERANCE


def get_recording(mbid: str, fetch: Fetch = net.get_json) -> dict:
    return fetch(f"{MB}/recording/{mbid}", {"inc": INC, "fmt": "json"}, rate=net.MUSICBRAINZ_RATE)


def search_recording(artist: str, title: str, duration: float | None, fetch: Fetch = net.get_json) -> dict | None:
    """The one search result that agrees on artist, title and length, or None."""
    def quote(s: str) -> str:
        return '"' + s.replace("\\", " ").replace('"', " ") + '"'

    main = feat_unify({"artist": artist, "title": title})
    clean = {c.field: c.new_value for c in main}
    artist_q, title_q = clean.get("artist", artist), title
    r = fetch(f"{MB}/recording", {"query": f"recording:{quote(title_q)} AND artist:{quote(artist_q)}",
                                  "limit": 5, "fmt": "json"}, rate=net.MUSICBRAINZ_RATE)
    for rec in r.get("recordings", []):
        if int(rec.get("score", 0)) < SEARCH_MIN_SCORE or not _length_ok(rec, duration):
            continue
        if (fuzz.token_sort_ratio(rec.get("title", "").casefold(), title_q.casefold()) >= NAME_MIN_SIMILARITY
                and fuzz.token_set_ratio(_credit(rec.get("artist-credit")).casefold(), artist_q.casefold())
                >= NAME_MIN_SIMILARITY):
            return rec
    return None


def _pick_release(rec: dict, current_album: str | None) -> dict | None:
    releases = [r for r in rec.get("releases", []) if r.get("status", "Official") == "Official"]
    if current_album:
        scored = [(fuzz.token_sort_ratio(r.get("title", "").casefold(), current_album.casefold()), r) for r in releases]
        scored = [x for x in scored if x[0] >= ALBUM_MIN_SIMILARITY]
        return max(scored, key=lambda x: (x[0], -_date_key(x[1])))[1] if scored else None
    albums = [r for r in releases if (r.get("release-group") or {}).get("primary-type") == "Album"
              and not (r.get("release-group") or {}).get("secondary-types")]
    return min(albums, key=_date_key) if albums else None


def _date_key(r: dict) -> int:
    d = (r.get("date") or "9999").replace("-", "")
    return int((d + "0000")[:8]) if d[:4].isdigit() else 99999999


def details(rec: dict, current: dict) -> dict:
    """The tags MusicBrainz gives for this recording, in Melophile's style."""
    out = {}
    canon = {"artist": _credit(rec.get("artist-credit")), "title": rec.get("title") or ""}
    for c in feat_unify(dict(canon)):
        canon[c.field] = c.new_value
    out.update({k: v for k, v in canon.items() if v})
    rel = _pick_release(rec, current.get("album"))
    if rel:
        out["album"] = rel.get("title") or ""
        aa = _credit(rel.get("artist-credit"))
        if aa:
            out["album_artist"] = aa
        if (rel.get("date") or "")[:4].isdigit():
            out["year"] = rel["date"][:4]
        for medium in rel.get("media", []):
            tracks = medium.get("track") or medium.get("tracks") or []
            if tracks:
                pos, count = tracks[0].get("position"), medium.get("track-count")
                if pos:
                    out["track_number"] = f"{pos}/{count}" if count else str(pos)
                if len(rel.get("media", [])) > 1 or (medium.get("position") or 1) > 1:
                    out["disc_number"] = str(medium.get("position"))
                break
    return {k: v for k, v in out.items() if v}


def _number(v: str | None) -> str:
    return (str(v or "").split("/")[0]).strip().lstrip("0")


def changes_for(current: dict, found: dict) -> list[ProposedFieldChange]:
    changes = []
    for field in FIELDS:
        new = found.get(field)
        old = current.get(field)
        if not new:
            continue
        if field in ("track_number", "disc_number"):
            if _number(old) == _number(new) and (old and "/" in str(old) or "/" not in new):
                continue
        elif field == "year":
            if str(old or "")[:4] == new:
                continue
        elif (old or "") == new:
            continue
        changes.append(ProposedFieldChange(field, old, new))
    return changes


def lookup(conn: sqlite3.Connection, config: Config, backend_name: str = "file", progress: Progress = SILENT,
           fetch: Fetch = net.get_json) -> dict:
    """Looks up every song not looked up before and proposes changes. Stoppable; resumes where it stopped."""
    backend = get_backend(backend_name)
    done = {r[0] for r in conn.execute("SELECT file_id FROM musicbrainz_lookups")}
    songs = [(fid, tags) for fid, tags in backend.iter_tags(conn, progress) if fid not in done]
    use_acoustid = config.acoustid.enabled and bool(config.acoustid.api_key)
    matched = proposed = errors = looked = 0
    for n, (fid, tags) in enumerate(songs, 1):
        if progress.stopping():
            break
        looked += 1
        progress.update(n - 1, len(songs), f"{tags.get('artist') or '?'} – {tags.get('title') or '?'}")
        try:
            rec, how = None, ""
            fp = db.get_fingerprint(conn, fid)
            mbid = fp["mb_recording_id"] if fp is not None else None
            if not mbid and use_acoustid and fp is not None and fp["chromaprint_fp"] and fp["duration_seconds"]:
                aid, mbid = lookup_acoustid(config.acoustid.api_key, fp["duration_seconds"], fp["chromaprint_fp"], fetch)
                if aid or mbid:
                    conn.execute("UPDATE fingerprints SET acoustid_id = COALESCE(?, acoustid_id), mb_recording_id = ? "
                                 "WHERE file_id = ?", (aid, mbid, fid))
            if mbid:
                rec, how = get_recording(mbid, fetch), "fingerprint"
            elif tags.get("artist") and tags.get("title"):
                rec, how = search_recording(tags["artist"], tags["title"], tags.get("duration_seconds"), fetch), "search"
            if not rec:
                db.record_musicbrainz_lookup(conn, fid, None, "no_match")
                conn.commit()
                continue
            matched += 1
            for c in changes_for(tags, details(rec, tags)):
                if not db.normalization_seen(conn, fid, RULE_ID, c.field, c.old_value, c.new_value):
                    db.add_normalization_log(conn, fid, RULE_ID, c.field, c.old_value, c.new_value, backend_name)
                    proposed += 1
            db.record_musicbrainz_lookup(conn, fid, rec.get("id"), f"matched_{how}")
        except net.NetError:
            errors += 1
            if errors >= 5 and matched == 0:
                conn.commit()
                raise  # the network is down: stop rather than mark everything as failed
            continue  # not recorded: tried again next time
        conn.commit()
    progress.update(len(songs), len(songs), "")
    return {"looked_up": looked, "matched": matched, "proposed": proposed,
            "errors": errors, "stopped": progress.stopping()}
