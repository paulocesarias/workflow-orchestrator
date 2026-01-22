"""Prometheus metrics endpoint."""

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
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

CLAUDE_TOKENS = Counter(
    "orchestrator_claude_tokens_total",
    "Total Claude tokens used",
    ["bot", "direction"],  # direction: input or output
)

CLAUDE_COST = Counter(
    "orchestrator_claude_cost_usd_total",
    "Total Claude cost in USD",
    ["bot"],
)

ACTIVE_TASKS = Gauge(
    "orchestrator_active_tasks",
    "Number of currently active tasks",
    ["bot"],
)


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
