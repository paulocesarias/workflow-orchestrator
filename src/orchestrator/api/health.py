"""Health check endpoints."""

from fastapi import APIRouter, Response

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Liveness probe - is the app running?"""
    return {"status": "healthy"}


@router.get("/ready")
async def readiness_check() -> dict[str, str]:
    """Readiness probe - is the app ready to serve traffic?"""
    # TODO: Add Redis connectivity check
    return {"status": "ready"}


@router.get("/health/live")
async def liveness() -> Response:
    """Kubernetes liveness probe."""
    return Response(status_code=200)


@router.get("/health/ready")
async def readiness() -> Response:
    """Kubernetes readiness probe."""
    # TODO: Add dependency checks
    return Response(status_code=200)
