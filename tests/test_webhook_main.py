import base64
import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from google.api_core.exceptions import AlreadyExists
from linebot.v3.webhook import WebhookParser

import services.webhook_main as webhook_main
from app.store import TabNotFoundError

FIXTURES = Path(__file__).parent / "fixtures"
TEST_SECRET = "test-secret"
GROUP_ID = "Callowedgroupidxxxxxxxxxxxxxxxxxxx"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    webhook_main.app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", GROUP_ID)
    monkeypatch.setenv("WORKER_URL", "https://worker.example")
    monkeypatch.setenv("RECEIPT_BOT_SA_EMAIL", "sa@p.iam.gserviceaccount.com")
    monkeypatch.setenv("GCP_PROJECT", "p")
    monkeypatch.setenv("REGION", "l")
    monkeypatch.setenv("TASKS_QUEUE", "q")
    monkeypatch.setenv("GCS_BUCKET", "my-bucket")


def _sign(body: bytes) -> str:
    digest = hmac.new(TEST_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _tasks_client() -> MagicMock:
    client = MagicMock()
    client.queue_path.return_value = "projects/p/locations/l/queues/q"
    client.task_path.side_effect = (
        lambda project, location, queue, task: f"projects/{project}/locations/{location}/queues/{queue}/tasks/{task}"
    )
    return client


def _client(*, store=None, line_api=None, tasks_client=None) -> TestClient:
    store = store if store is not None else MagicMock()
    line_api = line_api if line_api is not None else MagicMock()
    tasks_client = tasks_client if tasks_client is not None else _tasks_client()
    webhook_main.app.dependency_overrides[webhook_main.get_store] = lambda: store
    webhook_main.app.dependency_overrides[webhook_main.get_line_api] = lambda: line_api
    webhook_main.app.dependency_overrides[webhook_main.get_tasks_client] = lambda: tasks_client
    webhook_main.app.dependency_overrides[webhook_main.get_parser] = lambda: WebhookParser(TEST_SECRET)
    return TestClient(webhook_main.app)


def _post(client: TestClient, fixture_name: str, *, signature: str | None = None):
    body = _fixture_bytes(fixture_name)
    sig = signature if signature is not None else _sign(body)
    return client.post(
        "/callback", content=body, headers={"X-Line-Signature": sig, "Content-Type": "application/json"}
    )


def test_callback_bad_signature_returns_403():
    client = _client()

    resp = _post(client, "image_message.json", signature="bm90LWEtcmVhbC1zaWc=")

    assert resp.status_code == 403


def test_callback_missing_signature_header_returns_403():
    client = _client()
    body = _fixture_bytes("image_message.json")

    resp = client.post("/callback", content=body, headers={"Content-Type": "application/json"})

    assert resp.status_code == 403


def test_callback_empty_events_returns_200():
    client = _client()
    body = json.dumps({"destination": "Uxxx", "events": []}).encode("utf-8")

    resp = client.post(
        "/callback", content=body, headers={"X-Line-Signature": _sign(body), "Content-Type": "application/json"}
    )

    assert resp.status_code == 200


def test_callback_image_event_creates_task_returns_200():
    store = MagicMock()
    tasks_client = _tasks_client()
    client = _client(store=store, tasks_client=tasks_client)

    resp = _post(client, "image_message.json")

    assert resp.status_code == 200
    tasks_client.create_task.assert_called_once()
    _, kwargs = tasks_client.create_task.call_args
    created_task = kwargs["request"]["task"]
    assert created_task.name.endswith("01WHIMAGE0000000000000001")
    assert created_task.http_request.url == "https://worker.example/task"
    body_json = json.loads(created_task.http_request.body)
    assert body_json["message_id"] == "msg-image-1"
    assert body_json["reply_token"] == "tok-image-1"
    assert body_json["group_id"] == GROUP_ID
    store.append_receipt.assert_not_called()


def test_callback_duplicate_webhook_event_id_swallowed_still_200():
    tasks_client = _tasks_client()
    tasks_client.create_task.side_effect = AlreadyExists("duplicate")
    client = _client(tasks_client=tasks_client)

    resp = _post(client, "duplicate_image_message.json")

    assert resp.status_code == 200


def test_callback_card_postback_replies_synchronously():
    line_api = MagicMock()
    store = MagicMock()
    client = _client(store=store, line_api=line_api)

    resp = _post(client, "card_postback.json")

    assert resp.status_code == 200
    line_api.reply_message.assert_called_once()
    store.append_receipt.assert_not_called()


def test_callback_process_anyway_postback_calls_read_cards_and_replies():
    line_api = MagicMock()
    store = MagicMock()
    store.read_cards.return_value = []
    client = _client(store=store, line_api=line_api)

    resp = _post(client, "process_anyway_postback.json")

    assert resp.status_code == 200
    store.read_cards.assert_called_once()
    line_api.reply_message.assert_called_once()


def test_callback_skip_postback_writes_row_and_replies_recorded():
    line_api = MagicMock()
    store = MagicMock()
    client = _client(store=store, line_api=line_api)

    resp = _post(client, "skip_postback.json")

    assert resp.status_code == 200
    store.append_receipt.assert_called_once()
    row = store.append_receipt.call_args[0][0]
    assert row.card_id == "Card_A1"
    assert row.category == "grocery"
    line_api.reply_message.assert_called_once()
    sent_request = line_api.reply_message.call_args[0][0]
    assert "✓" in sent_request.messages[0].text


def test_callback_skip_postback_append_receipt_fails_replies_error_not_recorded():
    line_api = MagicMock()
    store = MagicMock()
    store.append_receipt.side_effect = TabNotFoundError("no tab named Card_A1")
    client = _client(store=store, line_api=line_api)

    resp = _post(client, "skip_postback.json")

    assert resp.status_code == 200
    line_api.reply_message.assert_called_once()
    sent_request = line_api.reply_message.call_args[0][0]
    assert "Failed to record" in sent_request.messages[0].text
    assert "✓" not in sent_request.messages[0].text


def test_callback_cancel_postback_replies_no_write():
    line_api = MagicMock()
    store = MagicMock()
    client = _client(store=store, line_api=line_api)

    resp = _post(client, "cancel_postback.json")

    assert resp.status_code == 200
    store.append_receipt.assert_not_called()
    line_api.reply_message.assert_called_once()
    sent_request = line_api.reply_message.call_args[0][0]
    assert "Cancelled" in sent_request.messages[0].text


def test_callback_typed_details_text_message_writes_row():
    line_api = MagicMock()
    store = MagicMock()
    client = _client(store=store, line_api=line_api)

    resp = _post(client, "typed_details_text.json")

    assert resp.status_code == 200
    store.append_receipt.assert_called_once()
    row = store.append_receipt.call_args[0][0]
    assert row.details == "birthday gift for mom"


def test_callback_plain_text_message_ignored_no_reply():
    line_api = MagicMock()
    client = _client(line_api=line_api)
    body = json.dumps(
        {
            "destination": "Uxxx",
            "events": [
                {
                    "type": "message",
                    "source": {"type": "group", "groupId": GROUP_ID, "userId": "U123"},
                    "timestamp": 1751000070000,
                    "mode": "active",
                    "webhookEventId": "wh-text-1",
                    "deliveryContext": {"isRedelivery": False},
                    "replyToken": "tok-text-1",
                    "message": {"type": "text", "id": "msg-text-1", "text": "just chatting", "quoteToken": "qt-1"},
                }
            ],
        }
    ).encode("utf-8")

    resp = client.post(
        "/callback", content=body, headers={"X-Line-Signature": _sign(body), "Content-Type": "application/json"}
    )

    assert resp.status_code == 200
    line_api.reply_message.assert_not_called()


def test_callback_non_allowlisted_group_no_reply_200(monkeypatch):
    monkeypatch.setenv("ALLOWED_GROUP_ID", "Csomeothergroupnotallowedxxxxxxxxxx")
    line_api = MagicMock()
    tasks_client = _tasks_client()
    client = _client(line_api=line_api, tasks_client=tasks_client)

    resp = _post(client, "image_message.json")

    assert resp.status_code == 200
    line_api.reply_message.assert_not_called()
    tasks_client.create_task.assert_not_called()


def test_callback_unexpected_exception_in_one_event_does_not_abort_batch():
    line_api = MagicMock()
    line_api.reply_message.side_effect = [RuntimeError("boom"), None]
    store = MagicMock()
    client = _client(store=store, line_api=line_api)

    body = json.dumps(
        {
            "destination": "Uxxx",
            "events": [
                json.loads(_fixture_bytes("card_postback.json"))["events"][0],
                json.loads(_fixture_bytes("cancel_postback.json"))["events"][0],
            ],
        }
    ).encode("utf-8")

    resp = client.post(
        "/callback", content=body, headers={"X-Line-Signature": _sign(body), "Content-Type": "application/json"}
    )

    assert resp.status_code == 200
    assert line_api.reply_message.call_count == 2
