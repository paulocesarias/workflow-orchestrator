"""Slack webhook handlers."""

import hashlib
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Request, Response

from orchestrator.models.slack import SlackEvent

router = APIRouter(prefix="/webhooks/slack", tags=["slack"])
logger = structlog.get_logger()


def generate_session_id(team_id: str, channel_id: str, thread_ts: str | None) -> str:
    """Generate deterministic session UUID from Slack context."""
    # Use thread_ts if in a thread, otherwise use a unique value per message
    key = f"{team_id}-{channel_id}-{thread_ts or 'main'}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))


@router.post("")
async def slack_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Handle incoming Slack events."""
    body = await request.json()

    # Handle URL verification challenge
    if body.get("type") == "url_verification":
        return Response(content=body.get("challenge", ""), media_type="text/plain")

    # Parse event
    event_data = body.get("event", {})
    event_type = event_data.get("type")

    if event_type != "message":
        return Response(status_code=200)

    # Filter bot messages
    if event_data.get("bot_id") or event_data.get("subtype") in [
        "bot_message",
        "message_changed",
        "message_deleted",
    ]:
        logger.debug("Filtered bot/system message", subtype=event_data.get("subtype"))
        return Response(status_code=200)

    # Parse Slack event
    try:
        event = SlackEvent(
            team_id=body.get("team_id", ""),
            channel_id=event_data.get("channel", ""),
            user_id=event_data.get("user", ""),
            text=event_data.get("text", ""),
            ts=event_data.get("ts", ""),
            thread_ts=event_data.get("thread_ts"),
            files=event_data.get("files", []),
        )
    except Exception as e:
        logger.error("Failed to parse Slack event", error=str(e))
        return Response(status_code=200)

    # Generate session ID for Claude conversation continuity
    session_id = generate_session_id(event.team_id, event.channel_id, event.thread_ts)

    logger.info(
        "Received Slack message",
        channel=event.channel_id,
        user=event.user_id,
        session_id=session_id,
        has_files=len(event.files) > 0,
    )

    # TODO: Enqueue Celery task for processing
    # background_tasks.add_task(process_message, event, session_id)

    # Return 200 immediately (Slack expects response within 3 seconds)
    return Response(status_code=200)
