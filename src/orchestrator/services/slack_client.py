"""Slack client service for sending messages and reactions."""

import time
from dataclasses import dataclass
from enum import Enum

import httpx
import structlog

logger = structlog.get_logger()

SLACK_API_BASE = "https://slack.com/api"


class Reaction(str, Enum):
    """Slack reaction emojis."""

    HOURGLASS = "hourglass_flowing_sand"
    CHECKMARK = "white_check_mark"
    ERROR = "x"


@dataclass
class SlackMessage:
    """Represents a Slack message."""

    channel: str
    ts: str
    text: str | None = None


class SlackClient:
    """Client for Slack API interactions.

    Handles sending messages, updating messages, and managing reactions.
    """

    def __init__(self, token: str):
        """Initialize Slack client.

        Args:
            token: Slack bot token (xoxb-...)
        """
        self.token = token
        self._client = httpx.Client(
            base_url=SLACK_API_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "SlackClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make a request to Slack API.

        Args:
            method: HTTP method
            endpoint: API endpoint (e.g., "chat.postMessage")
            **kwargs: Additional request parameters

        Returns:
            Response JSON

        Raises:
            SlackAPIError: If the API returns an error
        """
        response = self._client.request(method, endpoint, **kwargs)
        response.raise_for_status()
        data = response.json()

        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            logger.error("Slack API error", endpoint=endpoint, error=error)
            raise SlackAPIError(error)

        return data

    def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        mrkdwn: bool = True,
    ) -> SlackMessage:
        """Post a new message to a channel.

        Args:
            channel: Channel ID
            text: Message text
            thread_ts: Thread timestamp for replies
            mrkdwn: Enable markdown formatting

        Returns:
            SlackMessage with the posted message details
        """
        payload = {
            "channel": channel,
            "text": text,
            "mrkdwn": mrkdwn,
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts

        data = self._request("POST", "chat.postMessage", json=payload)

        logger.debug(
            "Posted Slack message",
            channel=channel,
            thread_ts=thread_ts,
            ts=data.get("ts"),
        )

        return SlackMessage(
            channel=data["channel"],
            ts=data["ts"],
            text=text,
        )

    def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
        mrkdwn: bool = True,
    ) -> SlackMessage:
        """Update an existing message.

        Args:
            channel: Channel ID
            ts: Message timestamp to update
            text: New message text
            mrkdwn: Enable markdown formatting

        Returns:
            SlackMessage with updated details
        """
        payload = {
            "channel": channel,
            "ts": ts,
            "text": text,
            "mrkdwn": mrkdwn,
        }

        data = self._request("POST", "chat.update", json=payload)

        logger.debug(
            "Updated Slack message",
            channel=channel,
            ts=ts,
        )

        return SlackMessage(
            channel=data["channel"],
            ts=data["ts"],
            text=text,
        )

    def add_reaction(self, channel: str, ts: str, reaction: Reaction | str) -> bool:
        """Add a reaction to a message.

        Args:
            channel: Channel ID
            ts: Message timestamp
            reaction: Reaction emoji name

        Returns:
            True if successful
        """
        if isinstance(reaction, Reaction):
            reaction = reaction.value

        payload = {
            "channel": channel,
            "timestamp": ts,
            "name": reaction,
        }

        try:
            self._request("POST", "reactions.add", json=payload)
            logger.debug("Added reaction", channel=channel, ts=ts, reaction=reaction)
            return True
        except SlackAPIError as e:
            # Ignore "already_reacted" error
            if "already_reacted" in str(e):
                return True
            raise

    def remove_reaction(self, channel: str, ts: str, reaction: Reaction | str) -> bool:
        """Remove a reaction from a message.

        Args:
            channel: Channel ID
            ts: Message timestamp
            reaction: Reaction emoji name

        Returns:
            True if successful
        """
        if isinstance(reaction, Reaction):
            reaction = reaction.value

        payload = {
            "channel": channel,
            "timestamp": ts,
            "name": reaction,
        }

        try:
            self._request("POST", "reactions.remove", json=payload)
            logger.debug("Removed reaction", channel=channel, ts=ts, reaction=reaction)
            return True
        except SlackAPIError as e:
            # Ignore "no_reaction" error
            if "no_reaction" in str(e):
                return True
            raise

    def download_file(self, url: str, dest_path: str) -> bool:
        """Download a file from Slack.

        Args:
            url: File URL (url_private from Slack)
            dest_path: Local destination path

        Returns:
            True if successful
        """
        try:
            response = self._client.get(
                url,
                headers={"Authorization": f"Bearer {self.token}"},
                follow_redirects=True,
            )
            response.raise_for_status()

            with open(dest_path, "wb") as f:
                f.write(response.content)

            logger.debug("Downloaded file", url=url, dest_path=dest_path)
            return True

        except Exception as e:
            logger.error("Failed to download file", url=url, error=str(e))
            return False


class SlackAPIError(Exception):
    """Slack API error."""

    pass


class StreamingSlackUpdater:
    """Helper for streaming updates to Slack.

    Manages message updates with rate limiting and batching.
    """

    def __init__(
        self,
        client: SlackClient,
        channel: str,
        thread_ts: str,
        update_interval: float = 1.0,
        max_message_length: int = 39000,
    ):
        """Initialize streaming updater.

        Args:
            client: SlackClient instance
            channel: Channel ID
            thread_ts: Thread timestamp for replies
            update_interval: Minimum seconds between updates
            max_message_length: Max message length before splitting
        """
        self.client = client
        self.channel = channel
        self.thread_ts = thread_ts
        self.update_interval = update_interval
        self.max_message_length = max_message_length

        self._current_message: SlackMessage | None = None
        self._last_update_time: float = 0
        self._pending_text: str = ""
        self._message_count: int = 0

    def start(self, initial_text: str = "...") -> SlackMessage:
        """Start streaming with an initial message.

        Args:
            initial_text: Initial message text (typing indicator)

        Returns:
            The created message
        """
        self._current_message = self.client.post_message(
            channel=self.channel,
            text=initial_text,
            thread_ts=self.thread_ts,
        )
        self._last_update_time = time.time()
        self._pending_text = initial_text
        self._message_count = 1
        return self._current_message

    def update(self, text: str, force: bool = False) -> None:
        """Update the current message with new text.

        Respects rate limiting unless force=True.

        Args:
            text: New message text
            force: Force update regardless of rate limit
        """
        if not self._current_message:
            self.start(text)
            return

        self._pending_text = text
        now = time.time()

        # Check if we should update (rate limiting)
        if not force and (now - self._last_update_time) < self.update_interval:
            return

        # Check if message is too long - need to split
        if len(text) > self.max_message_length:
            self._split_message(text)
            return

        try:
            self.client.update_message(
                channel=self.channel,
                ts=self._current_message.ts,
                text=text,
            )
            self._last_update_time = now
        except SlackAPIError as e:
            logger.warning("Failed to update message", error=str(e))

    def _split_message(self, text: str) -> None:
        """Split a long message into continuations.

        Args:
            text: Full message text
        """
        # Finalize current message with truncated content
        truncated = text[: self.max_message_length - 100] + "\n\n_(continued...)_"
        self.client.update_message(
            channel=self.channel,
            ts=self._current_message.ts,
            text=truncated,
        )

        # Start new message with continuation
        remaining = text[self.max_message_length - 100 :]
        continuation_header = f"_(continuation {self._message_count + 1})_\n\n"
        self._current_message = self.client.post_message(
            channel=self.channel,
            text=continuation_header + remaining,
            thread_ts=self.thread_ts,
        )
        self._message_count += 1
        self._last_update_time = time.time()

    def finalize(self, text: str | None = None, retries: int = 3) -> SlackMessage | None:
        """Finalize the message with optional final text.

        Args:
            text: Final message text (uses pending if None)
            retries: Number of retry attempts

        Returns:
            The final message or None if failed
        """
        if not self._current_message:
            return None

        final_text = text or self._pending_text

        for attempt in range(retries):
            try:
                return self.client.update_message(
                    channel=self.channel,
                    ts=self._current_message.ts,
                    text=final_text,
                )
            except SlackAPIError as e:
                logger.warning(
                    "Failed to finalize message",
                    attempt=attempt + 1,
                    error=str(e),
                )
                if attempt < retries - 1:
                    time.sleep(1)

        # Fallback: post as new message
        logger.warning("Finalize failed, posting as new message")
        return self.client.post_message(
            channel=self.channel,
            text=final_text,
            thread_ts=self.thread_ts,
        )
