variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "alert_email" {
  description = "Where budget and billing alerts go. Set in terraform.tfvars (gitignored)."
  type        = string
}

variable "monthly_budget" {
  type    = number
  default = 11
}

variable "budget_currency" {
  description = "Account only supports USD budgets; 11 USD is just under EUR 10."
  type        = string
  default     = "USD"
}

variable "billing_alarm_usd" {
  description = "EstimatedCharges is always reported in USD; ~EUR 10."
  type        = number
  default     = 11
}

variable "github_sub_prefix" {
  description = "OIDC sub prefix. The repo uses GitHub's immutable subject format (owner and repo IDs), see GET /repos/{repo}/actions/oidc/customization/sub."
  type        = string
  default     = "repo:duncan-bakare@182490072/river-forecast@1432169429"
}
