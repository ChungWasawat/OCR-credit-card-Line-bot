from unittest.mock import MagicMock

import pytest
from linebot.v3.messaging import TextMessage
from linebot.v3.messaging.exceptions import ApiException

import app.reply as reply_module
from app.reply import Reply, default_messaging_api, push_count, send


@pytest.fixture(autouse=True)
def _reset_push_count():
    reply_module._push_count = 0
    yield
    reply_module._push_count = 0


def _reply(**overrides) -> Reply:
    defaults = dict(
        reply_token="token-1",
        group_id="C123",
        messages=[TextMessage(text="hello")],
    )
    defaults.update(overrides)
    return Reply(**defaults)


def _invalid_token_400() -> ApiException:
    exc = ApiException(status=400, reason="Bad Request")
    exc.body = b'{"message":"Invalid reply token"}'
    return exc


def test_send_success_does_not_push():
    api = MagicMock()
    r = _reply()

    send(api, r)

    api.reply_message.assert_called_once()
    api.push_message.assert_not_called()
    assert push_count() == 0


def test_send_falls_back_to_push_on_400():
    api = MagicMock()
    api.reply_message.side_effect = _invalid_token_400()
    r = _reply(group_id="C999")

    send(api, r)

    api.push_message.assert_called_once()
    _, kwargs = api.push_message.call_args
    push_request = api.push_message.call_args[0][0]
    assert push_request.to == "C999"
    assert push_request.messages == r.messages
    assert push_count() == 1


def test_send_reraises_on_non_400_status():
    api = MagicMock()
    api.reply_message.side_effect = ApiException(status=500, reason="Internal error")
    r = _reply()

    with pytest.raises(ApiException):
        send(api, r)

    api.push_message.assert_not_called()
    assert push_count() == 0


def test_send_reraises_on_400_that_is_not_invalid_token():
    # A 400 whose body is NOT "Invalid reply token" means the message payload itself
    # is malformed — pushing the same payload would 400 again, so it must re-raise.
    api = MagicMock()
    exc = ApiException(status=400, reason="Bad Request")
    exc.body = b'{"message":"The request body has 1 error(s)"}'
    api.reply_message.side_effect = exc

    with pytest.raises(ApiException):
        send(api, _reply())

    api.push_message.assert_not_called()
    assert push_count() == 0


def test_send_falls_back_when_400_body_is_str_not_bytes():
    api = MagicMock()
    exc = ApiException(status=400, reason="Bad Request")
    exc.body = '{"message":"Invalid reply token"}'
    api.reply_message.side_effect = exc

    send(api, _reply())

    api.push_message.assert_called_once()


def test_push_count_accumulates_across_calls():
    api = MagicMock()
    api.reply_message.side_effect = _invalid_token_400()

    send(api, _reply())
    send(api, _reply())

    assert push_count() == 2


def test_default_messaging_api_reads_access_token_from_env(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")

    api = default_messaging_api()

    assert api is not None


def test_reply_message_uses_10s_request_timeout():
    api = MagicMock()

    send(api, _reply())

    _, kwargs = api.reply_message.call_args
    assert kwargs["_request_timeout"] == 10


def test_push_message_uses_10s_request_timeout():
    api = MagicMock()
    api.reply_message.side_effect = _invalid_token_400()

    send(api, _reply())

    _, kwargs = api.push_message.call_args
    assert kwargs["_request_timeout"] == 10
