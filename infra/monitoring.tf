# This file sets up monitoring and alerting.
# If something goes wrong (errors, high spending), you'll be notified by email.

data "google_project" "project" {
  project_id = var.gcp_project
}

resource "google_monitoring_notification_channel" "email" {
  display_name = "Receipt bot alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

# Fires when an ERROR-severity log line appears in either Cloud Run service.
# Four sites emit ERROR, all genuine failures worth a human look, not noise:
#   - worker: retry chain exhausted (Task 9's WARNING/WARNING/ERROR pattern,
#     services/worker_main.py — "task failed, retries exhausted")
#   - worker: task body's group_id isn't allowlisted (defense in depth,
#     services/worker_main.py — "rejected task: group not allowlisted")
#   - webhook: unhandled exception processing an event (Task 8's
#     defense-in-depth catch-all, services/webhook_main.py)
#   - webhook: Sheets row write failed, e.g. TabNotFoundError
#     (app/handlers.py::_write_and_confirm — "failed to append receipt row")
# See docs/logging.md for the full severity/field reference.
resource "google_monitoring_alert_policy" "error_logs" {
  display_name = "receipt-bot ERROR logs"
  combiner     = "OR"

  conditions {
    display_name = "ERROR entry from webhook/worker"

    condition_matched_log {
      filter = "resource.type=\"cloud_run_revision\" AND severity>=ERROR AND resource.labels.service_name=(\"webhook-service\" OR \"worker-service\")"
    }
  }

  # notification_rate_limit is REQUIRED with condition_matched_log — caps
  # how often this can page you even if many receipts fail in a burst.
  alert_strategy {
    notification_rate_limit {
      period = "3600s"  # at most 1 email/hour
    }
    auto_close = "604800s"  # auto-resolve after 7 days of no new matches
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  depends_on = [
    google_project_service.required_apis["monitoring.googleapis.com"]
  ]
}

# ============================================================================
# BILLING BUDGET — 100 THB/month (verified against the real billing account's
# currency via `gcloud billing accounts describe`, since a mismatched
# currency_code makes this API reject the resource outright)
# ============================================================================
resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account_id
  display_name    = "receipt-bot monthly budget"

  budget_filter {
    projects        = ["projects/${data.google_project.project.number}"]
    calendar_period = "MONTH"
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency_code
      units         = var.budget_amount_units
    }
  }

  threshold_rules { threshold_percent = 0.5 }  # fractions, not percentages
  threshold_rules { threshold_percent = 0.9 }
  threshold_rules { threshold_percent = 1.0 }

  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.email.id]
    disable_default_iam_recipients   = false  # billing admins still get GCP's own default emails too
  }

  depends_on = [
    google_project_service.required_apis["billingbudgets.googleapis.com"]
  ]
}
