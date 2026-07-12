# This file creates the Cloud Tasks queue that sits between webhook and worker services.
# The queue automatically retries failed tasks according to the retry_config.
# This is how receipts are processed asynchronously without blocking the webhook response.

resource "google_cloud_tasks_queue" "receipt_queue" {
  name     = "receipt-tasks"
  location = var.region

  # Retry configuration (matches Task 9 requirements).
  # max_attempts MUST equal app/tasks.py's MAX_ATTEMPTS constant — Task 9's
  # WARNING/WARNING/ERROR log-severity split is keyed off that same number
  # via the X-CloudTasks-TaskRetryCount header, on every retry attempt.
  retry_config {
    max_attempts  = 3        # Try 3 times total (if first attempt fails, retry twice more)
    min_backoff   = "10s"    # Give a transient provider hiccup time to clear (0.1s retries instantly)
    max_backoff   = "300s"   # Cap wait at 5 minutes between retries
    max_doublings = 3        # 10s → 20s → 40s → 80s (capped at max_backoff)
  }

  depends_on = [
    google_project_service.required_apis["cloudtasks.googleapis.com"]
  ]
}
