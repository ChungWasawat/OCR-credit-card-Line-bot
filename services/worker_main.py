from __future__ import annotations

import logging
import os
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

from app.gcs import filename_for
from app.handlers import allowed_group_id, handle_ocr_result
from app.image_store import ImageStore, get_image_store
from app.logging_setup import configure_logging
from app.ocr.base import OcrParseError
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
            "rejected task with non-allowlisted group_id=%s message_id=%s",
            body.group_id,
            body.message_id,
        )
        return {"status": "rejected"}

    try:
        line_api, blob_api = clients

        # Transient boundary: no try/except. Any exception here (network, timeout, 5xx
        # from Line's content API) propagates to the outer except below, then to
        # FastAPI's default 500 handler, and Cloud Tasks retries per the queue's
        # max-attempts/backoff config (Task 10).
        image_bytes = bytes(
            blob_api.get_message_content(body.message_id, _request_timeout=10)
        )

        # Transient boundary: GCS upload happens BEFORE OCR — Line content expires, the
        # GCS copy is the source of truth. Same no-try/except treatment.
        blob_name, _ = image_store.upload_image(
            image_bytes, filename_for(body.message_id, datetime.now(timezone.utc))
        )

        # Content-error boundary: the one content-error path not already covered by
        # handle_ocr_result (which assumes a successfully constructed ReceiptExtraction).
        # OcrParseError = the OCR provider couldn't recover JSON at all. ValidationError
        # is defensive/near-unreachable given ReceiptExtraction's tolerant coercion, but
        # still content-shaped (bad input, not a network problem) if it ever fires.
        # This `return` exits before the outer except below — content errors are not
        # transient failures and must not be logged/counted as a retry attempt.
        provider = get_ocr_provider()
        try:
            raw = provider.extract(image_bytes)
            extraction = ReceiptExtraction.model_validate(raw)
        except (OcrParseError, ValidationError):
            logger.warning(
                "unrecoverable OCR output, replying cannot-read",
                extra={"message_id": body.message_id, "step": "ocr"},
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
        # Any other exception from get_ocr_provider().extract() (e.g. a network/timeout/
        # 5xx error from the LLM call) is NOT caught here — propagates to the outer
        # except below. Matches Task 6's "raw propagation for retry-classification"
        # pattern.

        # Transient boundary: a Sheets read failure should retry, not silently drop the
        # receipt. No try/except.
        cards = store.read_cards()

        reply = handle_ocr_result(
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
        send(line_api, reply)
        return {"status": "ok"}
    except Exception:
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
            "task processing failed on attempt %d/%d%s",
            attempt,
            MAX_ATTEMPTS,
            " — all attempts exhausted, receipt dropped" if is_final_attempt else "",
            exc_info=True,
            extra={
                "message_id": body.message_id,
                "webhook_event_id": body.webhook_event_id,
                "attempt": attempt,
                "max_attempts": MAX_ATTEMPTS,
            },
        )
        raise
