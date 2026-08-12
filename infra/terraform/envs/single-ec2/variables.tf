variable "aws_profile" {
  description = "Named AWS CLI profile to use — never long-lived IAM user keys checked in anywhere."
  type        = string
  default     = "personal-finance-platform"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  type    = string
  default = "personal-finance-platform-portfolio"
}

variable "instance_type" {
  description = <<-EOT
    t3.large is the measured minimum, not a default picked without
    checking — see docs/adr/0012-single-ec2-instance-sizing.md for the
    actual docker stats measurements this is based on. Do not drop to
    t3.medium (4GiB): the production compose file's own memory *limits*
    alone sum to more than that, before OS overhead.
  EOT
  type        = string
  default     = "t3.large"
}

variable "root_volume_size_gb" {
  type    = number
  default = 40
}

variable "alert_email" {
  description = "Required — AWS Budgets notification target."
  type        = string
}

variable "monthly_budget_usd" {
  description = "A tripwire, not a spending cap — AWS Budgets can notify, it can't stop spend."
  type        = number
  default     = 80
}

variable "domain_name" {
  description = "Leave blank to deploy against the Elastic IP with no domain (HTTP only)."
  type        = string
  default     = ""
}

variable "route53_zone_id" {
  description = "Set only if the domain is already hosted elsewhere in Route 53. Leave blank with a domain_name set to have this create a new hosted zone."
  type        = string
  default     = ""
}
