# This file adopts two resources into Terraform state that were already created
# manually in Task 5 — Terraform will NOT create or destroy them, it just starts
# tracking the existing GCP objects. Safe to delete this file after the first
# successful `terraform apply` (imports are a one-time operation).

import {
  to = google_storage_bucket.receipts
  id = "line-ocr-bot-receipts"
}

import {
  to = google_service_account.receipt_bot
  id = "projects/line-credit-card-ocr-bot/serviceAccounts/line-receipt-bot@line-credit-card-ocr-bot.iam.gserviceaccount.com"
}
