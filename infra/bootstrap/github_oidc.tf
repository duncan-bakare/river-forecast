resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  state_bucket = aws_s3_bucket.tfstate.arn
}

data "aws_iam_policy_document" "gha_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only pushes to main can deploy; PRs (including forks) cannot assume this role.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "gha_deploy" {
  name                 = "river-forecast-gha-deploy"
  assume_role_policy   = data.aws_iam_policy_document.gha_trust.json
  max_session_duration = 3600
}

# Scoped to resources named river-forecast-* in the main stack. Bootstrap
# (budget, OIDC, this role) is applied locally and is not deployable from CI.
data "aws_iam_policy_document" "gha_deploy" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket]
  }
  statement {
    sid       = "StateObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.state_bucket}/main/*"]
  }
  statement {
    sid     = "ProjectBuckets"
    actions = ["s3:*"]
    resources = [
      "arn:aws:s3:::river-forecast-data-*",
      "arn:aws:s3:::river-forecast-data-*/*",
      "arn:aws:s3:::river-forecast-site-*",
      "arn:aws:s3:::river-forecast-site-*/*",
    ]
  }
  statement {
    sid       = "ProjectLambdas"
    actions   = ["lambda:*"]
    resources = ["arn:aws:lambda:${var.region}:${local.account_id}:function:river-forecast-*"]
  }
  statement {
    sid       = "PublicLayers"
    actions   = ["lambda:GetLayerVersion"]
    resources = ["*"]
  }
  statement {
    sid       = "ProjectRules"
    actions   = ["events:*"]
    resources = ["arn:aws:events:${var.region}:${local.account_id}:rule/river-forecast-*"]
  }
  statement {
    sid     = "ProjectLogs"
    actions = ["logs:*"]
    resources = [
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/river-forecast-*",
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/river-forecast-*:*",
    ]
  }
  statement {
    sid       = "ProjectAlarms"
    actions   = ["cloudwatch:*"]
    resources = ["arn:aws:cloudwatch:${var.region}:${local.account_id}:alarm:river-forecast-*"]
  }
  statement {
    sid       = "ProjectTopics"
    actions   = ["sns:*"]
    resources = ["arn:aws:sns:${var.region}:${local.account_id}:river-forecast-*"]
  }
  statement {
    sid = "ProjectRolesRead"
    actions = [
      "iam:GetRole", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole", "iam:DeleteRole", "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy", "iam:TagRole", "iam:UntagRole", "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy", "iam:GetPolicy", "iam:GetPolicyVersion",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:role/river-forecast-*",
      "arn:aws:iam::${local.account_id}:policy/river-forecast-*",
    ]
  }
  # Any role CI creates or modifies must carry the workload boundary, so a
  # compromised workflow cannot mint an admin role.
  statement {
    sid       = "ProjectRolesWriteWithBoundary"
    actions   = ["iam:CreateRole", "iam:PutRolePolicy", "iam:AttachRolePolicy"]
    resources = ["arn:aws:iam::${local.account_id}:role/river-forecast-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.workload_boundary.arn]
    }
  }
  statement {
    sid       = "PassProjectRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/river-forecast-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "events.amazonaws.com", "scheduler.amazonaws.com", "ecs-tasks.amazonaws.com"]
    }
  }
  statement {
    sid       = "NoBoundaryTampering"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary", "iam:PutRolePermissionsBoundary", "iam:CreatePolicyVersion", "iam:DeletePolicy", "iam:SetDefaultPolicyVersion"]
    resources = ["*"]
  }
  statement {
    sid       = "DenyEscalationOnDeployRole"
    effect    = "Deny"
    actions   = ["iam:*"]
    resources = [aws_iam_role.gha_deploy.arn]
  }
  statement {
    sid       = "ReadOnlyDescribes"
    actions   = ["logs:DescribeLogGroups", "cloudwatch:DescribeAlarms", "sts:GetCallerIdentity"]
    resources = ["*"]
  }
}

# Ceiling for every workload role in the main stack.
data "aws_iam_policy_document" "workload_boundary" {
  statement {
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"]
    resources = [
      "arn:aws:s3:::river-forecast-data-*",
      "arn:aws:s3:::river-forecast-data-*/*",
      "arn:aws:s3:::river-forecast-site-*",
      "arn:aws:s3:::river-forecast-site-*/*",
    ]
  }
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/river-forecast-*:*"]
  }
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = ["arn:aws:lambda:${var.region}:${local.account_id}:function:river-forecast-*"]
  }
  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "workload_boundary" {
  name   = "river-forecast-workload-boundary"
  policy = data.aws_iam_policy_document.workload_boundary.json
}

output "workload_boundary_arn" {
  value = aws_iam_policy.workload_boundary.arn
}

resource "aws_iam_role_policy" "gha_deploy" {
  name   = "river-forecast-gha-deploy"
  role   = aws_iam_role.gha_deploy.id
  policy = data.aws_iam_policy_document.gha_deploy.json
}

output "gha_deploy_role_arn" {
  value = aws_iam_role.gha_deploy.arn
}
