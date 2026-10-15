# herdr-settle

**Settle herdr spaces.** When you're done with a workspace, settle it: it's
snapshotted and closed, so your Spaces sidebar stays clean. Open the archive
browser any time to see settled spaces and restore one — the workspace comes
back with its layout, directories, and **agent conversations resumed in
place** (`pi --session`, `claude --resume`, `codex resume`, …).

Inspired by the inbox-triage "settle" idea from
[herdr-agent-inbox](https://github.com/douglascorrea/herdr-agent-inbox) and
[herdr-theos-settler](https://github.com/calebcauthon/herdr-theos-settler),
applied to **spaces** with true archiving: settled spaces don't just move to
the bottom of the list — they're closed and recorded, which is what makes a
clean view possible (herdr has no view-projection for the Spaces list) and
what makes conversation-resume on restore possible.

## How it works

- **Settle** the focused space: every tab's layout is exported
  (`layout.export`) and enriched with the resume command of each agent pane
  (from `agent.list` session refs), the record is written to the plugin state
  dir, and the workspace is closed (`workspace.close`). Linked-worktree
  groups are handled: every member is snapshotted before the group close.
- **Safety guard**: settling is refused while any agent in the space is
  `working` or `blocked` — settle-force overrides it.
- **Restore** from the archive browser: `workspace.create` + `layout.apply`
  per archived tab. Panes with a session ref relaunch their agent's resume
  command; everything else comes back as a fresh shell in its saved
  directory. herdr's agent detection re-registers the agents natively.
- No daemon, no events, no network — two actions and one popup, all through
  herdr's public plugin surface. State lives in
  `HERDR_PLUGIN_STATE_DIR/archives/` and survives restarts.

## Requirements

- herdr **0.9.3** or newer
- [uv](https://docs.astral.sh/uv/) (the plugin's build step creates its venv
  with it)
- macOS or Linux

## Install

```sh
herdr plugin install francocalvo/herdr-settle
```

Local development:

```sh
git clone https://github.com/francocalvo/herdr-settle.git
cd herdr-settle
uv sync --extra dev          # plugin link skips the build step
herdr plugin link .
```

## Keybindings

Add to `~/.config/herdr/config.toml`, then `herdr server reload-config`:

```toml
[[keys.command]]
key = "prefix+shift+s"
type = "plugin_action"
command = "francocalvo.settle.settle"
description = "settle current space (archive + close)"

[[keys.command]]
key = "prefix+a"
type = "shell"
command = "herdr plugin pane open --plugin francocalvo.settle --entrypoint archive"
description = "open settled-spaces archive browser"
```

## The archive browser

`prefix+a` opens the popup: one row per settled space with its tab count,
resumable-conversation count, and when it was settled.

| Key | Action |
| --- | --- |
| `enter` | Restore the selected space |
| `x` | Discard the record forever (space stays closed) — `y`/`n` confirm |
| `r` | Refresh |
| `q` / `esc` | Close |

There is also a CLI for scripting and debugging:

```sh
herdr plugin action invoke settle --plugin francocalvo.settle   # or settle-force
uv run python -m herdr_settle list                              # JSON, from the repo
uv run python -m herdr_settle restore <id> [--workspace-id <id>]
```

## Caveats

- Settling **kills running processes**. Agent conversations resume from
  their sessions; a running build or test does not. The guard makes this
  hard to do by accident.
- Only known agents resume (pi, claude, codex). Unknown agent panes and
  plain terminals return as fresh shells in their saved directories.
- No auto-resurface: a settled space has nothing running to resurface from.
  Restoring is always explicit.
- Archives live until restored or discarded — no pruning, no auto-settle.

## Development

```sh
uv sync --extra dev
uv run ruff check src tests
uv run mypy src tests
uv run pytest -q
```

Design notes: [DESIGN.md](DESIGN.md). License: [MIT](LICENSE).
