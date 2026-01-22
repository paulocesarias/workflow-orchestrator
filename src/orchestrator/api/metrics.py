"""Prometheus metrics endpoint."""

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)

router = APIRouter(tags=["metrics"])

# Metrics
REQUEST_COUNT = Counter(
    "orchestrator_requests_total",
    "Total number of requests",
    ["bot", "endpoint", "status"],
)

TASK_DURATION = Histogram(
    "orchestrator_task_duration_seconds",
    "Task processing duration in seconds",
    ["bot", "task_type"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

RATE_LIMIT_HITS = Counter(
    "orchestrator_rate_limit_hits_total",
    "Number of rate limit hits",
    ["bot", "user"],
)

SLACK_API_CALLS = Counter(
    "orchestrator_slack_api_calls_total",
    "Total Slack API calls",
    ["method", "status"],
)


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
