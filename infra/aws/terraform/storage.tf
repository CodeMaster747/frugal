# Receipt storage.
#
# The only AWS-hosted state in the deployment. Postgres is on Neon and Redis on
# Upstash, both on their own free tiers, so nothing here scales with usage
# except the images themselves -- and the lifecycle rule below bounds those.

resource "aws_s3_bucket" "receipts" {
  bucket = "frugal-receipts-${data.aws_caller_identity.current.account_id}"

  # Account id in the name because S3 bucket names are globally unique across
  # every AWS customer; "frugal-receipts" was taken by someone years ago.
}

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket_public_access_block" "receipts" {
  bucket = aws_s3_bucket.receipts.id

  # All four, explicitly. Receipts are photographs of someone's shopping: they
  # carry names, card fragments, and locations. Nothing here is ever public, and
  # the application reaches objects through presigned URLs rather than by making
  # the bucket readable.
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "receipts" {
  bucket = aws_s3_bucket.receipts.id

  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3 rather than SSE-KMS: KMS bills per request, and at receipt volume
      # the request charges would exceed the storage cost while adding nothing
      # against the threat that matters here (a bucket left readable).
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "receipts" {
  bucket = aws_s3_bucket.receipts.id

  rule {
    id     = "expire-processed-receipts"
    status = "Enabled"

    filter {}

    # The image is input to OCR, not the record. Extracted fields and the
    # transaction live in Postgres and are unaffected; what expires is the
    # photograph, once nobody is plausibly still reviewing it.
    expiration {
      days = var.receipt_expiry_days
    }

    # A failed multipart upload leaves parts that are invisible in the console
    # and bill as storage indefinitely.
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# Versioning is deliberately NOT enabled. It would keep every overwritten and
# deleted object, which turns the expiry rule above into a no-op and makes
# storage grow without bound. Receipts are immutable once uploaded, so there is
# no version history worth keeping.
