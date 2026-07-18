from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import Depends, FastAPI, Header
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    MessagingApiBlob,
    TextMessage,
)
from pydantic import ValidationError

from app.errors import classify_exception
from app.gcs import filename_for
from app.handlers import CleanupReply, allowed_group_id, handle_ocr_result
from app.image_store import ImageStore, get_image_store
from app.logging_setup import configure_logging
from app.ocr.base import OcrContentError
from app.ocr.factory import get_ocr_provider
from app.reply import Reply, send
from app.schema import ReceiptExtraction
from app.store import ReceiptStore, SheetsStore
from app.tasks import MAX_ATTEMPTS, TaskBody

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI()


@lru_cache
def get_store() -> ReceiptStore:
    return SheetsStore.from_env()


@lru_cache
def get_line_clients() -> tuple[MessagingApi, MessagingApiBlob]:
    # MessagingApiBlob's generated get_message_content operation hardcodes
    # api-data.line.me internally regardless of Configuration.host, so a single
    # ApiClient/Configuration(access_token=...) backs both the reply API and the
    # content-download API — no second client needed.
    api_client = ApiClient(Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"]))
    return MessagingApi(api_client), MessagingApiBlob(api_client)


@app.post("/task")
def task(
    body: TaskBody,
    store: ReceiptStore = Depends(get_store),
    clients: tuple[MessagingApi, MessagingApiBlob] = Depends(get_line_clients),
    image_store: ImageStore = Depends(get_image_store),
    x_cloudtasks_taskretrycount: int = Header(default=0),
):
    allowed = allowed_group_id()
    if not allowed or body.group_id != allowed:
        logger.error(
            "rejected task: group not allowlisted",
            extra={"group_id": body.group_id, "message_id": body.message_id},
        )
        return {"status": "rejected"}

    # Tracked outside the try so the outer except's final-attempt cleanup (below) knows
    # whether an upload ever happened — None means the failure was before any blob
    # existed (nothing to delete). `step` names the phase for the failure log below.
    blob_name: str | None = None
    step = "start"
    try:
        line_api, blob_api = clients

        # Transient boundary: no try/except. Any exception here (network, timeout, 5xx
        # from Line's content API) propagates to the outer except below, then to
        # FastAPI's default 500 handler, and Cloud Tasks retries per the queue's
        # max-attempts/backoff config (Task 10).
        step = "download"
        image_bytes = bytes(
            blob_api.get_message_content(body.message_id, _request_timeout=10)
        )

        # Transient boundary: GCS upload happens BEFORE OCR — Line content expires, the
        # GCS copy is the source of truth. Same no-try/except treatment.
        step = "upload"
        blob_name, _ = image_store.upload_image(
            image_bytes, filename_for(body.message_id, datetime.now(timezone.utc))
        )

        # Content-error boundary: the one content-error path not already covered by
        # handle_ocr_result (which assumes a successfully constructed ReceiptExtraction).
        # OcrContentError covers OcrParseError (unrecoverable JSON, or a response with
        # no text block at all) and OcrImageError (the provider API deterministically
        # rejected the image itself — oversized, corrupt, unsupported; retrying would
        # reproduce the same rejection). ValidationError is defensive/near-unreachable
        # given ReceiptExtraction's tolerant coercion, but still content-shaped (bad
        # input, not a network problem) if it ever fires. This `return` exits before
        # the outer except below — content errors are not transient failures and must
        # not be logged/counted as a retry attempt.
        step = "ocr"
        provider = get_ocr_provider()
        ocr_started = time.perf_counter()
        try:
            raw = provider.extract(image_bytes)
            extraction = ReceiptExtraction.model_validate(raw)
        except (OcrContentError, ValidationError):
            logger.warning(
                "unrecoverable OCR output, replying cannot-read",
                extra={"message_id": body.message_id, "step": step},
            )
            send(
                line_api,
                Reply(
                    reply_token=body.reply_token,
                    group_id=body.group_id,
                    messages=[TextMessage(text="Couldn't read that photo — please resend.")],
                ),
            )
            # Cleanup AFTER the reply: if send() raises (429/5xx) it propagates for a
            # Cloud Tasks retry with the blob still in place, and that retry's upload
            # hits gcs.py's 412 already-uploaded path instead of re-uploading and
            # re-paying for OCR. delete_image is best-effort and never raises.
            image_store.delete_image(blob_name)
            return {"status": "content_error"}
        # Positive log evidence for a successful OCR call: exactly one of {this INFO
        # line, the WARNING above} fires per OCR execution, so "exactly one LLM call"
        # (e.g. non-receipt/blurry-photo scenarios) becomes something a trace can
        # count instead of only inferring from an absence of retries.
        logger.info(
            "ocr result",
            extra={
                "message_id": body.message_id,
                "is_receipt": extraction.is_receipt,
                "amount": extraction.amount,
                "ocr_model": provider.name,
                "quality_issue": extraction.quality_issue,
                "latency_ms": int((time.perf_counter() - ocr_started) * 1000),
            },
        )
        # Any other exception from get_ocr_provider().extract() (e.g. a network/timeout/
        # 5xx error from the LLM call) is NOT caught here — propagates to the outer
        # except below. Matches Task 6's "raw propagation for retry-classification"
        # pattern.

        # Transient boundary: a Sheets read failure should retry, not silently drop the
        # receipt. No try/except.
        step = "read_cards"
        cards = store.read_cards()

        step = "reply"
        result = handle_ocr_result(
            extraction,
            message_id=body.message_id,
            blob=blob_name,
            sender=body.user_id or "unknown",
            ocr_model=provider.name,
            group_id=body.group_id,
            reply_token=body.reply_token,
            cards=cards,
        )
        # send() re-raises ApiException for any non-400 status (app/reply.py's documented
        # contract) — that propagation is preserved here unmodified, so a 429/5xx on the
        # final reply also triggers a Cloud Tasks retry rather than silently dropping the
        # receipt after OCR already succeeded.
        if isinstance(result, CleanupReply):
            send(line_api, result.reply)
            # Delete AFTER the reply, mirroring the OcrContentError branch above: if
            # send() raises, the blob must still be there for the Cloud Tasks retry's
            # upload to hit the 412 already-uploaded path.
            image_store.delete_image(result.blob)
        else:
            send(line_api, result)
        return {"status": "ok"}
    except Exception as exc:
        # X-CloudTasks-TaskRetryCount is 0-indexed (0 = first delivery, no retries
        # yet). This block is purely observational — it always re-raises so the
        # response is still a 500 either way; Cloud Tasks' own queue config (Task 10)
        # decides whether to retry again. Only the log severity depends on whether
        # this looks like the final exhausted attempt, so the owner notices a dropped
        # receipt without every ordinary mid-retry failure paging as an error.
        attempt = x_cloudtasks_taskretrycount + 1
        is_final_attempt = attempt >= MAX_ATTEMPTS
        log = logger.error if is_final_attempt else logger.warning
        log(
            "task failed, retries exhausted" if is_final_attempt else "task failed, retrying",
            exc_info=True,
            extra={
                "message_id": body.message_id,
                "webhook_event_id": body.webhook_event_id,
                "step": step,
                "error_type": classify_exception(exc),
                "attempt": attempt,
                "max_attempts": MAX_ATTEMPTS,
            },
        )
        if is_final_attempt:
            # Best-effort failure signal, mirroring webhook_main's catch-all: without
            # this, an exhausted retry chain is pure silence for the user. The reply
            # token is almost certainly expired/used by now (multiple LLM calls plus
            # Cloud Tasks' own backoff far exceed the ~1-minute token lifetime) —
            # send() falls back to push on Line's 400 "Invalid reply token". Wrapped in
            # its own try/except so a second failure here can never mask the re-raise.
            try:
                line_api, _ = clients
                send(
                    line_api,
                    Reply(
                        reply_token=body.reply_token,
                        group_id=body.group_id,
                        messages=[TextMessage(text="Something went wrong — please resend that.")],
                    ),
                )
            except Exception as reply_exc:
                logger.warning(
                    "best-effort failure reply also failed",
                    exc_info=True,
                    extra={"error_type": classify_exception(reply_exc)},
                )
            # Dead end: retries are exhausted, no reply carries buttons that could ever
            # reference this blob again — clean it up. None means the failure happened
            # before any upload (nothing to delete). delete_image is best-effort and
            # never raises, so it cannot mask the re-raise below.
            if blob_name is not None:
                image_store.delete_image(blob_name)
        raise
