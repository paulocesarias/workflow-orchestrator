"""Slack webhook handlers."""

import hashlib
import hmac
import re
import time
import uuid

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, Response

from orchestrator.api.metrics import RATE_LIMIT_HITS, REQUEST_COUNT
from orchestrator.config import get_bot_config, get_settings
from orchestrator.models.slack import SlackEvent, SlackFile
from orchestrator.utils.rate_limit import RateLimiter

router = APIRouter(prefix="/webhooks/slack", tags=["slack"])
logger = structlog.get_logger()

# Rate limiter: 10 requests per 60 seconds per user
rate_limiter = RateLimiter(max_requests=10, window_seconds=60)

# Supported file types for Claude
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS

# Explicit blocklist for message subtypes to filter
FILTERED_SUBTYPES = {
    "bot_message",
    "message_changed",
    "message_deleted",
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
    "channel_name",
    "channel_archive",
    "channel_unarchive",
    "group_join",
    "group_leave",
    "group_topic",
    "group_purpose",
    "group_name",
    "group_archive",
    "group_unarchive",
    "file_comment",
    "file_mention",
    "pinned_item",
    "unpinned_item",
}


def generate_session_id(team_id: str, channel_id: str, thread_ts: str | None) -> str:
    """Generate deterministic session UUID from Slack context.

    This ensures conversation continuity - same channel/thread always gets same session.
    """
    key = f"{team_id}-{channel_id}-{thread_ts or 'main'}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))


def verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    signing_secret: str,
) -> bool:
    """Verify Slack request signature for security."""
    if not signing_secret:
        logger.warning("No Slack signing secret configured, skipping verification")
        return True

    # Check timestamp to prevent replay attacks (allow 5 minute window)
    try:
        request_time = int(timestamp)
        current_time = int(time.time())
        if abs(current_time - request_time) > 300:
            logger.warning("Slack request timestamp too old", diff=abs(current_time - request_time))
            return False
    except ValueError:
        return False

    # Compute expected signature
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected_sig = "v0=" + hmac.new(
        signing_secret.encode(),
        sig_basestring.encode(),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_sig, signature)


def should_process_message(event_data: dict) -> tuple[bool, str]:
    """Determine if a message should be processed.

    Returns (should_process, reason).
    """
    subtype = event_data.get("subtype")

    # Filter by subtype blocklist
    if subtype in FILTERED_SUBTYPES:
        return False, f"filtered_subtype:{subtype}"

    # Filter bot messages by bot_id
    if event_data.get("bot_id"):
        return False, "bot_message"

    # Allow file_share events (for image/PDF uploads)
    if subtype == "file_share":
        return True, "file_share"

    # Allow regular messages (no subtype)
    if subtype is None:
        return True, "user_message"

    # Filter any other subtypes we haven't explicitly allowed
    return False, f"unknown_subtype:{subtype}"


def extract_files(files_data: list[dict]) -> list[SlackFile]:
    """Extract and validate file attachments."""
    valid_files = []

    for file_data in files_data[:5]:  # Limit to 5 files
        try:
            name = file_data.get("name", "")
            ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""

            # Only process supported file types
            if ext not in SUPPORTED_EXTENSIONS:
                logger.debug("Skipping unsupported file type", filename=name, ext=ext)
                continue

            # Check file size (10MB limit)
            size = file_data.get("size", 0)
            if size > 10 * 1024 * 1024:
                logger.warning("File too large, skipping", filename=name, size=size)
                continue

            valid_files.append(
                SlackFile(
                    id=file_data.get("id", ""),
                    name=name,
                    mimetype=file_data.get("mimetype", ""),
                    url_private=file_data.get("url_private", ""),
                    size=size,
                )
            )
        except Exception as e:
            logger.warning("Failed to parse file", error=str(e))

    return valid_files


def strip_mentions(text: str) -> str:
    """Remove @mentions from message text."""
    # Remove <@USERID> mentions
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


@router.post("")
async def slack_webhook(
    request: Request,
    x_slack_request_timestamp: str = Header(default="", alias="X-Slack-Request-Timestamp"),
    x_slack_signature: str = Header(default="", alias="X-Slack-Signature"),
) -> Response:
    """Handle incoming Slack events.

    Must return 200 within 3 seconds or Slack will retry.
    Actual processing is done asynchronously via Celery.
    """
    body_bytes = await request.body()
    body = await request.json()
    settings = get_settings()

    # Verify Slack signature (security)
    if settings.slack_signing_secret and not verify_slack_signature(
        body_bytes,
        x_slack_request_timestamp,
        x_slack_signature,
        settings.slack_signing_secret,
    ):
        logger.warning("Invalid Slack signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Handle URL verification challenge
    if body.get("type") == "url_verification":
        logger.info("Slack URL verification challenge")
        return Response(content=body.get("challenge", ""), media_type="text/plain")

    # Parse event
    event_data = body.get("event", {})
    event_type = event_data.get("type")

    # Only handle message events
    if event_type != "message":
        logger.debug("Ignoring non-message event", event_type=event_type)
        return Response(status_code=200)

    # Check if message should be processed
    should_process, reason = should_process_message(event_data)
    if not should_process:
        logger.debug("Filtered message", reason=reason)
        return Response(status_code=200)

    # Extract user info for rate limiting
    user_id = event_data.get("user", "")
    channel_id = event_data.get("channel", "")

    if not user_id:
        logger.warning("No user_id in message event")
        return Response(status_code=200)

    # Get bot configuration for this channel
    bot_config = get_bot_config(channel_id)
    if bot_config is None:
        logger.warning("No bot configured for channel", channel_id=channel_id)
        return Response(status_code=200)

    # Rate limiting
    rate_key = f"{channel_id}:{user_id}"
    if not rate_limiter.is_allowed(rate_key, bot_name=bot_config.name):
        logger.warning("Rate limited", user=user_id, channel=channel_id, bot=bot_config.name)
        RATE_LIMIT_HITS.labels(bot=bot_config.name, user=user_id).inc()
        # Still return 200 to Slack, but don't process
        return Response(status_code=200)

    # Extract and clean message text
    raw_text = event_data.get("text", "")
    text = strip_mentions(raw_text)

    # Skip empty messages (after stripping mentions)
    if not text and not event_data.get("files"):
        logger.debug("Empty message after processing")
        return Response(status_code=200)

    # Extract file attachments
    files = extract_files(event_data.get("files", []))

    # Parse Slack event
    try:
        event = SlackEvent(
            team_id=body.get("team_id", ""),
            channel_id=channel_id,
            user_id=user_id,
            text=text,
            ts=event_data.get("ts", ""),
            thread_ts=event_data.get("thread_ts"),
            files=[f.model_dump() for f in files],
        )
    except Exception as e:
        logger.error("Failed to parse Slack event", error=str(e))
        return Response(status_code=200)

    # Generate session ID for Claude conversation continuity
    session_id = generate_session_id(event.team_id, event.channel_id, event.thread_ts)

    logger.info(
        "Processing Slack message",
        bot_name=bot_config.name,
        channel=event.channel_id,
        user=event.user_id,
        session_id=session_id,
        working_dir=bot_config.working_dir,
        has_files=len(files) > 0,
        file_count=len(files),
        text_length=len(text),
    )

    # Track request for this bot
    REQUEST_COUNT.labels(bot=bot_config.name, endpoint="/webhooks/slack", status="2xx").inc()

    # Enqueue Celery task for async processing
    from orchestrator.tasks.slack import process_slack_message

    process_slack_message.delay(
        event=event.model_dump(),
        session_id=session_id,
        working_dir=bot_config.working_dir,
        bot_name=bot_config.name,
    )

    # Return 200 immediately (Slack expects response within 3 seconds)
    return Response(status_code=200)
