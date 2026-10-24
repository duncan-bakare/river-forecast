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

variable "github_repo" {
  type    = string
  default = "duncan-bakare/river-forecast"
}
