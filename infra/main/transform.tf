# Raw -> curated Parquet. Uses AWS's public "AWS SDK for pandas" layer for pyarrow/numpy.
locals {
  pandas_layer = "arn:aws:lambda:${var.region}:336392948345:layer:AWSSDKPandas-Python312-Arm64:31"
  transform_schedules = {
    transform-realtime  = { expr = "cron(0 3 * * ? *)", input = { job = "realtime" } }
    transform-hydrodata = { expr = "cron(30 5 1 * ? *)", input = { job = "hydrodata" } }
  }
}

resource "aws_iam_role" "transform" {
  name                 = "river-forecast-transform"
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = local.boundary_arn
}

data "aws_iam_policy_document" "transform" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/raw/*"]
  }
  statement {
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.data.arn}/curated/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.transform.arn}:*"]
  }
}

resource "aws_iam_role_policy" "transform" {
  name   = "river-forecast-transform"
  role   = aws_iam_role.transform.id
  policy = data.aws_iam_policy_document.transform.json
}

resource "aws_cloudwatch_log_group" "transform" {
  name              = "/aws/lambda/river-forecast-transform"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "transform" {
  function_name    = "river-forecast-transform"
  role             = aws_iam_role.transform.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "river_forecast.transform.handler.lambda_handler"
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  layers           = [local.pandas_layer]
  memory_size      = 2048
  timeout          = 900

  environment {
    variables = {
      DATA_BUCKET = aws_s3_bucket.data.bucket
    }
  }

  depends_on = [aws_cloudwatch_log_group.transform, aws_iam_role_policy.transform]
}

resource "aws_lambda_function_event_invoke_config" "transform" {
  function_name          = aws_lambda_function.transform.function_name
  maximum_retry_attempts = 1
}

resource "aws_cloudwatch_event_rule" "transform" {
  for_each            = local.transform_schedules
  name                = "river-forecast-${each.key}"
  schedule_expression = each.value.expr
}

resource "aws_cloudwatch_event_target" "transform" {
  for_each = local.transform_schedules
  rule     = aws_cloudwatch_event_rule.transform[each.key].name
  arn      = aws_lambda_function.transform.arn
  input    = jsonencode(each.value.input)
}

resource "aws_lambda_permission" "transform_events" {
  for_each      = local.transform_schedules
  statement_id  = "events-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.transform.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.transform[each.key].arn
}

resource "aws_cloudwatch_metric_alarm" "transform_errors" {
  alarm_name          = "river-forecast-transform-errors"
  alarm_description   = "A transform job failed. Check /aws/lambda/river-forecast-transform."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.transform.function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
}
