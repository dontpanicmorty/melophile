# Third-party software in the Melophile app

The packaged Melophile.app includes these programs and libraries, each under its own license.

| Component | What it does in Melophile | License |
|---|---|---|
| [Chromaprint](https://github.com/acoustid/chromaprint) `fpcalc` 1.6.2 | Acoustic fingerprints | LGPL 2.1 (Chromaprint's own code is MIT; it includes parts of FFmpeg) |
| [FFmpeg](https://ffmpeg.org), via [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | Conversion to M4A | LGPL 2.1 or later, as built by imageio-ffmpeg |
| [Python](https://www.python.org), via [python-build-standalone](https://github.com/astral-sh/python-build-standalone) | Runs the app | PSF License |
| [Qt](https://www.qt.io) and [PySide6](https://doc.qt.io/qtforpython/) | The interface | LGPL 3 |
| [mutagen](https://github.com/quodlibet/mutagen) | Reads and writes tags | GPL 2 or later |
| [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) | Fuzzy name matching | MIT |
| [truststore](https://github.com/sethmlarson/truststore) | Online lookups use the system's certificates | MIT |
| [PyObjC](https://github.com/ronaldoussoren/pyobjc) | The Music app and the Trash | MIT |
| [tomli-w](https://github.com/hukkin/tomli-w) | Writes settings | MIT |

Online services, used only when you ask: [AcoustID](https://acoustid.org) (fingerprint lookups, with your own
key) and [MusicBrainz](https://musicbrainz.org) (song details; its data is CC0, public domain).

`fpcalc` and `ffmpeg` are separate programs that Melophile runs; they are included unmodified. Their source
code is available from the links above.
