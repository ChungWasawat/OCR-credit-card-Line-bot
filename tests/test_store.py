import datetime
from unittest.mock import MagicMock

import google.auth
from google_auth_httplib2 import AuthorizedHttp

import app.store as store_module
from app.store import Card, ReceiptRow, SheetsStore, build_row


def _row(**overrides) -> ReceiptRow:
    defaults = dict(
        card_id="Card_A1",
        date=datetime.date(2026, 3, 5),
        category="Groceries",
        amount=199.5,
        details="Big C",
        receipt_link="https://drive.example/file",
        submitted_by="U123",
        ocr_model="claude",
        message_id="msg-1",
    )
    defaults.update(overrides)
    return ReceiptRow(**defaults)


def test_build_row_derives_year_and_month_from_date():
    row = _row(date=datetime.date(2026, 3, 5))
    now = datetime.datetime(2026, 3, 5, 12, 0, tzinfo=datetime.timezone.utc)

    values = build_row(row, now=now)

    assert values == [
        "2026-03-05",
        "Card_A1",
        "Groceries",
        "199.50",
        "Big C",
        "https://drive.example/file",
        "2026",
        "'03",
        "U123",
        "claude",
        "'msg-1",
        now.isoformat(),
    ]


def test_build_row_quotes_details_starting_with_formula_trigger():
    # USER_ENTERED would otherwise execute these as live formulas — details is the
    # row's one free-text field (OCR-extracted or user-typed).
    for trigger in ("=SUM(A1)", "+66 812345678", "@mention"):
        values = build_row(_row(details=trigger))
        assert values[4] == f"'{trigger}"


def test_build_row_leaves_plain_details_unquoted():
    values = build_row(_row(details="ผ่อน 10 เดือน"))
    assert values[4] == "ผ่อน 10 เดือน"


def test_read_cards_maps_columns_to_card_fields():
    service = MagicMock()
    service.spreadsheets().values().get().execute.return_value = {
        "values": [["Card_A1", "Bank A", "Platinum", "1234", "12/27", "active"]]
    }
    store = SheetsStore(service, sheet_id="sheet-1")

    cards = store.read_cards()

    assert cards == [
        Card(
            card_id="Card_A1",
            bank="Bank A",
            card_name="Platinum",
            last4="1234",
            expiry="12/27",
            status="active",
        )
    ]


def test_read_cards_excludes_expired():
    service = MagicMock()
    service.spreadsheets().values().get().execute.return_value = {
        "values": [
            ["Card_A1", "Bank A", "Platinum", "1234", "12/27", "active"],
            ["Card_A2", "Bank A", "Gold", "5678", "01/24", "expired"],
        ]
    }
    store = SheetsStore(service, sheet_id="sheet-1")

    cards = store.read_cards()

    assert [c.card_id for c in cards] == ["Card_A1"]


def test_read_cards_pads_short_row_and_skips_row_with_empty_card_id():
    service = MagicMock()
    service.spreadsheets().values().get().execute.return_value = {
        "values": [
            ["Card_A1"],  # only card_id typed so far — must not crash
            ["", "Bank B", "Cashback", "5678", "01/28", "active"],  # no card_id — skip
            ["Card_B1", "Bank B", "Platinum", "9012", "02/29", "active"],
        ]
    }
    store = SheetsStore(service, sheet_id="sheet-1")

    cards = store.read_cards()

    assert [c.card_id for c in cards] == ["Card_A1", "Card_B1"]
    assert cards[0].bank == ""
    assert cards[0].status == "active"


def test_append_receipt_routes_to_card_id_tab():
    service = MagicMock()
    service.spreadsheets().values().get().execute.return_value = {"values": []}
    store = SheetsStore(service, sheet_id="sheet-1")

    written = store.append_receipt(_row(card_id="Card_B1"))

    assert written is True
    _, kwargs = service.spreadsheets().values().append.call_args
    assert kwargs["range"] == "Card_B1!A1"
    assert kwargs["valueInputOption"] == "USER_ENTERED"
    assert kwargs["spreadsheetId"] == "sheet-1"


def test_append_receipt_skips_duplicate_message_id():
    service = MagicMock()
    service.spreadsheets().values().get().execute.return_value = {
        "values": [["msg-1"]]
    }
    store = SheetsStore(service, sheet_id="sheet-1")

    written = store.append_receipt(_row(card_id="Card_B1", message_id="msg-1"))

    assert written is False
    service.spreadsheets().values().append.assert_not_called()


def test_append_receipt_writes_when_message_id_absent():
    service = MagicMock()
    service.spreadsheets().values().get().execute.return_value = {
        "values": [["msg-other"]]
    }
    store = SheetsStore(service, sheet_id="sheet-1")

    written = store.append_receipt(_row(card_id="Card_B1", message_id="msg-1"))

    assert written is True
    service.spreadsheets().values().append.assert_called_once()


def test_from_env_wraps_credentials_with_30s_http_timeout(monkeypatch):
    monkeypatch.setenv("SHEET_ID", "sheet-x")
    fake_creds = MagicMock()
    monkeypatch.setattr(google.auth, "default", lambda scopes: (fake_creds, "proj"))
    captured = {}

    def fake_build(service, version, *, http=None, cache_discovery=None):
        captured["http"] = http
        captured["cache_discovery"] = cache_discovery
        return MagicMock()

    monkeypatch.setattr(store_module, "build", fake_build)

    result = SheetsStore.from_env()

    assert isinstance(result, SheetsStore)
    assert isinstance(captured["http"], AuthorizedHttp)
    assert captured["http"].http.timeout == 30
    assert captured["cache_discovery"] is False
