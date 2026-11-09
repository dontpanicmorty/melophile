"""MusicBrainz tag fixes (with canned replies, never the network) and Artist/Album folder sorting (on real files)."""
from pathlib import Path

import pytest
from conftest import make_audio
from mutagen import File as MutagenFile

from melophile import db, musicbrainz, net, normalize, organize, scan

REC_ID = "11111111-2222-3333-4444-555555555555"


def _release(title, date, track, count, artist="Drake", kind="Album", secondary=None, disc=1, discs=1):
    media = [{"position": disc, "track-count": count, "tracks": [{"position": track, "number": str(track)}]}]
    media += [{"position": d, "track-count": 10} for d in range(1, discs + 1) if d != disc]
    return {"id": f"rel-{title}", "title": title, "date": date, "status": "Official",
            "artist-credit": [{"name": artist}],
            "release-group": {"primary-type": kind, "secondary-types": secondary or []}, "media": media}


def _recording(**kw):
    rec = {"id": REC_ID, "title": "Too Good", "length": 263000, "score": 100,
           "artist-credit": [{"name": "Drake", "joinphrase": " feat. "}, {"name": "Rihanna"}],
           "releases": [_release("Views", "2016-04-29", 13, 20),
                        _release("Hits of 2016", "2016-11-01", 4, 40, artist="Various Artists", kind="Album",
                                 secondary=["Compilation"])]}
    rec.update(kw)
    return rec


class FakeMB:
    """Answers like MusicBrainz and AcoustID, and records every request."""

    def __init__(self, search=None, recording=None, acoustid=None, fail=False):
        self.search = search if search is not None else {"recordings": [_recording()]}
        self.recording = recording if recording is not None else _recording()
        self.acoustid = acoustid or {}
        self.fail = fail
        self.calls = []

    def __call__(self, url, params=None, rate=None, data=None):
        self.calls.append((url, params, data))
        if self.fail:
            raise net.NetError("offline")
        if "acoustid" in url:
            return self.acoustid
        if url.endswith("/recording"):
            return self.search
        return self.recording


def _scan_one(conn, config, tmp_path, name="song.mp3", **tags):
    lib = tmp_path / "library"
    path = make_audio(lib / "in" / name, seconds=4, tags=tags)
    scan.scan_root(conn, str(lib), config)
    return path, db.get_file_by_path(conn, str(path))["id"]


def _proposed(conn):
    return {e["field"]: e["new_value"] for e in db.get_normalizations(conn, "proposed") if e["rule_id"] == "musicbrainz"}


def test_details_follow_melophile_style_and_the_matching_album():
    d = musicbrainz.details(_recording(), {"album": "Views"})
    assert d == {"artist": "Drake", "title": "Too Good feat. Rihanna", "album": "Views", "album_artist": "Drake",
                 "year": "2016", "track_number": "13/20"}


def test_no_album_picks_the_earliest_official_album_not_a_compilation():
    assert musicbrainz.details(_recording(), {})["album"] == "Views"


def test_an_album_matching_no_release_is_left_alone():
    d = musicbrainz.details(_recording(), {"album": "Some Bootleg"})
    assert "album" not in d and "track_number" not in d and "year" not in d
    assert d["artist"] == "Drake"


def test_disc_number_only_for_multi_disc_releases():
    rec = _recording(releases=[_release("Double", "2001", 2, 12, disc=2, discs=2)])
    assert musicbrainz.details(rec, {"album": "Double"})["disc_number"] == "2"
    assert "disc_number" not in musicbrainz.details(_recording(), {"album": "Views"})


def test_unchanged_and_empty_values_are_not_proposed():
    current = {"artist": "Drake", "title": "Too Good feat. Rihanna", "album": "Views", "track_number": "13",
               "year": "2016-04-29"}
    found = {"artist": "Drake", "title": "Too Good feat. Rihanna", "album": "Views", "track_number": "13/20",
             "year": "2016", "album_artist": ""}
    assert [(c.field, c.new_value) for c in musicbrainz.changes_for(current, found)] == [("track_number", "13/20")]


def test_search_accepts_only_a_close_match_of_the_same_length():
    fetch = FakeMB()
    assert musicbrainz.search_recording("Drake feat. Rihanna", "Too Good", 263, fetch)["id"] == REC_ID
    assert musicbrainz.search_recording("Drake", "Too Good", 200, fetch) is None   # a different length
    assert musicbrainz.search_recording("Someone", "Too Good", 263, fetch) is None  # a different artist
    low = FakeMB(search={"recordings": [_recording(score=80)]})
    assert musicbrainz.search_recording("Drake", "Too Good", 263, low) is None


def test_lookup_proposes_applies_and_reverts_real_tags(conn, config, tmp_path):
    path, fid = _scan_one(conn, config, tmp_path, artist="drake ft rihanna", title="Too Good", album="Views")
    conn.execute("UPDATE tags SET duration_seconds = 263 WHERE file_id = ?", (fid,))  # the fake recording's length
    fetch = FakeMB()
    r = musicbrainz.lookup(conn, config, "file", fetch=fetch)
    assert r["matched"] == 1 and r["proposed"] >= 4
    p = _proposed(conn)
    assert p["artist"] == "Drake" and p["title"] == "Too Good feat. Rihanna" and p["track_number"] == "13/20"
    ids = [e["id"] for e in db.get_normalizations(conn, "proposed")]
    assert normalize.apply_many(conn, ids)["errors"] == []
    audio = MutagenFile(str(path), easy=True)
    assert audio["artist"] == ["Drake"] and audio["tracknumber"] == ["13/20"] and audio["albumartist"] == ["Drake"]
    applied = [e["id"] for e in db.get_normalizations(conn, "applied")]
    assert normalize.revert_many(conn, applied)["errors"] == []
    audio = MutagenFile(str(path), easy=True)
    assert audio["artist"] == ["drake ft rihanna"] and "tracknumber" not in audio and "albumartist" not in audio


def test_each_song_is_looked_up_once(conn, config, tmp_path):
    _scan_one(conn, config, tmp_path, artist="Drake", title="Too Good")
    fetch = FakeMB(search={"recordings": []})
    assert musicbrainz.lookup(conn, config, "file", fetch=fetch)["looked_up"] == 1
    assert musicbrainz.lookup(conn, config, "file", fetch=fetch)["looked_up"] == 0
    assert len(fetch.calls) == 1


def test_fingerprint_match_through_acoustid_is_preferred(conn, config, tmp_path):
    _, fid = _scan_one(conn, config, tmp_path, artist="Wrong", title="Tags")
    db.upsert_fingerprint(conn, fid, "AQAAcompressed", "1,2,3", 263.0)
    config.acoustid.enabled, config.acoustid.api_key = True, "key"
    fetch = FakeMB(acoustid={"results": [{"id": "aid-1", "score": 0.97, "recordings": [{"id": REC_ID}]}]})
    musicbrainz.lookup(conn, config, "file", fetch=fetch)
    assert [c[0].rsplit("/", 1)[-1] for c in fetch.calls] == ["lookup", REC_ID]  # no text search needed
    assert fetch.calls[0][2]["client"] == "key"
    assert _proposed(conn)["artist"] == "Drake"
    assert db.get_fingerprint(conn, fid)["mb_recording_id"] == REC_ID


def test_offline_stops_without_marking_songs_done(conn, config, tmp_path):
    lib = tmp_path / "library"
    for i in range(6):
        make_audio(lib / f"{i}.mp3", tags={"artist": "A", "title": f"T{i}"}, freq=300 + 20 * i)
    scan.scan_root(conn, str(lib), config)
    with pytest.raises(net.NetError):
        musicbrainz.lookup(conn, config, "file", fetch=FakeMB(fail=True))
    assert db.musicbrainz_stats(conn) == {}


# ---------------------------------------------------------------- organize ----

def _library(conn, config, tmp_path):
    lib = tmp_path / "library"
    a = make_audio(lib / "Old iTunes" / "track01.mp3", tags={"artist": "Drake feat. Rihanna", "title": "Too: Good",
                                                            "album": "Views", "track": "13/20"})
    b = make_audio(lib / "misc" / "x.flac", tags={"artist": "Someone", "album_artist": "Various Artists",
                                                 "title": "Thing", "album": "Mix", "track": "2", "disc": "2/2"})
    c = make_audio(lib / "unknown.m4a")
    managed = make_audio(lib / "Music" / "Media.localized" / "Music" / "A" / "B" / "01 S.m4a",
                         tags={"artist": "A", "title": "S"})
    scan.scan_root(conn, str(lib), config)
    return lib, a, b, c, managed


def test_plan_moves_nothing_and_skips_music_app_files(conn, config, tmp_path):
    lib, a, b, c, managed = _library(conn, config, tmp_path)
    r = organize.plan(conn, config, str(lib))
    assert r == {"planned": 3, "already_in_place": 0, "skipped_music_app": 1}
    assert a.exists() and b.exists() and c.exists()
    targets = sorted(Path(m["new_path"]).relative_to(lib).as_posix() for m in db.get_moves(conn, "planned"))
    assert targets == ["Drake/Views/13 Too- Good.mp3", "Unknown Artist/Unknown Album/unknown.m4a",
                       "Various Artists/Mix/2-02 Thing.flac"]


def test_apply_then_undo(conn, config, tmp_path):
    lib, a, *_ = _library(conn, config, tmp_path)
    organize.plan(conn, config, str(lib))
    ids = [m["id"] for m in db.get_moves(conn, "planned")]
    assert organize.apply_moves(conn, config, ids)["moved"] == 3
    moved = lib / "Drake" / "Views" / "13 Too- Good.mp3"
    assert moved.exists() and not a.exists() and not (lib / "Old iTunes").exists()  # emptied folder removed
    assert db.get_file_by_path(conn, str(moved)) is not None
    assert scan.scan_root(conn, str(lib), config)["files_missing"] == 0
    assert organize.plan(conn, config, str(lib))["planned"] == 0  # already in place
    assert organize.undo_moves(conn, config, [m["id"] for m in db.get_moves(conn, "applied")])["moved_back"] == 3
    assert a.exists() and not moved.exists()


def test_never_overwrites(conn, config, tmp_path):
    lib, a, *_ = _library(conn, config, tmp_path)
    organize.plan(conn, config, str(lib))
    (lib / "Drake" / "Views").mkdir(parents=True)
    (lib / "Drake" / "Views" / "13 Too- Good.mp3").write_bytes(b"someone else's")
    ids = [m["id"] for m in db.get_moves(conn, "planned")]
    organize.apply_moves(conn, config, ids)
    assert (lib / "Drake" / "Views" / "13 Too- Good.mp3").read_bytes() == b"someone else's"
    assert (lib / "Drake" / "Views" / "13 Too- Good (2).mp3").exists()


def test_a_file_that_moved_since_the_plan_is_left(conn, config, tmp_path):
    lib, a, *_ = _library(conn, config, tmp_path)
    organize.plan(conn, config, str(lib))
    a.rename(a.with_name("renamed.mp3"))
    r = organize.apply_moves(conn, config, [m["id"] for m in db.get_moves(conn, "planned")])
    assert r["moved"] == 2 and len(r["errors"]) == 1


def test_only_into_a_music_folder(conn, config, tmp_path):
    _library(conn, config, tmp_path)
    with pytest.raises(organize.OrganizeError):
        organize.plan(conn, config, str(tmp_path / "elsewhere"))


def test_safe_names():
    assert organize.safe_name("AC/DC: Live", "x") == "AC-DC- Live"
    assert organize.safe_name("...hidden", "x") == "hidden"
    assert organize.safe_name("  ", "Unknown") == "Unknown"
    assert len(organize.safe_name("a" * 300, "x")) == organize.MAX_NAME
