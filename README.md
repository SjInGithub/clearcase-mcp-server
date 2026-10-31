# clearcase-mcp-server

MCP server exposing Rational ClearCase operations (via the bundled `clearcase_util`
`ClearCaseUtil`) as MCP tools. Wraps `cleartool` and the ClearCase helper
scripts.

**28 tools** — 15 read-only, 6 write, 7 destructive.

## Install / run

```sh
# stdio server
.venv/bin/python -m clearcase_mcp_server.server
# or, once installed:  clearcase-mcp-server
```

Register with Claude Code (see `.mcp.json`):

```json
{
  "mcpServers": {
    "clearcase-mcp-server": {
      "command": "/path/to/clearcase-mcp-server/.venv/bin/python",
      "args": ["-m", "clearcase_mcp_server.server"]
    }
  }
}
```

### Environment

| Var | Default | Purpose |
|---|---|---|
| `CLEARTOOL_PATH` | `cleartool` | Path to the cleartool executable (`/usr/atria/bin/cleartool` on a typical install). |
| `CLEARCASE_SCRIPTS_PATH` | `/path/to/clearcase/scripts` | helper scripts (`lsview`). |

## View context — read this first

`cleartool` is almost entirely **cwd-relative**: `lsco`, `lsact`, `lsstream`,
`rebase`, and `deliver` all act on "the current view". An MCP server is a
long-lived process with a **fixed cwd**, so it has no meaningful "current view"
to change.

Therefore most tools take an optional **`view_path`** argument and run the
command with `cwd` set there:

```
clearcase_list_checked_out_files(view_path="/view/<view_tag>/vobs/<vob>")
```

Omitting `view_path` runs in the server's own cwd, which is usually **not**
inside a view — expect empty output or a "not a ClearCase object" error. A
`view_path` that doesn't exist is rejected up front rather than producing
confusing empty output.

Call `clearcase_start_view` first if the dynamic view isn't started yet.

### `setview` is deliberately not exposed

`cleartool setview` **execs a new interactive shell**. Called from a stdio MCP
server it would block forever with no way to return. Use `view_path` instead.

### GUI flags are stripped

The SDK issues `diff -g` and `deliver -g`, both of which request a **graphical**
tool. From a headless server those hang or fail, so this server uses
`diff -serial_format` and a plain `deliver`.

## Tools

**Read-only** — `clearcase_list_views`, `clearcase_current_view`,
`clearcase_list_private_files`, `clearcase_list_activities`,
`clearcase_list_checked_out_files`, `clearcase_list_streams`,
`clearcase_diff_files`

**Read-only, version/label queries** — `clearcase_list_labels`,
`clearcase_count_labelled_versions`, `clearcase_find_changed_between_labels`,
`clearcase_version_rule`, `clearcase_list_history`, `clearcase_describe`,
`clearcase_diff_versions`, `clearcase_config_spec`

**Write** — `clearcase_start_view`, `clearcase_create_activity`,
`clearcase_set_activity`, `clearcase_checkout_file`, `clearcase_set_stream`,
`clearcase_rebase_stream`

**Destructive** (annotated `destructiveHint`, so hosts prompt) —
`clearcase_remove_view`, `clearcase_checkin_file`,
`clearcase_add_to_source_control` (mkelem + checkin of view-private files/dirs,
recursive), `clearcase_deliver_changes`,
`clearcase_complete_delivery`, `clearcase_cancel_delivery`,
`clearcase_complete_rebase`

## Comparing two baselines

An element unchanged between two baselines carries **both** labels on the same
version, so the delta is `lbtype(new) && !lbtype(old)` —
`clearcase_find_changed_between_labels` issues exactly that.

Two things make this harder than it looks:

- **Labels are per component, not per chip.** A project baseline such as
  `z_soc_label_0717` may exist as a label *type* in a VOB while being
  applied to nothing under your module; delivered IP often uses a different
  scheme entirely (`proj_label_*`, `PROJ_LABEL_*`). Check with
  `clearcase_count_labelled_versions` first — a count of 0 means the label is
  not applied there, and the diff tool would then return an empty result that
  looks exactly like "nothing changed". Fall back to `clearcase_version_rule`
  (the label the view's config spec selects for that component) or to
  `clearcase_list_history` with `since=<date>`. `clearcase_config_spec` shows
  why the labels differ per component.
- **Module directories are frequently symlinks.** cleartool reports on the link,
  not the element behind it, so every path-taking query tool resolves symlinks
  by default. Turn `resolve_symlinks` off only to inspect a link itself.

Also note `cleartool lshistory` truncates long label lists with an ellipsis, so
`clearcase_list_history` requests `%Nl` by default to get them all.

Recursive `find`/`lshistory` over a large subtree runs for minutes, well past
the 300 s default used for interactive subcommands; the query tools raise it to
`CLEARTOOL_QUERY_TIMEOUT_SEC` (1800 s).

## Output caveats

Tools return **raw cleartool stdout**, with stderr appended under a
`[cleartool stderr]` marker. cleartool reports many failures on stderr with an
empty stdout, so *empty output means "no result OR an error"* — treat it as
inconclusive.

`co`/`ci` are issued without a comment flag (matching the SDK), so cleartool may
prompt for a comment depending on site config. stdin is closed and a 300 s
timeout applies, so such a call fails fast instead of hanging.

## Tests

```sh
.venv/bin/python run_tests.py
```

55 tests. cleartool is stubbed — no started view required. Coverage includes
`view_path`→cwd forwarding, bad-`view_path` rejection, annotation correctness,
absence of `setview`, GUI-flag avoidance, the cwd-aware runner itself, and for
the query tools: the label-difference predicate, symlink resolution (on and
off), `%Nl` in history, version-extended paths surviving unmangled, and the
longer query timeout actually reaching `subprocess.run`.
