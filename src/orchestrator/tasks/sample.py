"""Sample tasks for testing Celery functionality."""

import structlog
from celery import shared_task

from orchestrator.tasks.base import BaseTask

logger = structlog.get_logger()


@shared_task(bind=True, base=BaseTask)
def add(self, x: int, y: int) -> int:
    """Simple task to verify Celery is working.

    Args:
        x: First number
        y: Second number

    Returns:
        Sum of x and y
    """
    logger.info("Executing add task", x=x, y=y, task_id=self.request.id)
    result = x + y
    logger.info("Add task completed", result=result)
    return result


@shared_task(bind=True, base=BaseTask)
def process_message(self, message: str, channel_id: str, thread_ts: str | None = None) -> dict:
    """Process a message - placeholder for Claude integration.

    Args:
        message: The message content
        channel_id: Slack channel ID
        thread_ts: Thread timestamp if replying to a thread

    Returns:
        Processing result dictionary
    """
    logger.info(
        "Processing message",
        task_id=self.request.id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        message_length=len(message),
    )

    # Placeholder - actual Claude integration will be in OPS-84
    result = {
        "status": "processed",
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "message_length": len(message),
    }

    logger.info("Message processing completed", result=result)
    return result
