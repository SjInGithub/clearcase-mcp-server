import os
import subprocess
import logging
from typing import List, Optional


class ClearCaseUtil:
    def __init__(
        self,
        cleartool_path="cleartool",
        clearcase_scripts_path="/path/to/clearcase/scripts",
    ):
        """
        Constructor for ClearCaseUtil class.

        Args:
            cleartool_path (str): Path to the cleartool executable.
            clearcase_scripts_path (str): Path to ClearCase scripts.
        """
        self.cleartool_path = cleartool_path
        self.clearcase_scripts_path = clearcase_scripts_path

    def run_cleartool_command(self, command: str) -> str:
        """
        Runs a cleartool command and returns the output.

        Args:
            command (str): The cleartool command to run.

        Returns:
            str: Output from the cleartool command.
        """
        try:
            result = os.popen(f"{self.cleartool_path} {command}").read()
            return result
        except Exception as e:
            return f"Failed to run cleartool command '{command}': {e}"

    def run_clearcase_script(self, script_name: str, *arguments) -> str:
        """
        Runs a ClearCase script with the given arguments and returns the output.

        Args:
            script_name (str): The name of the script to run.
            arguments: Variable length argument list for the script.

        Returns:
            str: Output from the script.
        """
        script_path = os.path.join(self.clearcase_scripts_path, script_name)

        try:
            result = subprocess.run(
                [script_path] + list(arguments), capture_output=True, text=True
            )
            return result.stdout
        except Exception as e:
            return f"Failed to run script '{script_name}' with arguments '{arguments}': {e}"

    def list_views(self, user=None, pattern=None) -> str:
        """
        Lists all ClearCase views using lsview script with optional parameters.

        Args:
            user (str): User ID to filter views by user.
            pattern (str): Pattern to filter views by pattern.

        Returns:
            str: Output from the lsview script.
        """
        arguments = []

        if user:
            arguments.extend(["-user", user])

        if pattern:
            arguments.extend(["-pattern", pattern])

        return self.run_clearcase_script("lsview", *arguments)

    def start_view(self, view_tag: str) -> str:
        """
        Starts the ClearCase view.

        Args:
            view_tag (str): ClearCase view tag.

        Returns:
            str: Output from the cleartool startview command.
        """
        return self.run_cleartool_command(f"startview {view_tag}")

    def set_view(self, view_tag: str) -> str:
        """
        Sets the ClearCase view.

        Args:
            view_tag (str): ClearCase view tag.

        Returns:
            str: Output from the cleartool setview command.
        """
        return self.run_cleartool_command(f"setview {view_tag}")

    def remove_view(self, view_tag: str) -> str:
        """
        Removes the ClearCase view.

        Args:
            view_tag (str): ClearCase view tag.

        Returns:
            str: Output from the cleartool rmview command.
        """
        return self.run_cleartool_command(f"rmview -tag {view_tag}")

    def current_view(self) -> str:
        """
        Displays the current ClearCase view.

        Returns:
            str: Output from the cleartool pwv command.
        """
        return self.run_cleartool_command("pwv")

    def create_activity(self, activity_name: str) -> str:
        """
        Creates a new activity in ClearCase.

        Args:
            activity_name (str): The name of the activity to create.

        Returns:
            str: Output from the cleartool mkact command.
        """
        return self.run_cleartool_command(f"mkact {activity_name}")

    def set_activity(self, activity_name: str) -> str:
        """
        Sets the current activity in ClearCase.

        Args:
            activity_name (str): The name of the activity to set.

        Returns:
            str: Output from the cleartool setact command.
        """
        return self.run_cleartool_command(f"setact {activity_name}")

    def list_activities(self, activity_id: str = "") -> str:
        """
        Lists activities in ClearCase.

        Args:
            activity_id (str): The ID of the activity to list. Default is empty string to list all activities.

        Returns:
            str: Output from the cleartool lsact command.
        """
        return self.run_cleartool_command(f"lsact {activity_id}")

    def checkout_file(self, filename: str) -> str:
        """
        Checks out a file in ClearCase.

        Args:
            filename (str): The name of the file to check out.

        Returns:
            str: Output from the cleartool co command.
        """
        return self.run_cleartool_command(f"co {filename}")

    def checkin_file(self, filename: str) -> str:
        """
        Checks in a file in ClearCase.

        Args:
            filename (str): The name of the file to check in.

        Returns:
            str: Output from the cleartool ci command.
        """
        return self.run_cleartool_command(f"ci {filename}")

    def list_checked_out_files(self) -> str:
        """
        Lists all checked out files in ClearCase.

        Returns:
            str: Output from the cleartool lsco command.
        """
        return self.run_cleartool_command("lsco -avobs -cview -s")

    def diff_files(self, file_path: str) -> str:
        """
        Displays differences between files in ClearCase.

        Args:
            file_path (str): The path to the file to compare.

        Returns:
            str: Output from the cleartool diff command.
        """
        return self.run_cleartool_command(f"diff -g -predecessor {file_path}")
    
    def diff_baseline(self, baseline_name: str) -> str:
        """
        Displays differences between baseline in ClearCase.

        Args:
            baseline_name (str): The baseline to compare with the predecessor.

        Returns:
            str: Output from the cleartool diffbl command.
        """
        return self.run_cleartool_command(f"diffbl -predecessor -versions {baseline_name}")

    def list_private_files(self, view_tag: str) -> str:
        """
        Lists all private files in ClearCase.

        Args:
            view_tag (str): ClearCase view tag.

        Returns:
            str: Output from the cleartool lsprivate command.
        """
        return self.run_cleartool_command(f"lsprivate -tag {view_tag}")

    def set_stream(self) -> str:
        """
        Sets the current stream in ClearCase.

        Returns:
            str: Output from the cleartool setcs command.
        """
        return self.run_cleartool_command("setcs -stream")

    def list_streams(self) -> str:
        """
        Lists user's stream name, deliver stream name, all activities in the stream and foundation baselines name.

        Returns:
            str: Output from the cleartool lsstream command.
        """
        return self.run_cleartool_command("lsstream -cview -long")

    def rebase_stream(self, baseline_names: List[str]) -> str:
        """
        Rebases the current stream to specified baselines in ClearCase.

        Args:
            baseline_names (List[str]): List of baseline names to rebase to.

        Returns:
            str: Output from the cleartool rebase command.
        """
        baselines = ",".join(baseline_names)
        return self.run_cleartool_command(f"rebase -baseline {baselines}")

    def complete_rebase(self) -> str:
        """
        Completes the rebase operation in ClearCase.

        Returns:
            str: Output from the cleartool rebase -complete command.
        """
        return self.run_cleartool_command("rebase -complete")

    def deliver_changes(self) -> str:
        """
        Delivers changes in ClearCase.

        Returns:
            str: Output from the cleartool deliver -g command.
        """
        return self.run_cleartool_command("deliver -g")

    def complete_delivery(self) -> str:
        """
        Completes delivery operation in ClearCase.

        Returns:
            str: Output from the cleartool deliver -complete command.
        """
        return self.run_cleartool_command("deliver -complete")

    def cancel_delivery(self) -> str:
        """
        Cancels delivery operation in ClearCase.

        Returns:
            str: Output from the cleartool deliver -cancel command.
        """
        return self.run_cleartool_command("deliver -cancel")
