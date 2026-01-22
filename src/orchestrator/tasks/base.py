"""Base Celery task with common functionality."""

import structlog
from celery import Task

logger = structlog.get_logger()


class BaseTask(Task):
    """Base task with automatic retry and logging."""

    autoretry_for = (Exception,)
    retry_backoff = True
    retry_backoff_max = 600  # 10 minutes max
    retry_jitter = True
    max_retries = 3

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Log task failure."""
        logger.error(
            "Task failed",
            task_id=task_id,
            task_name=self.name,
            error=str(exc),
            args=args,
        )

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Log task retry."""
        logger.warning(
            "Task retrying",
            task_id=task_id,
            task_name=self.name,
            error=str(exc),
            retry_count=self.request.retries,
        )

    def on_success(self, retval, task_id, args, kwargs):
        """Log task success."""
        logger.info(
            "Task completed",
            task_id=task_id,
            task_name=self.name,
        )
