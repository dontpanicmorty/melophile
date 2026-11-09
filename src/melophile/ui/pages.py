"""The app's pages: Welcome, Library, Duplicates, Tag Cleanup, Convert and Settings."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import convert, db, dedupe, musicbrainz, normalize, organize, review, scan, tools
from ..config import BITRATES
from ..rules import RULE_LABELS
from .style import heading

MAX_ROWS = 2000  # rows shown in a table at once; the rest appear as these are dealt with


def human_size(n: int | None) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def human_time(seconds: float | None) -> str:
    if not seconds:
        return ""
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def card(*widgets, layout=None) -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    v = layout or QVBoxLayout()
    f.setLayout(v)
    v.setContentsMargins(16, 14, 16, 14)
    for w in widgets:
        if isinstance(w, QWidget):
            v.addWidget(w)
        else:
            v.addLayout(w)
    return f


def button(text: str, slot, kind: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.clicked.connect(slot)
    return b


def row(*items, stretch_end: bool = True) -> QHBoxLayout:
    h = QHBoxLayout()
    for it in items:
        if isinstance(it, QWidget):
            h.addWidget(it)
        else:
            h.addLayout(it)
    if stretch_end:
        h.addStretch(1)
    return h


def table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setStretchLastSection(True)
    t.setWordWrap(False)
    return t


def cell(text, data=None, align_right: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem("" if text is None else str(text))
    it.setToolTip("" if text is None else str(text))
    if data is not None:
        it.setData(Qt.ItemDataRole.UserRole, data)
    if align_right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


def pretty(path: str | Path) -> str:
    """A path with the home folder shown as ~."""
    p, home = str(path), str(Path.home())
    return "~" + p[len(home):] if p == home or p.startswith(home + "/") else p


def reveal(path: str) -> None:
    if Path(path).exists():
        subprocess.run(["open", "-R", path], check=False)


class Page(QWidget):
    title = ""

    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(28, 24, 28, 24)
        self.body.setSpacing(14)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

    @property
    def conn(self):
        return self.win.conn

    @property
    def config(self):
        return self.win.config

    def refresh(self) -> None:
        pass


# ------------------------------------------------------------------ welcome ----

class WelcomePage(Page):
    title = "Welcome"

    def __init__(self, win) -> None:
        super().__init__(win)
        self.body.addStretch(1)
        self.body.addWidget(heading("Welcome to Melophile"))
        self.body.addWidget(heading(
            "Melophile tidies a music library that has grown messy over the years: the same song saved several "
            "times or in different formats, artist and title tags spelled every which way, and a mix of MP3, FLAC "
            "and M4A files.\n\nNothing changes until you say so. Every tag change can be undone, and files are "
            "only ever moved aside or to the Trash, never deleted outright.", "muted"))
        self.body.addSpacing(8)
        self.body.addLayout(row(button("Choose your music folder…", self.choose, "primary")))
        self.body.addStretch(2)

    def choose(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose your music folder", str(Path.home() / "Music"))
        if folder:
            self.win.add_root(folder)
            self.win.show_page("Library")
            self.win.start_scan()


# ------------------------------------------------------------------ library ----

class LibraryPage(Page):
    title = "Library"

    def __init__(self, win) -> None:
        super().__init__(win)
        self.body.addWidget(heading("Library"))
        self.last = heading("", "muted")
        self.body.addWidget(self.last)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.metrics = {}
        for i, (key, label) in enumerate((("files", "Songs"), ("bytes", "Size"), ("fingerprinted", "Fingerprinted"),
                                          ("pending_groups", "Duplicate groups"), ("proposed_changes", "Tag changes"))):
            value = heading("–", "metric")
            self.metrics[key] = value
            grid.addWidget(card(value, heading(label, "muted")), 0, i)
        self.body.addLayout(grid)

        self.roots = QListWidget()
        self.roots.setMaximumHeight(88)
        self.fp_box = QCheckBox("Fingerprint new songs, to find the same song saved in different formats")
        self.fp_note = heading("", "muted")
        self.scan_btn = button("Scan now", win.start_scan, "primary")
        self.body.addWidget(card(
            heading("Music folders", "h2"), self.roots,
            row(button("Add folder…", self.add_root), button("Remove", self.remove_root)),
            self.fp_box, self.fp_note, row(self.scan_btn)))

        self.results = QPlainTextEdit()
        self.results.setReadOnly(True)
        self.results.setMaximumHeight(130)
        self.results.setPlaceholderText("Scan results appear here.")
        self.body.addWidget(card(heading("Last scan", "h2"), self.results))

        self.formats = table(["Format", "Songs", "Size"])
        self.formats.setMaximumHeight(220)
        self.formats.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.body.addWidget(card(
            heading("Formats", "h2"), self.formats,
            heading("Moving every file of one format into its own folder is handy for .m4p purchases, which are "
                    "copy-protected and can't be fingerprinted. They stay in the library at their new place.", "muted"),
            row(button("Move this format to a folder…", self.isolate))))
        self.body.addStretch(1)

    def refresh(self) -> None:
        s = db.library_stats(self.conn)
        self.metrics["files"].setText(f"{s['files']:,}")
        self.metrics["bytes"].setText(human_size(s["bytes"]))
        self.metrics["fingerprinted"].setText(f"{s['fingerprinted']:,}")
        self.metrics["pending_groups"].setText(f"{s['pending_groups']:,}")
        self.metrics["proposed_changes"].setText(f"{s['proposed_changes']:,}")
        last = db.last_scan(self.conn)
        self.last.setText(f"Last scanned {last['finished_at'][:16].replace('T', ' at ')} UTC" if last else "Not scanned yet.")
        self.roots.clear()
        for root in self.config.library_roots:
            item = QListWidgetItem(pretty(root))
            item.setData(Qt.ItemDataRole.UserRole, root)
            self.roots.addItem(item)
        fpcalc = tools.find_fpcalc(self.config.fpcalc_path)
        self.fp_box.setEnabled(bool(fpcalc))
        self.fp_box.setChecked(bool(fpcalc) and self.win.settings_fp)
        self.fp_note.setText("" if fpcalc else "Fingerprinting needs Chromaprint's fpcalc: the packaged app includes it, "
                                               "or choose it under Settings.")
        self.fp_note.setVisible(not fpcalc)
        self.formats.setRowCount(0)
        for fmt, n, size in s["by_format"]:
            r = self.formats.rowCount()
            self.formats.insertRow(r)
            self.formats.setItem(r, 0, cell(f".{fmt}", fmt))
            self.formats.setItem(r, 1, cell(f"{n:,}", align_right=True))
            self.formats.setItem(r, 2, cell(human_size(size), align_right=True))

    def add_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a music folder", str(Path.home() / "Music"))
        if folder:
            self.win.add_root(folder)

    def remove_root(self) -> None:
        item = self.roots.currentItem()
        if item:
            self.win.remove_root(item.data(Qt.ItemDataRole.UserRole))

    def show_results(self, result: dict) -> None:
        lines = []
        for r in result.get("roots", []):
            lines.append(f"{r['root']}: {r['files_seen']:,} songs, {r['files_new']:,} new, {r['files_updated']:,} "
                         f"changed, {r['files_missing']:,} missing, {r['files_failed']:,} unreadable")
            if r.get("warning"):
                lines.append(f"  ⚠ {r['warning']}")
            lines += [f"  {p}: {e}" for p, e in r["failures"]]
            if r.get("failures_truncated"):
                lines.append(f"  …and {r['failures_truncated']:,} more")
        lines += [f"⚠ {e}" for e in result.get("errors", [])]
        fp = result.get("fingerprints")
        if fp:
            lines.append(f"Fingerprints: {fp['fingerprinted']:,} made, {fp['failed']:,} couldn't be read"
                         + (f", {fp['remaining']:,} left for next time" if fp["remaining"] else ""))
        if result.get("stopped"):
            lines.append("Stopped. Everything scanned so far is kept; scan again to carry on.")
        self.results.setPlainText("\n".join(lines) or "Nothing to scan.")

    def isolate(self) -> None:
        r = self.formats.currentRow()
        if r < 0:
            QMessageBox.information(self, "Move a format", "Select a format in the table first.")
            return
        fmt = self.formats.item(r, 0).data(Qt.ItemDataRole.UserRole)
        folder = QFileDialog.getExistingDirectory(self, f"Move every .{fmt} file into…", str(Path.home() / "Music"))
        if not folder:
            return
        n = self.formats.item(r, 1).text()
        if QMessageBox.question(self, "Move a format", f"Move {n} .{fmt} file(s) into {folder}? Their folder "
                                "structure is kept.") != QMessageBox.StandardButton.Yes:
            return
        self.win.run_task(f"Moving .{fmt} files", lambda c, cfg, p: review.isolate_files_by_format(c, fmt, Path(folder)),
                          lambda moved: QMessageBox.information(self, "Move a format", f"Moved {len(moved):,} file(s)."))


# --------------------------------------------------------------- duplicates ----

FORMAT_RANK = {"flac": 5, "alac": 5, "wav": 4, "aiff": 4, "aif": 4, "m4a": 3, "aac": 2, "mp3": 2, "ogg": 2,
               "opus": 2, "m4p": 1}
TIER_TEXT = {
    "exact_hash": "These files are byte-for-byte identical.",
    "acoustic": "These sound like the same recording, saved differently.",
    "text_heuristic": "These sound similar and have matching artist and title. Worth a listen before deciding.",
}


def suggested_keep(members) -> int:
    """The best-quality copy: lossless first, then the highest bitrate, then the largest file."""
    return max(members, key=lambda m: (FORMAT_RANK.get(m["format"], 0), m["bitrate"] or 0, m["size_bytes"] or 0))["id"]


class DuplicatesPage(Page):
    title = "Duplicates"

    def __init__(self, win) -> None:
        super().__init__(win)
        self.group = None
        self.skipped: set[int] = set()
        self.body.addWidget(heading("Duplicates"))
        self.acoustic = QCheckBox("Also find the same recording in different formats (uses fingerprints)")
        self.acoustic.setChecked(True)
        self.find_btn = button("Find duplicates", self.find, "primary")
        self.body.addWidget(card(
            heading("Identical files are always found. With fingerprints, an MP3 and a FLAC of one song are found "
                    "too. Groups you've decided on are never shown again.", "muted"),
            self.acoustic, row(self.find_btn)))

        self.counter = heading("", "h2")
        self.tier = heading("", "muted")
        self.members = table(["File", "Folder", "Format", "Bitrate", "Length", "Size", "Match"])
        self.members.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.members.setMinimumHeight(170)
        h = self.members.horizontalHeader()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.members.itemSelectionChanged.connect(self._selection)
        self.keep_label = heading("", "muted")
        self.quarantine_btn = button("Keep selected, quarantine the others", self.quarantine, "primary")
        self.trash_btn = button("Keep selected, move the others to the Trash…", self.trash, "danger")
        self.group_card = card(
            self.counter, self.tier, self.members, self.keep_label,
            row(button("Reveal in Finder", lambda: self._each_selected(reveal)),
                button("Play", lambda: self._each_selected(lambda p: QDesktopServices.openUrl(QUrl.fromLocalFile(p))))),
            row(self.quarantine_btn, self.trash_btn),
            row(button("Keep them all", lambda: self.decide("keep_all")),
                button("They aren't duplicates", lambda: self.decide("not_duplicates")),
                button("Skip for now", self.skip)))
        self.body.addWidget(self.group_card)
        self.empty = heading("", "muted")
        self.body.addWidget(self.empty)
        self.body.addStretch(1)

    def find(self) -> None:
        include = self.acoustic.isChecked()
        self.skipped.clear()
        self.win.run_task("Finding duplicates", lambda c, cfg, p: dedupe.run_dedupe(c, cfg, include, p), self._found)

    def _found(self, r: dict) -> None:
        if r.get("stopped"):
            return
        QMessageBox.information(self, "Duplicates", f"Found {r['exact_groups']:,} group(s) of identical files and "
                                f"{r['acoustic_groups']:,} group(s) of the same recording.")

    def refresh(self) -> None:
        groups = [g for g in db.get_pending_duplicate_groups(self.conn) if g["id"] not in self.skipped]
        self.group = groups[0] if groups else None
        self.group_card.setVisible(self.group is not None)
        if self.group is None:
            self.empty.setText("Nothing to review." + (" Skipped groups come back next time." if self.skipped else
                                                       " Scan your library, then find duplicates."))
            return
        self.empty.setText("")
        self.counter.setText(f"Group {self.group['id']}  ·  {len(groups):,} to review")
        self.tier.setText(TIER_TEXT.get(self.group["tier"], ""))
        members = db.get_group_members(self.conn, self.group["id"])
        best = suggested_keep(members)
        self.members.setRowCount(0)
        for m in members:
            r = self.members.rowCount()
            self.members.insertRow(r)
            p = Path(m["path"])
            self.members.setItem(r, 0, cell(p.name, m["id"]))
            folder = cell(pretty(p.parent), str(p.parent))
            self.members.setItem(r, 1, folder)
            self.members.setItem(r, 2, cell(m["format"]))
            self.members.setItem(r, 3, cell(f"{m['bitrate'] // 1000} kbps" if m["bitrate"] else "", align_right=True))
            self.members.setItem(r, 4, cell(human_time(m["duration_seconds"]), align_right=True))
            self.members.setItem(r, 5, cell(human_size(m["size_bytes"]), align_right=True))
            score = m["similarity_score"]
            self.members.setItem(r, 6, cell(f"{score:.0%}" if score is not None else "identical", align_right=True))
            if m["id"] == best:
                self.members.selectRow(r)
        self._selection()

    def _selected_id(self) -> int | None:
        rows = self.members.selectionModel().selectedRows()
        return self.members.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole) if rows else None

    def _each_selected(self, fn) -> None:
        for r in self.members.selectionModel().selectedRows():
            folder = self.members.item(r.row(), 1).data(Qt.ItemDataRole.UserRole)
            fn(str(Path(folder) / self.members.item(r.row(), 0).text()))

    def _selection(self) -> None:
        has = self._selected_id() is not None
        self.quarantine_btn.setEnabled(has)
        self.trash_btn.setEnabled(has)
        self.keep_label.setText("Keeping the selected file (suggested: the best quality copy)." if has
                                else "Select the file to keep.")

    def quarantine(self) -> None:
        self.decide("keep_one_quarantine_rest")

    def trash(self) -> None:
        n = self.members.rowCount() - 1
        if QMessageBox.warning(self, "Move to the Trash", f"Move the other {n} file(s) to the Trash? You can put them "
                               "back from the Trash in Finder until it's emptied.",
                               QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                               QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self.decide("keep_one_trash_rest", confirm=True)

    def decide(self, action: str, confirm: bool = False) -> None:
        if self.group is None:
            return
        try:
            review.resolve_duplicate_group(self.conn, self.config.quarantine_dir, self.group["id"], action,
                                           self._selected_id(), confirm_trash=confirm)
        except Exception as exc:
            QMessageBox.warning(self, "Duplicates", str(exc))
        self.win.refresh_all()

    def skip(self) -> None:
        if self.group is not None:
            self.skipped.add(self.group["id"])
        self.refresh()


# -------------------------------------------------------------- tag cleanup ----

SOURCES = {"file": "Files on disk", "musicapp": "The Music app"}


class TagsPage(Page):
    title = "Tag Cleanup"

    def __init__(self, win) -> None:
        super().__init__(win)
        self.body.addWidget(heading("Tag Cleanup"))
        self.source = QComboBox()
        for k, v in SOURCES.items():
            self.source.addItem(v, k)
        self.source.currentIndexChanged.connect(self.refresh)
        self.body.addWidget(card(
            heading("Finds featured artists written different ways (moving them into the title), leftovers such as "
                    "“(Official Video)”, and stray spaces. MusicBrainz can also fill in each song's album, track "
                    "number and year. Nothing is written until you apply a change, and any applied change can be "
                    "reverted.", "muted"),
            row(QLabel("Look at"), self.source, button("Find changes", self.find, "primary"),
                button("Look up on MusicBrainz", self.lookup))))
        self.mb_note = heading("", "muted")
        self.body.addWidget(self.mb_note)

        self.tabs = QTabWidget()
        prop = QWidget()
        pv = QVBoxLayout(prop)
        self.proposed = table(["", "Change", "Field", "Before", "After", "File"])
        self._setup(self.proposed)
        self.proposed_count = heading("", "muted")
        pv.addWidget(self.proposed_count)
        pv.addWidget(self.proposed)
        pv.addLayout(row(button("Select all", lambda: self._check_all(self.proposed, True)),
                         button("Select none", lambda: self._check_all(self.proposed, False)),
                         button("Apply selected", self.apply, "primary"), button("Reject selected", self.reject)))
        appl = QWidget()
        av = QVBoxLayout(appl)
        self.applied = table(["", "Change", "Field", "Before", "After", "File"])
        self._setup(self.applied)
        av.addWidget(self.applied)
        av.addLayout(row(button("Revert selected", self.revert)))
        self.tabs.addTab(prop, "Proposed")
        self.tabs.addTab(appl, "Applied")
        self.tabs.setMinimumHeight(420)
        self.body.addWidget(self.tabs, 1)

    @staticmethod
    def _setup(t: QTableWidget) -> None:
        h = t.horizontalHeader()
        for c in (0, 1, 2):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        for c in (3, 4, 5):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)

    @property
    def backend(self) -> str:
        return self.source.currentData()

    def find(self) -> None:
        b = self.backend
        self.win.run_task("Finding tag changes", lambda c, cfg, p: normalize.propose_changes(c, b, p),
                          lambda n: None if isinstance(n, dict) else
                          QMessageBox.information(self, "Tag Cleanup", f"{n:,} new change(s) proposed."))

    def lookup(self) -> None:
        b = self.backend
        how = ("by fingerprint through AcoustID where possible, otherwise by artist and title"
               if self.config.acoustid.enabled and self.config.acoustid.api_key else
               "by artist and title (add an AcoustID key under Settings to match by sound instead)")
        if QMessageBox.question(
                self, "MusicBrainz", f"Look up each song not looked up before on MusicBrainz, {how}? MusicBrainz "
                "allows one request a second, so a large library takes hours. You can stop at any time and carry "
                "on later. Only song details are sent, never audio or file names.") != QMessageBox.StandardButton.Yes:
            return
        self.win.run_task("Looking up on MusicBrainz", lambda c, cfg, p: musicbrainz.lookup(c, cfg, b, p), self._looked_up)

    def _looked_up(self, r: dict) -> None:
        if "matched" not in r:
            return
        msg = (f"Looked up {r['looked_up']:,} song(s): {r['matched']:,} found on MusicBrainz, {r['proposed']:,} "
               "change(s) proposed.")
        if r["errors"]:
            msg += f" {r['errors']:,} couldn't be reached and will be tried again next time."
        QMessageBox.information(self, "MusicBrainz", msg)

    def _fill(self, t: QTableWidget, rows) -> None:
        t.setRowCount(0)
        for e in rows[:MAX_ROWS]:
            r = t.rowCount()
            t.insertRow(r)
            box = QTableWidgetItem()
            box.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            box.setCheckState(Qt.CheckState.Unchecked)
            box.setData(Qt.ItemDataRole.UserRole, e["id"])
            t.setItem(r, 0, box)
            t.setItem(r, 1, cell(RULE_LABELS.get(e["rule_id"], e["rule_id"])))
            t.setItem(r, 2, cell(e["field"].replace("_", " ")))
            t.setItem(r, 3, cell(e["old_value"]))
            t.setItem(r, 4, cell(e["new_value"]))
            path = e["path"]
            t.setItem(r, 5, cell("Music app track" if path.startswith(db.MUSICAPP_PREFIX) else Path(path).name))

    def refresh(self) -> None:
        proposed = db.get_normalizations(self.conn, "proposed", self.backend)
        self._fill(self.proposed, proposed)
        more = f" (showing the first {MAX_ROWS:,})" if len(proposed) > MAX_ROWS else ""
        self.proposed_count.setText(f"{len(proposed):,} proposed change(s){more}")
        self._fill(self.applied, db.get_normalizations(self.conn, "applied", self.backend, limit=MAX_ROWS))
        st = db.musicbrainz_stats(self.conn)
        found = sum(v for k, v in st.items() if k.startswith("matched"))
        self.mb_note.setText(f"MusicBrainz: {found:,} song(s) found, {st.get('no_match', 0):,} not found so far."
                             if st else "")
        self.mb_note.setVisible(bool(st))

    @staticmethod
    def _check_all(t: QTableWidget, on: bool) -> None:
        for r in range(t.rowCount()):
            t.item(r, 0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    @staticmethod
    def _checked(t: QTableWidget) -> list[int]:
        return [t.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(t.rowCount())
                if t.item(r, 0).checkState() == Qt.CheckState.Checked]

    def _report(self, r: dict, verb: str) -> None:
        if r.get("stopped"):
            return
        n = r.get(verb, 0)
        msg = f"{n:,} change(s) {verb}."
        if r["errors"]:
            msg += f"\n\n{len(r['errors']):,} couldn't be:\n" + "\n".join(r["errors"][:10])
        QMessageBox.information(self, "Tag Cleanup", msg)

    def apply(self) -> None:
        ids = self._checked(self.proposed)
        if ids:
            self.win.run_task("Applying changes", lambda c, cfg, p: normalize.apply_many(c, ids, p),
                              lambda r: self._report(r, "applied"))

    def reject(self) -> None:
        for i in self._checked(self.proposed):
            normalize.reject_change(self.conn, i)
        self.win.refresh_all()

    def revert(self) -> None:
        ids = self._checked(self.applied)
        if ids:
            self.win.run_task("Reverting changes", lambda c, cfg, p: normalize.revert_many(c, ids, p),
                              lambda r: self._report(r, "reverted"))


# ----------------------------------------------------------------- organize ----

class OrganizePage(Page):
    title = "Organize"

    def __init__(self, win) -> None:
        super().__init__(win)
        self.body.addWidget(heading("Organize"))
        self.dest = QComboBox()
        self.pattern = QComboBox()
        for k, v in organize.PATTERNS.items():
            self.pattern.addItem(v, k)
        form = QFormLayout()
        form.addRow("Sort into", self.dest)
        form.addRow("Layout", self.pattern)
        self.body.addWidget(card(
            heading("Sorts songs into Artist and Album folders, using their tags (tidy them first in Tag Cleanup). "
                    "Planning moves nothing: review the plan, then move the files you choose. Every move can be "
                    "undone. Files the Music app manages itself are left alone, so it doesn't lose track of them.",
                    "muted"),
            form, row(button("Plan moves", self.plan, "primary"))))

        self.tabs = QTabWidget()
        planned = QWidget()
        pv = QVBoxLayout(planned)
        self.planned_count = heading("", "muted")
        self.planned = table(["", "From", "To"])
        self._setup(self.planned)
        pv.addWidget(self.planned_count)
        pv.addWidget(self.planned)
        pv.addLayout(row(button("Select all", lambda: TagsPage._check_all(self.planned, True)),
                         button("Select none", lambda: TagsPage._check_all(self.planned, False)),
                         button("Move selected", self.apply, "primary")))
        done = QWidget()
        dv = QVBoxLayout(done)
        self.applied = table(["", "From", "To"])
        self._setup(self.applied)
        dv.addWidget(self.applied)
        dv.addLayout(row(button("Undo selected", self.undo)))
        self.tabs.addTab(planned, "Planned")
        self.tabs.addTab(done, "Moved")
        self.tabs.setMinimumHeight(420)
        self.body.addWidget(self.tabs, 1)

    @staticmethod
    def _setup(t: QTableWidget) -> None:
        h = t.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

    def refresh(self) -> None:
        current = self.dest.currentData()
        self.dest.clear()
        for r in self.config.library_roots:
            self.dest.addItem(pretty(r), r)
        if current in self.config.library_roots:
            self.dest.setCurrentIndex(self.config.library_roots.index(current))
        rows = db.get_moves(self.conn, "planned")
        self._fill(self.planned, rows)
        more = f" (showing the first {MAX_ROWS:,})" if len(rows) > MAX_ROWS else ""
        self.planned_count.setText(f"{len(rows):,} planned move(s){more}")
        self._fill(self.applied, db.get_moves(self.conn, "applied", limit=MAX_ROWS))

    def _fill(self, t: QTableWidget, rows) -> None:
        t.setRowCount(0)
        for m in rows[:MAX_ROWS]:
            r = t.rowCount()
            t.insertRow(r)
            box = QTableWidgetItem()
            box.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            box.setCheckState(Qt.CheckState.Unchecked)
            box.setData(Qt.ItemDataRole.UserRole, m["id"])
            t.setItem(r, 0, box)
            t.setItem(r, 1, cell(self._short(m["old_path"])))
            t.setItem(r, 2, cell(self._short(m["new_path"])))

    def _short(self, path: str) -> str:
        """The path inside its music folder (the folder's own path is the same for every row)."""
        for root in self.config.library_roots:
            if path.startswith(root.rstrip("/") + "/"):
                return path[len(root.rstrip("/")) + 1:]
        return pretty(path)

    def plan(self) -> None:
        dest, pattern = self.dest.currentData(), self.pattern.currentData()
        if not dest:
            QMessageBox.information(self, "Organize", "Add a music folder on the Library page first.")
            return
        self.win.run_task("Planning moves", lambda c, cfg, p: organize.plan(c, cfg, dest, pattern, p), self._planned)

    def _planned(self, r: dict) -> None:
        if "planned" not in r:
            return
        msg = f"{r['planned']:,} file(s) to move; {r['already_in_place']:,} already in place."
        if r["skipped_music_app"]:
            msg += f" {r['skipped_music_app']:,} managed by the Music app were left alone."
        QMessageBox.information(self, "Organize", msg)

    def _report(self, r: dict, key: str, verb: str) -> None:
        if key not in r:
            return
        msg = f"{r[key]:,} file(s) {verb}."
        if r["errors"]:
            msg += f"\n\n{len(r['errors']):,} couldn't be:\n" + "\n".join(r["errors"][:10])
        QMessageBox.information(self, "Organize", msg)

    def apply(self) -> None:
        ids = TagsPage._checked(self.planned)
        if ids:
            self.win.run_task("Moving files", lambda c, cfg, p: organize.apply_moves(c, cfg, ids, p),
                              lambda r: self._report(r, "moved", "moved"))

    def undo(self) -> None:
        ids = TagsPage._checked(self.applied)
        if ids:
            self.win.run_task("Moving files back", lambda c, cfg, p: organize.undo_moves(c, cfg, ids, p),
                              lambda r: self._report(r, "moved_back", "moved back"))


# ------------------------------------------------------------------ convert ----

class ConvertPage(Page):
    title = "Convert"

    def __init__(self, win) -> None:
        super().__init__(win)
        self.body.addWidget(heading("Convert"))
        self.fmt = QComboBox()
        for f in convert.SOURCE_FORMATS:
            self.fmt.addItem(f".{f}", f)
        self.dispo = QComboBox()
        for k, v in convert.DISPOSITIONS.items():
            self.dispo.addItem(v, k)
        self.bitrate = heading("", "muted")
        form = QFormLayout()
        form.addRow("Convert", self.fmt)
        form.addRow("Afterwards", self.dispo)
        form.addRow("Quality", self.bitrate)
        self.run_btn = button("Convert queued files", self.run, "primary")
        self.body.addWidget(card(
            heading("Makes an .m4a (AAC) copy of each file, beside the original, with its tags and album art. "
                    "Nothing is overwritten, and the original is only touched after its copy has been checked.", "muted"),
            form, row(button("Add these to the queue", self.enqueue), self.run_btn)))
        self.jobs = table(["Status", "File", "Problem"])
        h = self.jobs.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.jobs.setMinimumHeight(300)
        self.body.addWidget(card(heading("Queue", "h2"), self.jobs, row(button("Clear failed", self.clear))))
        self.body.addStretch(1)

    def refresh(self) -> None:
        self.bitrate.setText(f"{self.config.bitrate}bps AAC (change under Settings)")
        queued = db.library_stats(self.conn)["queued_jobs"]
        self.run_btn.setText(f"Convert queued files ({queued:,})")
        self.run_btn.setEnabled(queued > 0)
        self.jobs.setRowCount(0)
        for j in db.recent_conversion_jobs(self.conn):
            r = self.jobs.rowCount()
            self.jobs.insertRow(r)
            self.jobs.setItem(r, 0, cell(j["status"]))
            self.jobs.setItem(r, 1, cell(Path(j["source_path"]).name))
            self.jobs.setItem(r, 2, cell(j["error_message"] or ""))

    def enqueue(self) -> None:
        fmt, dispo = self.fmt.currentData(), self.dispo.currentData()
        if dispo == "trash" and QMessageBox.warning(
                self, "Convert", "After each copy is made and checked, its original goes to the Trash. Continue?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        n = convert.enqueue(self.conn, fmt, dispo)
        QMessageBox.information(self, "Convert", f"{n:,} .{fmt} file(s) added to the queue.")
        self.win.refresh_all()

    def run(self) -> None:
        self.win.run_task("Converting", lambda c, cfg, p: convert.run_queue(c, cfg, p), self._done)

    def _done(self, r: dict) -> None:
        if "succeeded" in r:
            QMessageBox.information(self, "Convert", f"{r['succeeded']:,} converted, {r['failed']:,} failed.")

    def clear(self) -> None:
        db.clear_finished_jobs(self.conn)
        self.refresh()


# ----------------------------------------------------------------- settings ----

class SettingsPage(Page):
    title = "Settings"

    def __init__(self, win) -> None:
        super().__init__(win)
        self.body.addWidget(heading("Settings"))
        self.quarantine = QLineEdit()
        qf = QFormLayout()
        qf.addRow("Quarantine folder", row(self.quarantine, button("Choose…", self.choose_quarantine), stretch_end=False))
        self.body.addWidget(card(heading("Files", "h2"), heading(
            "Duplicates you quarantine are moved here, inside a copy of their original folders.", "muted"), qf))

        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0, 30)
        self.tolerance.setSingleStep(0.5)
        self.tolerance.setSuffix(" s")
        self.acoustic = QDoubleSpinBox()
        self.acoustic.setRange(0.5, 1.0)
        self.acoustic.setSingleStep(0.01)
        self.text = QDoubleSpinBox()
        self.text.setRange(0.5, 1.0)
        self.text.setSingleStep(0.01)
        for spin in (self.tolerance, self.acoustic, self.text):
            spin.setMaximumWidth(120)
        df = QFormLayout()
        df.addRow("Lengths may differ by", self.tolerance)
        df.addRow("Same recording at a sound match of", self.acoustic)
        df.addRow("Weaker match, with names agreeing, at", self.text)
        self.body.addWidget(card(heading("Matching", "h2"), df))

        self.fpcalc = QLineEdit()
        self.fpcalc.setPlaceholderText("Use the copy included with Melophile")
        self.fp_status = heading("", "muted")
        self.acoustid_on = QCheckBox("Look songs up on AcoustID by fingerprint (online; sends fingerprints, not "
                                     "audio). Also makes MusicBrainz matches more reliable")
        self.acoustid_key = QLineEdit()
        self.acoustid_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.acoustid_key.setPlaceholderText("Free key from acoustid.org > My Applications")
        ff = QFormLayout()
        ff.addRow("fpcalc", row(self.fpcalc, button("Choose…", self.choose_fpcalc), stretch_end=False))
        ff.addRow("", self.fp_status)
        ff.addRow("", self.acoustid_on)
        ff.addRow("AcoustID key", self.acoustid_key)
        self.body.addWidget(card(heading("Fingerprints", "h2"), ff))

        self.bitrate = QComboBox()
        self.bitrate.addItems(BITRATES)
        self.bitrate.setMaximumWidth(120)
        cf = QFormLayout()
        cf.addRow("AAC quality", self.bitrate)
        self.body.addWidget(card(heading("Conversion", "h2"), cf))

        self.where = heading("", "muted")
        self.body.addWidget(card(heading("Catalog", "h2"), self.where,
                                 row(button("Show in Finder", lambda: reveal(str(self.config.db_path))))))
        self.body.addLayout(row(button("Save settings", self.save, "primary")))
        self.body.addStretch(1)

    def refresh(self) -> None:
        c = self.config
        self.quarantine.setText(pretty(c.quarantine_dir))
        self.tolerance.setValue(c.dedupe.duration_tolerance_seconds)
        self.acoustic.setValue(c.dedupe.acoustic_threshold)
        self.text.setValue(c.dedupe.text_threshold)
        self.fpcalc.setText(c.fpcalc_path)
        found = tools.find_fpcalc(c.fpcalc_path)
        self.fp_status.setText(f"Using {found}" if found else "fpcalc wasn't found, so fingerprints are off.")
        self.acoustid_on.setChecked(c.acoustid.enabled)
        self.acoustid_key.setText(c.acoustid.api_key)
        self.bitrate.setCurrentText(c.bitrate)
        self.where.setText(f"Melophile keeps what it knows about your library in {pretty(c.db_path)}. Your music itself "
                           "stays where it is.")

    def choose_quarantine(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Quarantine folder", self.quarantine.text())
        if folder:
            self.quarantine.setText(folder)

    def choose_fpcalc(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose fpcalc", str(Path.home()))
        if path:
            self.fpcalc.setText(path)

    def save(self) -> None:
        c = self.config
        c.quarantine_dir = Path(self.quarantine.text().strip() or c.quarantine_dir).expanduser()
        c.dedupe.duration_tolerance_seconds = self.tolerance.value()
        c.dedupe.acoustic_threshold = self.acoustic.value()
        c.dedupe.text_threshold = self.text.value()
        c.fpcalc_path = self.fpcalc.text().strip()
        c.acoustid.enabled = self.acoustid_on.isChecked()
        c.acoustid.api_key = self.acoustid_key.text().strip()
        c.bitrate = self.bitrate.currentText()
        self.win.save_config()
        QMessageBox.information(self, "Settings", "Saved.")
