"""Archive record store for herdr-settle.

Records live in ``$HERDR_PLUGIN_STATE_DIR/archives/<id>.json`` (fallback
``~/.local/state/herdr/plugins/francocalvo.settle/archives``). Writes are
atomic (temp file + ``os.replace``) so a crash mid-settle never leaves a
truncated record.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLUGIN_ID = "francocalvo.settle"


class StoreError(Exception):
    """Raised when an archive record is missing or unreadable."""


@dataclass(frozen=True, slots=True)
class ArchivedTab:
    """One archived tab: its label, layout tree, and focused pane label."""

    label: str
    layout: dict[str, Any]
    focused_pane_label: str | None


@dataclass(frozen=True, slots=True)
class ArchiveRecord:
    """A settled (archived) workspace."""

    id: str
    label: str
    cwd: str
    settled_at: str
    tabs: list[ArchivedTab] = field(default_factory=list)
    source_workspace_id: str = ""
    agents_total: int = 0
    agents_resumable: int = 0

    @property
    def tab_count(self) -> int:
        """Number of archived tabs."""
        return len(self.tabs)

    @property
    def resumable_count(self) -> int:
        """Number of agent conversations that will be resumed on restore."""
        return self.agents_resumable


def state_dir() -> Path:
    """Resolve the plugin state directory."""
    raw = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".local/state/herdr/plugins" / PLUGIN_ID


def now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_record_id() -> str:
    """Return a fresh archive record id."""
    return uuid.uuid4().hex


class ArchiveStore:
    """Reads and writes archive records under the state directory."""

    def __init__(self, root: Path | None = None) -> None:
        """Point the store at ``root`` (defaults to the state dir)."""
        self._root = root or (state_dir() / "archives")

    @property
    def root(self) -> Path:
        """The directory holding archive records."""
        return self._root

    def list(self) -> list[ArchiveRecord]:
        """Return all records, newest settled first."""
        if not self._root.exists():
            return []
        records: list[ArchiveRecord] = []
        for path in sorted(self._root.glob("*.json")):
            try:
                records.append(_read_record(path))
            except (OSError, ValueError, KeyError):
                continue
        records.sort(key=lambda record: record.settled_at)
        records.reverse()
        return records

    def get(self, record_id: str) -> ArchiveRecord:
        """Return one record.

        Raises:
            StoreError: when the record does not exist or is unreadable.
        """
        path = self._path(record_id)
        if not path.exists():
            raise StoreError(f"no archived space with id {record_id}")
        try:
            return _read_record(path)
        except (OSError, ValueError, KeyError) as exc:
            msg = f"archive record {record_id} is unreadable: {exc}"
            raise StoreError(msg) from exc

    def save(self, record: ArchiveRecord) -> None:
        """Write a record atomically."""
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(record.id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_record_to_dict(record), indent=2) + "\n")
        os.replace(tmp, path)

    def delete(self, record_id: str) -> None:
        """Delete a record.

        Raises:
            StoreError: when the record does not exist.
        """
        path = self._path(record_id)
        if not path.exists():
            raise StoreError(f"no archived space with id {record_id}")
        path.unlink()

    def _path(self, record_id: str) -> Path:
        """Return the file path for a record id, rejecting path tricks."""
        if not record_id or "/" in record_id or record_id.startswith("."):
            msg = f"invalid archive record id: {record_id!r}"
            raise StoreError(msg)
        return self._root / f"{record_id}.json"


def _record_to_dict(record: ArchiveRecord) -> dict[str, Any]:
    """Serialize a record to a JSON-ready dict."""
    data = asdict(record)
    data["tabs"] = [asdict(tab) for tab in record.tabs]
    return data


def _read_record(path: Path) -> ArchiveRecord:
    """Parse one record file."""
    data = json.loads(path.read_text())
    tabs = [
        ArchivedTab(
            label=str(tab.get("label", "")),
            layout=dict(tab.get("layout") or {}),
            focused_pane_label=tab.get("focused_pane_label"),
        )
        for tab in data.get("tabs") or []
    ]
    return ArchiveRecord(
        id=str(data["id"]),
        label=str(data["label"]),
        cwd=str(data["cwd"]),
        settled_at=str(data["settled_at"]),
        tabs=tabs,
        source_workspace_id=str(data.get("source_workspace_id", "")),
        agents_total=int(data.get("agents_total", 0)),
        agents_resumable=int(data.get("agents_resumable", 0)),
    )
