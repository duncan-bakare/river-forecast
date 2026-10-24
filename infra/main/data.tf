data "aws_caller_identity" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  boundary_arn = "arn:aws:iam::${local.account_id}:policy/river-forecast-workload-boundary"
  data_bucket  = "river-forecast-data-${local.account_id}"
}

resource "aws_s3_bucket" "data" {
  bucket = local.data_bucket
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Hourly national snapshots are a safety net; the per-station CSVs are the
  # primary record. Keep a year of snapshots.
  # Monthly Hydro-Data snapshots are kept as a record of OPW's revisions, but
  # only the newest is read. Older ZIPs go to cheap tiers, then expire after 5 years.
  # The size filter skips the small metadata JSONs (Glacier bills a 128 KB minimum).
  rule {
    id     = "archive-old-hydrodata-snapshots"
    status = "Enabled"
    filter {
      and {
        prefix                   = "raw/opw_hydrodata/"
        object_size_greater_than = 131072
      }
    }
    transition {
      days          = 35
      storage_class = "GLACIER_IR"
    }
    transition {
      days          = 180
      storage_class = "DEEP_ARCHIVE"
    }
    expiration {
      days = 1825
    }
  }

  rule {
    id     = "expire-national-snapshots"
    status = "Enabled"
    filter {
      prefix = "raw/opw_latest/"
    }
    expiration {
      days = 365
    }
  }
}

output "data_bucket" {
  value = aws_s3_bucket.data.bucket
}
