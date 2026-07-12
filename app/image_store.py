from __future__ import annotations

import abc
import os


class ImageStore(abc.ABC):
    """Isolates business logic from raw GCS calls behind one seam.

    Mirrors the OcrProvider swappable-provider pattern (Task 6). GCS is the only
    backend — Drive was tried and removed (see checklist3.md Task 5): service
    accounts on a personal, non-Workspace Google account have zero Drive storage
    quota, and Drive's official workarounds (Shared Drives, domain-wide delegation)
    both require paid Workspace. The interface stays in place as a seam — cheap to
    keep, and it's what made swapping backends possible in the first place when
    Drive turned out not to work.
    """

    @abc.abstractmethod
    def upload_image(self, image: bytes, filename: str) -> tuple[str, str]: ...


def get_image_store() -> ImageStore:
    backend = os.environ.get("IMAGE_STORE", "gcs")
    if backend == "gcs":
        from app.store_gcs import GcsStore

        return GcsStore()
    raise ValueError(f"unknown IMAGE_STORE={backend!r}")
