# This file creates Secret Manager secrets for sensitive values (API keys, tokens).
# These secrets are stored securely and only the receipt-bot service account can read them.
#
# IMPORTANT: This creates the secret containers, but you must add the actual values manually
# after "terraform apply" completes, using:
#   gcloud secrets versions add line-channel-secret --data-file=-
#   gcloud secrets versions add line-access-token --data-file=-
#   etc.
#
# The values come from your .env file (set up in Tasks 3–4).

resource "google_secret_manager_secret" "line_channel_secret" {
  secret_id = "line-channel-secret"  # Line Messaging API channel secret (from .env)
  replication {
    auto {}  # Auto-replicate across regions
  }
  depends_on = [
    google_project_service.required_apis["secretmanager.googleapis.com"]
  ]
}

resource "google_secret_manager_secret" "line_access_token" {
  secret_id = "line-access-token"  # Line Messaging API access token (from .env)
  replication {
    auto {}
  }
  depends_on = [
    google_project_service.required_apis["secretmanager.googleapis.com"]
  ]
}

resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "anthropic-key"  # Anthropic API key for Claude OCR (from .env)
  replication {
    auto {}
  }
  depends_on = [
    google_project_service.required_apis["secretmanager.googleapis.com"]
  ]
}

resource "google_secret_manager_secret" "typhoon_api_key" {
  secret_id = "typhoon-key"  # Typhoon API key (OpenAI-compatible, from .env)
  replication {
    auto {}
  }
  depends_on = [
    google_project_service.required_apis["secretmanager.googleapis.com"]
  ]
}

resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "gemini-key"  # Google Gemini API key (from .env)
  replication {
    auto {}
  }
  depends_on = [
    google_project_service.required_apis["secretmanager.googleapis.com"]
  ]
}

# ============================================================================
# Grant receipt-bot service account access to each secret
# ============================================================================
# These IAM roles let the receipt-bot read the secrets when running on Cloud Run.
# Each secret is restricted to this one service account (least privilege).

resource "google_secret_manager_secret_iam_member" "receipt_bot_line_secret" {
  secret_id = google_secret_manager_secret.line_channel_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.receipt_bot.email}"
}

resource "google_secret_manager_secret_iam_member" "receipt_bot_line_token" {
  secret_id = google_secret_manager_secret.line_access_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.receipt_bot.email}"
}

resource "google_secret_manager_secret_iam_member" "receipt_bot_anthropic" {
  secret_id = google_secret_manager_secret.anthropic_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.receipt_bot.email}"
}

resource "google_secret_manager_secret_iam_member" "receipt_bot_typhoon" {
  secret_id = google_secret_manager_secret.typhoon_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.receipt_bot.email}"
}

resource "google_secret_manager_secret_iam_member" "receipt_bot_gemini" {
  secret_id = google_secret_manager_secret.gemini_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.receipt_bot.email}"
}
