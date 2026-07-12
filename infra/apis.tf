# This file enables all the Google Cloud APIs needed by the receipt bot.
# Without these APIs enabled, Terraform cannot create the resources.
# These are the same APIs you enabled manually in Task 3 (via GCP Console).

resource "google_project_service" "required_apis" {
  # Loop through each API and enable it
  for_each = toset([
    "run.googleapis.com",              # Cloud Run (for webhook and worker services)
    "cloudtasks.googleapis.com",       # Cloud Tasks (for queuing OCR jobs)
    "secretmanager.googleapis.com",    # Secret Manager (for API keys)
    "artifactregistry.googleapis.com", # Artifact Registry (for Docker images)
    "sheets.googleapis.com",           # Google Sheets (for receipt storage)
    "storage.googleapis.com",          # Google Cloud Storage (for receipt images)
    "iamcredentials.googleapis.com",   # IAM Credentials (for OIDC tokens)
    "monitoring.googleapis.com",       # Cloud Monitoring (for alerts)
    "logging.googleapis.com",          # Cloud Logging (for logs)
    "billingbudgets.googleapis.com",   # Billing Budgets (for cost alerts)
    "iam.googleapis.com",              # IAM (service account creation/management)
    "sts.googleapis.com",              # Security Token Service (Workload Identity Federation)
  ])

  service = each.value
  disable_on_destroy = false  # Don't disable APIs when terraform destroy is run
}
