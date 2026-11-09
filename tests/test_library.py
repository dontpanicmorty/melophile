"""Scanning, tag cleanup, duplicate decisions and conversion, on real (generated) audio files."""
import os
import stat
import time
from pathlib import Path

import pytest
from conftest import make_audio
from mutagen import File as MutagenFile

from melophile import convert, db, normalize, review, scan, tools
from melophile.config import Config
from melophile.progress import Progress


# ------------------------------------------------------------------ scan ----

def test_scan_reads_real_files_and_is_incremental(conn, config, tmp_path):
    lib = tmp_path / "library"
    make_audio(lib / "a" / "song.mp3", tags={"artist": "Drake", "title": "Song", "track": "3/12"})
    make_audio(lib / "song.flac", tags={"artist": "Drake", "title": "Song"})
    (lib / "cover.jpg").write_bytes(b"x")
    (lib / ".hidden.mp3").write_bytes(b"x")
    r = scan.scan_root(conn, str(lib), config)
    assert (r["files_seen"], r["files_new"], r["files_failed"]) == (2, 2, 0)
    row = db.get_file_by_path(conn, str(lib / "a" / "song.mp3"))
    tags = db.get_tags(conn, row["id"])
    assert tags["artist"] == "Drake" and tags["track_number"] == "3/12" and 3.5 < tags["duration_seconds"] < 4.5
    assert tags["bitrate"]
    r = scan.scan_root(conn, str(lib), config)
    assert (r["files_new"], r["files_updated"], r["files_unchanged"]) == (0, 0, 2)


def test_scan_continues_past_a_bad_file(conn, config, tmp_path, monkeypatch):
    lib = tmp_path / "library"
    make_audio(lib / "good.mp3")
    (lib / "bad.mp3").write_bytes(b"not audio")
    real = scan.sha256_file
    monkeypatch.setattr(scan, "sha256_file",
                        lambda p: (_ for _ in ()).throw(OSError("network hiccup")) if Path(p).name == "bad.mp3" else real(p))
    r = scan.scan_root(conn, str(lib), config)
    assert r["files_failed"] == 1 and r["failures"][0][0].endswith("bad.mp3")
    assert db.get_file_by_path(conn, str(lib / "good.mp3")) is not None


def test_unreadable_audio_is_still_catalogued(conn, config, tmp_path):
    lib = tmp_path / "library"
    lib.mkdir()
    (lib / "broken.mp3").write_bytes(b"garbage" * 100)
    scan.scan_root(conn, str(lib), config)
    row = db.get_file_by_path(conn, str(lib / "broken.mp3"))
    assert row["sha256"] and db.get_tags(conn, row["id"])["read_error"]


def test_missing_files_marked_with_a_tilde_root(conn, config, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    lib = tmp_path / "Music"
    make_audio(lib / "a.mp3")
    make_audio(lib / "b.mp3", freq=500)
    scan.scan_root(conn, "~/Music", config)
    (lib / "b.mp3").unlink()
    r = scan.scan_root(conn, "~/Music", config)
    assert r["files_missing"] == 1
    assert db.get_file_by_path(conn, str(lib / "b.mp3"))["status"] == "missing"


def test_sibling_folder_with_similar_name_is_untouched(conn, config, tmp_path):
    """The old LIKE match treated '_' as a wildcard: scanning /Music_ also touched /MusicX."""
    a, b = tmp_path / "Music_", tmp_path / "MusicX"
    make_audio(a / "a.mp3")
    make_audio(b / "b.mp3")
    scan.scan_root(conn, str(a), config)
    scan.scan_root(conn, str(b), config)
    (a / "a.mp3").unlink()
    make_audio(a / "c.mp3")
    scan.scan_root(conn, str(a), config)
    assert db.get_file_by_path(conn, str(b / "b.mp3"))["status"] == "active"


def test_unplugged_drive_marks_nothing_missing(conn, config, tmp_path):
    lib = tmp_path / "library"
    make_audio(lib / "a.mp3")
    scan.scan_root(conn, str(lib), config)
    (lib / "a.mp3").unlink()  # the share came back empty
    r = scan.scan_root(conn, str(lib), config)
    assert "warning" in r and "Nothing was marked missing" in r["warning"]
    assert db.get_file_by_path(conn, str(lib / "a.mp3"))["status"] == "active"
    lib.rmdir()  # the folder itself is gone
    with pytest.raises(scan.ScanError):
        scan.scan_root(conn, str(lib), config)


def test_quarantine_folder_inside_the_library_is_not_rescanned(conn, config, tmp_path):
    lib = tmp_path / "library"
    config.quarantine_dir = lib / "Melophile Quarantine"
    make_audio(lib / "a.mp3")
    make_audio(config.quarantine_dir / "old.mp3")
    assert scan.scan_root(conn, str(lib), config)["files_seen"] == 1


def test_stopping_a_scan_keeps_progress_and_marks_nothing_missing(conn, config, tmp_path):
    lib = tmp_path / "library"
    for i in range(3):
        make_audio(lib / f"{i}.mp3", freq=300 + i * 50)
    seen = []
    p = Progress(lambda d, t, m: seen.append(d), should_stop=lambda: len(seen) >= 2)
    r = scan.scan_root(conn, str(lib), config, p)
    assert r["stopped"] and r["files_missing"] == 0
    assert len(db.get_active_files(conn)) >= 1


# ----------------------------------------------------------- tag cleanup ----

@pytest.mark.parametrize("ext", [".mp3", ".flac", ".m4a"])
def test_apply_and_revert_write_the_real_file(conn, config, tmp_path, ext):
    lib = tmp_path / "library"
    path = make_audio(lib / f"song{ext}", tags={"artist": "Drake feat. Rihanna", "title": "Song  (Official Video)"})
    scan.scan_root(conn, str(lib), config)
    assert normalize.propose_changes(conn, "file") >= 2
    ids = [e["id"] for e in db.get_normalizations(conn, "proposed")]
    assert normalize.apply_many(conn, ids)["errors"] == []
    audio = MutagenFile(str(path), easy=True)
    assert audio["artist"] == ["Drake"] and audio["title"] == ["Song feat. Rihanna"]
    # the catalog matches the changed file, so a scan finds nothing new
    assert scan.scan_root(conn, str(lib), config)["files_updated"] == 0
    applied = [e["id"] for e in db.get_normalizations(conn, "applied")]
    assert normalize.revert_many(conn, applied)["errors"] == []
    audio = MutagenFile(str(path), easy=True)
    assert audio["artist"] == ["Drake feat. Rihanna"] and audio["title"] == ["Song  (Official Video)"]


def test_mp3_without_any_tags_can_be_written(conn, config, tmp_path):
    lib = tmp_path / "library"
    path = make_audio(lib / "bare.mp3")
    from mutagen.id3 import ID3

    ID3(str(path)).delete()
    scan.scan_root(conn, str(lib), config)
    fid = db.get_file_by_path(conn, str(path))["id"]
    from melophile.backends import FileBackend

    FileBackend().write_field(conn, fid, "title", "Named")
    assert MutagenFile(str(path), easy=True)["title"] == ["Named"]


def test_rejected_changes_are_not_proposed_again(conn, config, tmp_path):
    lib = tmp_path / "library"
    make_audio(lib / "s.mp3", tags={"artist": "  Drake ", "title": "Song"})
    scan.scan_root(conn, str(lib), config)
    assert normalize.propose_changes(conn, "file") == 1
    assert normalize.propose_changes(conn, "file") == 0  # not a second copy
    (e,) = db.get_normalizations(conn, "proposed")
    normalize.reject_change(conn, e["id"])
    assert normalize.propose_changes(conn, "file") == 0


def test_a_tag_edited_since_proposing_is_not_overwritten(conn, config, tmp_path):
    lib = tmp_path / "library"
    path = make_audio(lib / "s.mp3", tags={"artist": "  Drake ", "title": "Song"})
    scan.scan_root(conn, str(lib), config)
    normalize.propose_changes(conn, "file")
    (e,) = db.get_normalizations(conn, "proposed")
    audio = MutagenFile(str(path), easy=True)
    audio["artist"] = ["Someone Else"]
    audio.save()
    with pytest.raises(normalize.StaleChange):
        normalize.apply_change(conn, e["id"])
    assert MutagenFile(str(path), easy=True)["artist"] == ["Someone Else"]


def test_drm_and_untaggable_formats_are_skipped(conn, config, tmp_path):
    lib = tmp_path / "library"
    make_audio(lib / "a.wav", tags={"artist": "  Drake "})
    scan.scan_root(conn, str(lib), config)
    assert normalize.propose_changes(conn, "file") == 0


# ------------------------------------------------------------ duplicates ----

def _group(conn, config, tmp_path, n=3):
    lib = tmp_path / "library"
    paths = [make_audio(lib / f"d{i}" / "song.mp3") for i in range(n)]
    scan.scan_root(conn, str(lib), config)
    ids = [db.get_file_by_path(conn, str(p))["id"] for p in paths]
    return db.create_duplicate_group(conn, "exact_hash", 1.0, ids), ids, paths


def test_keep_must_be_a_member_so_nothing_is_lost(conn, config, tmp_path, no_trash):
    gid, ids, paths = _group(conn, config, tmp_path)
    for bad in (None, 9999):
        with pytest.raises(review.ReviewError):
            review.resolve_duplicate_group(conn, config.quarantine_dir, gid, "keep_one_trash_rest", bad,
                                           confirm_trash=True)
    assert all(p.exists() for p in paths) and no_trash == []


def test_trash_needs_confirmation(conn, config, tmp_path, no_trash):
    gid, ids, paths = _group(conn, config, tmp_path)
    with pytest.raises(review.ReviewError):
        review.resolve_duplicate_group(conn, config.quarantine_dir, gid, "keep_one_trash_rest", ids[0])
    review.resolve_duplicate_group(conn, config.quarantine_dir, gid, "keep_one_trash_rest", ids[0], confirm_trash=True)
    assert paths[0].exists() and sorted(no_trash) == sorted(map(str, paths[1:]))
    assert db.get_file(conn, ids[1])["status"] == "trashed"
    with pytest.raises(review.ReviewError):  # decided already
        review.resolve_duplicate_group(conn, config.quarantine_dir, gid, "keep_all")


def test_quarantine_keeps_structure_and_never_overwrites(conn, config, tmp_path):
    gid, ids, paths = _group(conn, config, tmp_path)
    already = config.quarantine_dir / Path(*paths[1].parts[1:])
    already.parent.mkdir(parents=True)
    already.write_bytes(b"earlier")
    review.resolve_duplicate_group(conn, config.quarantine_dir, gid, "keep_one_quarantine_rest", ids[0])
    assert already.read_bytes() == b"earlier"
    moved = db.get_file(conn, ids[1])
    assert moved["status"] == "quarantined" and moved["path"].endswith("song (2).mp3") and Path(moved["path"]).exists()


def test_isolate_moves_one_format(conn, config, tmp_path):
    lib = tmp_path / "library"
    make_audio(lib / "sub" / "a.flac")
    make_audio(lib / "sub" / "b.mp3")
    scan.scan_root(conn, str(lib), config)
    moved = review.isolate_files_by_format(conn, "flac", tmp_path / "flacs")
    assert len(moved) == 1 and Path(moved[0][1]).exists() and (lib / "sub" / "b.mp3").exists()
    assert review.isolate_files_by_format(conn, "flac", tmp_path / "flacs") == []  # already there


# ------------------------------------------------------------ conversion ----

def test_conversion_keeps_tags_and_art(conn, config, tmp_path):
    lib = tmp_path / "library"
    src = make_audio(lib / "song.mp3", seconds=5, cover=True,
                     tags={"artist": "Drake", "title": "Song", "album": "Album", "track": "3/12", "date": "2016"})
    scan.scan_root(conn, str(lib), config)
    assert convert.enqueue(conn, "mp3") == 1
    assert convert.enqueue(conn, "mp3") == 0  # not twice
    r = convert.run_queue(conn, config)
    assert r == {"succeeded": 1, "failed": 0, "remaining": 0}
    from mutagen.mp4 import MP4

    m4a = MP4(str(lib / "song.m4a"))
    assert m4a["\xa9ART"] == ["Drake"] and m4a["\xa9alb"] == ["Album"] and m4a["trkn"] == [(3, 12)]
    assert m4a.get("covr"), "album art was lost"
    assert abs(m4a.info.length - 5) < 1 and src.exists()
    assert not list(lib.glob(".*partial*"))


def test_conversion_never_overwrites(conn, config, tmp_path):
    lib = tmp_path / "library"
    make_audio(lib / "song.flac")
    (lib / "song.m4a").write_bytes(b"mine")
    scan.scan_root(conn, str(lib), config)
    convert.enqueue(conn, "flac")
    r = convert.run_queue(conn, config)
    assert r["failed"] == 1 and (lib / "song.m4a").read_bytes() == b"mine"
    (job,) = db.recent_conversion_jobs(conn)
    assert "already exists" in job["error_message"]


def test_failed_conversion_leaves_original_and_no_partial(conn, config, tmp_path, no_trash):
    lib = tmp_path / "library"
    src = make_audio(lib / "song.mp3")
    scan.scan_root(conn, str(lib), config)
    src.write_bytes(b"corrupted since the scan")
    convert.enqueue(conn, "mp3", "trash")
    assert convert.run_queue(conn, config)["failed"] == 1
    assert src.exists() and no_trash == [] and not (lib / "song.m4a").exists()
    assert not list(lib.glob(".*partial*"))


def test_trash_disposition_after_success(conn, config, tmp_path, no_trash):
    lib = tmp_path / "library"
    src = make_audio(lib / "song.flac")
    scan.scan_root(conn, str(lib), config)
    convert.enqueue(conn, "flac", "trash")
    assert convert.run_queue(conn, config)["succeeded"] == 1
    assert no_trash == [str(src)] and (lib / "song.m4a").exists()


def test_interrupted_job_is_requeued(conn, config, tmp_path):
    lib = tmp_path / "library"
    make_audio(lib / "song.mp3")
    scan.scan_root(conn, str(lib), config)
    convert.enqueue(conn, "mp3")
    conn.execute("UPDATE conversion_jobs SET status='running'")
    conn.commit()
    assert convert.run_queue(conn, config)["succeeded"] == 1


# ----------------------------------------------------- tools and settings ----

def test_a_stuck_program_is_stopped():
    start = time.monotonic()
    with pytest.raises(tools.ToolError, match="longer than"):
        tools.run(["/bin/sleep", "30"], 0.5, "sleep")
    assert time.monotonic() - start < 5


def test_a_missing_program_is_reported():
    with pytest.raises(tools.ToolError):
        tools.run(["/nonexistent/fpcalc"], 5, "fpcalc")


def test_fpcalc_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert tools.find_fpcalc("") is None
    fake = tmp_path / "fpcalc"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    assert tools.find_fpcalc("") == str(fake)
    assert tools.find_fpcalc(str(fake)) == str(fake)


def test_settings_round_trip_privately(tmp_path):
    c = Config(library_roots=["/x"], config_path=tmp_path / "s" / "config.toml")
    c.acoustid.api_key = "secret"
    c.dedupe.acoustic_threshold = 0.93
    c.save()
    assert stat.S_IMODE(os.stat(c.config_path).st_mode) == 0o600
    d = Config.load(c.config_path)
    assert d.library_roots == ["/x"] and d.acoustid.api_key == "secret" and d.dedupe.acoustic_threshold == 0.93


def test_settings_out_of_range_are_clamped(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[dedupe]\nacoustic_threshold = 7\ntext_threshold = "x"\n[convert]\nbitrate = "9999k"\n')
    c = Config.load(p)
    assert c.dedupe.acoustic_threshold == 1.0 and c.dedupe.text_threshold == 0.85 and c.bitrate == "256k"


def test_missing_settings_give_defaults(tmp_path):
    c = Config.load(tmp_path / "nope.toml")
    assert not c.exists and c.library_roots == []
