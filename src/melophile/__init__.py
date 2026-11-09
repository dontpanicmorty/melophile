"""Melophile: clean up a messy macOS music library.

SPDX-License-Identifier: GPL-3.0-or-later
"""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("melophile")
except PackageNotFoundError:  # running from a source folder that isn't installed
    __version__ = "0.0.0"
