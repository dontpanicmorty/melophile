import random

import pytest

from melophile import db, dedupe
from melophile.fingerprint import compare_raw_fingerprints, parse_raw, similarity


def _fp(seed: int, n: int = 600) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(n)]


def _noisy(frames: list[int], flips_per_frame: int, seed: int, shift: int = 0) -> list[int]:
    """The same recording re-encoded: a few bits differ in each frame, and the start may move a little."""
    rng = random.Random(seed)
    out = []
    for f in frames:
        for _ in range(flips_per_frame):
            f ^= 1 << rng.randrange(32)
        out.append(f)
    return [rng.getrandbits(32) for _ in range(shift)] + out


def _raw(frames):
    # fpcalc prints signed 32-bit numbers
    return ",".join(str(f - (1 << 32) if f >= 1 << 31 else f) for f in frames)


def _add(conn, path, frames, duration=200.0, artist="A", title="T", sha=None):
    fid, _ = db.upsert_file(conn, path, 1, 1.0, sha or path, "mp3")
    db.upsert_tags(conn, fid, {"artist": artist, "title": title, "duration_seconds": duration})
    db.upsert_fingerprint(conn, fid, None, _raw(frames), duration)
    return fid


def test_similarity_bounds():
    fp = _fp(1, 50)
    assert similarity(fp, fp) == pytest.approx(1.0)
    assert compare_raw_fingerprints(",".join("0" for _ in range(50)), ",".join("-1" for _ in range(50))) == 0.0
    assert 0.4 < similarity(_fp(1), _fp(2)) < 0.6  # unrelated audio agrees on about half the bits


def test_parse_raw_handles_signed_numbers():
    assert parse_raw("-1,0,5") == [0xFFFFFFFF, 0, 5]


def test_exact_groups(conn):
    a, _ = db.upsert_file(conn, "/m/a.mp3", 1, 1.0, "h1", "mp3")
    b, _ = db.upsert_file(conn, "/m/b.mp3", 1, 1.0, "h1", "mp3")
    db.upsert_file(conn, "/m/c.mp3", 1, 1.0, "h2", "mp3")
    assert [set(g) for g in dedupe.find_exact_duplicate_groups(conn)] == [{a, b}]
    db.create_duplicate_group(conn, "exact_hash", 1.0, [a, b])
    assert dedupe.find_exact_duplicate_groups(conn) == []


def test_acoustic_finds_reencoded_copies_and_not_strangers(conn, config):
    song = _fp(10)
    a = _add(conn, "/m/song.mp3", song)
    b = _add(conn, "/m/song.flac", _noisy(song, 1, 1, shift=3), duration=201.0)
    c = _add(conn, "/m/song.m4a", _noisy(song, 2, 2), duration=199.5)
    for i in range(30):  # other songs of the same length
        _add(conn, f"/m/other{i}.mp3", _fp(100 + i), artist=f"X{i}", title=f"Y{i}")
    groups = dedupe.find_acoustic_duplicate_groups(conn, config)
    assert [set(g["file_ids"]) for g in groups] == [{a, b, c}]
    assert groups[0]["tier"] == "acoustic" and groups[0]["confidence"] >= 0.9


def test_acoustic_respects_length_tolerance(conn, config):
    song = _fp(11)
    _add(conn, "/m/a.mp3", song, duration=200)
    _add(conn, "/m/b.mp3", _noisy(song, 1, 3), duration=260)  # an extended mix
    assert dedupe.find_acoustic_duplicate_groups(conn, config) == []


def test_borderline_sound_needs_matching_names(conn, config):
    song = _fp(12)
    _add(conn, "/m/a.mp3", song, artist="Drake", title="Song")
    _add(conn, "/m/b.mp3", _noisy(song, 4, 4), artist="Drake", title="Song (Official Video)")
    _add(conn, "/m/c.mp3", _noisy(song, 4, 5), artist="Someone", title="Else")
    groups = dedupe.find_acoustic_duplicate_groups(conn, config)
    assert len(groups) == 1 and groups[0]["tier"] == "text_heuristic" and len(groups[0]["file_ids"]) == 2


def test_run_dedupe_never_regroups(conn, config):
    song = _fp(13)
    _add(conn, "/m/a.mp3", song)
    _add(conn, "/m/b.mp3", _noisy(song, 1, 6))
    assert dedupe.run_dedupe(conn, config) == {"exact_groups": 0, "acoustic_groups": 1}
    assert dedupe.run_dedupe(conn, config) == {"exact_groups": 0, "acoustic_groups": 0}


def test_large_library_is_quick(conn, config):
    """5,000 songs of similar length: the old all-pairs comparison would take hours; the index takes seconds."""
    import time

    for i in range(5000):
        _add(conn, f"/m/{i}.mp3", _fp(1000 + i, 200), duration=180 + (i % 60) / 10, artist=f"A{i}", title=f"T{i}")
    song = _fp(99, 200)
    _add(conn, "/m/x.mp3", song, duration=200)
    _add(conn, "/m/y.flac", _noisy(song, 1, 7), duration=200)
    start = time.monotonic()
    groups = dedupe.find_acoustic_duplicate_groups(conn, config)
    assert len(groups) == 1
    assert time.monotonic() - start < 30


def test_names_alone_never_match(conn, config):
    song = _fp(14)
    _add(conn, "/m/a.mp3", song, artist="Drake", title="Song")
    _add(conn, "/m/b.mp3", _noisy(song, 9, 8), artist="Drake", title="Song")  # ~0.72 sound in common
    assert dedupe.find_acoustic_duplicate_groups(conn, config) == []
