# Pulumi (Python) — Infrastructure as Code example
# Install: pip install pulumi pulumi-aws
# Run:     pulumi up

import pulumi
import pulumi_aws as aws

# Example: provision an S3 bucket for test artefacts
bucket = aws.s3.Bucket(
    "test-artefacts",
    tags={"Environment": "test", "ManagedBy": "pulumi"},
)

versioning = aws.s3.BucketVersioningV2(
    "test-artefacts-versioning",
    bucket=bucket.id,
    versioning_configuration=aws.s3.BucketVersioningV2VersioningConfigurationArgs(
        status="Enabled",
    ),
)

pulumi.export("bucket_name", bucket.id)
