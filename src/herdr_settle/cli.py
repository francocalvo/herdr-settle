"""CLI entrypoint for herdr-settle.

Exit codes: 0 ok, 2 user error (settle refused, unknown record, headless
archive), 1 internal error (socket or herdr failure).
"""

from __future__ import annotations

import contextlib
import json
from typing import Annotated

import typer

from herdr_settle.restore import RestoreError, restore_workspace
from herdr_settle.settle import SettleRefusedError, settle_workspace
from herdr_settle.socket import (
    HerdrClient,
    SocketError,
    socket_transport,
)
from herdr_settle.store import ArchiveStore, StoreError

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _print_error(message: str) -> None:
    """Print a user-facing error line."""
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)


def _print_internal_error(message: str) -> None:
    """Print an internal error line."""
    typer.secho(f"internal error: {message}", fg=typer.colors.RED, err=True)


def _client() -> HerdrClient:
    """Build a herdr client over the live socket transport."""
    return HerdrClient(socket_transport())


def _store() -> ArchiveStore:
    """Build the archive store."""
    return ArchiveStore()


@app.command("settle", help="Settle (archive + close) a space")
def settle(
    force: Annotated[
        bool,
        typer.Option("--force", help="Settle even with working agents."),
    ] = False,
    workspace_id: Annotated[
        str | None,
        typer.Option("--workspace-id", help="Workspace to settle (default: focused)."),
    ] = None,
) -> None:
    """Settle the focused (or given) workspace."""
    client = _client()
    target = workspace_id
    if not target:
        focused = client.focused_workspace()
        if focused is None:
            _print_error("no focused workspace to settle")
            raise typer.Exit(2)
        target = str(focused["workspace_id"])
    try:
        record = settle_workspace(client, _store(), target, force=force)
    except SettleRefusedError as exc:
        with contextlib.suppress(SocketError):
            client.notify("Settle refused", exc.message)
        _print_error(exc.message)
        raise typer.Exit(2) from exc
    except SocketError as exc:
        _print_internal_error(str(exc))
        raise typer.Exit(1) from exc
    typer.echo(
        f"settled {record.label}: {record.resumable_count} conversation(s) archived",
    )


@app.command("restore", help="Restore an archived space")
def restore(
    record_id: Annotated[str, typer.Argument(help="Archive record id (see `list`).")],
) -> None:
    """Restore an archived workspace with resumed conversations."""
    try:
        record = restore_workspace(_client(), _store(), record_id)
    except StoreError as exc:
        _print_error(str(exc))
        raise typer.Exit(2) from exc
    except (RestoreError, SocketError) as exc:
        _print_internal_error(str(exc))
        raise typer.Exit(1) from exc
    typer.echo(f"restored {record.label}")


@app.command("list", help="List archived spaces as JSON")
def list_archives() -> None:
    """Print the archive records as JSON (debugging/scripting)."""
    records = _store().list()
    typer.echo(
        json.dumps(
            [
                {
                    "id": record.id,
                    "label": record.label,
                    "cwd": record.cwd,
                    "settled_at": record.settled_at,
                    "tabs": record.tab_count,
                    "conversations": record.resumable_count,
                }
                for record in records
            ],
            indent=2,
        ),
    )


@app.command("archive", help="Open the settled-spaces browser")
def archive() -> None:
    """Open the archive browser popup."""
    from herdr_settle.tui import run_browser

    if run_browser(_client(), _store()) is None:
        raise typer.Exit(2)


def main() -> None:
    """CLI entrypoint."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()

