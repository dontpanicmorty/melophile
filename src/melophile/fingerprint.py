"""Acoustic fingerprints with Chromaprint's fpcalc, compared offline.

A raw fingerprint is a list of 32-bit numbers, one per ~0.12 s of audio. Two copies of the same recording
(say an MP3 and a FLAC) give nearly the same numbers, with a few bits different.
"""
from __future__ import annotations

from . import tools

FPCALC_SECONDS = 120  # how much of each song fpcalc listens to (its default); enough to tell songs apart
FPCALC_TIMEOUT = 60   # seconds before a stuck fpcalc is stopped
INDEX_FRAMES = 240    # frames of each fingerprint put in the candidate index (about 30 s of audio)
ACOUSTID_URL = "https://api.acoustid.org/v2/lookup"
INDEX_SHIFT = 12      # index on each frame's top 20 bits, which survive re-encoding most often


def _fpcalc(path: str, fpcalc: str, raw: bool) -> tuple[float | None, str | None]:
    cmd = [fpcalc, "-length", str(FPCALC_SECONDS)]
    if raw:
        cmd.append("-raw")
    cmd.append(path)
    out = tools.run(cmd, FPCALC_TIMEOUT, "fpcalc").stdout.decode("utf-8", "replace")
    duration = fingerprint = None
    for line in out.splitlines():
        if line.startswith("DURATION="):
            duration = float(line.split("=", 1)[1])
        elif line.startswith("FINGERPRINT="):
            fingerprint = line.split("=", 1)[1].strip()
    return duration, fingerprint


def compute_fingerprint(path: str, fpcalc: str, compressed: bool = False) -> dict:
    """The raw fingerprint, for offline comparison. The compressed one is only needed for AcoustID lookups,
    so fpcalc runs a second time only when those are turned on."""
    duration, raw = _fpcalc(path, fpcalc, raw=True)
    comp = _fpcalc(path, fpcalc, raw=False)[1] if compressed else None
    if not raw:
        raise tools.ToolError("fpcalc returned no fingerprint")
    return {"duration_seconds": duration, "raw_fp": raw, "chromaprint_fp": comp}


def lookup_acoustid(api_key: str, duration: float, chromaprint_fp: str, fetch=None) -> tuple[str | None, str | None]:
    """Online cross-check with AcoustID, using the person's own free application key. Returns the AcoustID of
    the best match and its MusicBrainz recording ID; (None, None) when there's no confident match."""
    from . import net

    r = (fetch or net.get_json)(ACOUSTID_URL, rate=net.ACOUSTID_RATE, data={
        "client": api_key, "duration": int(duration), "fingerprint": chromaprint_fp,
        "meta": "recordingids", "format": "json"})
    results = [x for x in r.get("results", []) if x.get("score", 0) >= 0.8]
    if not results:
        return None, None
    best = max(results, key=lambda x: x.get("score", 0))
    recordings = best.get("recordings") or []
    return best.get("id"), (recordings[0].get("id") if recordings else None)


def parse_raw(raw_fp: str | None) -> list[int]:
    if not raw_fp:
        return []
    out = []
    for x in raw_fp.split(","):
        x = x.strip()
        if x:
            out.append(int(x) & 0xFFFFFFFF)  # fpcalc prints signed numbers
    return out


def index_keys(frames: list[int]) -> set[int]:
    return {f >> INDEX_SHIFT for f in frames[:INDEX_FRAMES]}


def similarity(a: list[int], b: list[int], max_offset: int = 20, length: int = 512) -> float:
    """1.0 = identical, about 0.5 = unrelated audio. Tries small alignment offsets (encoders add a little
    silence at the start) and returns the best bit agreement found."""
    if not a or not b:
        return 0.0
    best = 0.0
    for offset in range(-max_offset, max_offset + 1):
        xs, ys = (a[offset:], b) if offset >= 0 else (a, b[-offset:])
        n = min(len(xs), len(ys), length)
        if n < 10:
            continue
        diff = sum((xs[i] ^ ys[i]).bit_count() for i in range(n))
        best = max(best, 1.0 - diff / (32.0 * n))
    return best


def compare_raw_fingerprints(raw_a: str, raw_b: str, max_offset: int = 20) -> float:
    return similarity(parse_raw(raw_a), parse_raw(raw_b), max_offset)
