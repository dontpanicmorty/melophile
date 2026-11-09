"""Settings, stored as TOML in the user's Application Support folder."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

from . import paths

BITRATES = ("128k", "192k", "256k", "320k")


@dataclass
class DedupeConfig:
    duration_tolerance_seconds: float = 3.0  # songs further apart in length are never compared
    acoustic_threshold: float = 0.90         # fingerprint similarity that counts as the same recording
    text_threshold: float = 0.85             # combined sound + artist/title score for a weaker match


@dataclass
class AcoustidConfig:
    enabled: bool = False
    api_key: str = ""


@dataclass
class Config:
    library_roots: list[str] = field(default_factory=list)
    db_path: Path = field(default_factory=lambda: paths.DEFAULT_DB_PATH)
    quarantine_dir: Path = field(default_factory=lambda: paths.DEFAULT_QUARANTINE_DIR)
    dedupe: DedupeConfig = field(default_factory=DedupeConfig)
    acoustid: AcoustidConfig = field(default_factory=AcoustidConfig)
    bitrate: str = "256k"
    fpcalc_path: str = ""  # empty: use the copy bundled with the app, or one on PATH
    config_path: Path = field(default_factory=lambda: paths.CONFIG_PATH)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """Reads the settings file; a missing file gives the defaults (with no music folders yet)."""
        path = Path(path or paths.CONFIG_PATH).expanduser()
        if not path.exists():
            return cls(config_path=path)
        with path.open("rb") as f:
            raw = tomllib.load(f)
        d, a = raw.get("dedupe", {}), raw.get("acoustid", {})
        c = cls(
            library_roots=[str(r) for r in raw.get("library", {}).get("roots", [])],
            db_path=Path(raw.get("catalog", {}).get("db_path", str(paths.DEFAULT_DB_PATH))).expanduser(),
            quarantine_dir=Path(raw.get("quarantine", {}).get("dir", str(paths.DEFAULT_QUARANTINE_DIR))).expanduser(),
            dedupe=DedupeConfig(
                duration_tolerance_seconds=_num(d.get("duration_tolerance_seconds"), 3.0, 0.0, 30.0),
                acoustic_threshold=_num(d.get("acoustic_threshold"), 0.90, 0.5, 1.0),
                text_threshold=_num(d.get("text_threshold"), 0.85, 0.5, 1.0),
            ),
            acoustid=AcoustidConfig(enabled=bool(a.get("enabled", False)), api_key=str(a.get("api_key", ""))),
            bitrate=str(raw.get("convert", {}).get("bitrate", "256k")),
            fpcalc_path=str(raw.get("fpcalc", {}).get("path", "")),
            config_path=path,
        )
        if c.bitrate not in BITRATES:
            c.bitrate = "256k"
        return c

    @property
    def exists(self) -> bool:
        return self.config_path.exists()

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def as_dict(self) -> dict:
        return {
            "library": {"roots": self.library_roots},
            "catalog": {"db_path": str(self.db_path)},
            "quarantine": {"dir": str(self.quarantine_dir)},
            "dedupe": {
                "duration_tolerance_seconds": float(self.dedupe.duration_tolerance_seconds),
                "acoustic_threshold": float(self.dedupe.acoustic_threshold),
                "text_threshold": float(self.dedupe.text_threshold),
            },
            "acoustid": {"enabled": self.acoustid.enabled, "api_key": self.acoustid.api_key},
            "convert": {"bitrate": self.bitrate},
            "fpcalc": {"path": self.fpcalc_path},
        }

    def save(self) -> None:
        """Writes the file readable only by its owner, since it can hold an AcoustID key."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".tmp")
        with tmp.open("wb") as f:
            tomli_w.dump(self.as_dict(), f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.config_path)


def _num(value, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v))
