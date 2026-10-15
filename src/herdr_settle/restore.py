"""The restore (un-settle) flow: recreate an archived workspace.

Steps: read the record, ``workspace.create``, apply each archived tab's
layout (the first into the default tab via ``tab_id``, later ones as new
tabs), focus the recorded tab, delete the record. Agent panes run their
resume argv directly, so conversations come back through herdr's native
agent detection.
"""

from __future__ import annotations

import contextlib
from typing import Any

from herdr_settle.socket import HerdrClient
from herdr_settle.store import ArchiveRecord, ArchiveStore, StoreError


class RestoreError(Exception):
    """Raised when a workspace cannot be restored from its record."""


def restore_workspace(
    client: HerdrClient,
    store: ArchiveStore,
    record_id: str,
) -> ArchiveRecord:
    """Restore an archived space and delete its record.

    Raises:
        StoreError: when the record is missing.
        RestoreError: when herdr cannot recreate the workspace.
    """
    record = store.get(record_id)
    workspace_id = client.create_workspace(label=record.label, cwd=record.cwd)

    try:
        default_tab_id = _default_tab_id(client, workspace_id)
        for index, tab in enumerate(record.tabs):
            if index == 0 and default_tab_id:
                # Into the default tab: target by tab_id alone — herdr rejects
                # a request carrying both tab_id and workspace_id.
                client.apply_layout(
                    tab.layout,
                    tab_id=default_tab_id,
                    tab_label=tab.label or None,
                )
                continue
            client.apply_layout(
                tab.layout,
                workspace_id=workspace_id,
                tab_label=tab.label or None,
            )
    except Exception:
        # Do not leave a half-built workspace behind; the record stays so a
        # later retry can start from a clean create.
        with contextlib.suppress(Exception):
            client.close_workspace(workspace_id)
        raise

    # Extra archived tabs were applied as NEW tabs; if the record has tabs
    # beyond the default one there is nothing left over. But if the record
    # itself was empty (no tabs), close the default tab to avoid an empty
    # leftover space pane.
    if not record.tabs and default_tab_id:
        client.close_tab(default_tab_id)

    _focus_recorded_tab(client, workspace_id, record)
    store.delete(record_id)
    client.notify(f"Restored {record.label}", _restore_summary(record))
    return record


def _default_tab_id(client: HerdrClient, workspace_id: str) -> str | None:
    """Return the first tab id of a fresh workspace, if any."""
    try:
        tabs = client.tabs(workspace_id)
    except Exception as exc:  # noqa: BLE001 - fall back to new-tab applies
        msg = f"could not list tabs of the restored workspace: {exc}"
        raise RestoreError(msg) from exc
    if not tabs:
        return None
    return str(tabs[0].get("tab_id", "")) or None


def _focus_recorded_tab(
    client: HerdrClient,
    workspace_id: str,
    record: ArchiveRecord,
) -> None:
    """Focus the tab holding a recorded focused pane, else the last tab.

    Best effort: pane ids change across restore, so matching goes by the
    recorded focused pane label; with no labels to match, the last applied
    tab is focused so the restored space opens somewhere sensible.
    """
    wanted = [tab.focused_pane_label for tab in record.tabs if tab.focused_pane_label]
    with contextlib.suppress(Exception):
        panes = client.panes(workspace_id)
        tabs = client.tabs(workspace_id)
        tab_ids = [str(tab["tab_id"]) for tab in tabs if tab.get("tab_id")]
        if not tab_ids:
            return
        target: str | None = None
        if wanted:
            pane_by_label = {
                str(pane["label"]): str(pane["tab_id"])
                for pane in panes
                if pane.get("label") and pane.get("tab_id")
            }
            for label in wanted:
                tab_id = pane_by_label.get(label)
                if tab_id in tab_ids:
                    target = tab_id
                    break
        client.focus_tab(target or tab_ids[-1])


def _restore_summary(record: ArchiveRecord) -> str:
    """Return a short human summary for the restore notification."""
    parts: list[str] = [f"{record.tab_count} tab(s)"]
    if record.resumable_count:
        parts.append(f"{record.resumable_count} conversation(s) resumed")
    return ", ".join(parts)


def discard(
    store: ArchiveStore,
    record_id: str,
) -> ArchiveRecord:
    """Drop an archive record without restoring the workspace.

    Raises:
        StoreError: when the record is missing.
    """
    record = store.get(record_id)
    store.delete(record_id)
    return record


def record_row(record: ArchiveRecord) -> dict[str, Any]:
    """Return the display fields for one archive record."""
    return {
        "id": record.id,
        "label": record.label,
        "tabs": record.tab_count,
        "conversations": record.resumable_count,
        "settled_at": record.settled_at,
        "cwd": record.cwd,
    }


__all__ = ["RestoreError", "discard", "record_row", "restore_workspace", "StoreError"]
