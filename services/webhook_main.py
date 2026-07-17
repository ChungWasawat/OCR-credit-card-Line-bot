from __future__ import annotations

import logging
import os
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from google.cloud import tasks_v2
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import MessagingApi, TextMessage
from linebot.v3.webhook import WebhookParser

from app.handlers import CleanupReply, Enqueue, route_event
from app.image_store import ImageStore, get_image_store
from app.logging_setup import configure_logging
from app.reply import Reply, default_messaging_api, send
from app.store import ReceiptStore, SheetsStore
from app.tasks import TaskBody, create_http_task

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI()


@lru_cache
def get_store() -> ReceiptStore:
    return SheetsStore.from_env()


@lru_cache
def get_line_api() -> MessagingApi:
    return default_messaging_api()


@lru_cache
def get_tasks_client() -> tasks_v2.CloudTasksClient:
    return tasks_v2.CloudTasksClient()


@lru_cache
def get_parser() -> WebhookParser:
    return WebhookParser(os.environ["LINE_CHANNEL_SECRET"])


def _enqueue(action: Enqueue, tasks_client: tasks_v2.CloudTasksClient) -> None:
    body = TaskBody(
        message_id=action.message_id,
        webhook_event_id=action.webhook_event_id,
        group_id=action.group_id,
        user_id=action.user_id,
        reply_token=action.reply_token,
    ).model_dump_json().encode("utf-8")
    created = create_http_task(
        name=action.webhook_event_id,
        url=f"{os.environ['WORKER_URL'].rstrip('/')}/task",
        body=body,
        service_account_email=os.environ["RECEIPT_BOT_SA_EMAIL"],
        client=tasks_client,
    )
    # Positive log evidence for a normal send: the worker's own logs (Task 9/16) only
    # fire on error/warning paths, so tracing "did this message even get enqueued" by
    # message_id had no INFO-level line to find before this. create_http_task already
    # logs its own "duplicate delivery collapsed" line when created is False.
    if created:
        logger.info(
            "enqueued task",
            extra={"message_id": action.message_id, "webhook_event_id": action.webhook_event_id},
        )


async def get_body(request: Request) -> bytes:
    return await request.body()


@app.post("/callback")
def callback(
    raw_body: bytes = Depends(get_body),
    x_line_signature: str = Header(default=""),
    store: ReceiptStore = Depends(get_store),
    line_api: MessagingApi = Depends(get_line_api),
    tasks_client: tasks_v2.CloudTasksClient = Depends(get_tasks_client),
    parser: WebhookParser = Depends(get_parser),
    image_store: ImageStore = Depends(get_image_store),
):
    # Deliberately a sync `def`: everything below (Sheets append/read, Cloud Tasks
    # create, Line reply) is blocking network I/O, so FastAPI must run this handler
    # in its threadpool instead of on the event loop. Only reading the request body
    # needs async, hence the get_body dependency.
    try:
        events = parser.parse(raw_body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=403, detail="invalid signature")

    for event in events:
        try:
            action = route_event(event, store)
            if isinstance(action, Enqueue):
                _enqueue(action, tasks_client)
            elif isinstance(action, CleanupReply):
                # Delete before confirming so "Cancelled"/the dead-end reply is
                # honest; delete_image is best-effort and never raises (app/gcs.py),
                # so a GCS hiccup can't block the reply.
                image_store.delete_image(action.blob)
                send(line_api, action.reply)
            elif isinstance(action, Reply):
                send(line_api, action)
        except Exception:
            # Defense in depth only: known failure modes (e.g. TabNotFoundError on a
            # Sheets write) are already handled inside app/handlers.py, which returns
            # an error Reply instead of raising. This catch is for anything else
            # unexpected. No Cloud Tasks queue sits behind this synchronous path, so
            # swallow-and-continue beats aborting the whole batch or returning a
            # non-200 that makes Line re-deliver every event again.
            logger.error(
                "unhandled error processing webhook event",
                exc_info=True,
                extra={"webhook_event_id": getattr(event, "webhook_event_id", None)},
            )
            # Best-effort failure signal to the user: this handler always returns 200,
            # so Line never redelivers — without this, an enqueue failure (e.g. the
            # Cloud Tasks clock-skew INVALID_ARGUMENT below) is pure silence. Only
            # message/postback events carry a reply_token; anything else stays silent
            # by design. Wrapped again: a second failure here must not abort the batch.
            reply_token = getattr(event, "reply_token", None)
            group_id = getattr(getattr(event, "source", None), "group_id", None)
            if reply_token and group_id:
                try:
                    send(
                        line_api,
                        Reply(
                            reply_token=reply_token,
                            group_id=group_id,
                            messages=[
                                TextMessage(text="Something went wrong — please resend that.")
                            ],
                        ),
                    )
                except Exception:
                    logger.warning("best-effort failure reply also failed", exc_info=True)

    return {"status": "ok"}
