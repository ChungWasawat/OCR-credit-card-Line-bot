from __future__ import annotations

import logging
import os
from datetime import date, datetime
from functools import lru_cache

import google.auth
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

logger = logging.getLogger(__name__)

GCS_SCOPES = ["https://www.googleapis.com/auth/devstorage.read_write"]


def filename_for(message_id: str, when: date | datetime) -> str:
    """`{YYYYMM}_{message_id}.jpg`.

    `when` must be the upload time, not the OCR-extracted receipt date — upload
    happens before OCR runs, so the receipt's own date isn't known yet.
    """
    return f"{when:%Y%m}_{message_id}.jpg"


def view_link_for(blob_name: str, *, bucket_name: str | None = None) -> str:
    """Rebuilds the authenticated-browser view link for an already-uploaded blob, from
    its name alone. Used at receipt-row-write time (handlers.py), when the flow only
    has the blob name (carried through the postback payload) and not a fresh upload
    result — keeps the URL format defined in exactly one place.
    """
    bucket_name = bucket_name or os.environ["GCS_BUCKET"]
    return f"https://storage.cloud.google.com/{bucket_name}/{blob_name}"


def upload_image(
    image: bytes,
    filename: str,
    *,
    client: storage.Client | None = None,
    bucket_name: str | None = None,
) -> tuple[str, str]:
    """Uploads `image` into the GCS bucket. Returns (blob_name, view_link).

    `view_link` uses the authenticated-browser endpoint (storage.cloud.google.com), not
    the public REST endpoint (storage.googleapis.com) — visibility is via per-account IAM
    (roles/storage.objectViewer granted to family accounts on the bucket), and only the
    authenticated endpoint checks the signed-in browser account against that IAM grant.

    Idempotent-create: a Cloud Tasks retry of an attempt that failed AFTER this upload
    (e.g. a transient error later in the pipeline) re-runs this call against the same
    filename. `if_generation_match=0` makes that a no-op 412 instead of an overwrite —
    which matters because the runtime SA only has create+delete, not update, so an
    overwrite (GCS treats it as delete+create) would otherwise 403.
    """
    client = client or _default_client()
    bucket_name = bucket_name or os.environ["GCS_BUCKET"]

    bucket = client.bucket(bucket_name)
    blob = bucket.blob(filename)
    try:
        blob.upload_from_string(
            image, content_type="image/jpeg", timeout=30, if_generation_match=0
        )
    except PreconditionFailed:
        # 412 = this filename was already uploaded by a previous attempt. The filename
        # embeds the Line message_id, so same name always means same bytes — already
        # uploaded IS success for this attempt.
        logger.info("blob %s already uploaded by a previous attempt, continuing", filename)

    view_link = view_link_for(blob.name, bucket_name=bucket_name)
    return blob.name, view_link


def delete_image(
    blob_name: str,
    *,
    client: storage.Client | None = None,
    bucket_name: str | None = None,
) -> None:
    """Best-effort delete of an uploaded receipt image (cancel flow / unreadable OCR).

    Deliberately never raises — unlike the transient-boundary calls elsewhere in this
    pipeline, deletion is cleanup, not the pipeline itself: a failed delete must not
    turn an already-delivered user reply into a dropped/retried request. Worst case is
    an orphaned photo, recoverable for 7 days via the bucket's soft-delete policy.
    """
    try:
        client = client or _default_client()
        bucket_name = bucket_name or os.environ["GCS_BUCKET"]
        client.bucket(bucket_name).blob(blob_name).delete(timeout=30)
        logger.info("deleted blob %s", blob_name)
    except NotFound:
        logger.info("blob %s already deleted, nothing to do", blob_name)
    except Exception:
        logger.warning("best-effort delete of blob %s failed, continuing", blob_name, exc_info=True)


@lru_cache
def _default_client() -> storage.Client:
    # Cached: every other per-request dependency in worker_main.py is already
    # lru_cache'd (get_store, get_line_clients, get_tasks_client) — this was the one
    # exception, re-running ADC auth and building a fresh storage.Client on every task.
    creds, project = google.auth.default(scopes=GCS_SCOPES)
    return storage.Client(credentials=creds, project=project)
