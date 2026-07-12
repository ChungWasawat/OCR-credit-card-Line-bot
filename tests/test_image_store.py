from unittest.mock import patch

import pytest

from app.image_store import get_image_store
from app.store_gcs import GcsStore


def test_get_image_store_defaults_to_gcs(monkeypatch):
    monkeypatch.delenv("IMAGE_STORE", raising=False)

    assert isinstance(get_image_store(), GcsStore)


def test_get_image_store_rejects_unknown_backend(monkeypatch):
    monkeypatch.setenv("IMAGE_STORE", "bogus")

    with pytest.raises(ValueError):
        get_image_store()


def test_gcs_store_delegates_to_gcs_upload_image():
    with patch("app.store_gcs.gcs_upload_image") as mock_upload:
        mock_upload.return_value = ("id1", "link1")

        result = GcsStore().upload_image(b"x", "f.jpg")

    mock_upload.assert_called_once_with(b"x", "f.jpg")
    assert result == ("id1", "link1")
