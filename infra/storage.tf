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

# Runtime SA can only CREATE new objects — never read, list, or delete
# existing ones (least privilege; matches "SA only ever uploads new receipts").
resource "google_storage_bucket_iam_member" "receipt_bot_object_creator" {
  bucket = google_storage_bucket.receipts.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.receipt_bot.email}"
}
