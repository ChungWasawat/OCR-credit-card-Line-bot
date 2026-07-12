from __future__ import annotations

import logging
import os

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Single source of truth for Task 9's retry-exhaustion logging (services/worker_main.py
# reads this to decide WARNING vs ERROR severity). Task 10's Terraform
# google_cloud_tasks_queue.retry_config.max_attempts MUST be set to this same value —
# if they drift, the ERROR/WARNING split fires on the wrong delivery attempt.
MAX_ATTEMPTS = 3


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
    Any other client error (permission, invalid argument, deadline) propagates
    unmodified. Returns True if a new task was created.
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
    try:
        client.create_task(request={"parent": parent, "task": task}, timeout=30.0)
        return True
    except AlreadyExists:
        logger.info("task name=%s already exists, duplicate delivery collapsed", name)
        return False
