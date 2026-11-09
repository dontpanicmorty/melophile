import os
import subprocess
import tempfile
from pathlib import Path

# Never touch the real settings or catalog: everything goes in a throwaway folder.
os.environ["MELOPHILE_HOME"] = tempfile.mkdtemp(prefix="melophile-tests-")

import pytest  # noqa: E402

from melophile import db, tools  # noqa: E402
from melophile.config import Config  # noqa: E402


@pytest.fixture
def config(tmp_path):
    return Config(library_roots=[str(tmp_path / "library")], db_path=tmp_path / "catalog.db",
                  quarantine_dir=tmp_path / "quarantine", config_path=tmp_path / "config.toml")


@pytest.fixture
def conn(config):
    c = db.connect(config.db_path)
    yield c
    c.close()


@pytest.fixture
def no_trash(monkeypatch):
    """Records what would go to the Trash instead of filling the real one."""
    trashed = []

    def fake(path):
        trashed.append(str(path))
        Path(path).unlink()

    monkeypatch.setattr("melophile.review.move_to_trash", fake)
    return trashed


def make_audio(path: Path, seconds: float = 4.0, freq: int = 440, tags: dict | None = None,
               cover: bool = False) -> Path:
    """A real, short audio file (a tone) made with the bundled ffmpeg; the format follows the extension."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [tools.ffmpeg_path(), "-nostdin", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}"]
    if cover:
        art = path.with_name(path.stem + "-cover.png")
        subprocess.run([tools.ffmpeg_path(), "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=red:s=32x32", "-frames:v", "1", str(art)], check=True)
        cmd += ["-i", str(art), "-map", "0:a", "-map", "1:v",
                "-c:v", "mjpeg" if path.suffix == ".mp3" else "png", "-disposition:v", "attached_pic"]
        if path.suffix == ".mp3":
            cmd += ["-id3v2_version", "3"]
    for k, v in (tags or {}).items():
        cmd += ["-metadata", f"{k}={v}"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    if cover:
        art.unlink()
    return path
