"""Finding duplicates, in two passes.

1. Byte-identical files (same SHA-256): certain.
2. The same recording saved differently (an MP3 and a FLAC of one song): by acoustic fingerprint. Comparing
   every song with every other would take hours on a big library, so candidates come from an index first:
   two copies of one recording share many fingerprint frames exactly, unrelated songs almost never do.
   Only those candidates of similar length are compared in full. A borderline sound match is accepted
   (as a weaker 'text_heuristic' group) only when the artist and title agree too.

Files already in a group, whatever was decided about it, are never grouped again.
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict

from rapidfuzz import fuzz

from . import db
from .config import Config
from .fingerprint import index_keys, parse_raw, similarity
from .progress import SILENT, Progress

MIN_SHARED_KEYS = 2      # index keys two files must share to be compared in full (unrelated songs share ~0.05)
MIN_SOUND_FOR_NAMES = 0.75  # a weaker, names-agree match still needs this much sound in common
MAX_POSTING = 200        # an index key shared by more files than this is too common to tell songs apart

_FEAT_RE = re.compile(r"\b(feat\.?|ft\.?|featuring)(?!\w).*$", re.IGNORECASE)
_BRACKETED_RE = re.compile(r"[\(\[].*?[\)\]]")
_SPACE_RE = re.compile(r"\s+")


def normalize_for_compare(text: str | None) -> str:
    if not text:
        return ""
    text = _BRACKETED_RE.sub("", text)
    text = _FEAT_RE.sub("", text)
    return _SPACE_RE.sub(" ", text).strip().casefold()


def text_similarity(artist_a: str | None, title_a: str | None, artist_b: str | None, title_b: str | None) -> float:
    a = f"{normalize_for_compare(artist_a)} {normalize_for_compare(title_a)}".strip()
    b = f"{normalize_for_compare(artist_b)} {normalize_for_compare(title_b)}".strip()
    if not a or not b:
        return 0.0
    return fuzz.token_sort_ratio(a, b) / 100.0


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def find_exact_duplicate_groups(conn: sqlite3.Connection) -> list[list[int]]:
    grouped = db.files_already_grouped(conn)
    by_hash: dict[str, list[int]] = defaultdict(list)
    for f in db.get_active_files(conn):
        if f["id"] not in grouped and f["sha256"]:
            by_hash[f["sha256"]].append(f["id"])
    return [ids for ids in by_hash.values() if len(ids) > 1]


def _candidates(conn: sqlite3.Connection) -> list[dict]:
    grouped = db.files_already_grouped(conn)
    rows = conn.execute(
        """SELECT f.id, fp.raw_fp, fp.acoustid_id, COALESCE(fp.duration_seconds, t.duration_seconds) AS duration,
                  t.artist, t.title
           FROM files f JOIN fingerprints fp ON fp.file_id = f.id LEFT JOIN tags t ON t.file_id = f.id
           WHERE f.status = 'active' AND fp.raw_fp IS NOT NULL ORDER BY f.id""").fetchall()
    out = []
    for r in rows:
        if r["id"] in grouped:
            continue
        frames = parse_raw(r["raw_fp"])
        if frames:
            out.append({"id": r["id"], "frames": frames, "keys": index_keys(frames), "aid": r["acoustid_id"],
                        "duration": r["duration"] or 0.0, "artist": r["artist"], "title": r["title"]})
    return out


def find_acoustic_duplicate_groups(conn: sqlite3.Connection, config: Config, progress: Progress = SILENT) -> list[dict]:
    cands = _candidates(conn)
    by_id = {c["id"]: c for c in cands}
    tolerance = config.dedupe.duration_tolerance_seconds

    # pairs worth comparing: sharing enough index keys, or the same AcoustID
    postings: dict[int, list[int]] = defaultdict(list)
    for c in cands:
        for k in c["keys"]:
            postings[k].append(c["id"])
    shared: Counter[tuple[int, int]] = Counter()
    for ids in postings.values():
        if len(ids) < 2 or len(ids) > MAX_POSTING:
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                shared[(ids[i], ids[j])] += 1
    pairs = {p for p, n in shared.items() if n >= MIN_SHARED_KEYS}
    by_aid: dict[str, list[int]] = defaultdict(list)
    for c in cands:
        if c["aid"]:
            by_aid[c["aid"]].append(c["id"])
    for ids in by_aid.values():
        pairs.update((ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids)))

    uf = UnionFind()
    scores: dict[tuple[int, int], tuple[float, str]] = {}
    ordered = sorted(pairs)
    for n, (a, b) in enumerate(ordered, 1):
        if n % 200 == 0:
            progress.update(n, len(ordered), "Comparing fingerprints")
            progress.check()
        ca, cb = by_id[a], by_id[b]
        if abs(ca["duration"] - cb["duration"]) > tolerance:
            continue
        if ca["aid"] and ca["aid"] == cb["aid"]:
            score, tier = 1.0, "acoustic"
        else:
            score, tier = similarity(ca["frames"], cb["frames"]), "acoustic"
            if score < config.dedupe.acoustic_threshold:
                if score < MIN_SOUND_FOR_NAMES:
                    continue  # matching names alone never make two recordings the same
                combined = (score + text_similarity(ca["artist"], ca["title"], cb["artist"], cb["title"])) / 2
                if combined < config.dedupe.text_threshold:
                    continue
                score, tier = combined, "text_heuristic"
        uf.union(a, b)
        scores[(a, b)] = (score, tier)

    members: dict[int, list[int]] = defaultdict(list)
    for a, b in scores:
        for x in (a, b):
            members[uf.find(x)].append(x)
    pair_by_root: dict[int, list[tuple[tuple[int, int], tuple[float, str]]]] = defaultdict(list)
    for pair, v in scores.items():
        pair_by_root[uf.find(pair[0])].append((pair, v))
    groups = []
    for root, ids in members.items():
        ids = sorted(set(ids))
        rel = pair_by_root[root]
        per_file: dict[int, float] = {}
        for (a, b), (s, _) in rel:
            per_file[a] = max(per_file.get(a, 0.0), s)
            per_file[b] = max(per_file.get(b, 0.0), s)
        tiers = {t for _, (_, t) in rel}
        groups.append({"file_ids": ids, "confidence": min(s for _, (s, _) in rel), "scores": per_file,
                       "tier": "text_heuristic" if "text_heuristic" in tiers else "acoustic"})
    return groups


def run_dedupe(conn: sqlite3.Connection, config: Config, include_acoustic: bool = True,
               progress: Progress = SILENT) -> dict:
    progress.update(0, 0, "Looking for identical files")
    exact = find_exact_duplicate_groups(conn)
    for ids in exact:
        db.create_duplicate_group(conn, "exact_hash", 1.0, ids)
    conn.commit()
    acoustic = []
    if include_acoustic:
        progress.update(0, 0, "Comparing fingerprints")
        acoustic = find_acoustic_duplicate_groups(conn, config, progress)
        for g in acoustic:
            db.create_duplicate_group(conn, g["tier"], g["confidence"], g["file_ids"], g["scores"])
        conn.commit()
    return {"exact_groups": len(exact), "acoustic_groups": len(acoustic)}
