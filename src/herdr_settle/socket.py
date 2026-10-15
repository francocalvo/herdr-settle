r"""JSON-lines socket client for the herdr server.

Every request is one JSON object plus ``\\n``; every response is one JSON
object plus ``\\n``. Errors come back as ``{"error": {"code", "message"}}``
and raise :class:`SocketError`. The transport is injectable so tests can
fake the server (the httpx ``MockTransport`` pattern from linear-ws).
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_SOCKET_PATH = "~/.config/herdr/herdr.sock"

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


class SocketError(Exception):
    """Raised when a herdr socket call fails."""


class SocketRequestError(SocketError):
    """Raised when herdr replies with an error response."""

    def __init__(self, code: str, message: str) -> None:
        """Store the error code and message from the server."""
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def default_socket_path() -> Path:
    """Resolve the herdr socket path from the environment."""
    raw = os.environ.get("HERDR_SOCKET_PATH") or DEFAULT_SOCKET_PATH
    return Path(raw).expanduser()


def socket_transport() -> Transport:
    """Return a transport that talks to the live herdr server socket."""
    path = default_socket_path()

    def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
        return _call_socket(path, method, params)

    return call


def _call_socket(
    path: Path,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Send one request over the unix socket and return the result object."""
    if not path.exists():
        msg = f"herdr socket not found at {path}"
        raise SocketError(msg)
    request = {"id": "herdr-settle", "method": method, "params": params}
    payload = (json.dumps(request) + "\n").encode()
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(30)
            sock.connect(str(path))
            sock.sendall(payload)
            chunks: list[bytes] = []
            while b"\n" not in b"".join(chunks):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError as exc:
        msg = f"herdr socket call to {method} failed: {exc}"
        raise SocketError(msg) from exc
    raw = b"".join(chunks)
    if not raw.strip():
        msg = f"herdr closed the socket without replying to {method}"
        raise SocketError(msg)
    try:
        response = json.loads(raw.split(b"\n", 1)[0].decode())
    except (ValueError, UnicodeDecodeError) as exc:
        msg = f"herdr sent an unparseable reply to {method}: {raw[:120]!r}"
        raise SocketError(msg) from exc
    if "error" in response:
        error = response["error"] or {}
        raise SocketRequestError(
            str(error.get("code", "unknown")),
            str(error.get("message", "unknown error")),
        )
    result = response.get("result")
    if not isinstance(result, dict):
        msg = f"herdr reply to {method} did not include a result object"
        raise SocketError(msg)
    return result


class HerdrClient:
    """Thin wrapper over the herdr socket API used by settle/restore."""

    def __init__(self, transport: Transport) -> None:
        """Wrap a transport function ``(method, params) -> result``."""
        self._call_raw = transport

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a herdr socket method and return the result object."""
        return self._call_raw(method, params or {})

    # --- typed helpers ---------------------------------------------------

    def workspaces(self) -> list[dict[str, Any]]:
        """Return the ordered workspace list."""
        result = self.call("workspace.list")
        return list(result.get("workspaces") or [])

    def focused_workspace(self) -> dict[str, Any] | None:
        """Return the focused workspace entry, if any."""
        for workspace in self.workspaces():
            if workspace.get("focused"):
                return workspace
        return None

    def tabs(self, workspace_id: str) -> list[dict[str, Any]]:
        """Return the tabs of a workspace."""
        result = self.call("tab.list", {"workspace_id": workspace_id})
        return list(result.get("tabs") or [])

    def panes(self, workspace_id: str) -> list[dict[str, Any]]:
        """Return the panes of a workspace."""
        result = self.call("pane.list", {"workspace_id": workspace_id})
        return list(result.get("panes") or [])

    def export_tab_layout(self, tab_id: str) -> dict[str, Any]:
        """Return the layout export for one tab."""
        result = self.call("layout.export", {"tab_id": tab_id})
        return dict(result.get("layout") or {})

    def agents(self) -> list[dict[str, Any]]:
        """Return every detected agent."""
        result = self.call("agent.list")
        return list(result.get("agents") or [])

    def close_workspace(
        self,
        workspace_id: str,
        *,
        close_group: bool = False,
    ) -> None:
        """Close a workspace; raises :class:`SocketRequestError` on failure."""
        self.call(
            "workspace.close",
            {"workspace_id": workspace_id, "close_group": close_group},
        )

    def create_workspace(
        self,
        *,
        label: str,
        cwd: str,
        focus: bool = True,
    ) -> str:
        """Create a workspace and return its id."""
        result = self.call(
            "workspace.create",
            {"label": label, "cwd": cwd, "focus": focus},
        )
        workspace = result.get("workspace") or {}
        workspace_id = workspace.get("workspace_id")
        if not workspace_id:
            msg = "workspace.create did not return a workspace_id"
            raise SocketError(msg)
        return str(workspace_id)

    def apply_layout(
        self,
        root: dict[str, Any],
        workspace_id: str,
        *,
        tab_id: str | None = None,
        tab_label: str | None = None,
        focus: bool = False,
    ) -> dict[str, Any]:
        """Apply a layout tree into a workspace tab."""
        params: dict[str, Any] = {
            "root": root,
            "workspace_id": workspace_id,
            "focus": focus,
        }
        if tab_id:
            params["tab_id"] = tab_id
        if tab_label:
            params["tab_label"] = tab_label
        result = self.call("layout.apply", params)
        return dict(result.get("layout") or {})

    def close_tab(self, tab_id: str) -> None:
        """Close one tab."""
        self.call("tab.close", {"tab_id": tab_id})

    def focus_tab(self, tab_id: str) -> None:
        """Focus one tab."""
        self.call("tab.focus", {"tab_id": tab_id})

    def notify(self, title: str, body: str | None = None) -> None:
        """Show a herdr notification."""
        params: dict[str, Any] = {"title": title}
        if body:
            params["body"] = body
        self.call("notification.show", params)
