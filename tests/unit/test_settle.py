"""Unit tests for the settle flow, against the in-memory herdr fake."""

from __future__ import annotations

from typing import Any

import pytest

from herdr_settle.settle import (
    SettleRefusedError,
    inject_resume_commands,
    resume_argv,
    settle_workspace,
)
from herdr_settle.socket import HerdrClient, SocketRequestError
from herdr_settle.store import ArchiveStore
from tests.conftest import FakeHerdr


def test_resume_argv_known_agents() -> None:
    """Known agents map to their resume commands."""
    assert resume_argv("pi", "abc") == ["pi", "--session", "abc"]
    assert resume_argv("claude", "s-1") == ["claude", "--resume", "s-1"]
    assert resume_argv("codex", "c-1") == ["codex", "resume", "c-1"]


def test_resume_argv_unknown_or_empty() -> None:
    """Unknown agents and empty values produce no command."""
    assert resume_argv("gemini", "v") is None
    assert resume_argv("pi", "") is None


def test_inject_resume_commands_walks_splits() -> None:
    """Commands are injected into pane nodes nested in splits."""
    root: dict[str, Any] = {
        "type": "split",
        "direction": "right",
        "ratio": 0.5,
        "first": {"type": "pane", "pane_id": "p1", "cwd": "/tmp"},
        "second": {"type": "pane", "pane_id": "p2", "cwd": "/tmp"},
    }
    total, resumable = inject_resume_commands(
        root,
        {"p2": {"agent": "claude", "value": "s9"}},
    )
    assert (total, resumable) == (1, 1)
    assert root["second"]["command"] == ["claude", "--resume", "s9"]
    assert "command" not in root["first"]


def test_settle_snapshots_and_closes(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """Settle writes a record, closes the workspace, and notifies."""
    workspace_id = fake_herdr.add_workspace(
        "proj",
        "/tmp/proj",
        panes=[
            {"cwd": "/tmp/proj", "label": "shell"},
            {"cwd": "/tmp/proj", "label": "pi"},
        ],
    )
    fake_herdr.add_agent(workspace_id, agent="pi", session_value="sess-1", pane_index=1)
    fake_herdr.workspaces[workspace_id]["focused"] = True

    record = settle_workspace(HerdrClient(fake_herdr), store, workspace_id)

    assert workspace_id not in fake_herdr.workspaces
    assert record.label == "proj"
    assert record.agents_total == 1
    assert record.agents_resumable == 1
    assert len(store.list()) == 1
    # the agent pane node carries the resume command
    assert record.tabs[0].layout["second"]["command"] == ["pi", "--session", "sess-1"]
    assert fake_herdr.notifications


def test_settle_refuses_while_working(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """A working agent blocks settle; nothing is closed or written."""
    workspace_id = fake_herdr.add_workspace("proj", "/tmp/proj")
    fake_herdr.add_agent(workspace_id, agent="claude", status="working")

    with pytest.raises(SettleRefusedError, match="still working/blocked"):
        settle_workspace(HerdrClient(fake_herdr), store, workspace_id)

    assert workspace_id in fake_herdr.workspaces
    assert store.list() == []


def test_settle_force_overrides_guard(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """Force settles despite working agents."""
    workspace_id = fake_herdr.add_workspace("proj", "/tmp/proj")
    fake_herdr.add_agent(workspace_id, agent="claude", status="blocked")

    record = settle_workspace(HerdrClient(fake_herdr), store, workspace_id, force=True)
    assert workspace_id not in fake_herdr.workspaces
    assert record.label == "proj"


def test_settle_unknown_workspace_refuses(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """Settling an unknown workspace id refuses cleanly."""
    with pytest.raises(SettleRefusedError, match="not found"):
        settle_workspace(HerdrClient(fake_herdr), store, "nope")


def test_settle_group_close_snapshots_all_members(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """A linked-worktree group closes as a group, each member archived."""
    primary = fake_herdr.add_workspace("main", "/tmp/main", group_id="g1")
    worktree = fake_herdr.add_workspace("wt", "/tmp/wt", group_id="g1")

    record = settle_workspace(HerdrClient(fake_herdr), store, primary)

    assert primary not in fake_herdr.workspaces
    assert worktree not in fake_herdr.workspaces
    ids = {entry.id for entry in store.list()}
    assert len(ids) == 2
    assert record.source_workspace_id == primary


def test_settle_group_unidentifiable_refuses(
    fake_herdr: FakeHerdr,
    store: ArchiveStore,
) -> None:
    """A group error without identifiable members refuses, nothing closed."""
    workspace_id = fake_herdr.add_workspace("proj", "/tmp/proj")
    # make the fake demand a group close while exposing no group fields
    fake_herdr.workspaces[workspace_id]["group_id"] = None
    original_close = fake_herdr._on_workspace_close

    def demand_group(params: dict[str, Any]) -> dict[str, Any]:
        if not params.get("close_group"):
            raise SocketRequestError(
                "workspace_group_close_required",
                "close the whole group or pass close_group: true",
            )
        return original_close(params)

    fake_herdr._on_workspace_close = demand_group  # type: ignore[method-assign]

    with pytest.raises(SettleRefusedError, match="could not be identified"):
        settle_workspace(HerdrClient(fake_herdr), store, workspace_id)

    assert workspace_id in fake_herdr.workspaces
    assert store.list() == []
