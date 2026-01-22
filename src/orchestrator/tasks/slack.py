"""Slack message processing tasks."""

import structlog
from celery import shared_task

from orchestrator.api.metrics import ACTIVE_TASKS, TASK_DURATION
from orchestrator.config import get_settings
from orchestrator.services.ssh_executor import SSHExecutor
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
    """Process an incoming Slack message by executing Claude on remote host.

    This mirrors the n8n approach:
    1. SSH to the VPS
    2. Run claude-streamer.py with the message
    3. The script handles all Claude interaction and Slack streaming

    Args:
        event: Slack event data (channel_id, user_id, text, ts, thread_ts, files)
        session_id: Deterministic session UUID for conversation continuity
        working_dir: Working directory for Claude CLI execution on remote host
        bot_name: Name of the bot for metrics tracking

    Returns:
        Processing result dictionary
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

    # Determine SSH user from working directory
    # e.g., /home/paulo -> paulo, /home/popy -> popy
    ssh_user = working_dir.split("/")[-1] if working_dir.startswith("/home/") else "paulo"

    logger.info(
        "Processing Slack message via SSH",
        task_id=self.request.id,
        bot_name=bot_name,
        channel_id=channel_id,
        user_id=user_id,
        session_id=session_id,
        text_length=len(text),
        file_count=len(files),
        is_thread=thread_ts is not None,
        working_dir=working_dir,
        ssh_user=ssh_user,
    )

    # Track active task
    ACTIVE_TASKS.labels(bot=bot_name).inc()

    try:
        # Create SSH executor and run
        executor = SSHExecutor(user=ssh_user)

        result = executor.execute_claude_streamer(
            slack_token=slack_token,
            channel=channel_id,
            thread_ts=reply_ts,
            message_ts=ts,
            session_id=session_id,
            message=text,
            working_dir=working_dir,
            files=files if files else None,
        )

        # Track duration metric
        TASK_DURATION.labels(bot=bot_name, task_type="claude_message").observe(
            result.duration_ms / 1000
        )

        response = {
            "status": "completed" if result.success else "error",
            "channel_id": channel_id,
            "user_id": user_id,
            "session_id": session_id,
            "reply_ts": reply_ts,
            "bot_name": bot_name,
            "duration_ms": result.duration_ms,
        }

        if result.error:
            response["error"] = result.error

        logger.info("Slack message processed via SSH", result=response)
        return response

    finally:
        ACTIVE_TASKS.labels(bot=bot_name).dec()
