terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region

  # Every resource carries these. `Project` is not decoration: the budget action
  # in setup-cost-guardrails.sh selects instances to stop by this exact tag, and
  # the teardown in the runbook finds resources by it. Changing it breaks both.
  default_tags {
    tags = {
      Project   = "frugal"
      ManagedBy = "terraform"
    }
  }
}
