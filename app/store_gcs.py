from __future__ import annotations

from app.gcs import delete_image as gcs_delete_image
from app.gcs import upload_image as gcs_upload_image
from app.image_store import ImageStore


class GcsStore(ImageStore):
    def upload_image(self, image: bytes, filename: str) -> tuple[str, str]:
        return gcs_upload_image(image, filename)

    def delete_image(self, blob_name: str) -> None:
        gcs_delete_image(blob_name)
