"""Task management endpoints for testing."""

from fastapi import APIRouter
import structlog

from orchestrator.tasks.sample import add, process_message

router = APIRouter(prefix="/tasks", tags=["tasks"])
logger = structlog.get_logger()


@router.post("/add")
async def queue_add_task(x: int, y: int) -> dict:
    """Queue an add task for testing.
    
    Args:
        x: First number
        y: Second number
        
    Returns:
        Task ID for tracking
    """
    result = add.delay(x, y)
    logger.info("Queued add task", task_id=result.id, x=x, y=y)
    return {"task_id": result.id, "status": "queued"}


@router.post("/process")
async def queue_process_task(message: str, channel_id: str, thread_ts: str | None = None) -> dict:
    """Queue a message processing task.
    
    Args:
        message: Message to process
        channel_id: Slack channel ID
        thread_ts: Optional thread timestamp
        
    Returns:
        Task ID for tracking
    """
    result = process_message.delay(message, channel_id, thread_ts)
    logger.info("Queued process task", task_id=result.id, channel_id=channel_id)
    return {"task_id": result.id, "status": "queued"}


@router.get("/{task_id}")
async def get_task_status(task_id: str) -> dict:
    """Get the status of a queued task.
    
    Args:
        task_id: The Celery task ID
        
    Returns:
        Task status and result if available
    """
    from orchestrator.celery_app import celery_app
    
    result = celery_app.AsyncResult(task_id)
    
    response = {
        "task_id": task_id,
        "status": result.status,
    }
    
    if result.ready():
        response["result"] = result.result
    
    return response
