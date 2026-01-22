"""Slack API client."""

import httpx
import structlog

from orchestrator.api.metrics import SLACK_API_CALLS

logger = structlog.get_logger()

SLACK_API_BASE = "https://slack.com/api"


class SlackClient:
    """Async Slack API client."""

    def __init__(self, token: str):
        self.token = token
        self._client = httpx.AsyncClient(
            base_url=SLACK_API_BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
    ) -> dict:
        """Post a message to a channel."""
        payload = {
            "channel": channel,
            "text": text,
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts

        response = await self._client.post("/chat.postMessage", json=payload)
        data = response.json()

        SLACK_API_CALLS.labels(method="chat.postMessage", status="ok" if data.get("ok") else "error").inc()

        if not data.get("ok"):
            logger.error("Slack API error", method="chat.postMessage", error=data.get("error"))

        return data

    async def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
    ) -> dict:
        """Update an existing message."""
        response = await self._client.post(
            "/chat.update",
            json={"channel": channel, "ts": ts, "text": text},
        )
        data = response.json()

        SLACK_API_CALLS.labels(method="chat.update", status="ok" if data.get("ok") else "error").inc()

        return data

    async def add_reaction(self, channel: str, ts: str, emoji: str) -> dict:
        """Add a reaction to a message."""
        response = await self._client.post(
            "/reactions.add",
            json={"channel": channel, "timestamp": ts, "name": emoji},
        )
        data = response.json()

        SLACK_API_CALLS.labels(method="reactions.add", status="ok" if data.get("ok") else "error").inc()

        return data

    async def remove_reaction(self, channel: str, ts: str, emoji: str) -> dict:
        """Remove a reaction from a message."""
        response = await self._client.post(
            "/reactions.remove",
            json={"channel": channel, "timestamp": ts, "name": emoji},
        )
        data = response.json()

        SLACK_API_CALLS.labels(method="reactions.remove", status="ok" if data.get("ok") else "error").inc()

        return data

    async def download_file(self, url: str) -> bytes:
        """Download a file from Slack."""
        response = await self._client.get(url)
        response.raise_for_status()
        return response.content

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
