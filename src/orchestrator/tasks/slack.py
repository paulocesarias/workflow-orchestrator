"""Slack message processing tasks."""

import structlog
from celery import shared_task

from orchestrator.api.metrics import ACTIVE_TASKS, CLAUDE_COST, CLAUDE_TOKENS, TASK_DURATION
from orchestrator.config import get_settings
from orchestrator.services.claude import ClaudeProcessor
from orchestrator.tasks.base import BaseTask

logger = structlog.get_logger()


@shared_task(bind=True, base=BaseTask)
def process_slack_message(
    self,
    event: dict,
    session_id: str,
    working_dir: str = "/home/paulo",
    bot_name: str = "unknown",
) -> dict:
    """Process an incoming Slack message using Claude CLI.

    Downloads file attachments, executes Claude CLI with streaming,
    and sends responses back to Slack.

    Args:
        event: Slack event data (channel_id, user_id, text, ts, thread_ts, files)
        session_id: Deterministic session UUID for conversation continuity
        working_dir: Working directory for Claude CLI execution
        bot_name: Name of the bot for metrics tracking

    Returns:
        Processing result dictionary with stats
    """
    settings = get_settings()
    slack_token = settings.slack_bot_token

    if not slack_token:
        logger.error("SLACK_BOT_TOKEN not configured")
        return {"status": "error", "error": "SLACK_BOT_TOKEN not configured"}

    channel_id = event.get("channel_id", "")
    user_id = event.get("user_id", "")
    text = event.get("text", "")
    ts = event.get("ts", "")
    thread_ts = event.get("thread_ts")
    files = event.get("files", [])

    # Use thread_ts if in thread, otherwise use message ts as thread parent
    reply_ts = thread_ts or ts

    logger.info(
        "Processing Slack message with Claude",
        task_id=self.request.id,
        bot_name=bot_name,
        channel_id=channel_id,
        user_id=user_id,
        session_id=session_id,
        text_length=len(text),
        file_count=len(files),
        is_thread=thread_ts is not None,
        working_dir=working_dir,
    )

    # Track active task
    ACTIVE_TASKS.labels(bot=bot_name).inc()

    try:
        # Create processor and run
        processor = ClaudeProcessor(
            slack_token=slack_token,
            channel=channel_id,
            thread_ts=reply_ts,
            message_ts=ts,
            session_id=session_id,
            working_dir=working_dir,
        )

        stats = processor.process(message=text, files=files)

        # Track metrics
        TASK_DURATION.labels(bot=bot_name, task_type="claude_message").observe(
            stats.duration_ms / 1000
        )
        CLAUDE_TOKENS.labels(bot=bot_name, direction="input").inc(stats.input_tokens)
        CLAUDE_TOKENS.labels(bot=bot_name, direction="output").inc(stats.output_tokens)
        CLAUDE_COST.labels(bot=bot_name).inc(stats.cost_usd)

        result = {
            "status": "completed",
            "channel_id": channel_id,
            "user_id": user_id,
            "session_id": session_id,
            "reply_ts": reply_ts,
            "bot_name": bot_name,
            "stats": {
                "duration_ms": stats.duration_ms,
                "cost_usd": stats.cost_usd,
                "input_tokens": stats.input_tokens,
                "output_tokens": stats.output_tokens,
                "reads": stats.reads,
                "edits": stats.edits,
                "writes": stats.writes,
                "commands": stats.commands,
            },
        }

        logger.info("Slack message processed with Claude", result=result)
        return result

    finally:
        ACTIVE_TASKS.labels(bot=bot_name).dec()
