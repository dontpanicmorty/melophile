"""Tag-cleanup rules. Each is a pure function from the current tags to proposed changes, so each can be tested
on plain strings, with no files and no Music app."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class ProposedFieldChange:
    field: str  # 'artist' | 'title' | 'album' | 'album_artist' | 'track_number' | 'disc_number' | 'year'
    old_value: str | None
    new_value: str


@dataclass
class Rule:
    rule_id: str
    label: str
    propose: Callable[[dict], list[ProposedFieldChange]]


_FEAT_TOKEN_RE = re.compile(r"\b(?:featuring|feat\.?(?!\w)|ft\.?(?!\w))", re.IGNORECASE)


def _canonical_feat(raw_clause: str) -> str:
    """raw_clause starts at the trigger word, e.g. 'featuring Rihanna' or 'ft. Rihanna)'."""
    who = _FEAT_TOKEN_RE.sub("", raw_clause, count=1).strip(" ()[]-").strip()
    return f"feat. {who}" if who else "feat."


def feat_unify(tags: dict) -> list[ProposedFieldChange]:
    """Catches feat./Feat./ft./featuring, with or without a period or brackets. A clause stuck in the Artist
    field moves into the Title; one already in the Title but spelled differently is made consistent."""
    changes: list[ProposedFieldChange] = []
    artist = tags.get("artist") or ""
    title = tags.get("title") or ""
    m = _FEAT_TOKEN_RE.search(artist)
    if m:
        main_artist = artist[: m.start()].rstrip(" -,([").rstrip()
        clause = _canonical_feat(artist[m.start():])
        if clause == "feat." or not main_artist:
            return changes  # 'feat.' with nobody after it, or nothing before it: leave it to a person
        if clause.lower() not in title.lower():
            changes.append(ProposedFieldChange("title", title or None, f"{title} {clause}".strip()))
        if main_artist != artist:
            changes.append(ProposedFieldChange("artist", artist or None, main_artist))
        return changes
    m = _FEAT_TOKEN_RE.search(title)
    if m:
        prefix = title[: m.start()].rstrip(" ([-").rstrip()
        clause = _canonical_feat(title[m.start():])
        if clause == "feat." or not prefix:
            return changes
        new_title = f"{prefix} {clause}".strip()
        if new_title != title:
            changes.append(ProposedFieldChange("title", title or None, new_title))
    return changes


_JUNK_RE = re.compile(
    r"[\(\[]\s*(?:official\s*(?:music\s*|lyric\s*)?(?:video|audio)?|lyrics?|hd|hq|audio|visualizer)\s*[\)\]]",
    re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")


def strip_bracketed_junk(tags: dict) -> list[ProposedFieldChange]:
    """Removes upload leftovers like '(Official Video)' or '[Lyrics]' from title and album. Only this fixed
    list: other brackets (a remaster year, 'Live') mean something and stay."""
    changes: list[ProposedFieldChange] = []
    for field in ("title", "album"):
        value = tags.get(field)
        if not value:
            continue
        new = _SPACE_RE.sub(" ", _JUNK_RE.sub("", value)).strip()
        if new and new != value:
            changes.append(ProposedFieldChange(field, value, new))
    return changes


def whitespace(tags: dict) -> list[ProposedFieldChange]:
    """Collapses repeated spaces and trims the ends of artist, title and album. Capitalization is left
    alone on purpose: forcing it would break names styled the artist's way (e.g. 'deadmau5')."""
    changes: list[ProposedFieldChange] = []
    for field in ("artist", "title", "album"):
        value = tags.get(field)
        if not value:
            continue
        new = _SPACE_RE.sub(" ", value).strip()
        if new and new != value:
            changes.append(ProposedFieldChange(field, value, new))
    return changes


RULES: list[Rule] = [
    Rule("feat_unify", "Featured artists: one spelling, in the title", feat_unify),
    Rule("strip_bracketed_junk", "Remove (Official Video), [Lyrics] and similar", strip_bracketed_junk),
    Rule("whitespace", "Tidy spaces", whitespace),
]
RULE_LABELS = {r.rule_id: r.label for r in RULES} | {"musicbrainz": "From MusicBrainz"}
