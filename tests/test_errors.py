from app.errors import classify_exception


class _StatusCodeExc(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


class _CodeExc(Exception):
    def __init__(self, code):
        self.code = code


class _StatusExc(Exception):
    def __init__(self, status):
        self.status = status


class _GrpcLikeExc(Exception):
    """Mimics grpc.RpcError: .code() is a callable, not an int attribute."""

    def code(self):
        return 14  # UNAVAILABLE


def test_status_code_401_is_auth():
    assert classify_exception(_StatusCodeExc(401)) == "auth"


def test_status_code_403_is_auth():
    assert classify_exception(_StatusCodeExc(403)) == "auth"


def test_status_code_429_is_rate_limit():
    assert classify_exception(_StatusCodeExc(429)) == "rate_limit"


def test_status_code_500_is_provider_error():
    assert classify_exception(_StatusCodeExc(500)) == "provider_error"


def test_status_code_503_is_provider_error():
    assert classify_exception(_StatusCodeExc(503)) == "provider_error"


def test_status_code_400_is_client_error():
    assert classify_exception(_StatusCodeExc(400)) == "client_error"


def test_status_code_404_is_client_error():
    assert classify_exception(_StatusCodeExc(404)) == "client_error"


def test_code_attribute_is_checked_when_no_status_code():
    # google.genai APIError / google.api_core exceptions expose `.code`, not
    # `.status_code`.
    assert classify_exception(_CodeExc(401)) == "auth"
    assert classify_exception(_CodeExc(429)) == "rate_limit"
    assert classify_exception(_CodeExc(503)) == "provider_error"


def test_status_attribute_is_checked_last():
    # linebot's ApiException exposes `.status`, not `.status_code` or `.code`.
    assert classify_exception(_StatusExc(429)) == "rate_limit"


def test_status_code_takes_precedence_over_code():
    class _Both(Exception):
        status_code = 401
        code = 429

    assert classify_exception(_Both()) == "auth"


def test_grpc_callable_code_is_ignored_not_an_int():
    assert classify_exception(_GrpcLikeExc()) == "unknown"


def test_timeout_error_instance_is_network():
    assert classify_exception(TimeoutError("timed out")) == "network"


def test_connection_error_by_class_name_is_network():
    class _WeirdConnectionIssue(Exception):
        pass

    assert classify_exception(_WeirdConnectionIssue()) == "network"


def test_timeout_by_class_name_is_network_even_without_timeouterror_base():
    # httpx.ConnectTimeout etc. do NOT subclass the builtin TimeoutError.
    class _HttpxLikeTimeout(Exception):
        pass

    assert classify_exception(_HttpxLikeTimeout()) == "network"


def test_plain_exception_with_no_signal_is_unknown():
    assert classify_exception(ValueError("nothing to go on")) == "unknown"


def test_out_of_range_status_is_unknown():
    assert classify_exception(_StatusCodeExc(200)) == "unknown"
