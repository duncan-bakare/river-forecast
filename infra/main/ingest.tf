data "archive_file" "ingest" {
  type        = "zip"
  source_dir  = "${path.module}/../../src"
  excludes    = ["river_forecast.egg-info", "**/__pycache__/**"]
  output_path = "${path.module}/build/ingest.zip"
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest" {
  name                 = "river-forecast-ingest"
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = local.boundary_arn
}

data "aws_iam_policy_document" "ingest" {
  statement {
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*"]
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "river-forecast-ingest"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/river-forecast-ingest"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "ingest" {
  function_name    = "river-forecast-ingest"
  role             = aws_iam_role.ingest.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "river_forecast.ingest.handler.lambda_handler"
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  memory_size      = 256
  timeout          = 600

  environment {
    variables = {
      DATA_BUCKET = aws_s3_bucket.data.bucket
    }
  }

  depends_on = [aws_cloudwatch_log_group.ingest, aws_iam_role_policy.ingest]
}

resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name          = aws_lambda_function.ingest.function_name
  maximum_retry_attempts = 1
}

locals {
  # Times are UTC.
  schedules = {
    opw-latest   = { expr = "cron(5 * * * ? *)", input = { job = "opw_latest" } }
    opw-csv-week = { expr = "cron(30 2 * * ? *)", input = { job = "opw_csv", period = "week" } }
    met-forecast = { expr = "cron(15 * * * ? *)", input = { job = "met_forecast" } }
    met-warnings = { expr = "cron(25 * * * ? *)", input = { job = "met_warnings" } }
    met-obs      = { expr = "cron(50 */3 * * ? *)", input = { job = "met_obs" } }
  }
}

resource "aws_cloudwatch_event_rule" "ingest" {
  for_each            = local.schedules
  name                = "river-forecast-${each.key}"
  schedule_expression = each.value.expr
}

resource "aws_cloudwatch_event_target" "ingest" {
  for_each = local.schedules
  rule     = aws_cloudwatch_event_rule.ingest[each.key].name
  arn      = aws_lambda_function.ingest.arn
  input    = jsonencode(each.value.input)
}

resource "aws_lambda_permission" "events" {
  for_each      = local.schedules
  statement_id  = "events-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ingest[each.key].arn
}

resource "aws_sns_topic" "ops" {
  name = "river-forecast-ops"
}

resource "aws_sns_topic_subscription" "ops_email" {
  count     = var.ops_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.ops.arn
  protocol  = "email"
  endpoint  = var.ops_email
}

resource "aws_cloudwatch_metric_alarm" "ingest_errors" {
  alarm_name          = "river-forecast-ingest-errors"
  alarm_description   = "An ingest job failed completely (all items). Check /aws/lambda/river-forecast-ingest."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.ingest.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
}
