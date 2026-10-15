"""Unit tests for the herdr socket client wire behavior."""

from __future__ import annotations

import json
import shutil
import socket
import tempfile
import threading
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest

from herdr_settle.socket import (
    HerdrClient,
    SocketError,
    SocketRequestError,
    _call_socket,
)


@pytest.fixture()
def fake_server() -> Generator[tuple[Path, list[dict[str, Any]]], None, None]:
    """A one-shot JSON-lines socket server on a short-lived unix socket.

    Replies are queued in ``replies`` (each a full response object). The
    server serves one connection per reply, like herdr's per-request flow.
    The socket lives under ``/tmp`` because AF_UNIX paths are capped at
    ~104 chars and pytest's default tmp dirs exceed that on macOS.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="herdr-settle-test-", dir="/tmp"))
    sock_path = tmp_dir / "test.sock"
    replies: list[dict[str, Any]] = []
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(sock_path))
    server.listen(8)

    def serve() -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            conn.recv(65536)
            if replies:
                response = replies.pop(0)
            else:
                response = {
                    "id": "x",
                    "error": {"code": "unknown", "message": "no reply staged"},
                }
            conn.sendall((json.dumps(response) + "\n").encode())
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield sock_path, replies
    finally:
        server.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def transport_for(sock_path: Path) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """Build a transport bound to a specific socket path."""

    def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
        return _call_socket(sock_path, method, params)

    return call


def test_success_response_is_returned(
    fake_server: tuple[Path, list[dict[str, Any]]],
) -> None:
    """A result response is returned as the result object."""
    sock_path, replies = fake_server
    replies.append({"id": "x", "result": {"workspaces": [{"workspace_id": "w1"}]}})
    client = HerdrClient(transport_for(sock_path))
    assert client.workspaces() == [{"workspace_id": "w1"}]


def test_error_response_raises_request_error(
    fake_server: tuple[Path, list[dict[str, Any]]],
) -> None:
    """An error response raises SocketRequestError with code and message."""
    sock_path, replies = fake_server
    replies.append(
        {
            "id": "x",
            "error": {"code": "workspace_group_close_required", "message": "nope"},
        },
    )
    client = HerdrClient(transport_for(sock_path))
    with pytest.raises(SocketRequestError) as exc_info:
        client.workspaces()
    assert exc_info.value.code == "workspace_group_close_required"
    assert exc_info.value.message == "nope"


def test_missing_socket_file_raises(tmp_path: Path) -> None:
    """A missing socket file raises SocketError."""
    client = HerdrClient(transport_for(tmp_path / "missing.sock"))
    with pytest.raises(SocketError, match="not found"):
        client.workspaces()


def test_focused_workspace_picks_focused_entry() -> None:
    """The focused helper returns the workspace marked focused."""
    workspaces = [
        {"workspace_id": "w1", "label": "one", "focused": False},
        {"workspace_id": "w2", "label": "two", "focused": True},
    ]

    def transport(method: str, _params: dict[str, Any]) -> dict[str, Any]:
        assert method == "workspace.list"
        return {"workspaces": workspaces}

    client = HerdrClient(transport)
    focused = client.focused_workspace()
    assert focused is not None
    assert focused["label"] == "two"


def test_focused_workspace_none_when_unmarked() -> None:
    """No focused workspace returns None."""

    def transport(_method: str, _params: dict[str, Any]) -> dict[str, Any]:
        return {"workspaces": [{"workspace_id": "w1", "focused": False}]}

    client = HerdrClient(transport)
    assert client.focused_workspace() is None
