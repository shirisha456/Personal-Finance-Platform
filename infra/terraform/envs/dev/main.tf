terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local state only. An S3+DynamoDB backend is the right choice for a
  # team sharing this state, commented out rather than deleted so it's
  # clear what's missing if this ever gets applied for real:
  #
  # backend "s3" {
  #   bucket         = "personal-finance-platform-terraform-state"
  #   key            = "dev/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "personal-finance-platform-terraform-locks"
  #   encrypt        = true
  # }
  #
  # This environment has never actually been `terraform apply`'d — see
  # docs/adr/0011-terraform-written-not-applied.md.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "personal-finance-platform"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

locals {
  name = "personal-finance-platform-${var.environment}"
}

module "vpc" {
  source = "../../modules/vpc"

  name     = local.name
  vpc_cidr = var.vpc_cidr
}

module "eks" {
  source = "../../modules/eks"

  name                = local.name
  kubernetes_version  = var.kubernetes_version
  public_subnet_ids   = module.vpc.public_subnet_ids
  private_subnet_ids  = module.vpc.private_subnet_ids
  node_instance_types = var.node_instance_types
  node_desired_size   = var.node_desired_size
}

# Re-reads the cluster just created so RDS/ElastiCache can scope their
# security groups to "whatever the EKS nodes' SG is" without the eks
# module having to know about RDS/ElastiCache at all.
data "aws_eks_cluster" "this" {
  name = module.eks.cluster_name

  depends_on = [module.eks]
}

module "rds" {
  source = "../../modules/rds"

  name                      = local.name
  vpc_id                    = module.vpc.vpc_id
  private_subnet_ids        = module.vpc.private_subnet_ids
  allowed_security_group_id = data.aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
  instance_class            = var.rds_instance_class
  multi_az                  = var.environment == "prod"
  deletion_protection       = var.environment == "prod"
}

module "elasticache" {
  source = "../../modules/elasticache"

  name                      = local.name
  vpc_id                    = module.vpc.vpc_id
  private_subnet_ids        = module.vpc.private_subnet_ids
  allowed_security_group_id = data.aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
  node_type                 = var.redis_node_type
}

module "iam" {
  source = "../../modules/iam"

  name              = local.name
  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_provider_url = module.eks.oidc_provider_url
  secret_arns       = [module.rds.secret_arn]
}

# Statement/net-worth exports and Plaid webhook payload archival —
# core-api reads accounts/holdings from Postgres directly at request
# time; only exports and archival go to S3.
resource "aws_s3_bucket" "archival" {
  bucket = "${local.name}-archival"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "archival" {
  bucket = aws_s3_bucket.archival.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}
