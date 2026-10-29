# StockPulse on AWS Lambda: container image, public Function URL, DynamoDB
# storage, Groq key read from SSM at startup.
#
# First apply, since the Lambda needs an image that exists:
#   terraform apply -target=aws_ecr_repository.app
#   (build + push an image tagged "bootstrap", see README)
#   terraform apply
# After that, pushes to main deploy new images via GitHub Actions.

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-west-2"
}

# Image the function is created with. Only used on the first apply: after that,
# the deploy workflow sets the live image and Terraform ignores it (see
# lifecycle on aws_lambda_function.app).
variable "image_tag" {
  type    = string
  default = "bootstrap"
}

# Created once by hand so the key never enters Terraform state:
#   aws ssm put-parameter --name /stockpulse/groq-api-key --type SecureString --value <key>
variable "groq_key_param" {
  type    = string
  default = "/stockpulse/groq-api-key"
}

data "aws_caller_identity" "me" {}

# --- Image registry ------------------------------------------------------------

resource "aws_ecr_repository" "app" {
  name         = "stockpulse"
  force_delete = true # lets `terraform destroy` remove it even with images inside
}

# Image storage is billed per GB after ECR's first-year allowance; keep only the
# newest 3 images (the live one plus two to roll back to).
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the newest 3 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}

# --- Permissions the function runs with ------------------------------------------

resource "aws_iam_role" "lambda" {
  name = "stockpulse-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# AWS-managed policy: lets the function write its own CloudWatch logs, nothing else.
resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Read access to the one SSM parameter holding the Groq key. SSM's default
# encryption key (aws/ssm) already lets callers decrypt through SSM, so no
# separate KMS grant is needed.
resource "aws_iam_role_policy" "groq_key" {
  name = "read-groq-key"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameter"
      Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.me.account_id}:parameter${var.groq_key_param}"
    }]
  })
}

# --- Storage -------------------------------------------------------------------------

# One table for the API cache, sentiment history, the shared Groq cap and the
# free-tier meter (see backend/app/dynamo_store.py for the key layout).
# Fixed capacity, not on-demand: DynamoDB's always-free tier covers 25 reads and
# 25 writes per second of fixed capacity per region, but nothing on-demand.
# A burst above 10/s gets slowed down instead of billed.
resource "aws_dynamodb_table" "app" {
  name           = "stockpulse"
  billing_mode   = "PROVISIONED"
  read_capacity  = 10
  write_capacity = 10
  hash_key       = "pk"
  range_key      = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  # DynamoDB deletes items once their `ttl` (epoch seconds) has passed.
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

# The function can read and write items in this one table, nothing else.
resource "aws_iam_role_policy" "table" {
  name = "use-table"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"]
      Resource = aws_dynamodb_table.app.arn
    }]
  })
}

# --- The function -----------------------------------------------------------------

resource "aws_lambda_function" "app" {
  function_name = "stockpulse"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
  architectures = ["arm64"]
  memory_size   = 1024
  timeout       = 180 # cold-ticker sentiment takes 90-130 s

  # Create the log group (with its retention) before the function can write to it.
  depends_on = [aws_cloudwatch_log_group.app]

  # CI deploys new images (.github/workflows/deploy.yml); don't roll them back.
  lifecycle {
    ignore_changes = [image_uri]
  }

  environment {
    variables = {
      GROQ_API_KEY_PARAM = var.groq_key_param
      STOCKPULSE_STORE   = "dynamodb"
      STOCKPULSE_TABLE   = aws_dynamodb_table.app.name
    }
  }
}

# --- Public HTTPS URL ---------------------------------------------------------------

resource "aws_lambda_function_url" "app" {
  function_name      = aws_lambda_function.app.function_name
  authorization_type = "NONE"
}

# A public URL needs two grants: one to call the URL, one to invoke the function
# through it.
resource "aws_lambda_permission" "url" {
  statement_id           = "public-url"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.app.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "invoke" {
  statement_id             = "public-invoke-via-url"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.app.function_name
  principal                = "*"
  invoked_via_function_url = true
}

output "ecr_url" {
  value = aws_ecr_repository.app.repository_url
}

output "url" {
  value = aws_lambda_function_url.app.function_url
}

# --- Free-tier meter switch --------------------------------------------------------

# The app's meter (backend/app/dynamo_store.py) sets the function's concurrency
# to 0 when monthly usage reaches 80% of the free allowance. This lets it.
resource "aws_iam_role_policy" "meter_switch" {
  name = "switch-self-off"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:PutFunctionConcurrency"
      Resource = aws_lambda_function.app.arn
    }]
  })
}

# --- Logs, alarms, budget ----------------------------------------------------------

variable "alert_email" {
  type    = string
  default = "iamsorenl@gmail.com"
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/aws/lambda/stockpulse"
  retention_in_days = 14
}

# Alarms publish here; AWS emails a confirmation link to click once.
resource "aws_sns_topic" "alerts" {
  name = "stockpulse-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Function crashed or timed out (an app-level 500 response doesn't count).
resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "stockpulse-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.app.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Requests are being rejected: either the meter switched the site off, or
# traffic exceeded the account's concurrency limit.
resource "aws_cloudwatch_metric_alarm" "throttles" {
  alarm_name          = "stockpulse-throttled"
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = aws_lambda_function.app.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Backup for the meter: emails if the real bill passes $0.50 or $1. Budgets
# only notify; they don't stop spending.
resource "aws_budgets_budget" "monthly" {
  name         = "stockpulse-monthly"
  budget_type  = "COST"
  limit_amount = "1"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [50, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }
}
