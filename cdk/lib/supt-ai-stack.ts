import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigatewayv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

interface SuptAiStackProps extends cdk.StackProps {
  /** GitHub org/user and repo name, e.g. "my-org/supt-ai" */
  githubRepo: string;
}

export class SuptAiStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: SuptAiStackProps) {
    super(scope, id, props);

    // ─── GitHub Actions OIDC Provider ────────────────────────────────────
    // NOTE: Only one OIDC provider per account per URL is allowed.
    // If you already have one, use iam.OpenIdConnectProvider.fromOpenIdConnectProviderArn() instead.
    const oidcProvider = new iam.OpenIdConnectProvider(this, 'GitHubOidcProvider', {
      url: 'https://token.actions.githubusercontent.com',
      clientIds: ['sts.amazonaws.com'],
    });

    // ─── GitHub Actions Deploy Role (OIDC) ───────────────────────────────
    // Scoped to the production environment on this repo.
    // Bootstrap note: the very first deploy must be done manually (or via
    // access keys) since this role won't exist yet. After that, the role
    // manages itself.
    const deployRole = new iam.Role(this, 'GitHubActionsDeployRole', {
      roleName: 'supt-ai-github-deploy',
      assumedBy: new iam.WebIdentityPrincipal(
        oidcProvider.openIdConnectProviderArn,
        {
          StringEquals: {
            'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
          },
          StringLike: {
            'token.actions.githubusercontent.com:sub': `repo:${props.githubRepo}:environment:production`,
          },
        },
      ),
      description: 'Role assumed by GitHub Actions via OIDC to deploy the SuptAi CDK stack',
      maxSessionDuration: cdk.Duration.hours(1),
    });

    // Grant the deploy role permission to manage this stack's resources.
    // Scope this down once your stack is stable.
    deployRole.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('AdministratorAccess'),
    );

    // Secrets Manager — create empty secrets, populate manually once
    const secrets = new secretsmanager.Secret(this, 'SuptAiSecrets', {
      secretName: 'supt-ai/config',
      description: 'supt-ai secrets (GitHub App credentials, webhook secret, xAI key, Discord URL)',
      secretObjectValue: {
        GITHUB_APP_ID: cdk.SecretValue.unsafePlainText(''),
        GITHUB_APP_PRIVATE_KEY: cdk.SecretValue.unsafePlainText(''),
        GITHUB_APP_INSTALLATION_ID: cdk.SecretValue.unsafePlainText(''),
        WEBHOOK_SECRET: cdk.SecretValue.unsafePlainText(''),
        XAI_API_KEY: cdk.SecretValue.unsafePlainText(''),
        DISCORD_WEBHOOK_URL: cdk.SecretValue.unsafePlainText(''),
      },
    });

    // Lambda function (Docker image built from local Dockerfile)
    const reviewerFn = new lambda.DockerImageFunction(this, 'ReviewerFunction', {
      functionName: 'supt-ai-reviewer',
      code: lambda.DockerImageCode.fromImageAsset('../docker'),
      memorySize: 512,
      timeout: cdk.Duration.seconds(90),
      architecture: lambda.Architecture.X86_64,
      reservedConcurrentExecutions: 2,
      logRetention: logs.RetentionDays.TWO_WEEKS,
      environment: {
        HOME: '/tmp',
        SECRETS_ARN: secrets.secretArn,
        CONFIG__MODEL: 'xai/grok-4.3',
        CONFIG__CUSTOM_MODEL_MAX_TOKENS: '131072',
        CONFIG__FALLBACK_MODELS: '["xai/grok-4.3"]',
        OPENAI__KEY: 'none',
      },
    });

    // Grant Lambda read access to secrets
    secrets.grantRead(reviewerFn);

    // API Gateway HTTP API
    const httpApi = new apigatewayv2.HttpApi(this, 'WebhookApi', {
      apiName: 'supt-ai-webhook',
      description: 'Receives GitHub webhook events for PR reviews',
    });

    // Throttle: ~10 requests/minute sustained, burst of 10 for short spikes
    const stage = httpApi.defaultStage?.node.defaultChild as apigatewayv2.CfnStage;
    stage.defaultRouteSettings = {
      throttlingRateLimit: 2,
      throttlingBurstLimit: 10,
    };

    // POST /webhook → Lambda
    const lambdaIntegration = new integrations.HttpLambdaIntegration(
      'ReviewerIntegration',
      reviewerFn,
    );

    httpApi.addRoutes({
      path: '/webhook',
      methods: [apigatewayv2.HttpMethod.POST],
      integration: lambdaIntegration,
    });

    // Outputs
    new cdk.CfnOutput(this, 'ApiEndpoint', {
      value: httpApi.apiEndpoint,
      description: 'API Gateway endpoint URL',
    });

    new cdk.CfnOutput(this, 'WebhookUrl', {
      value: `${httpApi.apiEndpoint}/webhook`,
      description: 'Full webhook URL for GitHub',
    });

    new cdk.CfnOutput(this, 'FunctionName', {
      value: reviewerFn.functionName,
      description: 'Lambda function name',
    });

    new cdk.CfnOutput(this, 'SecretsArn', {
      value: secrets.secretArn,
      description: 'Secrets Manager ARN — populate via console or CLI',
    });

    new cdk.CfnOutput(this, 'DeployRoleArn', {
      value: deployRole.roleArn,
      description: 'IAM Role ARN for GitHub Actions OIDC deploy — set as AWS_DEPLOY_ROLE_ARN secret in GitHub',
    });
  }
}
