variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "ops_email" {
  description = "Optional address for ingest failure alarms (needs a one-off confirmation click)."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  type    = number
  default = 30
}
