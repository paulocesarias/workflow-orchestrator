"""Slack event models."""

from pydantic import BaseModel, Field


class SlackFile(BaseModel):
    """Slack file attachment."""

    id: str
    name: str
    mimetype: str
    url_private: str
    size: int = 0


class SlackEvent(BaseModel):
    """Slack message event."""

    team_id: str
    channel_id: str
    user_id: str
    text: str
    ts: str
    thread_ts: str | None = None
    files: list[dict] = Field(default_factory=list)

    @property
    def reply_ts(self) -> str:
        """Get the timestamp to reply to (thread or original message)."""
        return self.thread_ts or self.ts

    @property
    def is_thread(self) -> bool:
        """Check if message is in a thread."""
        return self.thread_ts is not None
