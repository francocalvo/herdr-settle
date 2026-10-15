"""The settle flow: snapshot a workspace, then close it.

Steps: resolve the workspace, refuse while agents are working/blocked
(unless forced), export each tab's layout and inject resume commands for
detected agent sessions, write the archive record, then ``workspace.close``
— falling back to a whole-group close (every group member snapshotted as
its own record) when herdr demands it.
"""

from __future__ import annotations

import os
from typing import Any

from herdr_settle.socket import HerdrClient, SocketRequestError
from herdr_settle.store import (
    ArchivedTab,
    ArchiveRecord,
    ArchiveStore,
    new_record_id,
    now_iso,
)

GUARD_STATUSES = frozenset({"working", "blocked"})

RESUME_COMMANDS: dict[str, list[str]] = {
    "pi": ["pi", "--session"],
    "claude": ["claude", "--resume"],
    "codex": ["codex", "resume"],
}


class SettleRefusedError(Exception):
    """Raised when settling is refused (working agents, group ambiguity)."""

    def __init__(self, message: str) -> None:
        """Store the user-facing refusal reason."""
        super().__init__(message)
        self.message = message


def resume_argv(agent: str, value: str) -> list[str] | None:
    """Return the resume argv for an agent session, or ``None``.

    ``kind`` distinguishes id refs from path refs; every known agent takes
    the value as-is in the last position.
    """
    command = RESUME_COMMANDS.get(agent)
    if not command or not value:
        return None
    return [*command, value]


def inject_resume_commands(
    root: dict[str, Any],
    sessions_by_pane: dict[str, dict[str, Any]],
) -> tuple[int, int]:
    """Set pane ``command`` nodes for resumable agents inside a layout tree.

    Returns ``(agents_total, agents_resumable)`` — every session found in
    the tree counts towards total; only ones with a known resume command
    count as resumable.
    """
    total = 0
    resumable = 0

    def walk(node: dict[str, Any]) -> None:
        nonlocal total, resumable
        if node.get("type") == "split":
            for child in (node.get("first"), node.get("second")):
                if isinstance(child, dict):
                    walk(child)
            return
        pane_id = node.get("pane_id")
        session = sessions_by_pane.get(pane_id) if pane_id else None
        if not session:
            return
        total += 1
        agent = str(session.get("agent", ""))
        value = str(session.get("value", ""))
        argv = resume_argv(agent, value)
        if argv:
            node["command"] = argv
            resumable += 1

    walk(root)
    return total, resumable


def pane_label_of(root: dict[str, Any], pane_id: str) -> str | None:
    """Return the label of a pane node in a layout tree, best effort."""
    def walk(node: dict[str, Any]) -> str | None:
        if node.get("type") == "split":
            for child in (node.get("first"), node.get("second")):
                if isinstance(child, dict):
                    found = walk(child)
                    if found is not None:
                        return found
            return None
        if node.get("pane_id") == pane_id and node.get("label"):
            return str(node["label"])
        return None

    return walk(root)


def agents_in_workspace(
    agents: list[dict[str, Any]],
    workspace_id: str,
) -> list[dict[str, Any]]:
    """Filter agent entries down to one workspace."""
    return [agent for agent in agents if agent.get("workspace_id") == workspace_id]


def sessions_by_tab(
    agents: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Group agent session refs by ``tab_id`` then ``pane_id``."""
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for agent in agents:
        session = agent.get("agent_session")
        tab_id = agent.get("tab_id")
        pane_id = agent.get("pane_id")
        if not session or not tab_id or not pane_id:
            continue
        grouped.setdefault(str(tab_id), {})[str(pane_id)] = dict(session)
    return grouped


def snapshot_workspace(
    client: HerdrClient,
    workspace_id: str,
    *,
    record_id: str | None = None,
) -> ArchiveRecord:
    """Snapshot one workspace into an archive record (does not close it).

    Raises:
        SettleRefusedError: when the workspace is unknown.
        SocketRequestError: when a herdr call fails.
    """
    workspaces = client.workspaces()
    workspace = _find_workspace(workspaces, workspace_id)
    if workspace is None:
        raise SettleRefusedError(f"workspace {workspace_id} not found")
    label = str(workspace.get("label") or workspace_id)

    agents = agents_in_workspace(client.agents(), workspace_id)
    sessions = sessions_by_tab(agents)

    tabs: list[ArchivedTab] = []
    agents_total = 0
    agents_resumable = 0
    pane_cwd: str | None = None
    for tab in client.tabs(workspace_id):
        tab_id = str(tab.get("tab_id", ""))
        layout = client.export_tab_layout(tab_id)
        root = dict(layout.get("root") or {})
        tab_total, tab_resumable = inject_resume_commands(
            root,
            sessions.get(tab_id, {}),
        )
        agents_total += tab_total
        agents_resumable += tab_resumable
        pane_cwd = pane_cwd or _first_pane_cwd(root)
        focused_label = pane_label_of(root, str(layout.get("focused_pane_id") or ""))
        tabs.append(
            ArchivedTab(
                label=str(tab.get("label") or ""),
                layout=root,
                focused_pane_label=focused_label,
            ),
        )

    return ArchiveRecord(
        id=record_id or new_record_id(),
        label=label,
        cwd=_workspace_cwd(workspace, agents, pane_cwd),
        settled_at=now_iso(),
        tabs=tabs,
        source_workspace_id=workspace_id,
        agents_total=agents_total,
        agents_resumable=agents_resumable,
    )


def _find_workspace(
    workspaces: list[dict[str, Any]],
    workspace_id: str,
) -> dict[str, Any] | None:
    """Return the workspace entry with ``workspace_id``, if present."""
    return next(
        (entry for entry in workspaces if entry.get("workspace_id") == workspace_id),
        None,
    )


def _first_pane_cwd(root: dict[str, Any]) -> str | None:
    """Return the cwd of the first pane node in a layout tree."""
    if root.get("type") == "split":
        for child in (root.get("first"), root.get("second")):
            if isinstance(child, dict):
                found = _first_pane_cwd(child)
                if found:
                    return found
        return None
    cwd = root.get("cwd")
    return str(cwd) if cwd else None


def _workspace_cwd(
    workspace: dict[str, Any],
    agents: list[dict[str, Any]],
    pane_cwd: str | None = None,
) -> str:
    """Best-effort workspace cwd: workspace entry, agent cwd, pane, home.

    Real ``workspace.list`` entries do not carry a cwd, so the first
    snapshot pane's cwd is the useful fallback before home.
    """
    candidates = [workspace.get("cwd"), *(agent.get("cwd") for agent in agents)]
    for candidate in candidates:
        if candidate:
            return str(candidate)
    if pane_cwd:
        return pane_cwd
    return os.path.expanduser("~")


def settle_workspace(
    client: HerdrClient,
    store: ArchiveStore,
    workspace_id: str,
    *,
    force: bool = False,
) -> ArchiveRecord:
    """Settle (archive + close) one workspace.

    Raises:
        SettleRefusedError: when agents are working/blocked (without
            force), the workspace is unknown, or a group close cannot be
            done safely.
        SocketRequestError: when a herdr call fails.
    """
    agents = agents_in_workspace(client.agents(), workspace_id)
    busy = [agent for agent in agents if agent.get("agent_status") in GUARD_STATUSES]
    if busy and not force:
        names = ", ".join(str(agent.get("agent") or "agent") for agent in busy)
        raise SettleRefusedError(
            f"refusing to settle: {len(busy)} agent(s) still working/blocked "
            f"({names}); use settle-force to settle anyway",
        )

    # Snapshot while the workspace still exists: layout exports need live
    # tabs, so the record must be built before the close.
    record = snapshot_workspace(client, workspace_id)
    try:
        client.close_workspace(workspace_id)
    except SocketRequestError as exc:
        if exc.code != "workspace_group_close_required":
            raise
        return _settle_group(client, store, workspace_id, record, exc)

    store.save(record)
    client.notify(f"Settled {record.label}", _settle_summary(record))
    return record


def _settle_summary(record: ArchiveRecord) -> str:
    """Return a short human summary for the settle notification."""
    if record.resumable_count:
        return (
            f"{record.resumable_count} conversation(s) archived "
            f"across {record.tab_count} tab(s)"
        )
    return f"{record.tab_count} tab(s) archived"


def _settle_group(
    client: HerdrClient,
    store: ArchiveStore,
    workspace_id: str,
    primary_record: ArchiveRecord,
    group_error: SocketRequestError,
) -> ArchiveRecord:
    """Snapshot the remaining group members, then close the whole group.

    The requested workspace is already snapshotted (``primary_record``, taken
    before the failed close); the other members are snapshotted here, also
    before the group close, since layout exports need live tabs. Every member
    becomes its own archive record. Returns ``primary_record``.

    Raises:
        SettleRefusedError: when group members cannot be identified safely.
        SocketRequestError: when the group close fails.
    """
    members = group_members(client, workspace_id)
    if not members:
        msg = (
            "refusing to settle: the workspace belongs to a linked-worktree "
            "group whose members could not be identified"
        )
        raise SettleRefusedError(msg) from group_error
    records = [primary_record]
    records.extend(
        snapshot_workspace(client, member)
        for member in members
        if member != workspace_id
    )
    client.close_workspace(workspace_id, close_group=True)
    for member_record in records:
        store.save(member_record)
        client.notify(f"Settled {member_record.label}", _settle_summary(member_record))
    return primary_record


def group_members(client: HerdrClient, workspace_id: str) -> list[str]:
    """Identify the linked-worktree group members of a workspace.

    Uses group fields on workspace entries when herdr exposes them
    (``group_id``/``workspace_group_id``/``group``); returns ``[]`` when
    members cannot be determined.
    """
    workspaces = client.workspaces()
    target = _find_workspace(workspaces, workspace_id)
    if target is None:
        return []
    for field_name in ("group_id", "workspace_group_id", "group"):
        value = target.get(field_name)
        if value:
            return [
                str(entry["workspace_id"])
                for entry in workspaces
                if str(entry.get(field_name, "")) == str(value)
            ]
    return []
