# This file defines all input variables for the Terraform configuration.
# Real values are set in terraform.tfvars (gitignored) or terraform.tfvars.example
# (safe placeholders/non-secret values), never as defaults here for anything sensitive.

variable "gcp_project" {
  description = "GCP Project ID (from 'gcloud config get-value project')"
  type        = string
}

variable "region" {
  description = "GCP region for all resources (locked to Asia Southeast per checklist)"
  type        = string
  default     = "asia-southeast1"
}

variable "sheet_id" {
  description = "Google Sheets ID where receipts will be stored (from Task 2 .env). No default — set in gitignored terraform.tfvars only."
  type        = string
  sensitive   = true  # Don't print this value in logs
}

variable "gcs_bucket" {
  description = "GCS bucket name for storing receipt images (already created in Task 5, imported not created)"
  type        = string
}

variable "github_repo" {
  description = "GitHub repo (owner/repo) for Workload Identity Federation - used by Task 11 CI/CD"
  type        = string
}

variable "billing_account_id" {
  description = "GCP Billing Account ID (from 'gcloud billing accounts list'), used for the budget alert"
  type        = string
}

variable "alert_email" {
  description = "Email address for budget and ERROR-log alert notifications"
  type        = string
}

variable "budget_currency_code" {
  description = "Currency code for the monthly budget — MUST match the billing account's actual currency or the API rejects the resource"
  type        = string
  default     = "THB"
}

variable "budget_amount_units" {
  description = "Monthly budget amount in whole currency units (no decimals)"
  type        = string
  default     = "100"
}

variable "allowed_group_id" {
  description = "Line group ID allowlisted to use the bot. Empty until Task 12 discovers it from logs."
  type        = string
  default     = ""
}

variable "ocr_model" {
  description = "OCR provider for the worker service (claude|typhoon|gemini|typhoon_gemini). gemini is the first-deploy default — verified free path; claude is blocked until Anthropic billing is configured."
  type        = string
  default     = "gemini"
}

variable "deploy_services" {
  description = "Phase gate: false = core infra only (no image/secrets yet needed); true = also create the two Cloud Run services. Flip to true after Phase 1.5 (secret versions + docker image pushed)."
  type        = bool
  default     = false
}
