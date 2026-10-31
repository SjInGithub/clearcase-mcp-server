"""MCP server exposing ClearCaseUtil (Rational ClearCase) as MCP tools.

Wraps clearcase_mcp_server.clearcase_util.ClearCaseUtil,
which shells out to `cleartool` and the ClearCase helper scripts.

Two deliberate departures from a 1:1 SDK mirror, both forced by the fact that an
MCP server is a long-lived process with a fixed cwd:

1. `setview` is NOT exposed. `cleartool setview` execs a new interactive shell;
   calling it from a stdio server would block forever with no way to return.
2. View-dependent commands take an optional `view_path` argument and run with
   cwd set there, instead of relying on ambient process state. See
   ClearCaseUtilExt.
"""

import os
import shlex
from typing import Optional, Union

from clearcase_mcp_server.clearcase_util import ClearCaseUtil
from fastmcp import FastMCP

from clearcase_mcp_server.clearcase_util_ext import ClearCaseUtilExt




class _ClearCaseUtilWithExt(ClearCaseUtilExt, ClearCaseUtil):
    """ClearCaseUtil plus cwd-aware command execution."""


CLEARTOOL_PATH = os.environ.get("CLEARTOOL_PATH", "cleartool")
CLEARCASE_SCRIPTS_PATH = os.environ.get("CLEARCASE_SCRIPTS_PATH", "/path/to/clearcase/scripts")

_util = _ClearCaseUtilWithExt(
    cleartool_path=CLEARTOOL_PATH,
    clearcase_scripts_path=CLEARCASE_SCRIPTS_PATH,
)


def _check_view_path(view_path: Optional[str]) -> None:
    """Reject a bad view_path up front.

    ClearCaseUtil shells out via os.popen and returns whatever the shell wrote
    to stdout, so a nonexistent directory surfaces as empty/garbled output
    rather than an error. Fail loudly instead.
    """
    if view_path is None:
        return
    if not os.path.isdir(view_path):
        raise ValueError(
            f"view_path is not an existing directory: {view_path} "
            "(a dynamic view usually looks like /view/<view_tag>/vobs/<vob>)"
        )


def _resolve(path: str, resolve_symlinks: bool = True) -> str:
    """Optionally resolve symlinks before handing a path to cleartool.

    Project trees often expose modules as symlinks (e.g. a chip's
    top/sub/<module> pointing at the component's real VOB location). cleartool
    reports on the *link* in that case, not the versioned element behind it, so
    a query against the visible path silently describes the wrong object. That
    is the single most common mistake with these tools, hence the default.
    """
    return os.path.realpath(path) if resolve_symlinks else path


def _resolved_scope(paths: Union[str, list], resolve_symlinks: bool) -> list:
    """Split a one-or-many path argument and resolve each entry."""
    if isinstance(paths, str):
        items = [p for p in paths.split(",") if p.strip()]
    else:
        items = list(paths)
    resolved = [_resolve(p.strip(), resolve_symlinks) for p in items if p.strip()]
    if not resolved:
        raise ValueError("at least one path is required")
    return resolved


mcp = FastMCP(
    name="clearcase-mcp-server",
    instructions=(
        "Query and modify Rational ClearCase views, activities, streams, and "
        "checkouts via cleartool and the ClearCase helper scripts.\n\n"
        "VIEW CONTEXT: most cleartool commands are relative to a view and a "
        "working directory. This server is a long-lived process with a fixed "
        "cwd, so it does NOT hold a 'current view'. Pass `view_path` (e.g. "
        "'/view/<view_tag>/vobs/<vob>') to any tool that accepts it to run the "
        "command inside that view. Tools that omit it run in the server's own "
        "cwd, which is usually NOT inside a view -- expect empty output or a "
        "'not a ClearCase object' error in that case.\n\n"
        "`cleartool setview` is intentionally NOT exposed: it execs an "
        "interactive subshell that would hang this server. Use `view_path` "
        "instead. Call clearcase_start_view first if the view is not yet "
        "started.\n\n"
        "OUTPUT: tools return raw cleartool stdout. cleartool reports many "
        "failures on stderr with an empty stdout, so empty output means 'no "
        "result OR an error' -- treat it as inconclusive, not as success.\n\n"
        "BASELINE / LABEL QUERIES: labels are applied per VOB component, so a "
        "chip- or project-wide baseline name often is NOT applied in a given "
        "module's VOB even when the label type exists there. Before comparing "
        "two baselines, confirm both with clearcase_count_labelled_versions; a "
        "count of 0 means 'not applied here', and "
        "clearcase_find_changed_between_labels would then return nothing, which "
        "is indistinguishable from 'no changes'. Fall back to "
        "clearcase_version_rule (what the view actually selects) or "
        "clearcase_list_history with `since`.\n\n"
        "SYMLINKS: project trees commonly expose modules as symlinks. cleartool "
        "reports on the link, not the element behind it, so path-taking tools "
        "resolve symlinks by default (`resolve_symlinks`). Leave it on unless "
        "you mean to inspect the link itself."
    ),
)

READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}
WRITE = {"readOnlyHint": False, "openWorldHint": False}
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}


# ---------------------------------------------------------------------------
# View inspection
# ---------------------------------------------------------------------------


@mcp.tool(annotations={**READ_ONLY, "title": "List ClearCase views"})
def clearcase_list_views(user: Optional[str] = None, pattern: Optional[str] = None) -> str:
    """List ClearCase views via the `lsview` script.

    Args:
        user: Filter to views owned by this user ID. Omit for all users.
        pattern: Filter to view tags matching this pattern. Omit for no filter.

    Returns:
        Raw `lsview` stdout: one view per line.
    """
    return _util.list_views(user=user, pattern=pattern)


@mcp.tool(annotations={**READ_ONLY, "title": "Show current ClearCase view"})
def clearcase_current_view(view_path: Optional[str] = None) -> str:
    """Show the current view (`cleartool pwv`) for a given working directory.

    Because this server has no ambient view, the answer depends entirely on
    `view_path`. Without it you are asking about the server's own cwd, which is
    typically not inside a view.

    Args:
        view_path: Directory inside the view to interrogate, e.g.
            "/view/<view_tag>/vobs/<vob>". Omit to use the server's cwd.

    Returns:
        Raw `cleartool pwv` stdout.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command("pwv", cwd=view_path)


@mcp.tool(annotations={**READ_ONLY, "title": "List private files in a ClearCase view"})
def clearcase_list_private_files(view_tag: str) -> str:
    """List view-private files (files not under source control) in a view.

    Args:
        view_tag: ClearCase view tag (not a path).

    Returns:
        Raw `cleartool lsprivate -tag <view_tag>` stdout: one path per line.
    """
    return _util.list_private_files(view_tag)


@mcp.tool(annotations={**WRITE, "title": "Start a ClearCase view"})
def clearcase_start_view(view_tag: str) -> str:
    """Start a dynamic view so its /view/<view_tag> path becomes accessible.

    Not read-only: this activates a view server process. Safe and idempotent in
    practice, but it does change machine state. Run this before using a
    `view_path` under /view/<view_tag> if the view is not already started.

    Args:
        view_tag: ClearCase view tag.

    Returns:
        Raw `cleartool startview` stdout (usually empty on success).
    """
    return _util.start_view(view_tag)


@mcp.tool(annotations={**DESTRUCTIVE, "title": "Remove a ClearCase view"})
def clearcase_remove_view(view_tag: str) -> str:
    """PERMANENTLY delete a ClearCase view and all view-private files in it.

    DESTRUCTIVE AND IRREVERSIBLE. Any file in the view that was never checked in
    is lost. Confirm the view tag with clearcase_list_views, and the private
    files at risk with clearcase_list_private_files, before calling this.

    Args:
        view_tag: ClearCase view tag to remove.

    Returns:
        Raw `cleartool rmview -tag <view_tag>` stdout.
    """
    return _util.remove_view(view_tag)


# ---------------------------------------------------------------------------
# Activities (UCM)
# ---------------------------------------------------------------------------


@mcp.tool(annotations={**READ_ONLY, "title": "List ClearCase activities"})
def clearcase_list_activities(
    activity_id: str = "", view_path: Optional[str] = None
) -> str:
    """List UCM activities in the current stream.

    Args:
        activity_id: A specific activity ID to show. Omit (default "") to list
            every activity in the stream.
        view_path: Directory inside the view to run in. Activities are
            stream-scoped, so this is normally required to get real output.

    Returns:
        Raw `cleartool lsact` stdout.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command(f"lsact {activity_id}".strip(), cwd=view_path)


@mcp.tool(annotations={**WRITE, "title": "Create a ClearCase activity"})
def clearcase_create_activity(activity_name: str, view_path: Optional[str] = None) -> str:
    """Create a new UCM activity in the current stream (`cleartool mkact`).

    Args:
        activity_name: Name for the new activity.
        view_path: Directory inside the view to run in. Normally required.

    Returns:
        Raw `cleartool mkact` stdout.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command(f"mkact {activity_name}", cwd=view_path)


@mcp.tool(annotations={**WRITE, "title": "Set the current ClearCase activity"})
def clearcase_set_activity(activity_name: str, view_path: Optional[str] = None) -> str:
    """Set the current UCM activity (`cleartool setact`).

    The setting applies to the view identified by `view_path`, not to this
    server process, and it persists in the view for later calls.

    Args:
        activity_name: Activity to make current.
        view_path: Directory inside the view to run in. Normally required.

    Returns:
        Raw `cleartool setact` stdout.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command(f"setact {activity_name}", cwd=view_path)


# ---------------------------------------------------------------------------
# Checkout / checkin
# ---------------------------------------------------------------------------


@mcp.tool(annotations={**READ_ONLY, "title": "List checked-out ClearCase files"})
def clearcase_list_checked_out_files(view_path: Optional[str] = None) -> str:
    """List files checked out in the current view across all VOBs.

    Args:
        view_path: Directory inside the view to run in. Normally required --
            without it this reports on the server's cwd and is usually empty.

    Returns:
        Raw `cleartool lsco -avobs -cview -s` stdout: one path per line.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command("lsco -avobs -cview -s", cwd=view_path)


@mcp.tool(annotations={**WRITE, "title": "Check out a ClearCase file"})
def clearcase_checkout_file(filename: str, view_path: Optional[str] = None) -> str:
    """Check out a file for editing (`cleartool co`).

    Note the underlying SDK issues a bare `co` with no -nc/-c flag, so
    cleartool may block prompting for a comment depending on site config. If
    this call appears to hang, that is the cause.

    Args:
        filename: File to check out. Relative paths resolve against
            `view_path`.
        view_path: Directory inside the view to run in.

    Returns:
        Raw `cleartool co` stdout.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command(f"co {filename}", cwd=view_path)


@mcp.tool(annotations={**DESTRUCTIVE, "title": "Check in a ClearCase file"})
def clearcase_checkin_file(filename: str, view_path: Optional[str] = None) -> str:
    """Check in a file, publishing your changes to the VOB (`cleartool ci`).

    Marked destructive because it makes a change visible to every other user of
    the branch and creates a permanent version. Review with
    clearcase_diff_files first.

    As with checkout, the SDK passes no comment flag, so cleartool may prompt.

    Args:
        filename: File to check in. Relative paths resolve against `view_path`.
        view_path: Directory inside the view to run in.

    Returns:
        Raw `cleartool ci` stdout.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command(f"ci {filename}", cwd=view_path)


def _comment_flag(comment: Optional[str]) -> str:
    return f"-c {shlex.quote(comment)}" if comment else "-nc"


def _is_element(path: str, view_path: Optional[str]) -> bool:
    # `ls -d` prints `<path>@@/<branch>/<n>` for elements and the bare path for
    # view-private objects.
    return "@@" in _util.run_cleartool_command(f"ls -d {shlex.quote(path)}", cwd=view_path)


def _is_checked_out(path: str, view_path: Optional[str]) -> bool:
    return "checkedout" in _util.run_cleartool_command(f"ls -d {shlex.quote(path)}", cwd=view_path)


@mcp.tool(annotations={**DESTRUCTIVE, "title": "Add view-private files/dirs to source control"})
def clearcase_add_to_source_control(
    paths: Union[str, list],
    view_path: str,
    comment: Optional[str] = None,
    checkin: bool = True,
) -> str:
    """Put view-private files and directories under version control.

    Runs `cleartool mkelem` for each path, checking out the parent directory
    first as ClearCase requires. Directories are added recursively with every
    file and sub-directory they contain: the directory is moved aside, created
    as a directory element (`-eltype directory`), and its contents moved back
    and added one by one. With `checkin` the new elements and the modified
    parent directories are checked in too (deepest first), which publishes
    them to the VOB, hence the destructive annotation.

    Paths that are already elements are skipped. Nothing outside `paths` is
    touched, so exclude things like `.claude` by simply not listing them.

    Args:
        paths: View-private paths to add (list, or comma-separated string).
            Relative paths resolve against `view_path`.
        view_path: Directory inside the view to run in. Required.
        comment: Comment for the new elements and directory versions. Omit for
            `-nc`.
        checkin: Check everything in afterwards. If false, elements and parent
            directories are left checked out.

    Returns:
        A line per action taken plus a failure summary.
    """
    _check_view_path(view_path)
    items = _resolved_scope(paths, resolve_symlinks=False)
    cflag = _comment_flag(comment)
    log: list = []
    failed: list = []
    checked_out: list = []  # directories to check in, in the order they were opened

    def run(cmd: str) -> str:
        return _util.run_cleartool_command(cmd, cwd=view_path)

    def absolute(p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(view_path, p)

    def ok(out: str) -> bool:
        return "cleartool: Error" not in out and not out.startswith("ERROR")

    def ensure_checked_out(directory: str) -> bool:
        if _is_checked_out(directory, view_path):
            return True
        out = run(f"co {cflag} {shlex.quote(directory)}")
        if not ok(out):
            failed.append(f"co {directory}: {out}")
            return False
        log.append(f"checked out {directory}")
        checked_out.append(directory)
        return True

    def add(path: str) -> None:
        path = path.rstrip("/")
        full = absolute(path)
        if _is_element(path, view_path):
            log.append(f"skip (already an element) {path}")
            return
        parent = os.path.dirname(path) or "."
        if parent not in checked_out and not ensure_checked_out(parent):
            return
        q = shlex.quote(path)
        if os.path.isdir(full) and not os.path.islink(full):
            aside = absolute(f"{path}.__mkelem_tmp")
            os.rename(full, aside)
            out = run(f"mkelem {cflag} -eltype directory {q}")
            if not ok(out):
                os.rename(aside, full)
                failed.append(f"mkelem {path}: {out}")
                return
            log.append(f"added directory {path}")
            checked_out.append(path)
            for name in sorted(os.listdir(aside)):
                os.rename(os.path.join(aside, name), os.path.join(full, name))
                add(os.path.join(path, name))
            try:
                os.rmdir(aside)
            except OSError:
                failed.append(f"could not remove temporary directory {aside}")
        else:
            out = run(f"mkelem {cflag} {'-ci ' if checkin else ''}{q}")
            if ok(out):
                log.append(f"added file {path}")
            else:
                failed.append(f"mkelem {path}: {out}")

    for item in items:
        add(item)

    if checkin:
        for directory in reversed(checked_out):
            out = run(f"ci {cflag} {shlex.quote(directory)}")
            if ok(out):
                log.append(f"checked in {directory}")
            else:
                failed.append(f"ci {directory}: {out}")

    log.append(f"FAILED: {len(failed)}")
    log.extend(failed)
    return "\n".join(log)


@mcp.tool(annotations={**READ_ONLY, "title": "Diff a ClearCase file against its predecessor"})
def clearcase_diff_files(file_path: str, view_path: Optional[str] = None) -> str:
    """Diff a file against its immediate predecessor version.

    Args:
        file_path: File to compare. Relative paths resolve against
            `view_path`.
        view_path: Directory inside the view to run in.

    Returns:
        Raw `cleartool diff` stdout.
    """
    _check_view_path(view_path)
    # -g requests the graphical differ in the SDK's version of this call, which
    # would try to open a GUI window from a headless server process; use the
    # terminal-oriented -serial_format instead.
    return _util.run_cleartool_command(
        f"diff -serial_format -predecessor {file_path}", cwd=view_path
    )


# ---------------------------------------------------------------------------
# Version / label queries (read-only)
#
# These exist so callers can compare two labelled baselines without shelling
# out to cleartool themselves. Two facts drive their design:
#   * a label is applied per VOB/component, so "is this baseline here?" must be
#     asked per module rather than assumed chip-wide;
#   * `find -version` with only a label predicate enumerates every version of
#     every element, so a *difference* query needs both halves of the predicate.
# ---------------------------------------------------------------------------


@mcp.tool(annotations={**READ_ONLY, "title": "List label types in a VOB"})
def clearcase_list_labels(
    path: str,
    pattern: Optional[str] = None,
    resolve_symlinks: bool = True,
    view_path: Optional[str] = None,
) -> str:
    """List label types defined in the VOB containing `path` (`lstype -kind lbtype`).

    Use this to discover which baseline labels exist before comparing two of
    them. A label *type* existing in the VOB does NOT mean it is applied to any
    version under your module -- check that with clearcase_count_labelled_versions.

    Args:
        path: Any path inside the VOB to interrogate.
        pattern: Case-sensitive substring; only label names containing it are
            returned. Omit for every label in the VOB (can be hundreds).
        resolve_symlinks: Resolve `path` through symlinks first. Leave True
            unless you specifically want the link's own VOB.
        view_path: Directory inside the view to run in. Defaults to `path`.

    Returns:
        Raw `cleartool lstype` stdout, one label per line, filtered by `pattern`.
    """
    _check_view_path(view_path)
    target = _resolve(path, resolve_symlinks)
    out = _util.run_cleartool_command(
        f"lstype -kind lbtype -short -invob {shlex.quote(target)}",
        cwd=view_path or (target if os.path.isdir(target) else os.path.dirname(target)),
    )
    if pattern:
        return "\n".join(l for l in out.splitlines() if pattern in l)
    return out


@mcp.tool(annotations={**READ_ONLY, "title": "Count versions carrying a label"})
def clearcase_count_labelled_versions(
    path: str,
    label: str,
    resolve_symlinks: bool = True,
    view_path: Optional[str] = None,
) -> str:
    """Count versions under `path` that carry `label`, to prove it is applied here.

    Baselines are labelled per component, so a chip-level baseline name is often
    absent from a given module's VOB even though the label type exists. A count
    of 0 means "this label is not applied under this path" -- resolve the
    baseline another way (see clearcase_version_rule) rather than concluding
    there are no changes.

    Args:
        path: Directory or file to search under.
        label: Label type name, e.g. a baseline label.
        resolve_symlinks: Resolve `path` through symlinks first.
        view_path: Directory inside the view to run in.

    Returns:
        A line "count <n>" followed by up to 20 sample version-extended paths.
    """
    _check_view_path(view_path)
    target = _resolve(path, resolve_symlinks)
    out = _util.run_cleartool_command(
        f"find {shlex.quote(target)} -version {shlex.quote(f'lbtype({label})')} -print",
        cwd=view_path,
        timeout_sec=_util.CLEARTOOL_QUERY_TIMEOUT_SEC,
    )
    if out.startswith("ERROR:"):
        return out
    lines = [l for l in out.splitlines() if "@@" in l]
    return f"count {len(lines)}\n" + "\n".join(lines[:20])


@mcp.tool(annotations={**READ_ONLY, "title": "Find versions changed between two labels"})
def clearcase_find_changed_between_labels(
    paths: Union[str, list],
    from_label: str,
    to_label: str,
    resolve_symlinks: bool = True,
    view_path: Optional[str] = None,
) -> str:
    """List elements whose `to_label` version differs from their `from_label` one.

    This is the baseline-to-baseline delta. It works because an unchanged
    element carries BOTH labels on the same version, so the predicate
    `lbtype(to) && !lbtype(from)` matches exactly the changed elements.

    Both labels must actually be applied under `paths` for the result to mean
    anything -- verify with clearcase_count_labelled_versions first. If one is
    not applied, this returns nothing, which is indistinguishable from "no
    changes"; use clearcase_list_history with `since` instead in that case.

    Args:
        paths: One path, a comma-separated list, or a list of paths to search
            under (e.g. a module's src/ and regs/ directories).
        from_label: The older baseline label.
        to_label: The newer baseline label.
        resolve_symlinks: Resolve each path through symlinks first.
        view_path: Directory inside the view to run in.

    Returns:
        A line "count <n>" then one version-extended path per changed element.
    """
    _check_view_path(view_path)
    resolved = _resolved_scope(paths, resolve_symlinks)
    scope = " ".join(shlex.quote(p) for p in resolved)
    query = f"lbtype({to_label}) && !lbtype({from_label})"
    out = _util.run_cleartool_command(
        f"find {scope} -version {shlex.quote(query)} -print",
        cwd=view_path,
        timeout_sec=_util.CLEARTOOL_QUERY_TIMEOUT_SEC,
    )
    if out.startswith("ERROR:"):
        return out
    lines = [l for l in out.splitlines() if "@@" in l]
    return f"count {len(lines)}\n" + "\n".join(lines)


@mcp.tool(annotations={**READ_ONLY, "title": "Show which label a view selects"})
def clearcase_version_rule(
    path: str, resolve_symlinks: bool = True, view_path: Optional[str] = None
) -> str:
    """Show the version the view selects for `path` and the config-spec rule behind it.

    Output looks like `<path>@@/main/<branch>/7   Rule: <label> -nocheckout`. The
    label in that rule is the component's effective baseline in this view, which
    is the reliable way to pin "current" when a chip-level baseline name is not
    applied in the component's own VOB.

    Args:
        path: File or directory to interrogate.
        resolve_symlinks: Resolve through symlinks first. Required for a
            symlinked module directory -- otherwise you get the link's own rule,
            which carries no useful label.
        view_path: Directory inside the view to run in.

    Returns:
        Raw `cleartool ls -d` stdout.
    """
    _check_view_path(view_path)
    target = _resolve(path, resolve_symlinks)
    return _util.run_cleartool_command(f"ls -d {shlex.quote(target)}", cwd=view_path)


@mcp.tool(annotations={**READ_ONLY, "title": "List element history"})
def clearcase_list_history(
    paths: Union[str, list],
    since: Optional[str] = None,
    recurse: bool = True,
    fmt: str = r"%Xn\t%Sd\t%u\t%Nl\n",
    resolve_symlinks: bool = True,
    view_path: Optional[str] = None,
) -> str:
    """List version-creation history for elements (`cleartool lshistory`).

    Use this when a label-based comparison is not possible -- typically because
    the baseline label is not applied in this component -- and you need "what
    changed after <date>" instead.

    The default `fmt` is used deliberately: plain `lshistory` truncates a long
    label list with an ellipsis, so labels read from unformatted output are
    unreliable. `%Nl` prints them all.

    Args:
        paths: One path, a comma-separated list, or a list of paths.
        since: Only events at or after this date, in cleartool's form, e.g.
            "02-Jul-2026". Omit for the full history.
        recurse: Walk directory trees. Leave True for a directory scope.
        fmt: `-fmt` string. Fields: %Xn version-extended name, %Sd short date,
            %u user, %Nl all labels, %Vn version, %c comment.
        resolve_symlinks: Resolve each path through symlinks first.
        view_path: Directory inside the view to run in.

    Returns:
        Raw `cleartool lshistory` stdout, one event per line in `fmt`'s layout.
    """
    _check_view_path(view_path)
    resolved = _resolved_scope(paths, resolve_symlinks)
    parts = ["lshistory"]
    if recurse:
        parts.append("-recurse")
    if since:
        parts += ["-since", shlex.quote(since)]
    parts += ["-fmt", shlex.quote(fmt)]
    parts += [shlex.quote(p) for p in resolved]
    return _util.run_cleartool_command(
        " ".join(parts), cwd=view_path,
        timeout_sec=_util.CLEARTOOL_QUERY_TIMEOUT_SEC,
    )


@mcp.tool(annotations={**READ_ONLY, "title": "Describe an element or version"})
def clearcase_describe(
    path: str,
    fmt: Optional[str] = None,
    resolve_symlinks: bool = True,
    view_path: Optional[str] = None,
) -> str:
    """Describe an element, version, or type (`cleartool describe`).

    `path` may be a plain path (describes the view-selected version) or a
    version-extended path like `<file>@@/main/<branch>/8`.

    Handy `fmt` strings: `%PVn` the predecessor version (for diffing a change
    against what came before it), `%Nl` every label on the version, `%Vn` the
    version id, `%u` the author, `%Sd` the date.

    Args:
        path: Path, version-extended path, or object selector (e.g.
            `lbtype:<label>@<vob>`).
        fmt: `-fmt` string. Omit for the full human-readable description,
            which includes the Labels block.
        resolve_symlinks: Resolve through symlinks first. Ignored when `path`
            contains `@@`, since that already names an exact version.
        view_path: Directory inside the view to run in.

    Returns:
        Raw `cleartool describe` stdout.
    """
    _check_view_path(view_path)
    target = path if "@@" in path or path.startswith(("lbtype:", "brtype:", "vob:")) \
        else _resolve(path, resolve_symlinks)
    cmd = "describe"
    if fmt:
        cmd += f" -fmt {shlex.quote(fmt)}"
    cmd += f" {shlex.quote(target)}"
    return _util.run_cleartool_command(cmd, cwd=view_path)


@mcp.tool(annotations={**READ_ONLY, "title": "Diff two specific versions"})
def clearcase_diff_versions(
    version_a: str, version_b: str, view_path: Optional[str] = None
) -> str:
    """Diff two explicit versions of an element (`cleartool diff -serial_format`).

    Unlike clearcase_diff_files, which is fixed to file-vs-predecessor, this
    compares any two versions -- the point of it is diffing the same element as
    it stood at two different baselines.

    Pass version-extended paths, e.g.
    `/vobs/.../regs.blk@@/main/prj_int/4`. Obtain them from
    clearcase_find_changed_between_labels or clearcase_describe with `%PVn`.

    Output is `-serial_format`: hunk headers like `-----[40 changed to 40]-----`,
    old lines prefixed `<`, new lines prefixed `>`, separated by `---`. A banner
    of `***`/`<<<`/`>>>` lines precedes the first hunk; it is not content.

    Args:
        version_a: The older version-extended path.
        version_b: The newer version-extended path.
        view_path: Directory inside the view to run in.

    Returns:
        Raw `cleartool diff -serial_format` stdout.
    """
    _check_view_path(view_path)
    # -g would request the graphical differ, which cannot render headless.
    return _util.run_cleartool_command(
        f"diff -serial_format {shlex.quote(version_a)} {shlex.quote(version_b)}",
        cwd=view_path,
        timeout_sec=_util.CLEARTOOL_QUERY_TIMEOUT_SEC,
    )


@mcp.tool(annotations={**READ_ONLY, "title": "Show a view's config spec"})
def clearcase_config_spec(view_path: Optional[str] = None) -> str:
    """Show the view's config spec (`cleartool catcs`).

    Worth reading when baselines behave inconsistently across modules: a UCM
    stream's config spec selects a DIFFERENT label per component, so there is
    often no single chip-wide baseline label. The per-component `element ...
    <label>` rules here are the ground truth for what the view sees.

    Args:
        view_path: Directory inside the view to run in. Normally required.

    Returns:
        Raw `cleartool catcs` stdout.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command("catcs", cwd=view_path)


# ---------------------------------------------------------------------------
# Streams / rebase / deliver
# ---------------------------------------------------------------------------


@mcp.tool(annotations={**READ_ONLY, "title": "List ClearCase streams"})
def clearcase_list_streams(view_path: Optional[str] = None) -> str:
    """List streams in the current project (`cleartool lsstream -s`).

    Args:
        view_path: Directory inside the view to run in. Normally required.

    Returns:
        Raw `cleartool lsstream -s` stdout: one stream per line.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command("lsstream -s", cwd=view_path)


@mcp.tool(annotations={**WRITE, "title": "Set the config spec to the stream"})
def clearcase_set_stream(view_path: Optional[str] = None) -> str:
    """Reset the view's config spec to its stream's (`cleartool setcs -stream`).

    This rewrites the view's config spec, which changes which versions of every
    file the view sees.

    Args:
        view_path: Directory inside the view to run in. Normally required.

    Returns:
        Raw `cleartool setcs -stream` stdout.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command("setcs -stream", cwd=view_path)


@mcp.tool(annotations={**WRITE, "title": "Rebase a stream to baselines"})
def clearcase_rebase_stream(
    baseline_names: Union[str, list], view_path: Optional[str] = None
) -> str:
    """Start a rebase of the current stream onto the given baselines.

    This changes which versions the view sees and can produce merge conflicts
    that must be resolved before clearcase_complete_rebase succeeds.

    Args:
        baseline_names: Baseline names, as a list or a comma-separated string.
        view_path: Directory inside the view to run in. Normally required.

    Returns:
        Raw `cleartool rebase -baseline` stdout.
    """
    _check_view_path(view_path)
    if isinstance(baseline_names, str):
        baselines = baseline_names
    else:
        baselines = ",".join(baseline_names)
    return _util.run_cleartool_command(f"rebase -baseline {baselines}", cwd=view_path)


@mcp.tool(annotations={**DESTRUCTIVE, "title": "Complete an in-progress rebase"})
def clearcase_complete_rebase(view_path: Optional[str] = None) -> str:
    """Finish an in-progress rebase (`cleartool rebase -complete`).

    Destructive: commits the rebase result to the stream. Unresolved merges
    cause failure; verify with clearcase_list_checked_out_files first.

    Args:
        view_path: Directory inside the view to run in. Normally required.

    Returns:
        Raw `cleartool rebase -complete` stdout.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command("rebase -complete", cwd=view_path)


@mcp.tool(annotations={**DESTRUCTIVE, "title": "Deliver changes to the parent stream"})
def clearcase_deliver_changes(view_path: Optional[str] = None) -> str:
    """Start delivering this stream's changes to its parent stream.

    Destructive: publishes your work to a shared stream other people consume.

    The SDK issues `deliver -g`, which requests the GRAPHICAL merge tool. From
    a headless server that will fail or hang rather than render, so this tool
    omits -g and runs the terminal-driven deliver instead.

    Args:
        view_path: Directory inside the view to run in. Normally required.

    Returns:
        Raw `cleartool deliver` stdout.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command("deliver", cwd=view_path)


@mcp.tool(annotations={**DESTRUCTIVE, "title": "Complete an in-progress delivery"})
def clearcase_complete_delivery(view_path: Optional[str] = None) -> str:
    """Finish an in-progress delivery (`cleartool deliver -complete`).

    Destructive and effectively irreversible: the delivered versions land on
    the parent stream where other users pick them up.

    Args:
        view_path: Directory inside the view to run in. Normally required.

    Returns:
        Raw `cleartool deliver -complete` stdout.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command("deliver -complete", cwd=view_path)


@mcp.tool(annotations={**DESTRUCTIVE, "title": "Cancel an in-progress delivery"})
def clearcase_cancel_delivery(view_path: Optional[str] = None) -> str:
    """Abort an in-progress delivery and roll back its merges.

    Destructive: discards merge work already done in this delivery.

    Args:
        view_path: Directory inside the view to run in. Normally required.

    Returns:
        Raw `cleartool deliver -cancel` stdout.
    """
    _check_view_path(view_path)
    return _util.run_cleartool_command("deliver -cancel", cwd=view_path)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
