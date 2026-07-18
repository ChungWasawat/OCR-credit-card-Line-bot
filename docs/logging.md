# Logging reference

Both Cloud Run services (`webhook-service`, `worker-service`) call
`app/logging_setup.py::configure_logging()` once at import time, which installs one
`JsonFormatter` writing structured JSON lines to stdout. Cloud Run captures container
stdout automatically — no logging agent or client library to configure — and Cloud
Logging parses each JSON line into `jsonPayload`, promoting `severity` to the entry's
own top-level `severity` field (what Log Explorer's severity filter/color-coding
reads) and stamping every entry with `resource.labels.service_name` set to whichever
Cloud Run service emitted it (`webhook-service` or `worker-service`).

A separate, uncorrelated stream exists alongside this: Cloud Run's own automatic
`httpRequest` access logs (one per HTTP request, method/status/latency/URL). These are
not JSON lines this app wrote — they're what shows up if you ever see an entry with an
`httpRequest` field and no `jsonPayload`. `scripts/trace_receipt.py::_render()` handles
both shapes.

## Severity conventions

- **INFO** — normal breadcrumb. Something expected happened; no action needed.
- **WARNING** — recoverable friction (a retry in progress, a rejected malformed
  input, a fallback path taken). Worth noticing in a pattern, not urgent alone.
- **ERROR** — fires the Cloud Monitoring email alert (`infra/monitoring.tf`,
  `google_monitoring_alert_policy.error_logs`, rate-limited to 1 email/hour). Four
  sites emit ERROR, all genuine problems:
  1. Worker: retry chain exhausted after `MAX_ATTEMPTS` (3) — `"task failed, retries
     exhausted"` (`services/worker_main.py`). The receipt was **not** saved.
  2. Worker: a task body's `group_id` isn't the allowlisted one — `"rejected task:
     group not allowlisted"` (`services/worker_main.py`). Defense in depth; shouldn't
     normally fire since `webhook-service` already filters this at enqueue time.
  3. Webhook: an unhandled exception inside the per-event loop — `"unhandled error
     processing webhook event"` (`services/webhook_main.py`). Defense-in-depth
     catch-all around enqueue/Sheets-write/reply calls.
  4. Webhook: a Sheets row write failed (e.g. `TabNotFoundError`) — `"failed to
     append receipt row"` (`app/handlers.py::_write_and_confirm`).

## Field vocabulary

Every field a log line can carry, in the order a line should be read (which receipt →
where in the pipeline → what happened → how long):

| Field | Meaning | Present on |
|---|---|---|
| `message_id` | the LINE message id — the anchor. Same value across both services for one receipt's whole journey. | most receipt-related lines |
| `webhook_event_id` | the LINE webhook delivery id — the Cloud Tasks idempotency key | enqueue/dedup lines only |
| `group_id` | the LINE group the event came from | allowlist/reply-fallback lines |
| `blob` | the GCS object name (`{YYYYMM}_{message_id}.jpg`) | upload/delete lines |
| `tab` | the Sheets tab name (= `card_id`) | Sheets write lines |
| `step` | which pipeline phase was active: `start`\|`download`\|`upload`\|`ocr`\|`read_cards`\|`reply` (worker), or a `Payload.step` value (webhook) | worker failure lines; some webhook rejection lines |
| `error_type` | why a call failed: `auth`\|`rate_limit`\|`client_error`\|`provider_error`\|`network`\|`unknown` (`app/errors.py::classify_exception`) | every line with `exc_info=True` |
| `attempt` / `max_attempts` | retry position (1-indexed) | retry-related lines |
| `latency_ms` | wall-clock time of the OCR call (int) | the `"ocr result"` line |
| `is_receipt` / `amount` / `ocr_model` / `quality_issue` | OCR outcome fields | the `"ocr result"` line |
| `active_cards` / `max_items` | quick-reply capacity check | the cards-capacity warning |
| `push_total` | running count of push-fallback sends this process | the push-fallback INFO line |
| `row_index` | 1-indexed Sheets row (the `Cards` tab) | the malformed-card-row warning |

`error_type` follows one invariant: **any line carrying `exc_info=True` also carries
`error_type`** — if you see a traceback, the field next to it already tells you the
failure category without reading the traceback.

## Message catalog

Every message is now a **constant string** — no IDs or counts interpolated into the
text, so identical failures produce byte-identical `message` values and group/filter
cleanly in Log Explorer. All variable data lives in the fields above.

### worker-service (`services/worker_main.py`)

| Severity | Message | Key extras |
|---|---|---|
| ERROR | `rejected task: group not allowlisted` | group_id, message_id |
| WARNING | `unrecoverable OCR output, replying cannot-read` | message_id, step |
| INFO | `ocr result` | message_id, is_receipt, amount, ocr_model, quality_issue, latency_ms |
| WARNING | `task failed, retrying` | message_id, webhook_event_id, step, error_type, attempt, max_attempts |
| ERROR | `task failed, retries exhausted` | (same as above) |
| WARNING | `best-effort failure reply also failed` | error_type |

### webhook-service (`services/webhook_main.py`)

| Severity | Message | Key extras |
|---|---|---|
| INFO | `enqueued task` | message_id, webhook_event_id |
| ERROR | `unhandled error processing webhook event` | webhook_event_id, error_type |
| WARNING | `best-effort failure reply also failed` | error_type |

### Shared (`app/`) — emitted by whichever service calls the function

| Severity | Message | Source | Key extras |
|---|---|---|---|
| INFO | `ignored event: no group source` | handlers.py | — |
| WARNING | `rejected event: group not allowlisted` | handlers.py | group_id |
| WARNING | `rejected corrupt or stale postback data` | handlers.py | group_id |
| WARNING | `rejected corrupt or stale fill-in text` | handlers.py | group_id |
| WARNING | `rejected postback: unknown step` | handlers.py | step |
| ERROR | `failed to append receipt row` | handlers.py | message_id, step, error_type |
| INFO | `blob already uploaded by a previous attempt` | gcs.py | blob |
| INFO | `deleted blob` | gcs.py | blob |
| INFO | `blob already deleted` | gcs.py | blob |
| WARNING | `best-effort blob delete failed` | gcs.py | blob, error_type |
| INFO | `duplicate task delivery collapsed` | tasks.py | webhook_event_id |
| WARNING | `task enqueue failed, retrying in-process` | tasks.py | webhook_event_id, error_type, attempt, max_attempts |
| INFO | `duplicate receipt write skipped` | store.py | tab, message_id |
| WARNING | `Cards row has no card_id, skipped` | store.py | row_index |
| WARNING | `active cards exceed quick-reply capacity` | buttons.py | active_cards, max_items |
| WARNING | `reply token invalid, falling back to push` | reply.py | group_id |
| INFO | `push fallback used` | reply.py | group_id, push_total, note |

## Reading logs

`scripts/trace_receipt.py <id> [--minutes N]` wraps `gcloud logging read` — pass a
`message_id`, `webhook_event_id`, `group_id`, or `blob` and it filters on an exact
match against those fields (falling back to a substring match on the message text for
log entries written before this constant-message migration). Omit the id to see
everything recent.

Manual Log Explorer queries (Console → Logging → Logs Explorer):

```
# Every ERROR from either service in the last hour
resource.type="cloud_run_revision"
resource.labels.service_name=("webhook-service" OR "worker-service")
severity>=ERROR

# One receipt's full journey across both services
jsonPayload.message_id="<the id>"

# All auth-classified failures (expired/invalid API keys) this week
jsonPayload.error_type="auth"

# OCR calls slower than 5s
jsonPayload.message="ocr result"
jsonPayload.latency_ms>5000
```
