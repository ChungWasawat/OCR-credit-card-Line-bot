# This file creates/adopts the two service accounts and sets up their permissions.
# Service accounts are "robot" accounts that run code in the cloud.
# Each has only the minimum permissions it needs (least privilege principle).

# ============================================================================
# SERVICE ACCOUNT 1: line-receipt-bot (runs the bot on Cloud Run)
# ============================================================================
# NOTE on naming: the checklist calls this SA "receipt-bot", but it was
# already created manually in Task 5 as "line-receipt-bot" — the Google Sheet
# is shared Editor with that exact email, it already has bucket permissions,
# and the local key.json belongs to it. Rather than create a second SA and
# redo all of that sharing, this SA is IMPORTED (see imports.tf) under its
# real name. Deviation is deliberate, not an oversight.
#
# It can:
# - Enqueue Cloud Tasks
# - Invoke the worker service (scoped to that one service, not project-wide)
# - Access secrets
# - Upload (create-only) images to GCS
# - Write to Google Sheets (via Sheet-level sharing done manually in Task 5 —
#   Sheets ACLs are not a Terraform-managed resource)

resource "google_service_account" "receipt_bot" {
  account_id   = "line-receipt-bot"
  display_name = "Receipt Bot Runtime"
  description  = "Service account for the receipt bot runtime (Cloud Run, Cloud Tasks)"
}

# ============================================================================
# SERVICE ACCOUNT 2: github-actions-deployer (used by GitHub Actions CI/CD)
# ============================================================================
# This account is used by GitHub Actions to:
# - Build Docker images
# - Push to Artifact Registry
# - Deploy to Cloud Run
# It CANNOT touch data (no Sheets, no secrets) — separation of concerns.
# Genuinely created by Terraform (not imported — did not exist before).

resource "google_service_account" "github_deployer" {
  account_id   = "github-actions-deployer"
  display_name = "GitHub Actions Deployer"
  description  = "Service account for GitHub Actions CI/CD (deploys code, cannot access data)"
}

# ============================================================================
# Receipt Bot IAM Roles (what it can do)
# ============================================================================

# Let receipt-bot enqueue tasks — scoped to the one queue it uses, not the
# whole project (tighter than the checklist's project-level wording).
resource "google_cloud_tasks_queue_iam_member" "receipt_bot_enqueuer" {
  name     = google_cloud_tasks_queue.receipt_queue.name
  location = var.region
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.receipt_bot.email}"
}

# NOTE: run.invoker on worker-service lives in cloud_run.tf
# (google_cloud_run_v2_service_iam_member.worker_invoker) — deliberately NOT
# a project-wide grant here, per the checklist's "run.invoker on
# worker-service" (singular, scoped), not "run.invoker on the project".

# Let receipt-bot impersonate itself (needed for OIDC token minting when enqueuing tasks)
resource "google_service_account_iam_member" "receipt_bot_iam_sa_user" {
  service_account_id = google_service_account.receipt_bot.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.receipt_bot.email}"
}

# ============================================================================
# GitHub Deployer IAM Roles (what it can do)
# ============================================================================

# Let deployer develop Cloud Run services (deploy, update, view)
resource "google_project_iam_member" "deployer_run_developer" {
  project = var.gcp_project
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
  depends_on = [
    google_project_service.required_apis["run.googleapis.com"]
  ]
}

# Let deployer push Docker images — scoped to the one repo it pushes to, not
# the whole project (tighter than the checklist's project-level wording).
resource "google_artifact_registry_repository_iam_member" "deployer_ar_writer" {
  location   = google_artifact_registry_repository.receipt_bot.location
  repository = google_artifact_registry_repository.receipt_bot.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.github_deployer.email}"
}

# Let deployer impersonate the receipt-bot service account (scoped to that
# one SA resource only). This is how GitHub Actions deploys services that
# run AS receipt-bot, without ever holding receipt-bot's own permissions.
resource "google_service_account_iam_member" "deployer_iam_sa_user" {
  service_account_id = google_service_account.receipt_bot.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.github_deployer.email}"
}
