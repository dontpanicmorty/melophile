"""Where Melophile keeps things. Resolved at run time, never tied to one machine."""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PACKAGE_DIR / "assets"

# Settings and the catalog live in the standard per-user folder, so replacing the app keeps them.
# MELOPHILE_HOME moves them (the tests and the self-test use a throwaway folder).
HOME_DIR = Path(os.environ.get("MELOPHILE_HOME") or Path.home() / "Library" / "Application Support" / "Melophile")
CONFIG_PATH = HOME_DIR / "config.toml"
DEFAULT_DB_PATH = HOME_DIR / "catalog.db"
# Quarantined duplicates go somewhere the person can see and browse, not inside Application Support.
DEFAULT_QUARANTINE_DIR = Path.home() / "Music" / "Melophile Quarantine"
# The packaged app ships fpcalc here (see scripts/build_macos.sh).
BUNDLED_BIN_DIR = Path(os.environ["MELOPHILE_BIN"]) if os.environ.get("MELOPHILE_BIN") else None
