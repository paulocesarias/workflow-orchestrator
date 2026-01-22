"""Error handling middleware for FastAPI."""

import time
import traceback
from typing import Callable

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger()


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Middleware for catching and logging unhandled exceptions."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and handle any unhandled exceptions."""
        start_time = time.perf_counter()
        request_id = request.headers.get("X-Request-ID", "")
        
        # Add request context to logger
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )
        
        try:
            response = await call_next(request)
            
            # Log successful request
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.info(
                "Request completed",
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )
            
            return response
            
        except Exception as exc:
            # Log the full exception
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "Unhandled exception",
                error=str(exc),
                error_type=type(exc).__name__,
                traceback=traceback.format_exc(),
                duration_ms=round(duration_ms, 2),
            )
            
            # Return a generic error response
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal server error",
                    "request_id": request_id,
                },
            )
        finally:
            # Clear context vars
            structlog.contextvars.unbind_contextvars("request_id", "path", "method")
