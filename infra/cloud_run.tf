# This file creates the two Cloud Run services:
# 1. webhook-service: Public endpoint that Line calls when someone sends a receipt photo
# 2. worker-service: Private endpoint that Cloud Tasks calls to process OCR asynchronously
#
# Cloud Run is serverless — you pay only when code is actually running, no idle costs.
#
# PHASED APPLY: both services are gated behind var.deploy_services (count).
# On a fresh project there's no Docker image in Artifact Registry yet and no
# secret *versions* exist (only empty secret containers) — a Cloud Run
# revision referencing either would fail to start. Phase 1 applies everything
# EXCEPT these two resources; then secret versions are added and an image is
# pushed out-of-band; then deploy_services flips to true for Phase 2.

locals {
  # Docker image URI in Artifact Registry — GitHub Actions (Task 11) pushes here.
  image = "${var.region}-docker.pkg.dev/${var.gcp_project}/${google_artifact_registry_repository.receipt_bot.repository_id}/receipt-bot:latest"
}

# ============================================================================
# WEBHOOK SERVICE (public — callable by Line)
# ============================================================================
# Receives webhook events from Line. Verifies the signature, then either:
# - Enqueues a task to Cloud Tasks for async OCR processing (image messages), or
# - Handles card/category/skip/cancel postbacks synchronously (fast, no OCR)

resource "google_cloud_run_v2_service" "webhook" {
  count               = var.deploy_services ? 1 : 0
  name                = "webhook-service"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false  # hobby project — provider 6.x defaults this to true

  template {
    service_account = google_service_account.receipt_bot.email
    timeout         = "60s"  # webhook must always respond well under Line's own timeout
    scaling {
      max_instance_count = 3  # cost guardrail, not a real scale need at 4 users
      min_instance_count = 0  # scale to zero when idle; explicit to match the API's own
                               # reported default and avoid perpetual plan drift
    }

    containers {
      image = local.image
      # No args override: Dockerfile's default CMD already runs
      # services.webhook_main:app --host 0.0.0.0 --port 8080

      env {
        name = "LINE_CHANNEL_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.line_channel_secret.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "LINE_CHANNEL_ACCESS_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.line_access_token.secret_id
            version = "latest"
          }
        }
      }

      # WORKER_URL comes from the worker service's real .uri attribute — no
      # dependency cycle, since worker never references webhook.
      env {
        name  = "WORKER_URL"
        value = google_cloud_run_v2_service.worker[0].uri
      }
      # Required by services/webhook_main.py to mint the OIDC token used
      # when enqueuing tasks for Cloud Tasks to call the worker with.
      env {
        name  = "RECEIPT_BOT_SA_EMAIL"
        value = google_service_account.receipt_bot.email
      }
      env {
        name  = "GCP_PROJECT"
        value = var.gcp_project
      }
      env {
        name  = "REGION"
        value = var.region
      }
      env {
        name  = "TASKS_QUEUE"
        value = google_cloud_tasks_queue.receipt_queue.name
      }
      env {
        name  = "SHEET_ID"
        value = var.sheet_id
      }
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.receipts.name
      }
      # Empty until Task 12 discovers the real group ID from logs, then a
      # tfvars edit + apply fills this in and redeploys.
      env {
        name  = "ALLOWED_GROUP_ID"
        value = var.allowed_group_id
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.receipt_bot_line_secret,
    google_secret_manager_secret_iam_member.receipt_bot_line_token,
  ]

  # KNOWN ISSUE: `terraform plan` will always show a spurious in-place
  # update to this resource's `scaling` block (manual_instance_count and
  # min_instance_count "0 -> null"), a documented upstream quirk in the
  # google_cloud_run_v2_service provider's diff renderer. Confirmed via
  # `terraform show -json` that the plan's before/after values for scaling
  # are byte-identical — `terraform apply` is a genuine no-op here (doesn't
  # recreate the revision, doesn't change running behavior). Tried and
  # abandoned: `lifecycle { ignore_changes = [...] }` at several attribute
  # paths — none suppressed it, since the diff originates from the
  # provider's own CustomizeDiff logic, not a plain schema comparison
  # ignore_changes can override.
}

resource "google_cloud_run_v2_service_iam_member" "webhook_public" {
  count    = var.deploy_services ? 1 : 0
  name     = google_cloud_run_v2_service.webhook[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"  # Line's servers call this over plain HTTPS; the signature check is the real auth
}

# ============================================================================
# WORKER SERVICE (private — only the receipt-bot SA, via Cloud Tasks, can call it)
# ============================================================================
# Handles the async OCR pipeline:
# 1. Download the image from Line's content API
# 2. Upload to GCS
# 3. Run OCR (Claude / Typhoon / Gemini, per OCR_MODEL)
# 4. Validate the extracted data
# 5. Reply to the user with card buttons (or "please resend")

resource "google_cloud_run_v2_service" "worker" {
  count               = var.deploy_services ? 1 : 0
  name                = "worker-service"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"  # Cloud Tasks reaches it via Google's front end + OIDC; INTERNAL_ONLY would block that path
  deletion_protection = false

  template {
    service_account = google_service_account.receipt_bot.email
    timeout         = "600s"  # OCR calls can be slow; LLM client timeout itself is 60s (app/ocr), this is the outer Cloud Run bound
    scaling {
      max_instance_count = 3
      min_instance_count = 0  # scale to zero when idle; explicit to match the API's own
                               # reported default and avoid perpetual plan drift
    }

    containers {
      image = local.image
      # FULL uvicorn arg list required: ENTRYPOINT is bare ["uvicorn"], so
      # overriding `args` replaces the entire Dockerfile CMD, including the
      # --host/--port flags — omitting them here would make uvicorn bind its
      # default 127.0.0.1:8000 and fail Cloud Run's health check.
      args = ["services.worker_main:app", "--host", "0.0.0.0", "--port", "8080"]

      env {
        name = "LINE_CHANNEL_ACCESS_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.line_access_token.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "ANTHROPIC_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.anthropic_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "TYPHOON_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.typhoon_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "SHEET_ID"
        value = var.sheet_id
      }
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.receipts.name
      }
      env {
        name  = "IMAGE_STORE"
        value = "gcs"
      }
      env {
        name  = "OCR_MODEL"
        value = var.ocr_model
      }
      # worker_main.py independently re-checks the group allowlist
      # (defense-in-depth against a task somehow being enqueued with a
      # non-allowlisted group_id) — this was missing here originally,
      # causing every task to be rejected regardless of group_id.
      env {
        name  = "ALLOWED_GROUP_ID"
        value = var.allowed_group_id
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.receipt_bot_line_token,
    google_secret_manager_secret_iam_member.receipt_bot_anthropic,
    google_secret_manager_secret_iam_member.receipt_bot_typhoon,
    google_secret_manager_secret_iam_member.receipt_bot_gemini,
  ]

  # KNOWN ISSUE: `terraform plan` will always show a spurious in-place
  # update to this resource's `scaling` block (manual_instance_count and
  # min_instance_count "0 -> null"), a documented upstream quirk in the
  # google_cloud_run_v2_service provider's diff renderer. Confirmed via
  # `terraform show -json` that the plan's before/after values for scaling
  # are byte-identical — `terraform apply` is a genuine no-op here (doesn't
  # recreate the revision, doesn't change running behavior). Tried and
  # abandoned: `lifecycle { ignore_changes = [...] }` at several attribute
  # paths — none suppressed it, since the diff originates from the
  # provider's own CustomizeDiff logic, not a plain schema comparison
  # ignore_changes can override.
}

resource "google_cloud_run_v2_service_iam_member" "worker_invoker" {
  count    = var.deploy_services ? 1 : 0
  name     = google_cloud_run_v2_service.worker[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.receipt_bot.email}"  # ONLY this SA — no allUsers, no project-wide grant
}
