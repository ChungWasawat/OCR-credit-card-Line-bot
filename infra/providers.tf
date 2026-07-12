# This file configures Terraform itself and the Google Cloud provider.
# It specifies which Terraform version is needed, which providers to use,
# and where to store the Terraform state (local file by default).

terraform {
  required_version = ">= 1.5"  # >=1.5 needed for `import` blocks (see imports.tf)
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"  # Terraform Google provider for GCP resources
    }
  }

  # OPTIONAL: Use a GCS bucket to store Terraform state in the cloud
  # This is safer than storing state locally on your machine.
  # Uncomment after creating a GCS bucket for Terraform state:
  # backend "gcs" {
  #   bucket = "your-terraform-state-bucket"
  #   prefix = "receipt-bot"
  # }

  # DEFAULT: Local state stored in terraform.tfstate (gitignored in .gitignore)
}

# Configure the Google Cloud provider with your project and region
provider "google" {
  project = var.gcp_project
  region  = var.region

  # Required for google_billing_budget: user ADC needs an explicit quota
  # project, or Billing Budgets API calls fail with a permission error.
  user_project_override = true
  billing_project        = var.gcp_project
}
