import datetime
from unittest.mock import MagicMock

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed

from app.gcs import delete_image, filename_for, upload_image, view_link_for


def test_filename_for_convention():
    when = datetime.datetime(2026, 3, 5, tzinfo=datetime.timezone.utc)

    assert filename_for("msg-123", when) == "202603_msg-123.jpg"


def test_upload_image_writes_blob_and_returns_authenticated_link():
    blob = MagicMock()
    blob.name = "202603_msg-1.jpg"
    bucket = MagicMock()
    bucket.blob.return_value = blob
    client = MagicMock()
    client.bucket.return_value = bucket

    blob_name, view_link = upload_image(
        b"fake-bytes", "202603_msg-1.jpg", client=client, bucket_name="my-bucket"
    )

    client.bucket.assert_called_once_with("my-bucket")
    bucket.blob.assert_called_once_with("202603_msg-1.jpg")
    blob.upload_from_string.assert_called_once_with(
        b"fake-bytes", content_type="image/jpeg", timeout=30, if_generation_match=0
    )
    assert blob_name == "202603_msg-1.jpg"
    assert view_link == "https://storage.cloud.google.com/my-bucket/202603_msg-1.jpg"
    assert "storage.googleapis.com" not in view_link


def test_upload_image_already_uploaded_412_returns_same_success_tuple():
    blob = MagicMock()
    blob.name = "202603_msg-1.jpg"
    blob.upload_from_string.side_effect = PreconditionFailed("already exists")
    bucket = MagicMock()
    bucket.blob.return_value = blob
    client = MagicMock()
    client.bucket.return_value = bucket

    blob_name, view_link = upload_image(
        b"fake-bytes", "202603_msg-1.jpg", client=client, bucket_name="my-bucket"
    )

    assert blob_name == "202603_msg-1.jpg"
    assert view_link == "https://storage.cloud.google.com/my-bucket/202603_msg-1.jpg"


def test_upload_image_transient_error_still_propagates():
    blob = MagicMock()
    blob.upload_from_string.side_effect = ConnectionError("network blip")
    bucket = MagicMock()
    bucket.blob.return_value = blob
    client = MagicMock()
    client.bucket.return_value = bucket

    with pytest.raises(ConnectionError):
        upload_image(b"fake-bytes", "202603_msg-1.jpg", client=client, bucket_name="my-bucket")


def test_delete_image_deletes_blob():
    blob = MagicMock()
    bucket = MagicMock()
    bucket.blob.return_value = blob
    client = MagicMock()
    client.bucket.return_value = bucket

    delete_image("202603_msg-1.jpg", client=client, bucket_name="my-bucket")

    client.bucket.assert_called_once_with("my-bucket")
    bucket.blob.assert_called_once_with("202603_msg-1.jpg")
    blob.delete.assert_called_once_with(timeout=30)


def test_delete_image_not_found_is_swallowed():
    blob = MagicMock()
    blob.delete.side_effect = NotFound("gone")
    bucket = MagicMock()
    bucket.blob.return_value = blob
    client = MagicMock()
    client.bucket.return_value = bucket

    delete_image("202603_msg-1.jpg", client=client, bucket_name="my-bucket")


def test_delete_image_any_failure_is_swallowed_and_logged(caplog):
    blob = MagicMock()
    blob.delete.side_effect = ConnectionError("network blip")
    bucket = MagicMock()
    bucket.blob.return_value = blob
    client = MagicMock()
    client.bucket.return_value = bucket

    with caplog.at_level("WARNING"):
        delete_image("202603_msg-1.jpg", client=client, bucket_name="my-bucket")

    assert "202603_msg-1.jpg" in caplog.text


def test_view_link_for_rebuilds_authenticated_link_from_blob_name_alone():
    link = view_link_for("202607_msg-1.jpg", bucket_name="my-bucket")

    assert link == "https://storage.cloud.google.com/my-bucket/202607_msg-1.jpg"


def test_view_link_for_reads_bucket_from_env(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET", "env-bucket")

    link = view_link_for("202607_msg-1.jpg")

    assert link == "https://storage.cloud.google.com/env-bucket/202607_msg-1.jpg"
