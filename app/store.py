from __future__ import annotations

import abc
import logging
import os
from datetime import date, datetime, timezone

import google.auth
import httplib2
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from pydantic import BaseModel

logger = logging.getLogger(__name__)

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CARDS_TAB = "Cards"
CARDS_RANGE = f"{CARDS_TAB}!A2:F"
CARDS_COLUMNS = 6


class Card(BaseModel):
    card_id: str
    bank: str
    card_name: str
    last4: str
    expiry: str
    status: str = "active"

    @property
    def is_expired(self) -> bool:
        return self.status.strip().lower() == "expired"


class ReceiptRow(BaseModel):
    """Final, user-confirmed data (post button taps) — not the raw OCR extraction.

    See app/schema.py:ReceiptExtraction (Task 6) for that.
    """

    card_id: str
    date: date
    category: str
    amount: float
    details: str = ""
    receipt_link: str
    submitted_by: str
    ocr_model: str
    message_id: str
    created_at: datetime | None = None


class TabNotFoundError(Exception):
    pass


def build_row(row: ReceiptRow, *, now: datetime | None = None) -> list[str]:
    """12 ordered column values matching the Card tab header:

    date | card | category | amount | details | receipt_link | year | month |
    submitted_by | ocr_model | message_id | created_at

    `message_id` and `month` are prefixed with a leading apostrophe so Sheets'
    USER_ENTERED parsing stores them as literal text. Without it, Sheets parses the
    long numeric message_id as a number and silently truncates it (IEEE double has
    ~15 significant digits, message_id can run to 18), and strips month's zero-pad
    ("03" -> 3).

    `details` gets the same apostrophe treatment when it starts with a formula
    trigger character: it is the row's one free-text field (OCR-extracted from
    whatever is printed on the photographed receipt, or user-typed), and USER_ENTERED
    would otherwise execute e.g. "=IMPORTXML(...)" as a live formula.
    """
    ts = row.created_at or now or datetime.now(timezone.utc)
    details = row.details
    if details.startswith(("=", "+", "@")):
        details = f"'{details}"
    return [
        row.date.isoformat(),
        row.card_id,
        row.category,
        f"{row.amount:.2f}",
        details,
        row.receipt_link,
        str(row.date.year),
        f"'{row.date.month:02d}",
        row.submitted_by,
        row.ocr_model,
        f"'{row.message_id}",
        ts.isoformat(),
    ]


class ReceiptStore(abc.ABC):
    """Isolates business logic from raw Sheets calls behind one seam."""

    @abc.abstractmethod
    def append_receipt(self, row: ReceiptRow) -> bool:
        """Returns True if a new row was written, False if a row for this
        card_id/message_id already existed and the write was skipped as a duplicate.
        """
        ...

    @abc.abstractmethod
    def read_cards(self) -> list[Card]: ...


class SheetsStore(ReceiptStore):
    def __init__(self, service: Resource, *, sheet_id: str) -> None:
        self._svc = service
        self._sheet_id = sheet_id

    @classmethod
    def from_env(cls, *, sheet_id: str | None = None) -> "SheetsStore":
        creds, _ = google.auth.default(scopes=SHEETS_SCOPES)
        # build()'s `credentials=` and `http=` kwargs are mutually exclusive, and
        # neither `credentials=` nor HttpRequest.execute() offers a timeout — the only
        # way to bound a Sheets API call is to hand-wrap credentials in AuthorizedHttp
        # over a timeout-configured httplib2.Http.
        http = AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
        service = build("sheets", "v4", http=http, cache_discovery=False)
        return cls(service, sheet_id=sheet_id or os.environ["SHEET_ID"])

    def append_receipt(self, row: ReceiptRow) -> bool:
        tab = self._tab_for(row.card_id)
        try:
            if self._message_id_exists(tab, row.message_id):
                logger.info(
                    "duplicate receipt write skipped tab=%s message_id=%s",
                    tab,
                    row.message_id,
                )
                return False
            body = {"values": [build_row(row)]}
            self._svc.spreadsheets().values().append(
                spreadsheetId=self._sheet_id,
                range=f"{tab}!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body,
            ).execute()
            return True
        except HttpError as exc:
            if exc.resp is not None and exc.resp.status == 400:
                raise TabNotFoundError(
                    f"no tab named {tab!r} (card_id={row.card_id!r}, "
                    f"message_id={row.message_id!r})"
                ) from exc
            raise

    def _message_id_exists(self, tab: str, message_id: str) -> bool:
        # Column K per build_row's documented column order (date..created_at).
        resp = (
            self._svc.spreadsheets()
            .values()
            .get(spreadsheetId=self._sheet_id, range=f"{tab}!K2:K")
            .execute()
        )
        return any(r and r[0] == message_id for r in resp.get("values", []))

    def read_cards(self) -> list[Card]:
        resp = (
            self._svc.spreadsheets()
            .values()
            .get(spreadsheetId=self._sheet_id, range=CARDS_RANGE)
            .execute()
        )
        cards: list[Card] = []
        for i, raw_row in enumerate(resp.get("values", []), start=2):
            if not raw_row:
                continue
            padded = raw_row + [""] * (CARDS_COLUMNS - len(raw_row))
            card_id = padded[0].strip()
            if not card_id:
                logger.warning("Cards!A%d has no card_id, skipping row", i)
                continue
            cards.append(
                Card(
                    card_id=card_id,
                    bank=padded[1],
                    card_name=padded[2],
                    last4=padded[3],
                    expiry=padded[4],
                    status=padded[5] or "active",
                )
            )
        return [c for c in cards if not c.is_expired]

    @staticmethod
    def _tab_for(card_id: str) -> str:
        return card_id
