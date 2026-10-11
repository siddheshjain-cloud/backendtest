from functools import wraps

from flask import jsonify

from app import db


class ResearchValidationError(Exception):
    code = "validation_error"

    def __init__(self, details: dict[str, list[str]]):
        super().__init__("Request validation failed")
        self.details = details


class ResearchConflictError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ResearchNotFoundError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ResearchForbiddenError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_DOMAIN_ERROR_STATUS = (
    (ResearchValidationError, 400),
    (ResearchForbiddenError, 403),
    (ResearchNotFoundError, 404),
    (ResearchConflictError, 409),
)


def research_error_response(error: Exception) -> tuple[dict, int]:
    """Return the stable research payload and HTTP status for a domain error."""

    for error_class, status in _DOMAIN_ERROR_STATUS:
        if isinstance(error, error_class):
            return (
                {
                    "error": error.code,
                    "message": str(error),
                    "details": getattr(error, "details", {}),
                },
                status,
            )

    raise TypeError(f"Unsupported research error: {type(error).__name__}")


def research_error_boundary(view):
    """Translate research domain errors into the stable research payload.

    Applied inside the administrative decorator so the legacy decorator cannot
    convert a downstream domain error into a token error. Unexpected errors are
    rolled back and reported without leaking exception text.
    """

    @wraps(view)
    def boundary(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except (
            ResearchValidationError,
            ResearchConflictError,
            ResearchNotFoundError,
            ResearchForbiddenError,
        ) as error:
            db.session.rollback()
            payload, status = research_error_response(error)
            return jsonify(payload), status
        except Exception:
            db.session.rollback()
            return (
                jsonify(
                    {
                        "error": "internal_error",
                        "message": "Internal server error",
                        "details": {},
                    }
                ),
                500,
            )

    return boundary
