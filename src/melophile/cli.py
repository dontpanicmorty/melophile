"""`melophile <command>`: the same work as the app, for scripting. With no command, the app opens."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, convert, db, dedupe, musicbrainz, normalize, organize, review, scan
from .config import Config
from .progress import Progress


def _setup(args) -> tuple[Config, object]:
    config = Config.load(args.config)
    config.ensure_dirs()
    return config, db.connect(config.db_path)


def _printer() -> Progress:
    def show(done: int, total: int, msg: str) -> None:
        if sys.stderr.isatty():
            label = f"{done}/{total}" if total else str(done)
            sys.stderr.write(f"\r\033[K{label}  {msg[-90:]}")
            sys.stderr.flush()
    return Progress(show)


def _done() -> None:
    if sys.stderr.isatty():
        sys.stderr.write("\r\033[K")


def cmd_scan(args) -> int:
    config, conn = _setup(args)
    if args.root:
        config.library_roots = args.root
    if not config.library_roots:
        print("No music folders yet: pass --root, or add one in the app.", file=sys.stderr)
        return 1
    result = scan.scan_all(conn, config, fingerprints=not args.no_fingerprint, progress=_printer())
    _done()
    for r in result["roots"]:
        print(f"{r['root']}: {r['files_seen']} seen, {r['files_new']} new, {r['files_updated']} changed, "
              f"{r['files_missing']} missing, {r['files_failed']} unreadable")
        if r.get("warning"):
            print(f"  warning: {r['warning']}", file=sys.stderr)
        for path, err in r["failures"]:
            print(f"  {path}: {err}", file=sys.stderr)
    for e in result["errors"]:
        print(e, file=sys.stderr)
    if result["fingerprints"]:
        fp = result["fingerprints"]
        print(f"fingerprints: {fp['fingerprinted']} made, {fp['failed']} unreadable")
    elif not args.no_fingerprint:
        print("fingerprints: skipped (fpcalc isn't available)")
    return 1 if result["errors"] else 0


def cmd_report(args) -> int:
    config, conn = _setup(args)
    s = db.library_stats(conn)
    print(f"{s['files']} files, {s['bytes'] / 1024 ** 3:.2f} GB, {s['fingerprinted']} fingerprinted")
    for fmt, n, size in s["by_format"]:
        print(f"  {fmt:6} {n:7}  {size / 1024 ** 3:.2f} GB")
    print(f"{s['pending_groups']} duplicate groups to review, {s['proposed_changes']} tag changes proposed, "
          f"{s['queued_jobs']} conversions queued")
    return 0


def cmd_dedupe(args) -> int:
    config, conn = _setup(args)
    r = dedupe.run_dedupe(conn, config, include_acoustic=not args.exact_only, progress=_printer())
    _done()
    print(f"{r['exact_groups']} groups of identical files, {r['acoustic_groups']} groups of the same recording")
    return 0


def cmd_review_list(args) -> int:
    config, conn = _setup(args)
    groups = db.get_pending_duplicate_groups(conn)
    if not groups:
        print("No duplicate groups to review.")
    for g in groups:
        print(f"\ngroup {g['id']}  {g['tier']}  confidence {g['confidence']:.2f}")
        for m in db.get_group_members(conn, g["id"]):
            print(f"  [{m['id']}] {m['path']}")
    return 0


def cmd_review_resolve(args) -> int:
    config, conn = _setup(args)
    try:
        r = review.resolve_duplicate_group(conn, config.quarantine_dir, args.group_id, args.action,
                                           args.keep_file_id, confirm_trash=args.confirm_trash)
    except review.ReviewError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"group {args.group_id}: {review.ACTIONS[args.action].lower()} ({len(r['moved'])} moved)")
    return 0


def cmd_isolate(args) -> int:
    config, conn = _setup(args)
    moved = review.isolate_files_by_format(conn, args.format.lower().lstrip("."), Path(args.dest))
    print(f"moved {len(moved)} .{args.format} file(s) to {args.dest}")
    return 0


def cmd_tags_propose(args) -> int:
    config, conn = _setup(args)
    n = normalize.propose_changes(conn, args.source, progress=_printer())
    _done()
    print(f"{n} new change(s) proposed")
    return 0


def cmd_tags_list(args) -> int:
    config, conn = _setup(args)
    rows = db.get_normalizations(conn, "proposed", args.source)
    if not rows:
        print("No proposed changes.")
    for e in rows:
        print(f"[{e['id']}] {e['field']}: {e['old_value']!r} -> {e['new_value']!r}   ({e['path']})")
    return 0


def cmd_tags_apply(args) -> int:
    config, conn = _setup(args)
    r = normalize.apply_many(conn, args.id)
    for e in r["errors"]:
        print(e, file=sys.stderr)
    print(f"{r['applied']} applied")
    return 1 if r["errors"] else 0


def cmd_tags_reject(args) -> int:
    config, conn = _setup(args)
    for i in args.id:
        normalize.reject_change(conn, i)
    print(f"{len(args.id)} rejected")
    return 0


def cmd_tags_revert(args) -> int:
    config, conn = _setup(args)
    r = normalize.revert_many(conn, args.id)
    for e in r["errors"]:
        print(e, file=sys.stderr)
    print(f"{r['reverted']} reverted")
    return 1 if r["errors"] else 0


def cmd_tags_musicbrainz(args) -> int:
    config, conn = _setup(args)
    r = musicbrainz.lookup(conn, config, args.source, progress=_printer())
    _done()
    print(f"{r['looked_up']} looked up, {r['matched']} found on MusicBrainz, {r['proposed']} change(s) proposed"
          + (f", {r['errors']} unreachable (tried again next time)" if r["errors"] else ""))
    return 0


def cmd_organize_plan(args) -> int:
    config, conn = _setup(args)
    try:
        r = organize.plan(conn, config, args.dest or config.library_roots[0], args.layout)
    except (organize.OrganizeError, IndexError) as exc:
        print(exc or "No music folders yet.", file=sys.stderr)
        return 1
    print(f"{r['planned']} move(s) planned, {r['already_in_place']} already in place, "
          f"{r['skipped_music_app']} left to the Music app")
    return 0


def cmd_organize_list(args) -> int:
    config, conn = _setup(args)
    for m in db.get_moves(conn, args.status):
        print(f"[{m['id']}] {m['old_path']}\n    -> {m['new_path']}")
    return 0


def cmd_organize_apply(args) -> int:
    config, conn = _setup(args)
    ids = [m["id"] for m in db.get_moves(conn, "planned")] if args.all else args.id
    r = organize.apply_moves(conn, config, ids, progress=_printer())
    _done()
    for e in r["errors"]:
        print(e, file=sys.stderr)
    print(f"{r['moved']} moved")
    return 1 if r["errors"] else 0


def cmd_organize_undo(args) -> int:
    config, conn = _setup(args)
    r = organize.undo_moves(conn, config, args.id, progress=_printer())
    _done()
    for e in r["errors"]:
        print(e, file=sys.stderr)
    print(f"{r['moved_back']} moved back")
    return 1 if r["errors"] else 0


def cmd_convert_enqueue(args) -> int:
    config, conn = _setup(args)
    n = sum(convert.enqueue(conn, f.strip().lower(), args.original) for f in args.format.split(","))
    print(f"{n} file(s) queued")
    return 0


def cmd_convert_run(args) -> int:
    config, conn = _setup(args)
    r = convert.run_queue(conn, config, progress=_printer())
    _done()
    print(f"{r['succeeded']} converted, {r['failed']} failed")
    return 1 if r["failed"] else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="melophile", description="Clean up a messy macOS music library.")
    p.add_argument("--version", action="version", version=f"melophile {__version__}")
    p.add_argument("--config", help="settings file (default: the app's own, in Application Support)")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("app", help="open the app (the default)")
    sub.add_parser("selftest", help="check that this copy of Melophile works")

    s = sub.add_parser("scan", help="scan the music folders into the catalog")
    s.add_argument("--root", action="append", help="scan this folder instead (repeatable)")
    s.add_argument("--no-fingerprint", action="store_true", help="skip acoustic fingerprints")
    s.set_defaults(func=cmd_scan)
    sub.add_parser("report", help="summarize the catalog").set_defaults(func=cmd_report)
    s = sub.add_parser("dedupe", help="find duplicate groups")
    s.add_argument("--exact-only", action="store_true", help="only byte-identical files")
    s.set_defaults(func=cmd_dedupe)

    r = sub.add_parser("review", help="review duplicate groups").add_subparsers(dest="sub", required=True)
    r.add_parser("list").set_defaults(func=cmd_review_list)
    s = r.add_parser("resolve")
    s.add_argument("--group-id", type=int, required=True)
    s.add_argument("--action", required=True, choices=sorted(review.ACTIONS))
    s.add_argument("--keep-file-id", type=int)
    s.add_argument("--confirm-trash", action="store_true", help="required for keep_one_trash_rest")
    s.set_defaults(func=cmd_review_resolve)
    s = r.add_parser("isolate", help="move every file of one format into a folder")
    s.add_argument("--format", required=True)
    s.add_argument("--dest", required=True)
    s.set_defaults(func=cmd_isolate)

    t = sub.add_parser("tags", help="tag cleanup").add_subparsers(dest="sub", required=True)
    s = t.add_parser("propose")
    s.add_argument("--source", choices=["file", "musicapp"], default="file")
    s.set_defaults(func=cmd_tags_propose)
    s = t.add_parser("list")
    s.add_argument("--source", choices=["file", "musicapp"])
    s.set_defaults(func=cmd_tags_list)
    for name, fn in (("apply", cmd_tags_apply), ("reject", cmd_tags_reject), ("revert", cmd_tags_revert)):
        s = t.add_parser(name)
        s.add_argument("id", type=int, nargs="+")
        s.set_defaults(func=fn)

    s = t.add_parser("musicbrainz", help="propose fixes from MusicBrainz")
    s.add_argument("--source", choices=["file", "musicapp"], default="file")
    s.set_defaults(func=cmd_tags_musicbrainz)

    o = sub.add_parser("organize", help="sort files into Artist/Album folders").add_subparsers(dest="sub", required=True)
    s = o.add_parser("plan")
    s.add_argument("--dest", help="music folder to sort into (default: the first one)")
    s.add_argument("--layout", choices=sorted(organize.PATTERNS), default="artist_album_track")
    s.set_defaults(func=cmd_organize_plan)
    s = o.add_parser("list")
    s.add_argument("--status", choices=["planned", "applied", "failed", "reverted"], default="planned")
    s.set_defaults(func=cmd_organize_list)
    s = o.add_parser("apply")
    s.add_argument("id", type=int, nargs="*")
    s.add_argument("--all", action="store_true", help="every planned move")
    s.set_defaults(func=cmd_organize_apply)
    s = o.add_parser("undo")
    s.add_argument("id", type=int, nargs="+")
    s.set_defaults(func=cmd_organize_undo)

    c = sub.add_parser("convert", help="mp3/flac to m4a").add_subparsers(dest="sub", required=True)
    s = c.add_parser("enqueue")
    s.add_argument("--format", required=True, help="mp3, flac or mp3,flac")
    s.add_argument("--original", default="keep", choices=sorted(convert.DISPOSITIONS))
    s.set_defaults(func=cmd_convert_enqueue)
    c.add_parser("run").set_defaults(func=cmd_convert_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in (None, "app"):
        from .ui.app import run

        return run()
    if args.command == "selftest":
        from .selftest import main as selftest

        return selftest()
    return args.func(args)
