"""Local integration smoke test for the storage layer (Task 5).

Prerequisites (not available until Task 10 lands for production; can be set up manually
for local testing, as done here — see checklist3.md Task 5's Drive-removal note):
- key.json for the receipt-bot SA, GOOGLE_APPLICATION_CREDENTIALS exported in the shell
  (or set in .env) pointing at it
- Sheet shared with the receipt-bot SA email (Editor)
- GCS bucket (GCS_BUCKET) created, with the SA granted roles/storage.objectCreator and
  your own Google account granted roles/storage.objectViewer on it
- .env populated with real SHEET_ID / GCS_BUCKET (Task 2 / Task 5)
- Cards tab has at least one row with status != "expired"

Usage: uv run python scripts/smoke_storage.py
"""

from __future__ import annotations

import datetime
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from pathlib import Path

from app.gcs import filename_for
from app.image_store import get_image_store
from app.store import ReceiptRow, SheetsStore

# Not a structurally valid JPEG — GCS stores bytes as-is regardless of content, so a
# minimal SOI/EOI marker pair is enough to prove upload + MIME handling work.
# Used only as a fallback if no real sample photo is present.
_FAKE_JPEG = bytes.fromhex("ffd8" "ffd9")
_SAMPLE_PHOTO = Path(__file__).resolve().parent.parent / "data" / "IMG_2498.JPG"


def main() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    message_id = f"smoketest-{now:%Y%m%d%H%M%S}"
    filename = filename_for(message_id, now)

    if _SAMPLE_PHOTO.exists():
        image_bytes = _SAMPLE_PHOTO.read_bytes()
        print(f"Using real sample photo: {_SAMPLE_PHOTO}")
    else:
        image_bytes = _FAKE_JPEG
        print("No data/IMG_2498.JPG found — using a minimal fake JPEG instead.")

    # Upload first, before touching Sheets, so an upload problem isn't buried behind
    # unrelated success output.
    image_store = get_image_store()
    file_id, web_view_link = image_store.upload_image(image_bytes, filename)
    print(f"Uploaded via {type(image_store).__name__}: {file_id} -> {web_view_link}")

    sheet_id = os.environ["SHEET_ID"]
    store = SheetsStore.from_env(sheet_id=sheet_id)
    cards = store.read_cards()
    if not cards:
        print("No active cards in the Cards tab — fill it in (Task 2) first.", file=sys.stderr)
        sys.exit(1)

    card = cards[0]
    print(f"Using card: {card.card_id} ({card.bank} {card.card_name})")

    row = ReceiptRow(
        card_id=card.card_id,
        date=now.date(),
        category="Test",
        amount=1.23,
        details="smoke test row - safe to delete",
        receipt_link=web_view_link,
        submitted_by="scripts/smoke_storage.py",
        ocr_model="none",
        message_id=message_id,
    )
    store.append_receipt(row)
    print(f"Appended row to tab {card.card_id!r}. Verify manually in the sheet + bucket.")


if __name__ == "__main__":
    main()
