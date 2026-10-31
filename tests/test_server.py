"""Tests for clearcase-mcp-server tool wiring and view-context handling.

cleartool is not exercised for real: these tests stub the command runner and
assert on the argv/cwd that each tool would have produced. That keeps the suite
runnable without a started ClearCase view while still pinning the parts most
likely to regress -- the view_path plumbing and the GUI-flag avoidance.
"""

import pytest

from clearcase_mcp_server import server
from clearcase_mcp_server.clearcase_util_ext import ClearCaseUtilExt
from clearcase_mcp_server.server import mcp


@pytest.fixture
def recorder(monkeypatch, tmp_path):
    """Capture (command, cwd, timeout_sec) instead of running cleartool."""
    calls = []

    def _fake(command, cwd=None, timeout_sec=None):
        calls.append({"command": command, "cwd": cwd, "timeout_sec": timeout_sec})
        return f"stub:{command}"

    monkeypatch.setattr(server._util, "run_cleartool_command", _fake)
    view = tmp_path / "view"
    view.mkdir()
    return calls, str(view)


# ---------------------------------------------------------------------------
# setview must stay unexposed
# ---------------------------------------------------------------------------


async def test_setview_is_not_exposed():
    """`cleartool setview` execs a subshell that would hang the server."""
    tools = await mcp.list_tools()
    names = [t.name for t in tools]
    assert not any("set_view" in n or "setview" in n for n in names)


async def test_expected_tool_count():
    assert len(await mcp.list_tools()) == 28


# ---------------------------------------------------------------------------
# Annotations: destructive operations must be flagged for host confirmation
# ---------------------------------------------------------------------------


DESTRUCTIVE_TOOLS = {
    "clearcase_remove_view",
    "clearcase_checkin_file",
    "clearcase_add_to_source_control",
    "clearcase_deliver_changes",
    "clearcase_complete_delivery",
    "clearcase_cancel_delivery",
    "clearcase_complete_rebase",
}

READ_TOOLS = {
    "clearcase_list_views",
    "clearcase_current_view",
    "clearcase_list_private_files",
    "clearcase_list_activities",
    "clearcase_list_checked_out_files",
    "clearcase_list_streams",
    "clearcase_diff_files",
    # version / label query tools
    "clearcase_list_labels",
    "clearcase_count_labelled_versions",
    "clearcase_find_changed_between_labels",
    "clearcase_version_rule",
    "clearcase_list_history",
    "clearcase_describe",
    "clearcase_diff_versions",
    "clearcase_config_spec",
}


async def test_destructive_tools_are_annotated():
    tools = {t.name: t for t in await mcp.list_tools()}
    for name in DESTRUCTIVE_TOOLS:
        ann = tools[name].annotations
        assert ann.destructiveHint is True, f"{name} missing destructiveHint"
        assert ann.readOnlyHint is False, f"{name} must not be readOnly"


async def test_read_tools_are_annotated_read_only():
    tools = {t.name: t for t in await mcp.list_tools()}
    for name in READ_TOOLS:
        ann = tools[name].annotations
        assert ann.readOnlyHint is True, f"{name} should be readOnly"
        assert not getattr(ann, "destructiveHint", False)


# ---------------------------------------------------------------------------
# view_path plumbing
# ---------------------------------------------------------------------------


VIEW_PATH_TOOLS = [
    ("clearcase_current_view", {}, "pwv"),
    ("clearcase_list_checked_out_files", {}, "lsco -avobs -cview -s"),
    ("clearcase_list_streams", {}, "lsstream -s"),
    ("clearcase_set_stream", {}, "setcs -stream"),
    ("clearcase_complete_rebase", {}, "rebase -complete"),
    ("clearcase_complete_delivery", {}, "deliver -complete"),
    ("clearcase_cancel_delivery", {}, "deliver -cancel"),
    ("clearcase_checkout_file", {"filename": "f.v"}, "co f.v"),
    ("clearcase_checkin_file", {"filename": "f.v"}, "ci f.v"),
    ("clearcase_create_activity", {"activity_name": "act1"}, "mkact act1"),
    ("clearcase_set_activity", {"activity_name": "act1"}, "setact act1"),
]


@pytest.mark.parametrize("tool_name, extra, expected_cmd", VIEW_PATH_TOOLS)
async def test_view_path_is_forwarded_as_cwd(recorder, tool_name, extra, expected_cmd):
    calls, view = recorder
    await mcp.call_tool(tool_name, {"view_path": view, **extra})
    assert calls[-1]["cwd"] == view
    assert calls[-1]["command"] == expected_cmd


@pytest.mark.parametrize("tool_name, extra, expected_cmd", VIEW_PATH_TOOLS)
async def test_omitted_view_path_forwards_none(recorder, tool_name, extra, expected_cmd):
    """Without view_path the command runs in the server's cwd (cwd=None)."""
    calls, _view = recorder
    await mcp.call_tool(tool_name, dict(extra))
    assert calls[-1]["cwd"] is None


@pytest.mark.parametrize("tool_name, extra, _cmd", VIEW_PATH_TOOLS)
async def test_bad_view_path_is_rejected(recorder, tool_name, extra, _cmd):
    """A nonexistent view_path must raise, not silently run in the wrong place."""
    calls, _view = recorder
    with pytest.raises(Exception, match="not an existing directory"):
        await mcp.call_tool(tool_name, {"view_path": "/no/such/view", **extra})
    assert calls == []


# ---------------------------------------------------------------------------
# Headless-safety: no GUI flags
# ---------------------------------------------------------------------------


async def test_diff_does_not_request_gui(recorder):
    """The SDK uses `diff -g` (graphical); a headless server must not."""
    calls, view = recorder
    await mcp.call_tool("clearcase_diff_files", {"file_path": "f.v", "view_path": view})
    cmd = calls[-1]["command"]
    assert "-g" not in cmd.split(), f"GUI flag leaked into: {cmd}"
    assert "-predecessor" in cmd


async def test_deliver_does_not_request_gui(recorder):
    """The SDK uses `deliver -g`; that would hang or fail headless."""
    calls, view = recorder
    await mcp.call_tool("clearcase_deliver_changes", {"view_path": view})
    assert calls[-1]["command"] == "deliver"


# ---------------------------------------------------------------------------
# rebase baseline handling
# ---------------------------------------------------------------------------


async def test_rebase_accepts_list_of_baselines(recorder):
    calls, view = recorder
    await mcp.call_tool(
        "clearcase_rebase_stream",
        {"baseline_names": ["BL1", "BL2"], "view_path": view},
    )
    assert calls[-1]["command"] == "rebase -baseline BL1,BL2"


async def test_rebase_accepts_comma_string(recorder):
    """A single string must not be exploded into per-character baselines."""
    calls, view = recorder
    await mcp.call_tool(
        "clearcase_rebase_stream",
        {"baseline_names": "BL1,BL2", "view_path": view},
    )
    assert calls[-1]["command"] == "rebase -baseline BL1,BL2"


# ---------------------------------------------------------------------------
# The cwd-aware runner itself
# ---------------------------------------------------------------------------


class _Util(ClearCaseUtilExt):
    def __init__(self, cleartool_path):
        self.cleartool_path = cleartool_path


def test_runner_executes_in_cwd(tmp_path):
    marker = tmp_path / "marker.txt"
    marker.write_text("x")
    util = _Util("/bin/ls")
    out = util.run_cleartool_command("marker.txt", cwd=str(tmp_path))
    assert "marker.txt" in out


def test_runner_reports_missing_executable():
    util = _Util("/nonexistent/cleartool")
    out = util.run_cleartool_command("pwv")
    assert "not found" in out
    assert "CLEARTOOL_PATH" in out


def test_runner_surfaces_stderr(tmp_path):
    """cleartool reports failures on stderr; they must not be swallowed."""
    util = _Util("/bin/ls")
    out = util.run_cleartool_command("/no/such/path", cwd=str(tmp_path))
    assert "stderr" in out.lower() or "No such file" in out


def test_runner_does_not_hang_on_stdin_read():
    """stdin is closed, so an interactive prompt fails fast instead of hanging."""
    util = _Util("/bin/cat")
    out = util.run_cleartool_command("")
    assert isinstance(out, str)


def test_runner_honours_timeout_override():
    """A per-call timeout must reach subprocess.run, not be silently dropped."""
    util = _Util("/bin/sleep")
    out = util.run_cleartool_command("5", timeout_sec=1)
    assert "timed out after 1s" in out


# ---------------------------------------------------------------------------
# Version / label query tools
# ---------------------------------------------------------------------------


@pytest.fixture
def symlinked_module(tmp_path):
    """A module dir reached through a symlink, as chip trees expose them."""
    real = tmp_path / "component" / "mod"
    (real / "src").mkdir(parents=True)
    (real / "regs").mkdir()
    link = tmp_path / "sub" / "mod"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    return str(link), str(real)


async def test_label_diff_predicate_matches_changed_only(recorder):
    """An unchanged element carries both labels, so the delta needs both halves."""
    calls, view = recorder
    await mcp.call_tool(
        "clearcase_find_changed_between_labels",
        {"paths": "/vobs/m/src", "from_label": "OLD", "to_label": "NEW",
         "view_path": view, "resolve_symlinks": False},
    )
    cmd = calls[-1]["command"]
    assert "lbtype(NEW) && !lbtype(OLD)" in cmd
    assert cmd.startswith("find ")
    assert "-print" in cmd


async def test_label_diff_accepts_multiple_scopes(recorder):
    calls, view = recorder
    await mcp.call_tool(
        "clearcase_find_changed_between_labels",
        {"paths": ["/vobs/m/src", "/vobs/m/regs"], "from_label": "OLD",
         "to_label": "NEW", "view_path": view, "resolve_symlinks": False},
    )
    cmd = calls[-1]["command"]
    assert "/vobs/m/src" in cmd and "/vobs/m/regs" in cmd


async def test_label_diff_uses_long_timeout(recorder):
    """Recursive finds routinely exceed the 300 s interactive default."""
    calls, view = recorder
    await mcp.call_tool(
        "clearcase_find_changed_between_labels",
        {"paths": "/vobs/m/src", "from_label": "OLD", "to_label": "NEW",
         "view_path": view, "resolve_symlinks": False},
    )
    assert calls[-1]["timeout_sec"] == server._util.CLEARTOOL_QUERY_TIMEOUT_SEC


async def test_symlinked_module_is_resolved(recorder, symlinked_module):
    """cleartool must see the real element, not the link."""
    calls, view = recorder
    link, real = symlinked_module
    await mcp.call_tool(
        "clearcase_version_rule", {"path": link, "view_path": view},
    )
    assert real in calls[-1]["command"]
    assert link not in calls[-1]["command"]


async def test_symlink_resolution_can_be_disabled(recorder, symlinked_module):
    calls, view = recorder
    link, real = symlinked_module
    await mcp.call_tool(
        "clearcase_version_rule",
        {"path": link, "view_path": view, "resolve_symlinks": False},
    )
    assert link in calls[-1]["command"]


async def test_history_requests_all_labels(recorder):
    """Default lshistory truncates label lists; %Nl must be requested."""
    calls, view = recorder
    await mcp.call_tool(
        "clearcase_list_history",
        {"paths": "/vobs/m/src", "since": "02-Jul-2026", "view_path": view,
         "resolve_symlinks": False},
    )
    cmd = calls[-1]["command"]
    assert "%Nl" in cmd
    assert "-recurse" in cmd
    assert "-since" in cmd and "02-Jul-2026" in cmd


async def test_describe_passes_version_extended_path_through(recorder):
    """A path naming an exact version must not be mangled by realpath."""
    calls, view = recorder
    ver = "/vobs/m/regs/x.blk@@/main/br/8"
    await mcp.call_tool(
        "clearcase_describe", {"path": ver, "fmt": "%PVn", "view_path": view},
    )
    cmd = calls[-1]["command"]
    assert ver in cmd
    assert "%PVn" in cmd


async def test_diff_versions_does_not_request_gui(recorder):
    calls, view = recorder
    await mcp.call_tool(
        "clearcase_diff_versions",
        {"version_a": "/vobs/m/f.v@@/main/br/4",
         "version_b": "/vobs/m/f.v@@/main/br/8", "view_path": view},
    )
    cmd = calls[-1]["command"]
    assert "-g" not in cmd.split()
    assert "-serial_format" in cmd
    assert "/main/br/4" in cmd and "/main/br/8" in cmd


async def test_empty_path_list_is_rejected(recorder):
    calls, view = recorder
    with pytest.raises(Exception, match="at least one path"):
        await mcp.call_tool(
            "clearcase_find_changed_between_labels",
            {"paths": "", "from_label": "OLD", "to_label": "NEW", "view_path": view},
        )
    assert calls == []


# ---------------------------------------------------------------------------
# clearcase_add_to_source_control
# ---------------------------------------------------------------------------


async def test_add_to_source_control_adds_dir_tree_and_checks_in(monkeypatch, tmp_path):
    view = tmp_path / "view"
    (view / "sub" / "pkg").mkdir(parents=True)
    (view / "sub" / "a.sv").write_text("a")
    (view / "sub" / "pkg" / "b.sv").write_text("b")
    (view / "parent").mkdir()
    (view / "parent" / "c.tcl").write_text("c")
    calls = []

    def _fake(command, cwd=None, timeout_sec=None):
        calls.append(command)
        if command.startswith("ls -d"):
            return command[len("ls -d ") :]  # no "@@": everything is view-private
        if "mkelem" in command and "-eltype directory" in command:
            (view / command.split()[-1]).mkdir()  # real mkelem creates the empty dir
        return ""

    monkeypatch.setattr(server._util, "run_cleartool_command", _fake)
    result = await mcp.call_tool(
        "clearcase_add_to_source_control",
        {"paths": ["parent/c.tcl", "sub"], "view_path": str(view)},
    )

    mk = [c for c in calls if c.startswith("mkelem")]
    assert "mkelem -nc -ci parent/c.tcl" in mk
    assert "mkelem -nc -eltype directory sub" in mk
    assert "mkelem -nc -eltype directory sub/pkg" in mk
    assert "mkelem -nc -ci sub/a.sv" in mk
    assert "mkelem -nc -ci sub/pkg/b.sv" in mk
    # contents were moved back into place and the temp dir removed
    assert (view / "sub" / "pkg" / "b.sv").read_text() == "b"
    assert not (view / "sub.__mkelem_tmp").exists()
    # directories are checked in deepest first
    ci = [c for c in calls if c.startswith("ci")]
    assert ci.index("ci -nc sub/pkg") < ci.index("ci -nc sub")
    assert "FAILED: 0" in str(result)


async def test_add_to_source_control_skips_existing_elements(monkeypatch, tmp_path):
    view = tmp_path / "view"
    view.mkdir()
    (view / "f.v").write_text("x")
    calls = []

    def _fake(command, cwd=None, timeout_sec=None):
        calls.append(command)
        return "f.v@@/main/1" if command.startswith("ls -d") else ""

    monkeypatch.setattr(server._util, "run_cleartool_command", _fake)
    await mcp.call_tool("clearcase_add_to_source_control", {"paths": "f.v", "view_path": str(view)})
    assert not any(c.startswith(("mkelem", "co ", "ci ")) for c in calls)
