# This file defines what values Terraform outputs after "terraform apply" finishes.
# These are important information you'll need for next steps (Task 11–12).

output "webhook_url" {
  description = "PUBLIC webhook URL (copy this into Line Developers console → Message API → Webhook URL). Placeholder until deploy_services=true (Phase 2)."
  value       = var.deploy_services ? google_cloud_run_v2_service.webhook[0].uri : "(not deployed yet — set deploy_services=true and re-apply)"
}

output "worker_url" {
  description = "PRIVATE worker URL (used internally by Cloud Tasks, not needed manually). Placeholder until deploy_services=true (Phase 2)."
  value       = var.deploy_services ? google_cloud_run_v2_service.worker[0].uri : "(not deployed yet — set deploy_services=true and re-apply)"
}

output "receipt_bot_sa_email" {
  description = "Receipt bot service account email — already has Sheet Editor + bucket access from Task 5"
  value       = google_service_account.receipt_bot.email
}

output "github_deployer_sa_email" {
  description = "GitHub Actions deployer service account email (used in Task 11 for Workload Identity Federation)"
  value       = google_service_account.github_deployer.email
}

output "workload_identity_provider" {
  description = "Full WIF provider resource name — pass this to google-github-actions/auth in Task 11's deploy.yml"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "artifact_registry_uri" {
  description = "Artifact Registry push URI (where GitHub Actions will push Docker images)"
  value       = "${var.region}-docker.pkg.dev/${var.gcp_project}/${google_artifact_registry_repository.receipt_bot.repository_id}"
}

output "gcs_bucket" {
  description = "GCS bucket name where receipt images are stored"
  value       = google_storage_bucket.receipts.name
}

output "tasks_queue_name" {
  description = "Cloud Tasks queue name (matches the name in app's TASKS_QUEUE env var)"
  value       = google_cloud_tasks_queue.receipt_queue.name
}
