"""Moves files to the macOS Trash instead of deleting them, so a mistaken delete can be undone in Finder."""
from __future__ import annotations

from pathlib import Path


class TrashError(OSError):
    pass


def move_to_trash(path: str | Path) -> None:
    from Foundation import NSURL, NSFileManager

    url = NSURL.fileURLWithPath_(str(path))
    ok, _, error = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(url, None, None)
    if not ok:
        raise TrashError(f"Could not move {path} to the Trash: {error.localizedDescription() if error else 'unknown error'}")
