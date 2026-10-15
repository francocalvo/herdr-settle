"""Unit tests for the restore flow, against the in-memory herdr fake."""

from __future__ import annotations

from typing import Any

import pytest

from herdr_settle.restore import RestoreError, discard, restore_workspace
from herdr_settle.socket import HerdrClient
from herdr_settle.store import ArchivedTab, ArchiveRecord, ArchiveStore
from tests.conftest import FakeHerdr


def make_record(**overrides: Any) -> ArchiveRecord:
    """Build a record with one split tab by default."""
    defaults: dict[str, Any] = {
        "id": "rec1",
        "label": "proj",
        "cwd": "/tmp/proj",
        "settled_at": "2026-02-09T10:00:00+00:00",
        "tabs": [
            ArchivedTab(
                label="1",
                layout={
                    "type": "split",
                    "direction": "right",
                    "ratio": 0.5,
                    "first": {"type": "pane", "cwd": "/tmp/proj", "label": "shell"},
                    "second": {
                        "type": "pane",
                        "cwd": "/tmp/proj",
                        "label": "pi",
                        "command": ["pi", "--session", "sess-1"],
                    },
                },
                focused_pane_label="pi",
            ),
        ],
        "source_workspace_id": "ws-old",
        "agents_total": 1,
        "agents_resumable": 1,
    }
    defaults.update(overrides)
    return ArchiveRecord(**defaults)


def test_restore_applies_first_tab_into_default_tab(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """The first tab reuses the default tab via tab_id; no leftover tab."""
    store.save(make_record())

    record = restore_workspace(HerdrClient(fake_herdr), store, "rec1")

    assert record.label == "proj"
    applies = fake_herdr.layout_applies
    assert len(applies) == 1
    first = applies[0]
    # applied into the default tab of the freshly created workspace
    client = HerdrClient(fake_herdr)
    new_ws = client.focused_workspace()
    assert new_ws is not None
    default_tab = fake_herdr.tabs[new_ws["workspace_id"]][0]["tab_id"]
    assert first["tab_id"] == default_tab
    assert first["root"]["second"]["command"] == ["pi", "--session", "sess-1"]
    # the record is consumed
    assert store.list() == []
    # the created workspace has exactly one tab (no leftover)
    assert len(fake_herdr.tabs[new_ws["workspace_id"]]) == 1
    assert fake_herdr.notifications


def test_restore_extra_tabs_become_new_tabs(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """Tabs beyond the first are applied as new tabs with their labels."""
    record = make_record(
        tabs=[
            ArchivedTab(
                label="1",
                layout={"type": "pane", "cwd": "/tmp"},
                focused_pane_label=None,
            ),
            ArchivedTab(
                label="logs",
                layout={"type": "pane", "cwd": "/var/log"},
                focused_pane_label=None,
            ),
        ],
    )
    store.save(record)

    restore_workspace(HerdrClient(fake_herdr), store, "rec1")

    client = HerdrClient(fake_herdr)
    new_ws = client.focused_workspace()
    assert new_ws is not None
    tabs = fake_herds_tabs(fake_herdr, str(new_ws["workspace_id"]))
    assert [tab["label"] for tab in tabs] == ["1", "logs"]
    assert len(fake_herdr.layout_applies) == 2


def fake_herds_tabs(server: FakeHerdr, workspace_id: str) -> list[dict[str, object]]:
    """Read tabs of a workspace from the fake."""
    return server.tabs[workspace_id]


def test_restore_empty_record_closes_default_tab(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """A record with no tabs still yields a clean workspace."""
    store.save(make_record(tabs=[]))

    restore_workspace(HerdrClient(fake_herdr), store, "rec1")

    assert fake_herdr.closed_tabs, "default tab should be closed"


def test_restore_focuses_recorded_tab(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """The recorded focused tab label is focused after restore."""
    store.save(make_record())

    restore_workspace(HerdrClient(fake_herdr), store, "rec1")

    assert fake_herdr.focused_tabs, "a tab should have been focused"


def test_restore_missing_record_raises(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """Restoring an unknown record raises StoreError, creates nothing."""
    with pytest.raises(Exception, match="no archived space"):
        restore_workspace(HerdrClient(fake_herdr), store, "ghost")
    assert fake_herdr.workspaces == {}


def test_restore_socket_failure_keeps_record(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """If layout.apply fails the record is kept for a later retry."""

    def broken_apply(params: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("herdr exploded")

    fake_herdr._on_layout_apply = broken_apply  # type: ignore[method-assign]
    store.save(make_record())

    with pytest.raises((RestoreError, Exception)):
        restore_workspace(HerdrClient(fake_herdr), store, "rec1")

    assert store.get("rec1") is not None


def test_discard_deletes_without_restoring(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """discard() removes the record; no workspace is created."""
    store.save(make_record())

    record = discard(store, "rec1")

    assert record.label == "proj"
    assert store.list() == []
    assert fake_herdr.workspaces == {}
