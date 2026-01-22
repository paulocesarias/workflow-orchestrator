"""Claude CLI integration service."""

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

import httpx
import structlog

logger = structlog.get_logger()

# Constants
SLACK_MAX_MESSAGE_LENGTH = 39000
STREAM_UPDATE_INTERVAL_MS = 500
STREAM_MIN_CHARS = 50
STREAM_TYPING_INDICATOR = "..."
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB


@dataclass
class ClaudeStats:
    """Statistics from Claude execution."""

    duration_ms: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reads: int = 0
    edits: int = 0
    writes: int = 0
    commands: int = 0
    globs: int = 0
    greps: int = 0
    web_fetches: int = 0
    web_searches: int = 0
    tasks: int = 0
    mcp_calls: int = 0


@dataclass
class StreamState:
    """State for streaming response to Slack."""

    text: str = ""
    msg_ts: str | None = None
    last_update_ms: float = 0
    last_streamed_len: int = 0
    continuation_count: int = 0
    all_message_ts: list = field(default_factory=list)
    reported_files: set = field(default_factory=set)
    reported_actions: set = field(default_factory=set)


class SlackMessenger:
    """Synchronous Slack messenger for Celery tasks."""

    def __init__(self, token: str, channel: str, thread_ts: str):
        self.token = token
        self.channel = channel
        self.thread_ts = thread_ts
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def post(self, text: str, timeout: int = 10) -> str | None:
        """Post a message and return ts if successful."""
        try:
            response = httpx.post(
                "https://slack.com/api/chat.postMessage",
                headers=self._headers,
                json={
                    "channel": self.channel,
                    "thread_ts": self.thread_ts,
                    "text": text,
                },
                timeout=timeout,
            )
            data = response.json()
            if data.get("ok"):
                return data.get("ts")
            logger.warning("Slack post failed", error=data.get("error"))
        except Exception as e:
            logger.error("Slack post error", error=str(e))
        return None

    def update(self, ts: str, text: str, timeout: int = 10) -> bool:
        """Update an existing message."""
        # Truncate if needed
        if len(text) > SLACK_MAX_MESSAGE_LENGTH:
            text = text[:SLACK_MAX_MESSAGE_LENGTH] + "\n\n_[Message truncated]_"

        try:
            response = httpx.post(
                "https://slack.com/api/chat.update",
                headers=self._headers,
                json={"channel": self.channel, "ts": ts, "text": text},
                timeout=timeout,
            )
            data = response.json()
            if data.get("ok"):
                return True
            logger.warning("Slack update failed", error=data.get("error"), ts=ts)
        except httpx.TimeoutException:
            logger.warning("Slack update timeout", ts=ts, timeout=timeout)
        except Exception as e:
            logger.error("Slack update error", error=str(e), ts=ts)
        return False

    def add_reaction(self, ts: str, emoji: str) -> bool:
        """Add reaction to a message."""
        try:
            response = httpx.post(
                "https://slack.com/api/reactions.add",
                headers=self._headers,
                json={"channel": self.channel, "timestamp": ts, "name": emoji},
                timeout=10,
            )
            return response.json().get("ok", False)
        except Exception as e:
            logger.warning("Failed to add reaction", error=str(e))
            return False

    def remove_reaction(self, ts: str, emoji: str) -> bool:
        """Remove reaction from a message."""
        try:
            response = httpx.post(
                "https://slack.com/api/reactions.remove",
                headers=self._headers,
                json={"channel": self.channel, "timestamp": ts, "name": emoji},
                timeout=10,
            )
            return response.json().get("ok", False)
        except Exception as e:
            logger.warning("Failed to remove reaction", error=str(e))
            return False


def download_file(token: str, url: str, dest_path: str) -> tuple[bool, str | None]:
    """Download a file from Slack with size limit."""
    try:
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.stream("GET", url, headers=headers, timeout=60) as response:
            if response.status_code != 200:
                return False, f"HTTP {response.status_code}"

            # Check content length
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_FILE_SIZE_BYTES:
                return False, "exceeds_size_limit"

            # Stream download with size check
            downloaded = 0
            with open(dest_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=8192):
                    downloaded += len(chunk)
                    if downloaded > MAX_FILE_SIZE_BYTES:
                        os.remove(dest_path)
                        return False, "exceeds_size_limit"
                    f.write(chunk)

        return True, None
    except Exception as e:
        return False, str(e)


class ClaudeProcessor:
    """Processes messages using Claude CLI."""

    def __init__(
        self,
        slack_token: str,
        channel: str,
        thread_ts: str,
        message_ts: str,
        session_id: str,
        working_dir: str = "/home/paulo",
    ):
        self.slack = SlackMessenger(slack_token, channel, thread_ts)
        self.slack_token = slack_token
        self.channel = channel
        self.thread_ts = thread_ts
        self.message_ts = message_ts
        self.session_id = session_id
        self.working_dir = working_dir
        self.stats = ClaudeStats()
        self.stream_state = StreamState()

    def process(self, message: str, files: list[dict]) -> ClaudeStats:
        """Process a message with Claude CLI and stream response to Slack."""
        # Add processing reaction
        self.slack.add_reaction(self.message_ts, "hourglass_flowing_sand")

        temp_dir = None
        try:
            # Download files if present
            temp_dir, downloaded_files = self._download_files(files)

            # Build full message with file references
            full_message = self._build_message(message, downloaded_files)

            if not full_message.strip():
                self.slack.post("Hey! How can I help you?")
                self._finish_reactions(success=True)
                return self.stats

            # Run Claude CLI
            start_time = time.time()
            self._run_claude(full_message, temp_dir)
            self.stats.duration_ms = int((time.time() - start_time) * 1000)

            # Finalize
            self._finalize_stream()
            self._send_summary()
            self._finish_reactions(success=True)

        except Exception as e:
            logger.error("Claude processing error", error=str(e))
            self.slack.post(f"Error: {str(e)}")
            self._finish_reactions(success=False)

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

        return self.stats

    def _download_files(self, files: list[dict]) -> tuple[str | None, list[dict]]:
        """Download files from Slack to temp directory."""
        if not files:
            return None, []

        # Create secure temp directory
        uid = os.getuid()
        runtime_dir = f"/run/user/{uid}" if os.path.exists(f"/run/user/{uid}") else None
        temp_dir = tempfile.mkdtemp(prefix="claude_", dir=runtime_dir)
        os.chmod(temp_dir, 0o700)

        downloaded = []
        for file_info in files[:5]:  # Max 5 files
            url = file_info.get("url_private", "")
            name = file_info.get("name", "file")
            mimetype = file_info.get("mimetype", "")

            if not url:
                continue

            dest_path = os.path.join(temp_dir, name)
            success, error = download_file(self.slack_token, url, dest_path)

            if success:
                file_type = "image" if mimetype.startswith("image/") else "PDF"
                downloaded.append({"path": dest_path, "name": name, "type": file_type})
                self.slack.post(f"Downloaded {file_type}: `{name}`")
            else:
                self.slack.post(f"Failed to download `{name}`: {error}")

        return temp_dir, downloaded

    def _build_message(self, message: str, files: list[dict]) -> str:
        """Build full message with file references."""
        if not files:
            return message

        file_instructions = [f"- {f['type'].upper()}: {f['path']}" for f in files]
        files_text = "\n".join(file_instructions)

        if message:
            intro = "The user has attached the following file(s). "
            intro += "Please read and analyze them as part of your response:"
            return f"{intro}\n\n{files_text}\n\nUser's message: {message}"
        else:
            intro = "The user has attached the following file(s). Please read and analyze them:"
            return f"""{intro}

{files_text}"""

    def _run_claude(self, message: str, temp_dir: str | None) -> None:
        """Run Claude CLI and process stream output."""
        # Build command - try --session-id first
        cmd = self._build_cmd(message, temp_dir, use_session_id=True)

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=self.working_dir,
        )

        # Check for session error on first line
        first_line = process.stdout.readline()
        if first_line and "already in use" in first_line:
            process.kill()
            process.wait()
            cmd = self._build_cmd(message, temp_dir, use_session_id=False)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=self.working_dir,
            )
            first_line = None

        # Process output
        if first_line:
            self._process_line(first_line)

        for line in process.stdout:
            self._process_line(line)

        process.wait()

    def _build_cmd(self, message: str, temp_dir: str | None, use_session_id: bool) -> list[str]:
        """Build Claude CLI command."""
        cmd = [
            "claude",
            "--output-format", "stream-json",
            "--verbose",
            "-p", message,
        ]

        if use_session_id:
            cmd.extend(["--session-id", self.session_id])
        else:
            cmd.extend(["--resume", self.session_id])

        cmd.append("--dangerously-skip-permissions")

        if temp_dir:
            cmd.extend(["--add-dir", temp_dir])

        return cmd

    def _process_line(self, line: str) -> None:
        """Process a line of Claude CLI output."""
        line = line.strip()
        if not line:
            return

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return

        # Handle assistant messages
        if data.get("type") == "assistant" and "message" in data:
            content = data["message"].get("content", [])

            for item in content:
                if item.get("type") == "text":
                    text = item.get("text", "")
                    if text:
                        self.stream_state.text += text
                        self._update_stream_if_needed()

                elif item.get("type") == "tool_use":
                    self._process_tool_use(item)

        # Handle result
        if data.get("type") == "result":
            self.stats.duration_ms = data.get("duration_ms", self.stats.duration_ms)
            self.stats.cost_usd = data.get("total_cost_usd", 0)
            usage = data.get("usage", {})
            self.stats.input_tokens = (
                usage.get("input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
            )
            self.stats.output_tokens = usage.get("output_tokens", 0)

    def _process_tool_use(self, item: dict) -> None:
        """Process a tool_use item and update stats/stream."""
        tool_name = item.get("name", "")
        tool_input = item.get("input", {})
        state = self.stream_state
        stats = self.stats

        if tool_name == "Edit":
            stats.edits += 1
            file_path = tool_input.get("file_path", "")
            if file_path:
                filename = file_path.split("/")[-1]
                if filename not in state.reported_files:
                    state.reported_files.add(filename)
                    state.text += f"\n_Editing `{filename}`..._\n"
                    self._update_stream_if_needed(force=True)

        elif tool_name == "Write":
            stats.writes += 1
            file_path = tool_input.get("file_path", "")
            if file_path:
                filename = file_path.split("/")[-1]
                if filename not in state.reported_files:
                    state.reported_files.add(filename)
                    state.text += f"\n_Creating `{filename}`..._\n"
                    self._update_stream_if_needed(force=True)

        elif tool_name == "Read":
            stats.reads += 1

        elif tool_name == "Bash":
            cmd_str = tool_input.get("command", "")
            skip_prefixes = ("cat ", "head ", "tail ", "ls ", "pwd", "echo ", "grep ", "find ")
            if cmd_str and not cmd_str.startswith(skip_prefixes):
                stats.commands += 1
                display_cmd = cmd_str[:50] + "..." if len(cmd_str) > 50 else cmd_str
                state.text += f"\n_Running `{display_cmd}`..._\n"
                self._update_stream_if_needed(force=True)

        elif tool_name == "Glob":
            stats.globs += 1
            pattern = tool_input.get("pattern", "")
            if pattern and "glob" not in state.reported_actions:
                state.reported_actions.add("glob")
                self.slack.post(f"Searching for files `{pattern}`...")

        elif tool_name == "Grep":
            stats.greps += 1
            pattern = tool_input.get("pattern", "")
            if pattern and "grep" not in state.reported_actions:
                state.reported_actions.add("grep")
                self.slack.post(f"Searching in files for `{pattern[:30]}`...")

        elif tool_name == "WebFetch":
            stats.web_fetches += 1
            url = tool_input.get("url", "")
            if url:
                domain = url.split("/")[2] if "/" in url else url
                self.slack.post(f"Fetching `{domain}`...")

        elif tool_name == "WebSearch":
            stats.web_searches += 1
            query = tool_input.get("query", "")
            if query:
                display = query[:40] + "..." if len(query) > 40 else query
                self.slack.post(f"Searching the web: `{display}`...")

        elif tool_name == "Task":
            stats.tasks += 1
            desc = tool_input.get("description", "agent task")
            self.slack.post(f"Spawning agent: {desc}...")

        elif tool_name.startswith("mcp__"):
            stats.mcp_calls += 1
            parts = tool_name.split("__")
            if len(parts) >= 3:
                server, action = parts[1], parts[2]
                key = f"mcp_{server}"
                if key not in state.reported_actions:
                    state.reported_actions.add(key)
                    self.slack.post(f"Calling {server}: {action}...")

    def _update_stream_if_needed(self, force: bool = False) -> bool:
        """Update streaming message in Slack if needed."""
        state = self.stream_state
        now = time.time() * 1000

        new_chars = len(state.text) - state.last_streamed_len
        time_elapsed = now - state.last_update_ms

        should_update = force or (
            new_chars >= STREAM_MIN_CHARS and time_elapsed >= STREAM_UPDATE_INTERVAL_MS
        )

        if not should_update or not state.text:
            return True

        # Add typing indicator if not final
        display_text = state.text + (STREAM_TYPING_INDICATOR if not force else "")
        timeout = 30 if force else 10

        # Handle message splitting for long responses
        if len(display_text) > SLACK_MAX_MESSAGE_LENGTH:
            return self._split_and_continue(display_text, timeout)

        # Update or create message
        success = False
        if state.msg_ts:
            success = self.slack.update(state.msg_ts, display_text, timeout)
        else:
            new_ts = self.slack.post(display_text)
            if new_ts:
                state.msg_ts = new_ts
                success = True

        state.last_update_ms = now
        state.last_streamed_len = len(state.text)
        return success

    def _split_and_continue(self, display_text: str, timeout: int) -> bool:
        """Split long message and continue in new message."""
        state = self.stream_state
        safe_cutoff = SLACK_MAX_MESSAGE_LENGTH - 200

        # Find break point
        break_point = safe_cutoff
        newline_pos = state.text.rfind("\n", safe_cutoff - 500, safe_cutoff)
        if newline_pos > 0:
            break_point = newline_pos
        else:
            space_pos = state.text.rfind(" ", safe_cutoff - 100, safe_cutoff)
            if space_pos > 0:
                break_point = space_pos

        # Finalize current message
        if state.msg_ts:
            finalized = state.text[:break_point] + "\n\n_[Continued in next message...]_"
            self.slack.update(state.msg_ts, finalized, timeout)
            state.all_message_ts.append(state.msg_ts)

        # Start continuation
        remainder = state.text[break_point:].lstrip()
        state.continuation_count += 1
        state.text = f"_[Continuation {state.continuation_count}]_\n\n" + remainder
        state.msg_ts = None
        state.last_streamed_len = 0

        return True

    def _finalize_stream(self) -> None:
        """Finalize streaming with retry logic."""
        if not self.stream_state.text:
            return

        for attempt in range(3):
            if self._update_stream_if_needed(force=True):
                logger.info("Final update succeeded", attempt=attempt + 1)
                return
            time.sleep(1)

        # Fallback: send as new message
        logger.warning("All final updates failed, sending fallback")
        text = self.stream_state.text
        if len(text) > SLACK_MAX_MESSAGE_LENGTH:
            text = "...\n\n" + text[-(SLACK_MAX_MESSAGE_LENGTH - 10):]
        self.slack.post(text)

    def _send_summary(self) -> None:
        """Send action summary and stats."""
        stats = self.stats
        parts = []

        if stats.reads > 0:
            parts.append(f"read {stats.reads} file(s)")
        if stats.edits > 0:
            parts.append(f"edited {stats.edits} file(s)")
        if stats.writes > 0:
            parts.append(f"created {stats.writes} file(s)")
        if stats.commands > 0:
            parts.append(f"ran {stats.commands} command(s)")
        if stats.globs > 0:
            parts.append(f"searched {stats.globs} pattern(s)")
        if stats.greps > 0:
            parts.append(f"grepped {stats.greps} time(s)")
        if stats.web_fetches > 0:
            parts.append(f"fetched {stats.web_fetches} URL(s)")
        if stats.web_searches > 0:
            parts.append(f"web searched {stats.web_searches} time(s)")
        if stats.tasks > 0:
            parts.append(f"spawned {stats.tasks} agent(s)")
        if stats.mcp_calls > 0:
            parts.append(f"called {stats.mcp_calls} MCP tool(s)")

        if parts:
            self.slack.post(f"Done: {', '.join(parts)}")

        # Stats
        stat_parts = []
        if stats.duration_ms:
            stat_parts.append(f"{stats.duration_ms / 1000:.1f}s")
        if stats.cost_usd:
            stat_parts.append(f"${stats.cost_usd:.4f}")
        if stats.input_tokens or stats.output_tokens:
            stat_parts.append(f"{stats.input_tokens:,} in / {stats.output_tokens:,} out tokens")

        if stat_parts:
            self.slack.post(f"_Stats: {' | '.join(stat_parts)}_")

    def _finish_reactions(self, success: bool) -> None:
        """Update reactions on the original message."""
        self.slack.remove_reaction(self.message_ts, "hourglass_flowing_sand")
        emoji = "white_check_mark" if success else "x"
        self.slack.add_reaction(self.message_ts, emoji)
