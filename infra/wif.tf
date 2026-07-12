# This file sets up Workload Identity Federation (WIF) — lets GitHub Actions
# authenticate to GCP as github-actions-deployer WITHOUT any stored Google
# credential in GitHub. GitHub's own OIDC token is exchanged for short-lived
# GCP credentials at CI run time. Used by Task 11's deploy.yml.
#
# CAUTION: deleted WIF pools soft-delete for 30 days and the pool ID cannot
# be reused during that window — don't casually destroy/recreate this.

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions"
  description               = "Identity pool for GitHub Actions CI/CD (Task 11)"

  depends_on = [
    google_project_service.required_apis["iam.googleapis.com"]
  ]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  # REQUIRED by Google for the GitHub issuer: without an attribute_condition,
  # any GitHub repo anywhere could mint tokens against this pool. This scopes
  # it to exactly one repo.
  attribute_condition = "assertion.repository == \"${var.github_repo}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Let workflows running in that one GitHub repo impersonate the deployer SA.
resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.github_deployer.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}
