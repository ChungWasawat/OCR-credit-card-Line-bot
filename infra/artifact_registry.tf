# This file creates the Artifact Registry repository where Docker images are stored.
# GitHub Actions will build and push Docker images here, and Cloud Run will pull from here.
# This is like a private Docker Hub for your project — images are not publicly accessible.

resource "google_artifact_registry_repository" "receipt_bot" {
  location      = var.region
  repository_id = "receipt-bot"
  description   = "Private Docker images for the receipt bot (webhook and worker services)"
  format        = "DOCKER"

  cleanup_policy_dry_run = false

  # Every CI deploy (Task 11) tags with a permanent, unique git-SHA tag that
  # is never removed or reused, so a tag_state=UNTAGGED condition would
  # almost never match anything — age is the only signal that actually
  # changes over time for this project's tagging scheme.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      tag_state  = "ANY"
      older_than = "2592000s"  # 30 days
    }
  }

  depends_on = [
    google_project_service.required_apis["artifactregistry.googleapis.com"]
  ]
}
