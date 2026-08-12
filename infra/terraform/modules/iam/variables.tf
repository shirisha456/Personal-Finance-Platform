variable "name" {
  type = string
}

variable "oidc_provider_arn" {
  type = string
}

variable "oidc_provider_url" {
  description = "Bare issuer host, no https:// scheme (module.eks's oidc_provider_url output already strips it)."
  type        = string
}

variable "namespace" {
  type    = string
  default = "personal-finance-platform"
}

variable "service_account_name" {
  type    = string
  default = "core-api"
}

variable "secret_arns" {
  description = "Secrets Manager ARNs this role may read (e.g. module.rds.secret_arn)."
  type        = list(string)
}

variable "tags" {
  type    = map(string)
  default = {}
}
