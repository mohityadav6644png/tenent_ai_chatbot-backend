"""
Core Exceptions (app/core/exceptions.py)
Domain and application exceptions with structured error codes.
"""

from typing import Optional


class AppError(Exception):
    """Base application exception with error code and metadata"""

    def __init__(
        self,
        code: str = "INTERNAL_ERROR",
        message: str = "An unexpected error occurred.",
        http_status: int = 500,
        retryable: bool = False,
        retry_after: Optional[int] = None,
        status: str = "error",
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
        self.retry_after = retry_after
        self.status = status


class InvalidCredentialsError(AppError):
    def __init__(self, message: str = "Invalid credentials provided."):
        super().__init__("INVALID_CREDENTIALS", message, 401)


class UnauthenticatedError(AppError):
    def __init__(self, message: str = "Authentication required."):
        super().__init__("UNAUTHENTICATED", message, 401)


class ForbiddenRoleError(AppError):
    def __init__(self, message: str = "You do not have permission for this resource."):
        super().__init__("FORBIDDEN_ROLE", message, 403)


class CsrfFailedError(AppError):
    def __init__(self, message: str = "CSRF verification failed."):
        super().__init__("CSRF_FAILED", message, 403)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found."):
        super().__init__("NOT_FOUND", message, 404)


class TenantExistsError(AppError):
    def __init__(self, message: str = "A tenant with this name already exists."):
        super().__init__("TENANT_EXISTS", message, 409)


class UserExistsError(AppError):
    def __init__(self, message: str = "A user with this email already exists."):
        super().__init__("USER_EXISTS", message, 409)


class InviteExistsError(AppError):
    def __init__(self, message: str = "A pending invite already exists."):
        super().__init__("INVITE_EXISTS", message, 409)


class LastAdminError(AppError):
    def __init__(self, message: str = "Cannot deactivate the last remaining admin."):
        super().__init__("LAST_ADMIN", message, 409)


class BadRequestError(AppError):
    def __init__(self, message: str = "Bad request."):
        super().__init__("BAD_REQUEST", message, 400)


class ValidationError(AppError):
    def __init__(self, message: str = "Request validation failed."):
        super().__init__("VALIDATION_ERROR", message, 422)


class RateLimitedError(AppError):
    def __init__(
        self, message: str = "Rate limit exceeded.", retry_after: Optional[int] = 60
    ):
        super().__init__("RATE_LIMITED", message, 429, retryable=True, retry_after=retry_after)


class LlmUnavailableError(AppError):
    def __init__(self, message: str = "AI service is currently unavailable."):
        super().__init__("LLM_UNAVAILABLE", message, 503, retryable=True, retry_after=10)


class QueryTimeoutError(AppError):
    def __init__(self, message: str = "Query execution timed out."):
        super().__init__("QUERY_TIMEOUT", message, 200, retryable=True, retry_after=5, status="error")


class SqlValidationFailedError(AppError):
    def __init__(self, message: str = "The query could not be validated or contains forbidden operations."):
        super().__init__("SQL_VALIDATION_FAILED", message, 200, retryable=False, status="refused")


class InternalError(AppError):
    def __init__(self, message: str = "An internal error occurred."):
        super().__init__("INTERNAL_ERROR", message, 500)


# Backward-compatibility aliases
ValidationFailed = ValidationError
RateLimited = RateLimitedError
