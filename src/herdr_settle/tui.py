"""Textual archive browser for herdr-settle.

Lists settled (archived) spaces; ``enter`` restores one (recreating the
workspace with resumed conversations), ``x`` discards its record with a
``y/n`` confirm, ``r`` refreshes, ``q``/``esc`` closes. Blocking
socket/restore work runs in a thread worker (linear-ws's approach);
screen pushes stay on the app thread.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Label, Static

from herdr_settle.restore import discard, restore_workspace
from herdr_settle.socket import HerdrClient
from herdr_settle.store import ArchiveRecord, ArchiveStore, StoreError


class ConfirmDiscardScreen(ModalScreen[bool]):
    """Ask for confirmation before discarding an archive record."""

    BINDINGS = [
        ("y", "confirm", "discard"),
        ("n", "cancel", "keep"),
        ("escape", "cancel", "keep"),
    ]

    def __init__(self, label: str) -> None:
        """Build the confirm dialog for one archived space."""
        super().__init__()
        self._label = label

    def compose(self) -> ComposeResult:
        """Render the confirm dialog."""
        with Vertical(id="confirm-box"):
            yield Label(f"Discard archive for [b]{self._label}[/b]?")
            yield Label("The workspace stays closed; the record is deleted.")
            yield Static("y = discard · n/esc = keep", id="confirm-hint")

    def action_confirm(self) -> None:
        """Dismiss with ``True`` (discard)."""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """Dismiss with ``False`` (keep)."""
        self.dismiss(False)


class ArchiveBrowser(App[None]):
    """The settled-spaces browser popup."""

    CSS = """
    #empty-state { padding: 1 2; }
    #error-line { color: red; padding: 0 2; }
    #confirm-box { padding: 1 2; }
    #confirm-hint { color: #808080; padding-top: 1; }
    """

    BINDINGS = [
        ("enter", "restore", "restore"),
        ("x", "discard", "discard"),
        ("r", "refresh", "refresh"),
        ("q", "quit", "close"),
        ("escape", "quit", "close"),
    ]

    def __init__(self, client: HerdrClient, store: ArchiveStore) -> None:
        """Build the browser around a client and store."""
        super().__init__()
        self._client = client
        self._store = store
        self._records: list[ArchiveRecord] = []

    def compose(self) -> ComposeResult:
        """Render the table and footer."""
        table: DataTable[Text] = DataTable(cursor_type="row")
        table.add_columns("space", "tabs", "chats", "settled", "path")
        yield table
        yield Static("", id="error-line")
        yield Static("", id="empty-state")
        yield Footer()

    def on_mount(self) -> None:
        """Load records and focus the table."""
        self._load_records()
        self.query_one(DataTable).focus()

    def _load_records(self) -> None:
        """Fill the table from the store."""
        table = self.query_one(DataTable)
        table.clear()
        self._records = self._store.list()
        empty = self.query_one("#empty-state", Static)
        empty.display = not self._records
        if not self._records:
            empty.update("No settled spaces. Settle one with prefix+shift+s.")
            return
        for record in self._records:
            table.add_row(
                Text(record.label, style="bold"),
                str(record.tab_count),
                str(record.resumable_count),
                _relative_time(record.settled_at),
                Text(record.cwd, style="dim"),
                key=record.id,
            )

    def action_refresh(self) -> None:
        """Reload the record list."""
        self._error("")
        self._load_records()

    def _selected(self) -> ArchiveRecord | None:
        """Return the record behind the table cursor."""
        table = self.query_one(DataTable)
        row = table.cursor_row
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def action_restore(self) -> None:
        """Restore the selected archived space."""
        record = self._selected()
        if record is None:
            return
        self._run_restore(record)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Restore the row chosen with enter/mouse on the table.

        DataTable consumes the enter key itself (``select_cursor``), so the
        restore shortcut reaches the app through this message instead of the
        app-level binding.
        """
        record_id = str(event.row_key.value or "")
        record = next((r for r in self._records if r.id == record_id), None)
        if record is not None:
            self._run_restore(record)

    @work(thread=True, exclusive=True)
    def _run_restore(self, record: ArchiveRecord) -> None:
        """Restore in a worker thread, then refresh on the UI thread."""
        try:
            restore_workspace(self._client, self._store, record.id)
        except Exception as exc:  # noqa: BLE001 - surfaced in the popup
            self.call_from_thread(self._error, f"restore failed: {exc}")
            return
        self.call_from_thread(self._load_records)

    async def action_discard(self) -> None:
        """Discard the selected record after confirmation."""
        record = self._selected()
        if record is None:
            return

        def on_confirm(confirmed: bool | None) -> None:
            """Run the discard worker when the dialog is confirmed."""
            if confirmed:
                self._run_discard(record)

        # push_screen_wait needs worker context; the callback form works
        # from a plain message-pump action.
        self.push_screen(ConfirmDiscardScreen(record.label), on_confirm)

    @work(thread=True, exclusive=True)
    def _run_discard(self, record: ArchiveRecord) -> None:
        """Discard in a worker thread, then refresh on the UI thread."""
        try:
            discard(self._store, record.id)
        except StoreError as exc:
            self.call_from_thread(self._error, f"discard failed: {exc}")
            return
        self.call_from_thread(self._load_records)

    def _error(self, message: str) -> None:
        """Show or clear the error line."""
        self.query_one("#error-line", Static).update(message)


def _relative_time(iso_timestamp: str) -> str:
    """Render a settled-at timestamp as a relative age."""
    try:
        settled = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return iso_timestamp
    if settled.tzinfo is None:
        settled = settled.replace(tzinfo=UTC)
    seconds = max(0, int((datetime.now(UTC) - settled).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def run_browser(client: HerdrClient, store: ArchiveStore) -> str | None:
    """Run the archive browser; returns the result summary or ``None``.

    Guards against headless invocation: herdr popup panes are real
    terminals, but a piped stdout is not, and Textual needs one.
    """
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        print(
            "herdr-settle archive needs a terminal; run it from a herdr pane "
            "or bind it to a plugin key.",
            file=sys.stderr,
        )
        return None
    app: ArchiveBrowser = ArchiveBrowser(client, store)
    app.run()
    return "closed"
