from __future__ import annotations

_AUTH_STATUSES = frozenset({401, 403})
_RATE_LIMIT_STATUSES = frozenset({429})


def classify_exception(exc: BaseException) -> str:
    """Coarse failure category for a log line's error_type field — first int-valued
    status_code/code/status attribute found wins (covers anthropic/openai/
    googleapiclient HttpError, google.genai/api_core, and linebot ApiException
    respectively, in that order); grpc's callable .code() is skipped since it isn't
    an int. Falls back to a class-name heuristic for connection/timeout errors that
    carry no status at all (httpx timeouts don't subclass TimeoutError).
    """
    status = None
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            status = value
            break

    if status is not None:
        if status in _AUTH_STATUSES:
            return "auth"
        if status in _RATE_LIMIT_STATUSES:
            return "rate_limit"
        if 500 <= status < 600:
            return "provider_error"
        if 400 <= status < 500:
            return "client_error"
        return "unknown"

    if isinstance(exc, TimeoutError) or any(
        s in type(exc).__name__ for s in ("Timeout", "Connection")
    ):
        return "network"

    return "unknown"
