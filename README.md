# Melophile

A native macOS app for tidying a music library that has grown messy over the years: the same song saved
several times or in different formats, artist and title tags spelled every which way, and a mix of MP3, FLAC
and M4A files from old iTunes and Music app eras.

Agentic AI has been used in the creation of this application.

## What it does

- **Library.** Scans your music folders into a local catalog. Later scans are quick: unchanged files are
  skipped. One unreadable file is noted and skipped, never the end of a scan.
- **Duplicates.** Finds byte-identical copies, and, with acoustic fingerprints, the same recording saved
  differently (an MP3 and a FLAC of one song). It suggests the best-quality copy to keep. You can quarantine the
  rest, move them to the Trash, keep them all, or mark them as not duplicates.
- **Tag Cleanup.** Proposes fixes: featured artists written as feat./ft./featuring (moved from the artist into
  the title), leftovers like “(Official Video)” or “[Lyrics]”, and stray spaces. It works on files on disk or
  on tracks in the Music app. Nothing is written until you apply a change, and every applied change can be
  reverted.
- **MusicBrainz.** Also in Tag Cleanup: looks each song up on [MusicBrainz](https://musicbrainz.org) and proposes
  its artist, title, album, album artist, track and disc numbers and year.
  - It matches by sound through [AcoustID](https://acoustid.org) when you've added an AcoustID key, and by
    artist and title otherwise, accepting only a close match of the same length.
  - Album details come only from the release that matches the song's current album, so a song isn't moved onto
    a compilation by mistake.
  - MusicBrainz allows one request a second, so a large library takes hours. You can stop at any time and
    carry on later.
- **Organize.** Sorts songs into `Artist/Album/01 Title` folders (or `Artist/Album/` with their original file
  names), using their tags.
  - It plans the moves first, so you can review them, then moves the ones you choose. Any move can be undone.
  - Files the Music app manages itself are left alone, so it doesn't lose track of them.
- **Convert.** Makes M4A (AAC) copies of MP3 or FLAC files, with their tags and album art.

## Safe by design

- **Nothing is deleted outright.** Duplicates go to a quarantine folder (inside a copy of their original
  folders) or to the macOS Trash, from which Finder can put them back.
- **The file you keep must be in the group.** Moving files to the Trash also needs a separate confirmation.
- **Edited tags aren't overwritten.** A tag change is applied only if the tag still holds the value it was
  proposed against, and reverted only if it still holds the value it was changed to.
- **A rejected change isn't proposed again.**
- **Conversion never overwrites a file.** It writes to a temporary file, checks the result, and only then
  touches the original (if you asked it to).
- **An unplugged drive or a dropped network share** doesn't mark every song as missing.
- **Online only when you ask.** The network is used only for AcoustID and MusicBrainz lookups you start. Only
  fingerprints and song details are sent, never audio or file names.

## Light on your Mac

- Fingerprinting and conversion run at most two at a time, at a lower priority than everything else. Each
  has a time limit, and ffmpeg uses at most two threads.
- Long work runs in the background with a progress bar and a Stop button. Whatever was done before you
  stopped is kept.
- Finding the same recording uses an index of fingerprints rather than comparing every pair of songs. A
  library of tens of thousands of songs takes seconds to minutes, not hours.

## Install

Download `Melophile-<version>-arm64.dmg` (Apple silicon) or `-x86_64.dmg` (Intel) from the releases, open
it and drag Melophile to Applications. It needs macOS 14 or later.

The app is signed ad hoc, not notarized. The first time, Control-click it and choose **Open**.

Everything Melophile needs is inside the app, including Chromaprint's `fpcalc` for fingerprints and ffmpeg
for conversion. Its settings and catalog are in `~/Library/Application Support/Melophile`. Your music
stays where it is.

The first time you use **The Music app** in Tag Cleanup, macOS asks whether Melophile may control Music. You
can change your answer later under System Settings › Privacy & Security › Automation.

### AcoustID (optional)

Matching works offline. To also check songs against the AcoustID database:

1. Get a free key from [acoustid.org](https://acoustid.org) › My Applications.
2. Enter the key under Settings.

Only fingerprints are sent, never audio.

## Run from source

You need [uv](https://docs.astral.sh/uv/), installed with Astral's installer.

```bash
uv sync
uv run melophile                     # the app
uv run pytest                        # the tests (they make their own short audio files)
uv run melophile selftest            # an end-to-end check
```

When run from source, fingerprints need `fpcalc`. You can choose it under Settings, or put it on `PATH`:
take the macOS archive from [Chromaprint's releases](https://github.com/acoustid/chromaprint/releases).

### Command line

The command line drives the same work as the app, for scripting.

```bash
uv run melophile scan                 # the folders from Settings, or --root <folder>
uv run melophile report
uv run melophile dedupe
uv run melophile review list
uv run melophile review resolve --group-id 1 --action keep_one_quarantine_rest --keep-file-id 42
uv run melophile tags propose --source file
uv run melophile tags list
uv run melophile tags musicbrainz --source file
uv run melophile tags apply 7 8 9     # tags revert 7, tags reject 8
uv run melophile organize plan        # organize list, organize apply --all, organize undo 3
uv run melophile convert enqueue --format mp3,flac --original keep
uv run melophile convert run
```

## Build the app

```bash
scripts/build_macos.sh                       # this Mac's kind
MELOPHILE_ARCH=x86_64 scripts/build_macos.sh  # an Intel build on Apple silicon (needs Rosetta)
```

The build does the following:

1. Embeds Python 3.12 and the libraries.
2. Removes the parts of Qt the app doesn't use.
3. Downloads `fpcalc` and checks it against its published SHA-256.
4. Runs the self-test.
5. Signs the app ad hoc and makes the disk image in `dist/`.

## Known limits

- **DRM-protected `.m4p` purchases** from the old iTunes Store can't be fingerprinted by any tool, because
  their audio is encrypted. They're still catalogued and found as byte-identical duplicates. Library ›
  Formats can move them all into one folder.
- **Network shares (SMB)** work, but every byte crosses the network to be hashed and fingerprinted. If your
  music lives on another Mac, running Melophile on that Mac is faster.

## License

Melophile is free software under the GNU General Public License, version 3 or (at your option) any later
version. See [LICENSE](LICENSE). Third-party components and their licenses are listed in
[THIRD_PARTY.md](THIRD_PARTY.md).
