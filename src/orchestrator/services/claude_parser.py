"""Claude stream-json output parser."""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator

import structlog

logger = structlog.get_logger()


class EventType(str, Enum):
    """Claude stream event types."""

    SYSTEM = "system"
    ASSISTANT = "assistant"
    USER = "user"
    RESULT = "result"


class ToolName(str, Enum):
    """Common tool names for tracking."""

    READ = "Read"
    EDIT = "Edit"
    WRITE = "Write"
    BASH = "Bash"
    GLOB = "Glob"
    GREP = "Grep"
    WEB_FETCH = "WebFetch"
    WEB_SEARCH = "WebSearch"
    TASK = "Task"
    TODO_WRITE = "TodoWrite"


@dataclass
class ToolUse:
    """Represents a tool use event."""

    id: str
    name: str
    input: dict
    file_path: str | None = None


@dataclass
class ToolResult:
    """Represents a tool result."""

    tool_use_id: str
    content: str | None = None
    is_error: bool = False


@dataclass
class ClaudeStats:
    """Statistics from Claude execution."""

    duration_ms: int = 0
    duration_api_ms: int = 0
    num_turns: int = 0
    total_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass
class ParsedEvent:
    """Parsed event from Claude stream."""

    type: EventType
    text: str | None = None
    tool_use: ToolUse | None = None
    tool_result: ToolResult | None = None
    stats: ClaudeStats | None = None
    is_final: bool = False
    is_error: bool = False
    session_id: str | None = None


@dataclass
class StreamState:
    """State accumulated during stream parsing."""

    session_id: str | None = None
    current_text: str = ""
    tool_notifications: list[str] = field(default_factory=list)
    files_read: int = 0
    files_edited: int = 0
    files_written: int = 0
    commands_run: int = 0
    searches: int = 0
    stats: ClaudeStats | None = None
    is_complete: bool = False
    is_error: bool = False
    error_message: str | None = None


class ClaudeStreamParser:
    """Parser for Claude CLI stream-json output.

    Parses newline-delimited JSON events and tracks state.
    """

    def __init__(self):
        self.state = StreamState()

    def parse_line(self, line: str) -> ParsedEvent | None:
        """Parse a single line of stream output.

        Args:
            line: Raw line from Claude output

        Returns:
            ParsedEvent or None if line should be skipped
        """
        line = line.strip()
        if not line:
            return None

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON line", line=line[:100])
            return None

        event_type = data.get("type")

        if event_type == EventType.SYSTEM:
            return self._parse_system_event(data)
        elif event_type == EventType.ASSISTANT:
            return self._parse_assistant_event(data)
        elif event_type == EventType.USER:
            return self._parse_user_event(data)
        elif event_type == EventType.RESULT:
            return self._parse_result_event(data)
        else:
            logger.debug("Unknown event type", event_type=event_type)
            return None

    def _parse_system_event(self, data: dict) -> ParsedEvent:
        """Parse system event (init)."""
        subtype = data.get("subtype")
        session_id = data.get("session_id")

        if subtype == "init" and session_id:
            self.state.session_id = session_id

        return ParsedEvent(
            type=EventType.SYSTEM,
            session_id=session_id,
        )

    def _parse_assistant_event(self, data: dict) -> ParsedEvent:
        """Parse assistant message event."""
        message = data.get("message", {})
        content = message.get("content", [])
        session_id = data.get("session_id")

        if session_id:
            self.state.session_id = session_id

        text_parts = []
        tool_use = None

        for block in content:
            block_type = block.get("type")

            if block_type == "text":
                text = block.get("text", "")
                text_parts.append(text)

            elif block_type == "tool_use":
                tool_use = ToolUse(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    input=block.get("input", {}),
                )
                # Extract file path for common tools
                tool_use.file_path = self._extract_file_path(tool_use)
                # Track tool notification
                self._track_tool_use(tool_use)

        full_text = "".join(text_parts)
        if full_text:
            self.state.current_text = full_text

        return ParsedEvent(
            type=EventType.ASSISTANT,
            text=full_text if full_text else None,
            tool_use=tool_use,
            session_id=session_id,
        )

    def _parse_user_event(self, data: dict) -> ParsedEvent:
        """Parse user/tool result event."""
        message = data.get("message", {})
        content = message.get("content", [])

        tool_result = None
        for block in content:
            if block.get("type") == "tool_result":
                tool_result = ToolResult(
                    tool_use_id=block.get("tool_use_id", ""),
                    is_error=block.get("is_error", False),
                )

        return ParsedEvent(
            type=EventType.USER,
            tool_result=tool_result,
        )

    def _parse_result_event(self, data: dict) -> ParsedEvent:
        """Parse final result event."""
        subtype = data.get("subtype")
        is_error = data.get("is_error", False) or subtype == "error"
        result_text = data.get("result", "")
        session_id = data.get("session_id")

        # Extract stats
        usage = data.get("usage", {})
        stats = ClaudeStats(
            duration_ms=data.get("duration_ms", 0),
            duration_api_ms=data.get("duration_api_ms", 0),
            num_turns=data.get("num_turns", 0),
            total_cost_usd=data.get("total_cost_usd", 0.0),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        )

        self.state.stats = stats
        self.state.is_complete = True
        self.state.is_error = is_error

        if is_error:
            self.state.error_message = result_text

        return ParsedEvent(
            type=EventType.RESULT,
            text=result_text,
            stats=stats,
            is_final=True,
            is_error=is_error,
            session_id=session_id,
        )

    def _extract_file_path(self, tool_use: ToolUse) -> str | None:
        """Extract file path from tool input."""
        input_data = tool_use.input

        if tool_use.name in [ToolName.READ.value, ToolName.EDIT.value, ToolName.WRITE.value]:
            return input_data.get("file_path")
        elif tool_use.name == ToolName.BASH.value:
            return input_data.get("command", "")[:50]
        elif tool_use.name in [ToolName.GLOB.value, ToolName.GREP.value]:
            return input_data.get("pattern", "")
        elif tool_use.name == ToolName.WEB_FETCH.value:
            return input_data.get("url", "")[:50]
        elif tool_use.name == ToolName.WEB_SEARCH.value:
            return input_data.get("query", "")[:50]

        return None

    def _track_tool_use(self, tool_use: ToolUse) -> None:
        """Track tool usage for summary."""
        name = tool_use.name
        file_path = tool_use.file_path

        if name == ToolName.READ.value:
            self.state.files_read += 1
            notification = f"_Reading {file_path}_" if file_path else "_Reading file_"
        elif name == ToolName.EDIT.value:
            self.state.files_edited += 1
            notification = f"_Editing {file_path}_" if file_path else "_Editing file_"
        elif name == ToolName.WRITE.value:
            self.state.files_written += 1
            notification = f"_Writing {file_path}_" if file_path else "_Writing file_"
        elif name == ToolName.BASH.value:
            self.state.commands_run += 1
            notification = f"_Running: {file_path}_" if file_path else "_Running command_"
        elif name in [ToolName.GLOB.value, ToolName.GREP.value]:
            self.state.searches += 1
            notification = f"_Searching: {file_path}_" if file_path else "_Searching_"
        elif name == ToolName.WEB_FETCH.value:
            notification = f"_Fetching: {file_path}_" if file_path else "_Fetching URL_"
        elif name == ToolName.WEB_SEARCH.value:
            notification = f"_Searching web: {file_path}_" if file_path else "_Web search_"
        elif name == ToolName.TASK.value:
            notification = "_Launching agent_"
        else:
            notification = f"_Using {name}_"

        self.state.tool_notifications.append(notification)

    def get_summary(self) -> str:
        """Generate summary of actions taken."""
        parts = []

        if self.state.files_read:
            parts.append(f"read {self.state.files_read} file{'s' if self.state.files_read > 1 else ''}")
        if self.state.files_edited:
            parts.append(f"edited {self.state.files_edited} file{'s' if self.state.files_edited > 1 else ''}")
        if self.state.files_written:
            parts.append(f"wrote {self.state.files_written} file{'s' if self.state.files_written > 1 else ''}")
        if self.state.commands_run:
            parts.append(f"ran {self.state.commands_run} command{'s' if self.state.commands_run > 1 else ''}")
        if self.state.searches:
            parts.append(f"{self.state.searches} search{'es' if self.state.searches > 1 else ''}")

        if not parts:
            return ""

        return "Done: " + ", ".join(parts)

    def get_stats_line(self) -> str:
        """Generate stats line for display."""
        if not self.state.stats:
            return ""

        stats = self.state.stats
        duration_s = stats.duration_ms / 1000

        parts = [f"{duration_s:.1f}s"]

        if stats.total_cost_usd > 0:
            parts.append(f"${stats.total_cost_usd:.4f}")

        total_tokens = stats.input_tokens + stats.output_tokens
        if total_tokens > 0:
            parts.append(f"{total_tokens:,} tokens")

        return " | ".join(parts)

    def parse_stream(self, lines: Iterator[str]) -> Iterator[ParsedEvent]:
        """Parse a stream of lines.

        Args:
            lines: Iterator of raw lines

        Yields:
            ParsedEvent for each meaningful line
        """
        for line in lines:
            event = self.parse_line(line)
            if event:
                yield event


def format_response_with_tools(text: str, tool_notifications: list[str]) -> str:
    """Format response text with inline tool notifications.

    Args:
        text: Main response text
        tool_notifications: List of tool notification strings

    Returns:
        Formatted message with tools inline
    """
    if not tool_notifications:
        return text

    # Insert tool notifications inline
    # For simplicity, append them at the end with newline separation
    tools_section = "\n".join(tool_notifications)
    return f"{text}\n\n{tools_section}"
