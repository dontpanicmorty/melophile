"""With the real fpcalc: one recording in several formats is grouped, different songs aren't. Skipped when
fpcalc isn't available (set MELOPHILE_BIN to a folder holding it, e.g. a built app's Resources/bin)."""
import subprocess

import pytest

from melophile import dedupe, scan, tools

FPCALC = tools.find_fpcalc()
pytestmark = pytest.mark.skipif(not FPCALC, reason="fpcalc isn't available")


def _song(folder, name, seed):
    expr = (f"0.25*sin(2*PI*t*(220+110*mod(floor(t*{1 + seed % 3}),4)))"
            f"+0.2*sin(2*PI*t*(330+55*mod(floor(t*2+{seed}),5)))")
    wav = folder / f"{name}.wav"
    ff = tools.ffmpeg_path()
    subprocess.run([ff, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"anoisesrc=color=pink:seed={seed}:amplitude=0.3:duration=30", "-f", "lavfi", "-i",
                    f"aevalsrc=exprs='{expr}':s=44100:d=30", "-filter_complex", "amix=inputs=2", str(wav)], check=True)
    out = []
    for ext, args in ((".flac", []), (".mp3", ["-b:a", "128k"]), (".m4a", ["-c:a", "aac", "-b:a", "192k"])):
        p = folder / ext.lstrip(".") / f"{name}{ext}"
        p.parent.mkdir(exist_ok=True)
        subprocess.run([ff, "-loglevel", "error", "-y", "-i", str(wav), *args, str(p)], check=True)
        out.append(p)
    wav.unlink()
    return out


def test_same_recording_grouped_across_formats(conn, config, tmp_path):
    lib = tmp_path / "library"
    lib.mkdir()
    a = _song(lib, "first", 1)
    _song(lib, "second", 2)
    _song(lib, "third", 3)
    r = scan.scan_all(conn, config)
    assert r["fingerprints"]["fingerprinted"] == 9
    groups = dedupe.find_acoustic_duplicate_groups(conn, config)
    paths = [{conn.execute("SELECT path FROM files WHERE id=?", (i,)).fetchone()[0] for i in g["file_ids"]}
             for g in groups]
    assert len(groups) == 3, paths
    assert {str(p) for p in a} in paths
    assert all(g["tier"] == "acoustic" and g["confidence"] > 0.95 for g in groups)
