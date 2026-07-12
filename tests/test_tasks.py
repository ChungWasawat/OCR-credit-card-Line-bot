from unittest.mock import MagicMock

from google.api_core.exceptions import AlreadyExists, PermissionDenied
import pytest

from app.tasks import create_http_task


def _client(*, queue_path="projects/p/locations/l/queues/q") -> MagicMock:
    client = MagicMock()
    client.queue_path.return_value = queue_path
    client.task_path.side_effect = (
        lambda project, location, queue, task: f"{queue_path}/tasks/{task}"
    )
    return client


def test_create_http_task_builds_expected_request():
    client = _client()

    result = create_http_task(
        name="wh-event-1",
        url="https://worker.example/task",
        body=b'{"message_id": "msg-1"}',
        service_account_email="receipt-bot@p.iam.gserviceaccount.com",
        client=client,
        project="p",
        location="l",
        queue="q",
    )

    assert result is True
    client.queue_path.assert_called_once_with("p", "l", "q")
    client.task_path.assert_called_once_with("p", "l", "q", "wh-event-1")

    _, kwargs = client.create_task.call_args
    request = kwargs["request"]
    assert request["parent"] == "projects/p/locations/l/queues/q"
    task = request["task"]
    assert task.name == "projects/p/locations/l/queues/q/tasks/wh-event-1"
    assert task.http_request.url == "https://worker.example/task"
    assert task.http_request.body == b'{"message_id": "msg-1"}'
    assert task.http_request.oidc_token.service_account_email == (
        "receipt-bot@p.iam.gserviceaccount.com"
    )
    assert task.http_request.oidc_token.audience == "https://worker.example/task"
    assert kwargs["timeout"] == 30.0


def test_create_http_task_reads_project_region_queue_from_env(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT", "env-project")
    monkeypatch.setenv("REGION", "env-region")
    monkeypatch.setenv("TASKS_QUEUE", "env-queue")
    client = _client()

    create_http_task(
        name="wh-event-2",
        url="https://worker.example/task",
        body=b"{}",
        service_account_email="sa@p.iam.gserviceaccount.com",
        client=client,
    )

    client.queue_path.assert_called_once_with("env-project", "env-region", "env-queue")


def test_create_http_task_swallows_already_exists_returns_false():
    client = _client()
    client.create_task.side_effect = AlreadyExists("duplicate")

    result = create_http_task(
        name="wh-event-dup",
        url="https://worker.example/task",
        body=b"{}",
        service_account_email="sa@p.iam.gserviceaccount.com",
        client=client,
        project="p",
        location="l",
        queue="q",
    )

    assert result is False


def test_create_http_task_propagates_other_errors():
    client = _client()
    client.create_task.side_effect = PermissionDenied("nope")

    with pytest.raises(PermissionDenied):
        create_http_task(
            name="wh-event-3",
            url="https://worker.example/task",
            body=b"{}",
            service_account_email="sa@p.iam.gserviceaccount.com",
            client=client,
            project="p",
            location="l",
            queue="q",
        )
