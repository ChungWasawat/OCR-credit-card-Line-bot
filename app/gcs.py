from __future__ import annotations

import os
from datetime import date, datetime
from functools import lru_cache

import google.auth
from google.cloud import storage

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
    """
    client = client or _default_client()
    bucket_name = bucket_name or os.environ["GCS_BUCKET"]

    bucket = client.bucket(bucket_name)
    blob = bucket.blob(filename)
    blob.upload_from_string(image, content_type="image/jpeg", timeout=30)

    view_link = view_link_for(blob.name, bucket_name=bucket_name)
    return blob.name, view_link


@lru_cache
def _default_client() -> storage.Client:
    # Cached: every other per-request dependency in worker_main.py is already
    # lru_cache'd (get_store, get_line_clients, get_tasks_client) — this was the one
    # exception, re-running ADC auth and building a fresh storage.Client on every task.
    creds, project = google.auth.default(scopes=GCS_SCOPES)
    return storage.Client(credentials=creds, project=project)
