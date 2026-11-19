"""
API Error Handlers (app/api/error_handlers.py)
Translates exceptions into standardized JSON responses without leaking internal details.
"""

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.core.exceptions import AppError, InternalError
from app.core.logging import logger, request_id_ctx


def _format_error_response(
    request: Request,
    code: str,
    message: str,
    http_status: int,
    retryable: bool = False,
    retry_after: int | None = None,
    status_str: str = "error",
) -> JSONResponse:
    req_id = getattr(request.state, "request_id", None) or request_id_ctx.get() or "unknown"
    err_body = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if retry_after is not None:
        err_body["retry_after"] = retry_after

    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)

    return JSONResponse(
        status_code=http_status,
        content={
            "request_id": req_id,
            "status": status_str,
            "error": err_body,
            "detail": message,
        },
        headers=headers,
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return _format_error_response(
        request=request,
        code=exc.code,
        message=exc.message,
        http_status=exc.http_status,
        retryable=exc.retryable,
        retry_after=exc.retry_after,
        status_str=getattr(exc, "status", "error"),
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _format_error_response(
        request=request,
        code="VALIDATION_ERROR",
        message="The request payload is invalid.",
        http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        retryable=False,
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code_map = {
        401: "UNAUTHENTICATED",
        403: "FORBIDDEN_ROLE",
        404: "NOT_FOUND",
        409: "TENANT_EXISTS",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        503: "LLM_UNAVAILABLE",
    }
    code = code_map.get(exc.status_code, "INTERNAL_ERROR")
    msg = str(exc.detail) if exc.status_code < 500 else "A server error occurred."
    return _format_error_response(
        request=request,
        code=code,
        message=msg,
        http_status=exc.status_code,
        retryable=(exc.status_code in [429, 503]),
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled server exception encountered")
    return _format_error_response(
        request=request,
        code="INTERNAL_ERROR",
        message="An internal error occurred.",
        http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        retryable=False,
    )


def register_error_handlers(app: FastAPI):
    """Register all custom error handlers with the FastAPI application."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
