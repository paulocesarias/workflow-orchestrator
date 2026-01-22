"""SSH executor service for running Claude CLI on remote host."""

import subprocess
import time
from dataclasses import dataclass
from collections.abc import Iterator

import structlog

logger = structlog.get_logger()

# Default SSH settings
DEFAULT_SSH_HOST = "72.61.78.57"
DEFAULT_SSH_PORT = 28473
DEFAULT_SSH_USER = "paulo"
DEFAULT_SSH_KEY_PATH = "/app/secrets/ssh_key"
SSH_TIMEOUT_SECONDS = 600  # 10 minutes max
CONNECT_TIMEOUT_SECONDS = 10


@dataclass
class ExecutionResult:
    """Result from SSH execution."""

    success: bool
    duration_ms: int = 0
    error: str | None = None
    session_id: str | None = None


class SSHExecutor:
    """Execute Claude CLI on remote host via SSH.

    Connects to VPS and runs Claude CLI directly, streaming output back
    for real-time parsing and Slack updates.
    """

    def __init__(
        self,
        host: str = DEFAULT_SSH_HOST,
        port: int = DEFAULT_SSH_PORT,
        user: str | None = None,
        key_path: str = DEFAULT_SSH_KEY_PATH,
    ):
        self.host = host
        self.port = port
        self.user = user or DEFAULT_SSH_USER
        self.key_path = key_path

    def _build_ssh_command(self, remote_cmd: str) -> list[str]:
        """Build SSH command with proper options.

        Args:
            remote_cmd: Command to execute on remote host

        Returns:
            List of command arguments
        """
        return [
            "ssh",
            "-i", self.key_path,
            "-p", str(self.port),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}",
            "-o", "BatchMode=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            f"{self.user}@{self.host}",
            remote_cmd,
        ]

    def execute_claude_streaming(
        self,
        message: str,
        working_dir: str,
        session_id: str | None = None,
        resume_session: str | None = None,
        files_dir: str | None = None,
    ) -> Iterator[str]:
        """Execute Claude CLI and stream output lines.

        Args:
            message: User message to send to Claude
            working_dir: Working directory on remote host
            session_id: Session ID for new sessions
            resume_session: Session ID to resume (mutually exclusive with session_id)
            files_dir: Directory containing files to add

        Yields:
            Lines of JSON output from Claude
        """
        # Build Claude command
        claude_args = [
            "claude",
            "--output-format", "stream-json",
            "--verbose",
            "-p", f'"{self._escape_message(message)}"',
        ]

        # Session handling
        if resume_session:
            claude_args.extend(["--resume", resume_session])
        elif session_id:
            claude_args.extend(["--session-id", session_id])

        # Add files directory if provided
        if files_dir:
            claude_args.extend(["--add-dir", files_dir])

        claude_cmd = " ".join(claude_args)
        remote_cmd = f"cd {working_dir} && {claude_cmd}"

        ssh_cmd = self._build_ssh_command(remote_cmd)

        logger.info(
            "Starting Claude via SSH",
            host=self.host,
            port=self.port,
            user=self.user,
            working_dir=working_dir,
            session_id=session_id or resume_session,
        )

        try:
            process = subprocess.Popen(
                ssh_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )

            # Stream stdout lines
            if process.stdout:
                for line in process.stdout:
                    line = line.strip()
                    if line:
                        yield line

            # Wait for completion
            process.wait(timeout=SSH_TIMEOUT_SECONDS)

            if process.returncode != 0:
                stderr = process.stderr.read() if process.stderr else ""
                logger.error(
                    "SSH execution failed",
                    returncode=process.returncode,
                    stderr=stderr[:500] if stderr else None,
                )

        except subprocess.TimeoutExpired:
            process.kill()
            logger.error("SSH execution timed out", timeout=SSH_TIMEOUT_SECONDS)
            raise

        except Exception as e:
            logger.error("SSH execution error", error=str(e))
            raise

    def execute_claude(
        self,
        message: str,
        working_dir: str,
        session_id: str | None = None,
        resume_session: str | None = None,
        files_dir: str | None = None,
    ) -> tuple[list[str], ExecutionResult]:
        """Execute Claude CLI and collect all output.

        Non-streaming version that collects all output.

        Args:
            message: User message to send to Claude
            working_dir: Working directory on remote host
            session_id: Session ID for new sessions
            resume_session: Session ID to resume
            files_dir: Directory containing files to add

        Returns:
            Tuple of (output_lines, ExecutionResult)
        """
        start_time = time.time()
        lines = []
        error = None

        try:
            for line in self.execute_claude_streaming(
                message=message,
                working_dir=working_dir,
                session_id=session_id,
                resume_session=resume_session,
                files_dir=files_dir,
            ):
                lines.append(line)

            success = True

        except subprocess.TimeoutExpired:
            success = False
            error = f"Timeout after {SSH_TIMEOUT_SECONDS}s"

        except Exception as e:
            success = False
            error = str(e)

        duration_ms = int((time.time() - start_time) * 1000)

        return lines, ExecutionResult(
            success=success,
            duration_ms=duration_ms,
            error=error,
        )

    def test_connection(self) -> bool:
        """Test SSH connection to the host.

        Returns:
            True if connection successful
        """
        ssh_cmd = self._build_ssh_command("echo 'connected'")

        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=CONNECT_TIMEOUT_SECONDS + 5,
            )
            return result.returncode == 0 and "connected" in result.stdout

        except Exception as e:
            logger.error("SSH connection test failed", error=str(e))
            return False

    def check_claude_available(self) -> bool:
        """Check if Claude CLI is available on remote host.

        Returns:
            True if Claude CLI is available
        """
        ssh_cmd = self._build_ssh_command("which claude")

        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=CONNECT_TIMEOUT_SECONDS + 5,
            )
            return result.returncode == 0 and result.stdout.strip() != ""

        except Exception as e:
            logger.error("Claude check failed", error=str(e))
            return False

    def _escape_message(self, message: str) -> str:
        """Escape message for shell execution.

        Args:
            message: Raw message text

        Returns:
            Escaped message safe for shell
        """
        # Escape single quotes and backslashes
        escaped = message.replace("\\", "\\\\")
        escaped = escaped.replace('"', '\\"')
        escaped = escaped.replace("$", "\\$")
        escaped = escaped.replace("`", "\\`")
        escaped = escaped.replace("!", "\\!")
        return escaped
