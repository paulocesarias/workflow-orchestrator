"""SSH executor service for running Claude on remote host."""

import base64
import json
import subprocess
import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# Default SSH settings
DEFAULT_SSH_HOST = "72.61.78.57"
DEFAULT_SSH_USER = "paulo"
DEFAULT_SSH_KEY_PATH = "/app/secrets/ssh_key"
SSH_TIMEOUT_SECONDS = 600  # 10 minutes max


@dataclass
class ExecutionResult:
    """Result from SSH execution."""

    success: bool
    duration_ms: int = 0
    error: str | None = None


class SSHExecutor:
    """Execute commands on remote host via SSH.

    This mirrors the n8n approach: SSH to the VPS and run claude-streamer.py
    which handles all the Claude CLI interaction and Slack streaming.
    """

    def __init__(
        self,
        host: str = DEFAULT_SSH_HOST,
        user: str | None = None,
        key_path: str = DEFAULT_SSH_KEY_PATH,
    ):
        self.host = host
        self.user = user or DEFAULT_SSH_USER
        self.key_path = key_path

    def execute_claude_streamer(
        self,
        slack_token: str,
        channel: str,
        thread_ts: str,
        message_ts: str,
        session_id: str,
        message: str,
        working_dir: str,
        files: list[dict] | None = None,
    ) -> ExecutionResult:
        """Execute claude-streamer.py on remote host.

        This matches exactly what n8n does:
        1. SSH to host with specific user's working directory
        2. Export SLACK_TOKEN env var
        3. Run claude-streamer.py with base64-encoded args

        Args:
            slack_token: Slack bot token
            channel: Slack channel ID
            thread_ts: Thread timestamp for replies
            message_ts: Original message timestamp
            session_id: Claude session ID for continuity
            message: User's message text
            working_dir: Working directory on remote host
            files: Optional list of file attachments

        Returns:
            ExecutionResult with success status and timing
        """
        start_time = time.time()

        # Base64 encode message (matches n8n approach)
        message_b64 = base64.b64encode(message.encode()).decode()

        # Build the remote command
        script_path = "/opt/slack-bots/claude-streamer.py"
        args = [channel, thread_ts, message_ts, session_id, message_b64]

        # Add files if present
        if files:
            files_json = json.dumps(files)
            files_b64 = base64.b64encode(files_json.encode()).decode()
            args.append(files_b64)

        # The remote command exports SLACK_TOKEN and runs the script
        # This matches exactly what n8n does in the SSH node
        args_str = " ".join(args)
        remote_cmd = f'cd {working_dir} && export SLACK_TOKEN="{slack_token}" && python3 {script_path} {args_str}'

        # Build SSH command
        ssh_cmd = [
            "ssh",
            "-i", self.key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-o", "BatchMode=yes",
            f"{self.user}@{self.host}",
            remote_cmd,
        ]

        logger.info(
            "Executing Claude via SSH",
            host=self.host,
            user=self.user,
            working_dir=working_dir,
            channel=channel,
            session_id=session_id,
            has_files=bool(files),
        )

        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=SSH_TIMEOUT_SECONDS,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or f"Exit code {result.returncode}"
                logger.error(
                    "SSH execution failed",
                    returncode=result.returncode,
                    stderr=result.stderr[:500] if result.stderr else None,
                    stdout=result.stdout[:500] if result.stdout else None,
                )
                return ExecutionResult(
                    success=False,
                    duration_ms=duration_ms,
                    error=error_msg[:500],
                )

            logger.info(
                "SSH execution completed",
                duration_ms=duration_ms,
            )

            return ExecutionResult(
                success=True,
                duration_ms=duration_ms,
            )

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error("SSH execution timed out", timeout=SSH_TIMEOUT_SECONDS)
            return ExecutionResult(
                success=False,
                duration_ms=duration_ms,
                error=f"Timeout after {SSH_TIMEOUT_SECONDS}s",
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error("SSH execution error", error=str(e))
            return ExecutionResult(
                success=False,
                duration_ms=duration_ms,
                error=str(e),
            )
