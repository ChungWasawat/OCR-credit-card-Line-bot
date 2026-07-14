# Problems found and fixed (2026-07-13)

## Problem 1: Non-idempotent GCS upload broke Cloud Tasks retries

`worker-service` uploads the photo to GCS before OCR. If that attempt later failed for
an unrelated transient reason (e.g. a metadata-server auth glitch during the Sheets
read), Cloud Tasks retried, and the retry re-uploaded the same object name. The runtime
service account only had `roles/storage.objectCreator` (create, no delete), and GCS
treats an overwrite as an implicit delete+create — so the retry got 403 "does not have
storage.objects.delete access," all 3 attempts were exhausted, and the receipt was
dropped with no reply.

### Solution

`app/gcs.py::upload_image` now passes `if_generation_match=0` (create-only semantics)
and treats the resulting 412 `PreconditionFailed` as "already uploaded by a previous
attempt" instead of an error, so a retry after this point is now a safe no-op.

---

## Problem 2: Cloud Tasks enqueue failed under container clock skew

`create_task` calls used a 30s timeout. gRPC turns that into an absolute deadline of
"this container's clock + 30s," but Cloud Tasks rejects any deadline more than 30s in
its own future — so any positive clock skew on the Cloud Run container produced
`InvalidArgument("The deadline cannot be more than 30s in the future")`. The webhook's
catch-all just logged this and returned 200, so Line never redelivered the message —
this was the original traceback that started the investigation.

### Solution

`app/tasks.py` now uses a 20s timeout (10s headroom for skew) plus a bounded one-retry
loop for genuinely transient gRPC errors (`DeadlineExceeded`, `InternalServerError`,
`ServiceUnavailable`). `InvalidArgument` is deliberately never retried, since a
skew-induced rejection would just recompute the same bad deadline immediately — the
timeout headroom is the actual fix for that case.

---

## Problem 3: Failures were silent — no user-facing signal

When an enqueue or reply failure happened inside `webhook_main`'s per-event handler,
the catch-all only logged the error and returned 200. The user got no reply and no way
to know their message had been silently dropped.

### Solution

`services/webhook_main.py` now also attempts one best-effort reply ("Something went
wrong — please resend that.") using the event's own `reply_token`/`group_id` when
present, wrapped in its own try/except so a second failure there still can't abort the
rest of the batch.

---

## Problem 4: No cleanup of uploaded photos on cancel or OCR failure

Uploaded receipt photos were never deleted when the user cancelled the confirm flow or
OCR failed unrecoverably, and the service account's IAM only granted create (no
delete), so there was no way to clean them up even once code tried to.

### Solution

Added `app/gcs.py::delete_image()` (best-effort, never raises) wired through
`ImageStore`/`GcsStore`. `app/handlers.py`'s `STEP_CANCEL` branch now returns a new
`CancelCleanup` action that `webhook_main` executes (delete, then reply); `worker_main`
deletes the blob after sending the "couldn't read that photo" reply on unrecoverable
OCR failure. `infra/storage.tf`'s bucket IAM binding was replaced with a custom
`receiptImageWriter` role (`storage.objects.create` + `storage.objects.delete` only, no
read/list) so the SA can actually perform the delete.
