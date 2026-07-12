import logging
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from linebot.v3.messaging.exceptions import ApiException
from pydantic import ValidationError

import services.worker_main as worker_main
from app.ocr.base import OcrParseError
from app.payload import decode
from app.schema import ReceiptExtraction
from app.store import Card


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    worker_main.app.dependency_overrides.clear()


def _valid_ocr_dict() -> dict:
    return {
        "is_receipt": True,
        "date": "2026-07-10",
        "merchant": "Big C",
        "amount": 199.5,
        "last4": "1234",
        "details": "",
    }


def _body(**overrides) -> dict:
    defaults = dict(
        message_id="msg-1",
        webhook_event_id="wh-1",
        group_id="Cgroup",
        user_id="U123",
        reply_token="tok-1",
    )
    defaults.update(overrides)
    return defaults


def _wire(
    monkeypatch,
    *,
    store=None,
    line_api=None,
    blob_api=None,
    image_store=None,
    ocr_provider=None,
) -> TestClient:
    # ALLOWED_GROUP_ID must match _body()'s default group_id="Cgroup" — the worker now
    # enforces its own allowlist check (mirroring the webhook's) before doing any work.
    monkeypatch.setenv("ALLOWED_GROUP_ID", "Cgroup")

    store = store if store is not None else MagicMock()
    line_api = line_api if line_api is not None else MagicMock()
    blob_api = blob_api if blob_api is not None else MagicMock()
    blob_api.get_message_content.return_value = bytearray(b"fake-image-bytes")
    image_store = image_store if image_store is not None else MagicMock()
    image_store.upload_image.return_value = ("202607_msg-1.jpg", "https://storage.cloud.google.com/b/x.jpg")
    # Only apply the default valid-receipt dict when the caller didn't supply an
    # ocr_provider at all — a MagicMock()'s .extract.return_value auto-vivifies to
    # another MagicMock (never None), so "is it already configured?" can't be
    # detected on an object the caller passed in; callers who pass their own
    # ocr_provider must configure .extract themselves.
    if ocr_provider is None:
        ocr_provider = MagicMock()
        ocr_provider.extract.return_value = _valid_ocr_dict()
        # MagicMock() special-cases `.name` (reserved for the mock's own repr) — it does
        # NOT auto-vivify into a child mock like every other attribute, so it must be
        # set explicitly or worker_main's provider.name ends up non-string and breaks
        # payload encoding downstream.
        ocr_provider.name = "claude"

    worker_main.app.dependency_overrides[worker_main.get_store] = lambda: store
    worker_main.app.dependency_overrides[worker_main.get_line_clients] = lambda: (line_api, blob_api)
    worker_main.app.dependency_overrides[worker_main.get_image_store] = lambda: image_store
    # worker_main did `from app.ocr.factory import get_ocr_provider`, binding the name
    # into its own module namespace — patching app.ocr.factory.get_ocr_provider has no
    # effect on the already-bound name the handler actually calls. Patch the name where
    # it's looked up: worker_main.get_ocr_provider.
    monkeypatch.setattr(worker_main, "get_ocr_provider", lambda: ocr_provider)

    return TestClient(worker_main.app, raise_server_exceptions=False)


def test_task_happy_path_valid_receipt_returns_200(monkeypatch):
    store = MagicMock()
    store.read_cards.return_value = [
        Card(card_id="Card_A1", bank="Bank A", card_name="Plat", last4="1234", expiry="12/27")
    ]
    line_api = MagicMock()
    blob_api = MagicMock()
    image_store = MagicMock()
    ocr_provider = MagicMock()
    ocr_provider.extract.return_value = _valid_ocr_dict()
    ocr_provider.name = "claude"
    client = _wire(
        monkeypatch, store=store, line_api=line_api, blob_api=blob_api,
        image_store=image_store, ocr_provider=ocr_provider,
    )

    resp = client.post("/task", json=_body())

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    blob_api.get_message_content.assert_called_once_with("msg-1", _request_timeout=10)
    image_store.upload_image.assert_called_once()
    ocr_provider.extract.assert_called_once_with(b"fake-image-bytes")
    store.read_cards.assert_called_once()
    line_api.reply_message.assert_called_once()
    sent_request = line_api.reply_message.call_args[0][0]
    quick_reply_data = sent_request.messages[0].quick_reply.items[0].action.data
    assert decode(quick_reply_data).ocr_model == "claude"


def test_task_uploads_to_gcs_before_running_ocr(monkeypatch):
    image_store = MagicMock()
    ocr_provider = MagicMock()
    ocr_provider.name = "claude"
    call_order = []
    image_store.upload_image.side_effect = lambda *a, **k: (
        call_order.append("upload"),
        ("202607_msg-1.jpg", "https://x"),
    )[1]
    ocr_provider.extract.side_effect = lambda *a, **k: (call_order.append("ocr"), _valid_ocr_dict())[1]
    client = _wire(monkeypatch, image_store=image_store, ocr_provider=ocr_provider)

    resp = client.post("/task", json=_body())

    assert resp.status_code == 200
    assert call_order == ["upload", "ocr"]


def test_task_sender_defaults_to_unknown_when_user_id_none(monkeypatch):
    line_api = MagicMock()
    client = _wire(monkeypatch, line_api=line_api)

    resp = client.post("/task", json=_body(user_id=None))

    assert resp.status_code == 200
    line_api.reply_message.assert_called_once()


def test_task_ocr_parse_error_replies_cannot_read_returns_200_no_retry(monkeypatch):
    store = MagicMock()
    line_api = MagicMock()
    ocr_provider = MagicMock()
    ocr_provider.extract.side_effect = OcrParseError("model did not return JSON")
    client = _wire(monkeypatch, store=store, line_api=line_api, ocr_provider=ocr_provider)

    resp = client.post("/task", json=_body())

    assert resp.status_code == 200
    assert resp.json() == {"status": "content_error"}
    line_api.reply_message.assert_called_once()
    sent_request = line_api.reply_message.call_args[0][0]
    assert "Couldn't read" in sent_request.messages[0].text
    store.read_cards.assert_not_called()


def test_task_validation_error_from_model_validate_replies_cannot_read_returns_200(monkeypatch):
    line_api = MagicMock()
    client = _wire(monkeypatch, line_api=line_api)

    def _raise_validation_error(*a, **k):
        raise ValidationError.from_exception_data("ReceiptExtraction", [])

    monkeypatch.setattr(ReceiptExtraction, "model_validate", staticmethod(_raise_validation_error))

    resp = client.post("/task", json=_body())

    assert resp.status_code == 200
    assert resp.json() == {"status": "content_error"}
    line_api.reply_message.assert_called_once()


def test_task_transient_error_downloading_image_propagates_500(monkeypatch):
    blob_api = MagicMock()
    blob_api.get_message_content.side_effect = TimeoutError("network timeout")
    image_store = MagicMock()
    client = _wire(monkeypatch, blob_api=blob_api, image_store=image_store)

    resp = client.post("/task", json=_body())

    assert resp.status_code == 500
    image_store.upload_image.assert_not_called()


def test_task_transient_error_uploading_to_gcs_propagates_500(monkeypatch):
    image_store = MagicMock()
    image_store.upload_image.side_effect = ConnectionError("gcs 503")
    client = _wire(monkeypatch, image_store=image_store)

    resp = client.post("/task", json=_body())

    assert resp.status_code == 500


def test_task_transient_error_from_ocr_provider_propagates_500(monkeypatch):
    ocr_provider = MagicMock()
    ocr_provider.extract.side_effect = TimeoutError("llm timeout")
    client = _wire(monkeypatch, ocr_provider=ocr_provider)

    resp = client.post("/task", json=_body())

    assert resp.status_code == 500


def test_task_transient_error_reading_cards_propagates_500(monkeypatch):
    store = MagicMock()
    store.read_cards.side_effect = TimeoutError("sheets timeout")
    client = _wire(monkeypatch, store=store)

    resp = client.post("/task", json=_body())

    assert resp.status_code == 500


def test_task_reply_send_failure_5xx_propagates_500(monkeypatch):
    line_api = MagicMock()
    line_api.reply_message.side_effect = ApiException(status=500, reason="Internal error")
    client = _wire(monkeypatch, line_api=line_api)

    resp = client.post("/task", json=_body())

    assert resp.status_code == 500


def test_task_malformed_body_returns_422(monkeypatch):
    # Dependencies still need wiring even though the request never reaches the
    # handler body: FastAPI resolves Depends() params alongside body validation, so
    # an unwired get_store()/get_line_clients()/get_image_store() would hit real
    # Google/Line clients (and fail on missing ADC) before the 422 is ever produced.
    client = _wire(monkeypatch)

    resp = client.post("/task", json={"message_id": "msg-1"})

    assert resp.status_code == 422


def test_task_installment_receipt_records_total_not_monthly(monkeypatch):
    store = MagicMock()
    store.read_cards.return_value = [
        Card(card_id="Card_A1", bank="Bank A", card_name="Plat", last4="1234", expiry="12/27")
    ]
    line_api = MagicMock()
    ocr_provider = MagicMock()
    ocr_provider.name = "claude"
    ocr_provider.extract.return_value = {
        "is_receipt": True,
        "date": "2026-07-10",
        "merchant": "Big C",
        "amount": 5000.0,  # TOTAL, not the monthly installment figure
        "last4": "1234",
        "details": "10-month installment",
    }
    client = _wire(monkeypatch, store=store, line_api=line_api, ocr_provider=ocr_provider)

    resp = client.post("/task", json=_body())

    assert resp.status_code == 200
    sent_request = line_api.reply_message.call_args[0][0]
    assert "5,000.00" in sent_request.messages[0].text


def test_task_transient_failure_first_attempt_logs_warning_not_error(monkeypatch, caplog):
    ocr_provider = MagicMock()
    ocr_provider.extract.side_effect = TimeoutError("llm timeout")
    client = _wire(monkeypatch, ocr_provider=ocr_provider)

    with caplog.at_level(logging.WARNING):
        resp = client.post(
            "/task", json=_body(), headers={"X-CloudTasks-TaskRetryCount": "0"}
        )

    assert resp.status_code == 500
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].attempt == 1
    assert warnings[0].max_attempts == 3


def test_task_transient_failure_final_attempt_logs_error(monkeypatch, caplog):
    ocr_provider = MagicMock()
    ocr_provider.extract.side_effect = TimeoutError("llm timeout")
    client = _wire(monkeypatch, ocr_provider=ocr_provider)

    with caplog.at_level(logging.WARNING):
        resp = client.post(
            "/task", json=_body(), headers={"X-CloudTasks-TaskRetryCount": "2"}
        )

    assert resp.status_code == 500
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert errors[0].attempt == 3
    assert errors[0].max_attempts == 3


def test_task_missing_retry_count_header_treated_as_first_attempt(monkeypatch, caplog):
    ocr_provider = MagicMock()
    ocr_provider.extract.side_effect = TimeoutError("llm timeout")
    client = _wire(monkeypatch, ocr_provider=ocr_provider)

    with caplog.at_level(logging.WARNING):
        resp = client.post("/task", json=_body())  # no header at all

    assert resp.status_code == 500
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


def test_task_content_error_path_on_final_attempt_header_still_returns_200_no_error_log(
    monkeypatch, caplog
):
    ocr_provider = MagicMock()
    ocr_provider.extract.side_effect = OcrParseError("bad json")
    client = _wire(monkeypatch, ocr_provider=ocr_provider)

    with caplog.at_level(logging.WARNING):
        resp = client.post(
            "/task", json=_body(), headers={"X-CloudTasks-TaskRetryCount": "2"}
        )

    assert resp.status_code == 200
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]
    assert not [r for r in caplog.records if hasattr(r, "attempt")]


def test_task_non_allowlisted_group_id_rejected_no_processing(monkeypatch, caplog):
    blob_api = MagicMock()
    line_api = MagicMock()
    client = _wire(monkeypatch, blob_api=blob_api, line_api=line_api)

    with caplog.at_level(logging.ERROR):
        resp = client.post("/task", json=_body(group_id="Csomeothergroup"))

    assert resp.status_code == 200
    assert resp.json() == {"status": "rejected"}
    blob_api.get_message_content.assert_not_called()
    line_api.reply_message.assert_not_called()
    assert "Csomeothergroup" in caplog.text


def test_task_empty_allowlist_rejects_every_task(monkeypatch, caplog):
    blob_api = MagicMock()
    client = _wire(monkeypatch, blob_api=blob_api)
    monkeypatch.delenv("ALLOWED_GROUP_ID", raising=False)

    with caplog.at_level(logging.ERROR):
        resp = client.post("/task", json=_body())

    assert resp.status_code == 200
    assert resp.json() == {"status": "rejected"}
    blob_api.get_message_content.assert_not_called()
