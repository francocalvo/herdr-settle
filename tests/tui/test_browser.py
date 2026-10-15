"""Textual pilot tests for the archive browser, with the herdr fake."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import pytest
from textual.widgets import DataTable

from herdr_settle.socket import HerdrClient
from herdr_settle.store import ArchivedTab, ArchiveRecord, ArchiveStore
from herdr_settle.tui import ArchiveBrowser
from tests.conftest import FakeHerdr


def make_record(record_id: str, label: str, chats: int = 1) -> ArchiveRecord:
    """Build a minimal record for browser tests."""
    return ArchiveRecord(
        id=record_id,
        label=label,
        cwd=f"/tmp/{label}",
        settled_at="2026-02-09T10:00:00+00:00",
        tabs=[ArchivedTab(label="1", layout={"type": "pane"}, focused_pane_label=None)],
        agents_total=chats,
        agents_resumable=chats,
    )


async def wait_until(
    predicate: Callable[[], bool],
    timeout: float = 3.0,
) -> bool:
    """Wait for a condition by pumping the asyncio loop."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        if predicate():
            return True
    return False


@pytest.fixture()
def populated_store(store: ArchiveStore) -> ArchiveStore:
    """A store with two archive records."""
    store.save(make_record("rec1", "alpha"))
    store.save(make_record("rec2", "beta", chats=2))
    return store


async def test_empty_state_shows_hint(store: ArchiveStore) -> None:
    """With no records the empty hint is displayed."""
    app = ArchiveBrowser(HerdrClient(FakeHerdr()), store)
    async with app.run_test() as pilot:
        await pilot.pause()
        empty = app.query_one("#empty-state")
        assert empty.display
        assert "No settled spaces" in str(empty.render())


async def test_rows_render_and_restore(
    fake_herdr: FakeHerdr,
    populated_store: ArchiveStore,
) -> None:
    """Enter restores the selected space and consumes its record."""
    app = ArchiveBrowser(HerdrClient(fake_herdr), populated_store)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.row_count == 2
        await pilot.press("enter")
        assert await wait_until(
            lambda: len(populated_store.list()) == 1,
        ), "record should be consumed after restore"
        # a workspace was created in the fake herdr
        assert len(fake_herdr.workspaces) == 1


async def test_discard_confirm_yes(
    fake_herdr: FakeHerdr,
    populated_store: ArchiveStore,
) -> None:
    """X then y discards the record without creating a workspace."""
    app = ArchiveBrowser(HerdrClient(fake_herdr), populated_store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("y")
        assert await wait_until(
            lambda: len(populated_store.list()) == 1,
        ), "one record should remain"
        assert fake_herdr.workspaces == {}, "discard must not restore"


async def test_discard_confirm_no(
    fake_herdr: FakeHerdr,
    populated_store: ArchiveStore,
) -> None:
    """X then n keeps the record."""
    app = ArchiveBrowser(HerdrClient(fake_herdr), populated_store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert len(populated_store.list()) == 2


async def test_refresh_keeps_rows(
    fake_herdr: FakeHerdr,
    populated_store: ArchiveStore,
) -> None:
    """R reloads the table without losing rows."""
    app = ArchiveBrowser(HerdrClient(fake_herdr), populated_store)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.row_count == 2
