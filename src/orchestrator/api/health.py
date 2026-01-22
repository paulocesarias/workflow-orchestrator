"""Health check endpoints."""

from datetime import datetime, timezone

import redis
import structlog
from fastapi import APIRouter, Response

from orchestrator.config import get_settings

router = APIRouter(tags=["health"])
logger = structlog.get_logger()


def check_redis() -> bool:
    """Check Redis connectivity."""
    try:
        settings = get_settings()
        r = redis.from_url(settings.celery_broker_url)
        r.ping()
        return True
    except Exception as e:
        logger.warning("Redis health check failed", error=str(e))
        return False


@router.get("/health")
async def health_check() -> dict:
    """Liveness probe - is the app running?"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
async def readiness_check() -> dict:
    """Readiness probe - is the app ready to serve traffic?"""
    redis_ok = check_redis()
    status = "ready" if redis_ok else "not_ready"

    return {
        "status": status,
        "checks": {
            "redis": "ok" if redis_ok else "failed",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/live")
async def liveness() -> dict:
    """Kubernetes liveness probe."""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness() -> dict:
    """Kubernetes readiness probe."""
    redis_ok = check_redis()
    status = "ready" if redis_ok else "not_ready"

    return {
        "status": status,
        "checks": {
            "redis": "ok" if redis_ok else "failed",
        },
    }
