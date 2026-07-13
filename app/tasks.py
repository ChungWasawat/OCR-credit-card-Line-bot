from __future__ import annotations

import logging
import os

from google.api_core.exceptions import (
    AlreadyExists,
    DeadlineExceeded,
    InternalServerError,
    ServiceUnavailable,
)
from google.cloud import tasks_v2
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Single source of truth for Task 9's retry-exhaustion logging (services/worker_main.py
# reads this to decide WARNING vs ERROR severity). Task 10's Terraform
# google_cloud_tasks_queue.retry_config.max_attempts MUST be set to this same value —
# if they drift, the ERROR/WARNING split fires on the wrong delivery attempt.
MAX_ATTEMPTS = 3

# Cloud Tasks rejects any gRPC deadline more than 30s in its own future, and gRPC
# computes that deadline as this container's clock + timeout. A Cloud Run container
# clock running even slightly fast turned timeout=30.0 into
# InvalidArgument("The deadline cannot be more than 30s in the future") in production —
# 20s keeps a 10s skew budget while still far exceeding the call's real latency.
CREATE_TASK_TIMEOUT = 20.0

# Only genuinely transient gRPC codes. InvalidArgument is deliberately absent: a
# skew-induced deadline rejection recomputes the same bad deadline on an immediate
# retry — CREATE_TASK_TIMEOUT's headroom is the fix for that, not retrying it.
_TRANSIENT_ENQUEUE_ERRORS = (DeadlineExceeded, InternalServerError, ServiceUnavailable)
_ENQUEUE_ATTEMPTS = 2


class TaskBody(BaseModel):
    """Wire contract POSTed to worker-service's /task. One-for-one with
    app.handlers.Enqueue's fields, so the field list lives in exactly one place —
    webhook_main encodes it, worker_main decodes it.
    """

    message_id: str
    webhook_event_id: str
    group_id: str
    user_id: str | None = None
    reply_token: str


def create_http_task(
    *,
    name: str,
    url: str,
    body: bytes,
    service_account_email: str,
    client: tasks_v2.CloudTasksClient | None = None,
    project: str | None = None,
    location: str | None = None,
    queue: str | None = None,
) -> bool:
    """Creates an HTTP task with OIDC auth. `name` is the idempotency key: Cloud
    Tasks task names are unique per queue, so a duplicate Line webhook delivery
    (same webhookEventId) attempting to create a task with the same name gets
    AlreadyExists from Cloud Tasks itself — swallowed here, returns False so the
    caller can log a "duplicate collapsed" line instead of treating it as an error.
    Any other client error (permission, invalid argument) propagates unmodified after
    one bounded retry for transient codes (see _TRANSIENT_ENQUEUE_ERRORS). Returns True
    if a new task was created.
    """
    client = client or tasks_v2.CloudTasksClient()
    project = project or os.environ["GCP_PROJECT"]
    location = location or os.environ["REGION"]
    queue = queue or os.environ["TASKS_QUEUE"]

    parent = client.queue_path(project, location, queue)
    task = tasks_v2.Task(
        name=client.task_path(project, location, queue, name),
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST,
            url=url,
            headers={"Content-Type": "application/json"},
            body=body,
            oidc_token=tasks_v2.OidcToken(
                service_account_email=service_account_email, audience=url
            ),
        ),
    )
    # Bounded in-process retry: there is no queue behind the webhook (Line never
    # redelivers after a 200), so this is the only retry this path will ever get. It's
    # safe because `name` is the idempotency key — if attempt 1 actually succeeded
    # server-side and only the response was lost, attempt 2 gets AlreadyExists, handled
    # below as the duplicate it is.
    for attempt in range(1, _ENQUEUE_ATTEMPTS + 1):
        try:
            client.create_task(
                request={"parent": parent, "task": task}, timeout=CREATE_TASK_TIMEOUT
            )
            return True
        except AlreadyExists:
            logger.info("task name=%s already exists, duplicate delivery collapsed", name)
            return False
        except _TRANSIENT_ENQUEUE_ERRORS:
            if attempt == _ENQUEUE_ATTEMPTS:
                raise
            logger.warning(
                "create_task transient failure on attempt %d/%d, retrying",
                attempt, _ENQUEUE_ATTEMPTS, exc_info=True,
            )
