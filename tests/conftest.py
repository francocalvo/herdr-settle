"""Shared fixtures: an in-memory herdr server fake and a tmp archive store."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from herdr_settle.socket import SocketRequestError
from herdr_settle.store import ArchiveStore


class FakeHerdr:
    """In-memory herdr server implementing the surface settle/restore uses.

    Instances are callable as a ``Transport`` (``(method, params) -> result``).
    Workspace/group-close semantics mirror herdr: closing a primary
    workspace while linked-worktree (``group_id``) members are open requires
    ``close_group: true``.
    """

    def __init__(self) -> None:
        """Start with no workspaces, tabs, panes, or agents."""
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.workspace_order: list[str] = []
        self.tabs: dict[str, list[dict[str, Any]]] = {}
        self.panes: dict[str, dict[str, Any]] = {}
        self.agents: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.layout_applies: list[dict[str, Any]] = []
        self.closed_tabs: list[str] = []
        self.focused_tabs: list[str] = []
        self._counter = 0

    # --- test setup helpers ----------------------------------------------

    def add_workspace(
        self,
        label: str,
        cwd: str,
        *,
        group_id: str | None = None,
        panes: list[dict[str, Any]] | None = None,
    ) -> str:
        """Create a workspace with one default tab; return its id."""
        self._counter += 1
        workspace_id = f"ws{self._counter}"
        self.workspaces[workspace_id] = {
            "workspace_id": workspace_id,
            "label": label,
            "cwd": cwd,
            "focused": False,
            "group_id": group_id,
        }
        self.workspace_order.append(workspace_id)
        tab_id = f"{workspace_id}:t1"
        self.tabs[workspace_id] = [
            {"tab_id": tab_id, "label": "1", "workspace_id": workspace_id},
        ]
        if panes:
            for pane in panes:
                self._add_pane(workspace_id, tab_id, pane)
        else:
            self._add_pane(workspace_id, tab_id, {"cwd": cwd, "label": None})
        return workspace_id

    def _add_pane(
        self,
        workspace_id: str,
        tab_id: str,
        pane: dict[str, Any],
    ) -> str:
        """Register one pane under a tab and return its id."""
        self._counter += 1
        pane_id = f"{workspace_id}:p{self._counter}"
        self.panes[pane_id] = {
            "pane_id": pane_id,
            "workspace_id": workspace_id,
            "tab_id": tab_id,
            "cwd": pane.get("cwd"),
            "label": pane.get("label"),
            "command": pane.get("command"),
        }
        return pane_id

    def add_agent(
        self,
        workspace_id: str,
        *,
        agent: str,
        status: str = "idle",
        session_value: str | None = None,
        pane_index: int = 0,
    ) -> str:
        """Attach an agent to the ``pane_index``-th pane of a workspace."""
        tab_id = self.tabs[workspace_id][0]["tab_id"]
        pane_ids = [
            pane_id
            for pane_id, pane in self.panes.items()
            if pane["workspace_id"] == workspace_id and pane["tab_id"] == tab_id
        ]
        pane_ids.sort()
        pane_id = pane_ids[pane_index]
        self.agents.append(
            {
                "pane_id": pane_id,
                "tab_id": tab_id,
                "workspace_id": workspace_id,
                "agent": agent,
                "agent_status": status,
                "agent_session": (
                    {
                        "source": "integration",
                        "agent": agent,
                        "kind": "id",
                        "value": session_value,
                    }
                    if session_value
                    else None
                ),
                "cwd": self.panes[pane_id]["cwd"],
            },
        )
        return pane_id

    # --- transport --------------------------------------------------------

    def __call__(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one herdr socket request."""
        handler: Callable[[dict[str, Any]], dict[str, Any]] | None = getattr(
            self,
            f"_on_{method.replace('.', '_')}",
            None,
        )
        if handler is None:
            raise AssertionError(f"unexpected method: {method}")
        return handler(params)

    def _on_workspace_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Return the ordered workspace list."""
        ordered = [self.workspaces[wid] for wid in self.workspace_order]
        return {"workspaces": ordered}

    def _on_workspace_create(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a workspace with one default tab and pane."""
        workspace_id = self.add_workspace(
            params.get("label") or "",
            params.get("cwd") or "/",
        )
        if params.get("focus"):
            for wid in self.workspace_order:
                self.workspaces[wid]["focused"] = wid == workspace_id
        workspace = dict(self.workspaces[workspace_id])
        workspace["active_tab_id"] = f"{workspace_id}:t1"
        return {"type": "workspace_created", "workspace": workspace}

    def _on_workspace_close(self, params: dict[str, Any]) -> dict[str, Any]:
        """Close a workspace; require close_group for linked-worktree groups."""
        workspace_id = params["workspace_id"]
        if workspace_id not in self.workspaces:
            raise SocketRequestError("not_found", "workspace not found")
        group_id = self.workspaces[workspace_id].get("group_id")
        members = (
            [
                wid
                for wid in self.workspace_order
                if self.workspaces[wid].get("group_id") == group_id
            ]
            if group_id
            else []
        )
        if len(members) > 1 and not params.get("close_group"):
            raise SocketRequestError(
                "workspace_group_close_required",
                "close the whole group or pass close_group: true",
            )
        targets = members if params.get("close_group") else [workspace_id]
        for wid in targets:
            self.workspace_order.remove(wid)
            del self.workspaces[wid]
            self.tabs.pop(wid, None)
            pane_ids = [
                pane_id
                for pane_id, pane in self.panes.items()
                if pane["workspace_id"] == wid
            ]
            for pane_id in pane_ids:
                del self.panes[pane_id]
        return {"type": "ok"}

    def _on_tab_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the tabs of one workspace."""
        return {"tabs": list(self.tabs.get(params["workspace_id"], []))}

    def _on_pane_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the panes of one workspace."""
        return {
            "panes": [
                dict(pane)
                for pane in self.panes.values()
                if pane["workspace_id"] == params["workspace_id"]
            ],
        }

    def _on_tab_close(self, params: dict[str, Any]) -> dict[str, Any]:
        """Close one tab."""
        tab_id = params["tab_id"]
        workspace_id = tab_id.split(":")[0]
        self.tabs[workspace_id] = [
            tab for tab in self.tabs.get(workspace_id, []) if tab["tab_id"] != tab_id
        ]
        self.closed_tabs.append(tab_id)
        return {"type": "ok"}

    def _on_tab_focus(self, params: dict[str, Any]) -> dict[str, Any]:
        """Focus one tab."""
        self.focused_tabs.append(params["tab_id"])
        return {"type": "ok"}

    def _tab_panes(self, tab_id: str) -> list[dict[str, Any]]:
        """Return panes of one tab in creation order."""
        return sorted(
            (pane for pane in self.panes.values() if pane["tab_id"] == tab_id),
            key=lambda pane: pane["pane_id"],
        )

    def _on_layout_export(self, params: dict[str, Any]) -> dict[str, Any]:
        """Export a tab's layout tree from its panes."""
        panes = self._tab_panes(params["tab_id"])
        nodes = [
            {
                "type": "pane",
                "pane_id": pane["pane_id"],
                "cwd": pane["cwd"],
                **({"label": pane["label"]} if pane["label"] else {}),
                **({"command": pane["command"]} if pane.get("command") else {}),
            }
            for pane in panes
        ]
        if len(nodes) == 1:
            root: dict[str, Any] = nodes[0]
        else:
            root = {
                "type": "split",
                "direction": "right",
                "ratio": 0.5,
                "first": nodes[0],
                "second": nodes[1],
            }
        return {
            "layout": {
                "tab_id": params["tab_id"],
                "focused_pane_id": nodes[0]["pane_id"],
                "root": root,
            },
        }

    def _on_layout_apply(self, params: dict[str, Any]) -> dict[str, Any]:
        """Recreate panes from a layout tree into a workspace tab."""
        self.layout_applies.append(dict(params))
        if params.get("tab_id"):
            workspace_id = str(params["tab_id"]).split(":")[0]
            tab_id = str(params["tab_id"])
        else:
            workspace_id = str(params["workspace_id"])
            self._counter += 1
            tab_id = f"{workspace_id}:t{self._counter}"
            self.tabs.setdefault(workspace_id, []).append(
                {
                    "tab_id": tab_id,
                    "label": params.get("tab_label") or "",
                    "workspace_id": workspace_id,
                },
            )
        counter = {"n": 0}

        def materialize(node: dict[str, Any]) -> dict[str, Any]:
            if node.get("type") == "split":
                return {
                    "type": "split",
                    "direction": node["direction"],
                    "ratio": node["ratio"],
                    "first": materialize(node["first"]),
                    "second": materialize(node["second"]),
                }
            counter["n"] += 1
            pane_id = self._add_pane(
                workspace_id,
                tab_id,
                {
                    "cwd": node.get("cwd"),
                    "label": node.get("label"),
                    "command": node.get("command"),
                },
            )
            return {**node, "pane_id": pane_id}

        root = materialize(params["root"])
        return {"layout": {"tab_id": tab_id, "root": root}}

    def _on_agent_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Return every agent."""
        return {"agents": self.agents}

    def _on_notification_show(self, params: dict[str, Any]) -> dict[str, Any]:
        """Record a notification."""
        self.notifications.append(params)
        return {"type": "ok"}


@pytest.fixture()
def fake_herdr() -> FakeHerdr:
    """A fresh in-memory herdr server."""
    return FakeHerdr()


@pytest.fixture()
def store(tmp_path: Any) -> ArchiveStore:
    """An archive store rooted in a tmp directory."""
    return ArchiveStore(root=tmp_path / "archives")
