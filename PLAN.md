# herdr-settle

Settle (archive) herdr **spaces**: when you're done with a workspace, `settle`
it — it's snapshotted and closed, so the Spaces sidebar stays clean. Open the
archive browser to see settled spaces and restore one; restoring recreates the
workspace with its **agent conversations resumed in place** (`pi --session`,
`claude --resume`, `codex resume`, …).

Mental model: the inbox-triage "settle" idea from
[douglascorrea/herdr-agent-inbox](https://github.com/douglascorrea/herdr-agent-inbox)
and [calebcauthon/herdr-theos-settler](https://github.com/calebcauthon/herdr-theos-settler),
applied to **spaces** with true archiving. Both references keep settled work
in the sidebar (moved/prefixed) because herdr has no view-projection for the
Spaces list; hiding requires `workspace.close`, which is what makes the
resume-on-restore flow both necessary and possible.

## Non-goals

- No daemon, no event subscriptions, no transcript reading, no titles —
  settle/restore are explicit user actions only.
- No agent-level settling (whole space or nothing, M1).
- No pane screen-history preservation (herdr-native limitation; conversations
  resume, screens don't).
- No auto-resurface: a settled space has no running processes, so there is
  nothing to resurface from.

## Verified native surface (herdr 0.9.3, protocol 22)

Proven live during design — the exact calls the plugin makes:

- `workspace.create {label, cwd, focus}` → new workspace (with one default
  tab/pane that must be dealt with on restore).
- `workspace.list` / `workspace.get` / `workspace.close {workspace_id,
  close_group?}` — close may reject a primary workspace in a linked-worktree
  group with `workspace_group_close_required` unless `close_group: true`.
- `tab.list {workspace_id}` → tabs; `layout.export {tab_id}` →
  `{root: LayoutNode, focused_pane_id, zoomed}`.
- `layout.apply {root, workspace_id, tab_label, focus}` — recreates the tree;
  pane nodes accept `command: [argv]` and **run it as the pane's program**
  (verified: a pane with `command: ["sh","-c","…"]` ran it as the foreground
  process). Applies create a **new tab**; the default tab from
  `workspace.create` remains and must be closed (or reused via `tab_id`).
- `agent.list` → per agent: `pane_id`, `tab_id`, `workspace_id`, `agent`
  (e.g. "pi", "claude"), `agent_status` (idle/working/blocked/done/unknown),
  `agent_session {source, agent, kind, value}` (session ref, present when an
  integration reported it).
- `notification.show` for feedback; `HERDR_SOCKET_PATH` for the socket.

LayoutNode = pane `{type, cwd, label?, env?, command?, pane_id?}` | split
`{type, direction, ratio, first, second}`.

## Flow

### Settle (`prefix+shift+s`, action on focused workspace)

1. Resolve the focused workspace (via `pane.current` → `workspace_id`, or the
   action context).
2. **Safety guard:** `agent.list` filtered to the workspace — if any agent is
   `working` or `blocked`, refuse with a notification ("settle-scratch has 2
   working agents") unless `--force`.
3. Snapshot per tab: `layout.export(tab_id)`; enrich pane nodes with resume
   commands from the tab's agent session refs:
   `pi` → `["pi", "--session", value]`, `claude` → `["claude", "--resume",
   value]`, `codex` → `["codex", "resume", value]`, unknown agent without a
   mapping → plain shell in the saved cwd. Panes without agents stay plain
   shells in their saved cwd.
4. Write the archive record to `$HERDR_PLUGIN_STATE_DIR/archives/<uuid>.json`
   (atomic write): label, cwd, settled_at, tabs (label, layout root,
   focused_pane label), agent summary (count, which were resumable), source
   workspace id.
5. `workspace.close`. If it returns `workspace_group_close_required`:
   snapshot every workspace in the group (each its own archive record, taken
   before closing), then close with `close_group: true`.
6. `notification.show`: "Settled <label> — N conversations archived".

### Archive browser (`prefix+a`, plugin popup pane)

Textual popup (same entrypoint mechanics as herdr-linear-ws's wizard):

- Rows: label, tab count, resumable-conversation count, settled-at (relative
  time), workspace cwd (dim).
- `enter` → restore; `x` → confirm-free **discard** (record deleted, space
  stays closed; a second confirm step `y/n`); `r` → refresh; `q`/`esc` →
  close.
- Empty state: "No settled spaces" hint.

### Restore (un-settle)

1. `workspace.create {label, cwd, focus: true}`.
2. Per archived tab: `layout.apply {root, workspace_id, tab_label}` — first
   tab applies into the default tab via `tab_id` (fallback: close the default
   tab after applying; verify which works).
3. Focus the recorded focused tab/pane (best effort by label, else last).
4. Delete the archive record, notify "Restored <label>", close the popup.
5. Panes run their resume argv → herdr's agent detection + integrations
   re-report sessions natively.

## Stack & repo

Same shape as herdr-linear-ws: `~/personal/herdr-settle`, git, uv, Python
3.12+, `src/herdr_settle/` (socket client, settle, restore, archive store,
tui, cli), Textual for the popup, pytest/ruff/mypy gates. No config file, no
credentials — state only in `HERDR_PLUGIN_STATE_DIR`.

`herdr-plugin.toml`: id `francocalvo.settle`; actions `settle` (+ `--force`
via a separate `settle-force` action for keybinding symmetry) and the
`archive` pane entrypoint (popup placement). Manifest commands invoke the
repo's venv python directly (machine-local linked plugin, same as
linear-ws). Keybindings live in the user's `config.toml`:
`prefix+shift+s` settle, `prefix+a` archive browser.

## Testing

- Socket client with injectable transport (stdlib `socket` JSON-lines);
  MockTransport-style fake for tests.
- Settle: guard refusal, resume-command injection per agent type, group-close
  fallback, atomic record write.
- Restore: layout.apply calls per tab with the enriched roots, default-tab
  handling, record cleanup.
- TUI pilot test: list, restore, discard with confirm, empty state.

## Milestones

- **M1** (this build): settle / archive browser / restore, guard, group
  close, notifications, tests, linked + keybound in the live config.
- **M2** (later, if wanted): agent-level "finished" indicator on restore
  (start agents paused/notify), auto-settle on idle timeout, archive record
  pruning after N days, sidebar metadata token showing archive count.
