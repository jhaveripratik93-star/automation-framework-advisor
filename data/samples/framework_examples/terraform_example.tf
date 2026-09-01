# Terraform — Infrastructure as Code example
# Install: https://developer.hashicorp.com/terraform/install
# Run:     terraform init && terraform plan && terraform apply

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

# Example: provision an S3 bucket for test artefacts
resource "aws_s3_bucket" "test_artefacts" {
  bucket = "my-test-artefacts-bucket"

  tags = {
    Environment = "test"
    ManagedBy   = "terraform"
  }
}

resource "aws_s3_bucket_versioning" "test_artefacts" {
  bucket = aws_s3_bucket.test_artefacts.id
  versioning_configuration {
    status = "Enabled"
  }
}
