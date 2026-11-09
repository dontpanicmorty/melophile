"""The two outside programs Melophile runs, fpcalc (fingerprints) and ffmpeg (conversion), and how it runs them.

Every run is kept light on the computer:
- it has a time limit, after which it is stopped;
- it runs at a lower priority than the app (nice 10), so the Mac stays responsive;
- it is stopped together with anything it started;
- ffmpeg is held to two threads, and Melophile runs at most MAX_PARALLEL of these programs at once.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path

from . import paths

MAX_PARALLEL = 2  # outside programs running at once
_slots = threading.BoundedSemaphore(MAX_PARALLEL)


class ToolError(RuntimeError):
    pass


class ToolMissing(ToolError):
    pass


def find_fpcalc(configured: str = "") -> str | None:
    """The fpcalc to use: the one chosen in Settings, else the app's own copy, else one on PATH."""
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    if paths.BUNDLED_BIN_DIR:
        candidates.append(paths.BUNDLED_BIN_DIR / "fpcalc")
    found = shutil.which("fpcalc")
    if found:
        candidates.append(Path(found))
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def ffmpeg_path() -> str:
    """imageio-ffmpeg ships its own ffmpeg inside the Python package: nothing else to install."""
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run(cmd: list[str], timeout: float, what: str) -> subprocess.CompletedProcess:
    """Runs an outside program politely (see the module docstring). Raises ToolError on failure or timeout."""
    with _slots:
        try:
            proc = subprocess.Popen(["/usr/bin/nice", "-n", "10", *cmd], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            raise ToolMissing(f"{what} could not start: {exc}") from exc
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            proc.communicate()
            raise ToolError(f"{what} took longer than {int(timeout)} seconds and was stopped") from None
        except BaseException:
            _kill(proc)
            raise
    if proc.returncode != 0:
        detail = err.decode("utf-8", "replace").strip().splitlines()
        raise ToolError(f"{what} failed: {detail[-1] if detail else f'exit code {proc.returncode}'}")
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
