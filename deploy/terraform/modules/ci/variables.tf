variable "name" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "repository_names" {
  description = "ECR repositories to create. One is enough: the all-in-one image serves both services."
  type        = list(string)
  default     = ["snowobs"]
}

variable "keep_release_images" {
  description = "Release images to retain. Keep enough to roll back past a bad week."
  type        = number
  default     = 30
}

variable "github_repository" {
  description = "owner/repo the deploy role trusts, e.g. acme/snowobs."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "github_repository must be in owner/repo form."
  }
}

variable "allowed_git_refs" {
  description = <<-EOT
    Git refs whose workflow runs may assume the deploy role. Default is the
    default branch and release tags — deliberately not `*`, which would let a
    pull request from a fork assume a role that can push images.
  EOT
  type        = list(string)
  default     = ["refs/heads/main", "refs/tags/v*"]

  validation {
    condition     = !contains(var.allowed_git_refs, "*")
    error_message = "Refusing a wildcard ref: that trusts every branch and every fork PR."
  }
}

variable "create_oidc_provider" {
  description = "False when the account already has the GitHub OIDC provider (only one per issuer)."
  type        = bool
  default     = true
}

variable "existing_oidc_provider_arn" {
  description = "Required when create_oidc_provider is false."
  type        = string
  default     = null
}

variable "oidc_thumbprints" {
  description = <<-EOT
    Root CA thumbprints for token.actions.githubusercontent.com. IAM no longer
    validates these for the GitHub issuer, but the argument is still required;
    both of GitHub's published values are included so a rotation does not break
    an apply.
  EOT
  type        = list(string)
  default = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

variable "passable_role_arns" {
  description = "Task and execution role ARNs the pipeline may pass to ECS. Nothing else."
  type        = list(string)
}

variable "tags" {
  type    = map(string)
  default = {}
}
