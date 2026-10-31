"""Locally-added ClearCaseUtil behaviour: cwd-aware cleartool execution.

ClearCaseUtil.run_cleartool_command() builds an f-string and runs it through
os.popen(), which (a) offers no way to choose a working directory and (b)
discards stderr and the exit status. Both matter here:

- cleartool is almost entirely cwd-relative (lsco, lsact, deliver, rebase all
  act on "the current view"), and an MCP server's cwd is fixed for its whole
  lifetime, so the caller must be able to say where a command runs.
- cleartool reports most failures on stderr with an empty stdout, so dropping
  stderr turns every error into an indistinguishable empty string.

This mixin overrides the method with a subprocess-based implementation that
accepts `cwd` and folds stderr into the returned text. The signature stays
compatible with the base class (cwd is keyword-only with a default), so
inherited helpers that call run_cleartool_command() without it still work.
"""

import shlex
import subprocess
from typing import Optional


class ClearCaseUtilExt:
    # Set generously: interactive cleartool subcommands (co/ci without a comment
    # flag) can sit waiting on stdin forever otherwise. stdin is closed below to
    # make such a prompt fail fast instead of hanging, and this is the backstop.
    CLEARTOOL_TIMEOUT_SEC = 300

    # Tree-walking queries (`find -version`, `lshistory -recurse`) over a large
    # VOB subtree routinely run for minutes; 300 s is not enough for them, so
    # those tools pass a longer timeout explicitly.
    CLEARTOOL_QUERY_TIMEOUT_SEC = 1800

    def run_cleartool_command(
        self, command: str, cwd: Optional[str] = None, timeout_sec: Optional[int] = None
    ) -> str:
        """Run a cleartool subcommand, optionally inside a specific directory.

        Args:
            command: The cleartool subcommand and its arguments, e.g.
                "lsco -avobs -cview -s". Do NOT include the cleartool
                executable itself.
            cwd: Directory to run in -- for a dynamic view, a path under
                /view/<view_tag>/. None runs in the server process's own cwd.
            timeout_sec: Override the default timeout. Recursive tree queries
                pass CLEARTOOL_QUERY_TIMEOUT_SEC.

        Returns:
            Combined stdout and stderr. Errors are returned as text rather than
            raised, matching the base class's contract, but unlike the base
            class stderr is preserved so failures are visible.
        """
        limit = timeout_sec or self.CLEARTOOL_TIMEOUT_SEC
        argv = [self.cleartool_path] + shlex.split(command)
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=limit,
            )
        except FileNotFoundError:
            return (
                f"ERROR: cleartool executable not found at '{self.cleartool_path}'. "
                "Set CLEARTOOL_PATH to its full path."
            )
        except subprocess.TimeoutExpired:
            return (
                f"ERROR: cleartool timed out after {limit}s: "
                f"{' '.join(argv)}. A recursive query over a large subtree may need "
                "a narrower scope, or a subcommand may be waiting on input (e.g. "
                "co/ci prompting for a comment)."
            )
        except OSError as exc:
            return f"ERROR: failed to run '{' '.join(argv)}': {exc}"

        output = result.stdout
        if result.stderr:
            # cleartool puts real diagnostics here; label them so the caller can
            # tell them apart from command output.
            output = f"{output}\n[cleartool stderr] {result.stderr}".strip()
        if result.returncode != 0 and not output:
            output = f"ERROR: cleartool exited {result.returncode} with no output."
        return output
