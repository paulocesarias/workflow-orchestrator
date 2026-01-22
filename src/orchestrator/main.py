"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from orchestrator.api.health import router as health_router
from orchestrator.api.metrics import router as metrics_router
from orchestrator.api.tasks import router as tasks_router
from orchestrator.api.webhooks.slack import router as slack_router
from orchestrator.config import get_settings
from orchestrator.middleware.error_handler import ErrorHandlerMiddleware
from orchestrator.utils.logging import setup_logging

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    settings = get_settings()
    setup_logging(log_level=settings.log_level, json_logs=not settings.debug)

    logger.info(
        "Starting application",
        app_name=settings.app_name,
        debug=settings.debug,
    )

    yield

    logger.info("Shutting down application")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Workflow Orchestrator",
        description="Custom workflow orchestration for Slack bot automation",
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # Add middleware
    app.add_middleware(ErrorHandlerMiddleware)

    # Include routers
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(tasks_router)
    app.include_router(slack_router)

    return app


app = create_app()
