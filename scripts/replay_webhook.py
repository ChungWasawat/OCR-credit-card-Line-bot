"""Task 12 tooling: signed replay of an image-message webhook event against the real
deployed /callback, to test duplicate-delivery collapse on demand.

LINE offers no "redeliver this event" button, and re-sending a photo from the phone
creates a brand-new messageId/webhookEventId (a different, already-covered scenario —
see the locked "Duplicates" decision in checklist3.md). What's actually under test here
is Cloud Tasks' task-name collapse (task name = webhookEventId, see app/tasks.py's
AlreadyExists handling) on the REAL deployed stack: real signature verification, real
enqueue, real queue — not the mocked TestClient path the unit tests already cover.

Reuses tests/fixtures/image_message.json's envelope shape and the same HMAC-SHA256
signing scheme as tests/test_webhook_main.py's `_sign()` helper.

SAFETY: only replay a message_id whose original flow was already CANCELLED. Replaying
(and then cancelling) a duplicate of an already-recorded row would delete the GCS blob
that row's receipt_link points at (see app/handlers.py's CancelCleanup). The replayed
prompt also arrives via LINE's Push API, not a reply (the replay uses a deliberately
invalid reply_token to exercise that fallback), so it lands as a new message in the
group, not attached to anything.

Prerequisites: LINE_CHANNEL_SECRET and ALLOWED_GROUP_ID in .env; a message_id from a
recent "enqueued task" log line (see scripts/trace_receipt.py) whose content LINE can
still serve (the worker re-downloads it fresh on each replay).

Usage:
  uv run python scripts/replay_webhook.py --message-id msg-abc123 --url https://webhook-service-xxx.a.run.app
  uv run python scripts/replay_webhook.py --message-id msg-abc123 --url $WEBHOOK_URL --count 3
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _build_body(*, message_id: str, webhook_event_id: str, group_id: str) -> bytes:
    event = {
        "destination": "Ue2ereplaydestinationxxxxxxxxxxxx",
        "events": [
            {
                "type": "message",
                "source": {
                    "type": "group",
                    "groupId": group_id,
                    "userId": "Ue2ereplay00000000000000000000001",
                },
                "timestamp": int(time.time() * 1000),
                "mode": "active",
                "webhookEventId": webhook_event_id,
                "deliveryContext": {"isRedelivery": False},
                # Deliberately invalid: the real reply token from the original send has
                # long since expired/been used, and a replay has none of its own. This
                # forces send() through its documented 400 -> push fallback (app/reply.py)
                # — a bonus live assertion, not a workaround.
                "replyToken": f"e2e-replay-{webhook_event_id}",
                "message": {
                    "type": "image",
                    "id": message_id,
                    "contentProvider": {"type": "line"},
                    "quoteToken": "qt-e2e-replay",
                },
            }
        ],
    }
    return json.dumps(event).encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--message-id", required=True, help="a real, still-retrievable LINE image message id")
    parser.add_argument("--url", required=True, help="deployed webhook base URL (terraform output webhook_url)")
    parser.add_argument(
        "--webhook-event-id", default=None, help="default: e2edup-<unix timestamp>"
    )
    parser.add_argument("--count", type=int, default=2, help="times to POST the identical body (default: 2)")
    args = parser.parse_args()

    secret = os.environ["LINE_CHANNEL_SECRET"]
    group_id = os.environ["ALLOWED_GROUP_ID"]
    webhook_event_id = args.webhook_event_id or f"e2edup-{int(time.time())}"

    body = _build_body(message_id=args.message_id, webhook_event_id=webhook_event_id, group_id=group_id)
    signature = _sign(secret, body)
    endpoint = f"{args.url.rstrip('/')}/callback"

    print(f"Replaying message_id={args.message_id!r} as webhook_event_id={webhook_event_id!r}")
    print(f"POST {endpoint} x{args.count}")

    for attempt in range(1, args.count + 1):
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                print(f"  attempt {attempt}/{args.count}: HTTP {resp.status}")
        except urllib.error.HTTPError as exc:
            print(f"  attempt {attempt}/{args.count}: HTTP {exc.code} — {exc.read().decode(errors='replace')}")

    print()
    print("Next: trace the result —")
    print(f"  uv run python scripts/trace_receipt.py {webhook_event_id}")
    print("Expect: exactly one 'ocr result' line and (count - 1) 'duplicate delivery collapsed' lines.")


if __name__ == "__main__":
    main()
