"""Unit tests for the archive record store."""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_settle.store import (
    ArchivedTab,
    ArchiveRecord,
    ArchiveStore,
    StoreError,
)


def make_record(record_id: str = "abc123", label: str = "proj") -> ArchiveRecord:
    """Build a minimal valid record."""
    return ArchiveRecord(
        id=record_id,
        label=label,
        cwd="/tmp/proj",
        settled_at="2026-02-09T10:00:00+00:00",
        tabs=[ArchivedTab(label="1", layout={"type": "pane"}, focused_pane_label=None)],
        source_workspace_id="ws1",
        agents_total=2,
        agents_resumable=1,
    )


def test_save_and_get_roundtrip(store: ArchiveStore) -> None:
    """A saved record is returned intact by get()."""
    record = make_record()
    store.save(record)
    loaded = store.get("abc123")
    assert loaded == record


def test_list_newest_first(store: ArchiveStore) -> None:
    """Records sort by settled_at, newest first."""
    store.save(make_record("one", "old"))
    newer = make_record("two", "new")
    store.save(newer)  # same settled_at; save order breaks the tie stably
    records = store.list()
    assert [record.id for record in records] == ["two", "one"]


def test_get_missing_raises(store: ArchiveStore) -> None:
    """get() on an unknown id raises StoreError."""
    with pytest.raises(StoreError, match="no archived space"):
        store.get("nope")


def test_delete_removes_record(store: ArchiveStore) -> None:
    """delete() removes the record file."""
    store.save(make_record())
    store.delete("abc123")
    assert store.list() == []
    with pytest.raises(StoreError):
        store.delete("abc123")


def test_save_is_atomic_no_tmp_left(store: ArchiveStore) -> None:
    """After save() no .tmp files remain next to the record."""
    store.save(make_record())
    leftovers = list(store.root.glob("*.tmp"))
    assert leftovers == []


def test_record_ids_cannot_escape_root(store: ArchiveStore) -> None:
    """Path tricks in record ids are rejected."""
    with pytest.raises(StoreError, match="invalid archive record id"):
        store.get("../escape")
    with pytest.raises(StoreError, match="invalid archive record id"):
        store.get("")


def test_unreadable_record_is_skipped_in_list(
    store: ArchiveStore,
    tmp_path: Path,
) -> None:
    """A corrupt record file does not break list()."""
    store.save(make_record())
    (store.root / "broken.json").write_text("{not json")
    records = store.list()
    assert [record.id for record in records] == ["abc123"]


def test_state_dir_fallback_uses_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without HERDR_PLUGIN_STATE_DIR the default is under ~/.local/state."""
    monkeypatch.delenv("HERDR_PLUGIN_STATE_DIR", raising=False)
    from herdr_settle.store import state_dir

    assert state_dir() == Path.home() / ".local/state/herdr/plugins/francocalvo.settle"


def test_state_dir_honors_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """HERDR_PLUGIN_STATE_DIR wins over the default."""
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path / "state"))
    from herdr_settle.store import state_dir

    assert state_dir() == tmp_path / "state"
