# This file codifies the GCS bucket for receipt images. The bucket itself was
# created manually via gcloud in Task 5 (before Terraform existed for this
# project) — it is imported (see imports.tf), not created, on first apply.
# Attributes below are copied EXACTLY from `gcloud storage buckets describe`
# so the import plans clean with zero diff.

resource "google_storage_bucket" "receipts" {
  name                        = var.gcs_bucket
  location                    = "ASIA-SOUTHEAST1"
  storage_class                = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false  # never auto-delete receipt images on terraform destroy

  # Matches the real bucket's default soft-delete policy (7 days) — included
  # explicitly so Terraform doesn't see a diff against GCS's own default.
  soft_delete_policy {
    retention_duration_seconds = 604800
  }
}

# Custom role instead of roles/storage.objectCreator (create-only) or
# roles/storage.objectAdmin (would add get/list/update): the runtime SA needs exactly
# create (new receipt uploads) and delete (cancel-flow and failed-OCR cleanup,
# app/gcs.py delete_image) — never read or list. Although create+delete technically
# permits overwrite, the app forbids it: every upload passes if_generation_match=0
# (create-only semantics, app/gcs.py).
resource "google_project_iam_custom_role" "receipt_image_writer" {
  role_id     = "receiptImageWriter"
  title       = "Receipt Image Writer"
  description = "Create and delete receipt objects only — no read, list, or metadata update"
  permissions = [
    "storage.objects.create",
    "storage.objects.delete",
  ]
}

resource "google_storage_bucket_iam_member" "receipt_bot_image_writer" {
  bucket = google_storage_bucket.receipts.name
  role   = google_project_iam_custom_role.receipt_image_writer.id
  member = "serviceAccount:${google_service_account.receipt_bot.email}"
}
