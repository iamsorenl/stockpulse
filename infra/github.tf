# Deploys from GitHub Actions without storing AWS keys in GitHub.
#
# GitHub signs a short-lived token for each workflow run (OIDC). AWS trusts
# tokens from GitHub's issuer, and this role accepts only ones issued to pushes
# on main of this repo. The workflow trades the token for temporary credentials.

variable "github_repo" {
  type    = string
  default = "iamsorenl/stockpulse"
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "deploy" {
  name = "stockpulse-github-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          # Only pushes to main. Pull requests and other branches get no access.
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:ref:refs/heads/main"
        }
      }
    }]
  })
}

# What a deploy needs: push an image to this one repository, point this one
# function at it, and read the function URL for the smoke test.
resource "aws_iam_role_policy" "deploy" {
  name = "deploy-stockpulse"
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Docker login to ECR; AWS doesn't allow scoping this one to a repository.
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
        ]
        Resource = aws_ecr_repository.app.arn
      },
      {
        # Logged-in pulls from the public ECR registry (where the Lambda Web
        # Adapter image lives). Anonymous pulls from GitHub's shared runner IPs
        # get rate-limited (429). Neither action can be scoped to a resource.
        Effect   = "Allow"
        Action   = ["ecr-public:GetAuthorizationToken", "sts:GetServiceBearerToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "lambda:UpdateFunctionCode", "lambda:GetFunction",
          "lambda:GetFunctionConfiguration", # `aws lambda wait function-updated` polls this
          "lambda:GetFunctionUrlConfig",
        ]
        Resource = aws_lambda_function.app.arn
      },
    ]
  })
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}
