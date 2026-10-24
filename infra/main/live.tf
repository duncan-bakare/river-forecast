# Hourly live Clonmel forecast. Reads OPW levels and rain, applies the saved models (numpy only, no extra layer)
# and writes site/data/forecast.json to the site bucket (created by infra/site) plus an archive copy.
locals {
  site_bucket  = "river-forecast-site-${local.account_id}"
  model_prefix = "models/clonmel/v1-2026-10-24/"
}

resource "aws_iam_role" "live" {
  name                 = "river-forecast-live"
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = local.boundary_arn
}

data "aws_iam_policy_document" "live" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/models/*", "${aws_s3_bucket.data.arn}/live/*"]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/live/*", "${aws_s3_bucket.data.arn}/forecasts/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${local.site_bucket}/data/forecast.json"]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.live.arn}:*"]
  }
}

resource "aws_iam_role_policy" "live" {
  name   = "river-forecast-live"
  role   = aws_iam_role.live.id
  policy = data.aws_iam_policy_document.live.json
}

resource "aws_cloudwatch_log_group" "live" {
  name              = "/aws/lambda/river-forecast-live"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "live" {
  function_name    = "river-forecast-live"
  role             = aws_iam_role.live.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "river_forecast.live.handler.lambda_handler"
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  layers           = [local.pandas_layer]
  memory_size      = 1024
  timeout          = 180

  environment {
    variables = {
      DATA_BUCKET  = aws_s3_bucket.data.bucket
      SITE_BUCKET  = local.site_bucket
      MODEL_PREFIX = local.model_prefix
    }
  }

  depends_on = [aws_cloudwatch_log_group.live, aws_iam_role_policy.live]
}

resource "aws_lambda_function_event_invoke_config" "live" {
  function_name          = aws_lambda_function.live.function_name
  maximum_retry_attempts = 0 # the next hourly run replaces a failed one; the site hides stale forecasts
}

resource "aws_cloudwatch_event_rule" "live" {
  name                = "river-forecast-live"
  schedule_expression = "cron(25 * * * ? *)"
}

resource "aws_cloudwatch_event_target" "live" {
  rule  = aws_cloudwatch_event_rule.live.name
  arn   = aws_lambda_function.live.arn
  input = jsonencode({ job = "live" })
}

resource "aws_lambda_permission" "live_events" {
  statement_id  = "events-live"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.live.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.live.arn
}

resource "aws_cloudwatch_metric_alarm" "live_errors" {
  alarm_name          = "river-forecast-live-errors"
  alarm_description   = "The live forecast Lambda failed. The site shows 'forecast unavailable' once the last forecast is old."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.live.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
}
