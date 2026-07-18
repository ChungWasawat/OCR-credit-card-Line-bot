from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
)
from linebot.v3.messaging.exceptions import ApiException

logger = logging.getLogger(__name__)

# Line's free "Communication" plan push-message quota — unverified against this
# account's actual plan; check the Line Developer Console before relying on it.
_PUSH_QUOTA_NOTE = "unverified — check Line Developer Console for this account's plan"

_push_count = 0


@dataclass(frozen=True)
class Reply:
    reply_token: str
    group_id: str
    messages: list


def push_count() -> int:
    return _push_count


def default_messaging_api() -> MessagingApi:
    config = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
    return MessagingApi(ApiClient(config))


def _is_invalid_reply_token(exc: ApiException) -> bool:
    body = exc.body
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    return "invalid reply token" in (body or "").lower()


def send(api: MessagingApi, reply: Reply) -> None:
    """Tries the reply token first; falls back to Push API to the group only on Line's
    400 "Invalid reply token" — covers both the ~1-min expiry and an already-used
    token. Any other error is re-raised: 429/5xx are not token problems and must reach
    Task 9's retry/alert machinery, and a 400 with any OTHER message means the message
    payload itself is malformed — pushing the same payload would just 400 again, and
    retrying it hides a deterministic bug behind retry noise.
    """
    try:
        api.reply_message(
            ReplyMessageRequest(reply_token=reply.reply_token, messages=reply.messages),
            _request_timeout=10,
        )
    except ApiException as exc:
        if exc.status != 400 or not _is_invalid_reply_token(exc):
            raise
        logger.warning(
            "reply token invalid, falling back to push",
            extra={"group_id": reply.group_id},
        )
        api.push_message(
            PushMessageRequest(to=reply.group_id, messages=reply.messages),
            _request_timeout=10,
        )
        global _push_count
        _push_count += 1
        logger.info(
            "push fallback used",
            extra={
                "group_id": reply.group_id,
                "push_total": _push_count,
                "note": _PUSH_QUOTA_NOTE,
            },
        )
