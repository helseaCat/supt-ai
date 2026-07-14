import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaEventSources from 'aws-cdk-lib/aws-lambda-event-sources';
import * as apigatewayv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sqs from 'aws-cdk-lib/aws-sqs';
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

    // ─── SQS: Review Queue + DLQ ───────────────────────────────────────
    const dlq = new sqs.Queue(this, 'ReviewDLQ', {
      queueName: 'supt-ai-review-dlq',
      retentionPeriod: cdk.Duration.days(14),
    });

    const reviewQueue = new sqs.Queue(this, 'ReviewQueue', {
      queueName: 'supt-ai-review-queue',
      visibilityTimeout: cdk.Duration.seconds(120), // > Lambda timeout
      deadLetterQueue: {
        queue: dlq,
        maxReceiveCount: 3,
      },
    });

    // ─── Reviewer Lambda (SQS-triggered, zip-packaged) ───────────────────
    const reviewerFn = new lambda.Function(this, 'ReviewerFunction', {
      functionName: 'supt-ai-reviewer',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset('../reviewer', {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c',
            'pip install -r requirements.txt -t /asset-output && cp -au . /asset-output',
          ],
        },
      }),
      memorySize: 512,
      timeout: cdk.Duration.seconds(90),
      architecture: lambda.Architecture.X86_64,
      reservedConcurrentExecutions: 2,
      logRetention: logs.RetentionDays.TWO_WEEKS,
      environment: {
        SECRETS_ARN: secrets.secretArn,
      },
    });

    // Reviewer consumes from SQS (one message at a time — each review is heavy)
    reviewerFn.addEventSource(new lambdaEventSources.SqsEventSource(reviewQueue, {
      batchSize: 1,
    }));

    // Grant Lambda read access to secrets
    secrets.grantRead(reviewerFn);

    // ─── Intake Lambda (lightweight, validates + enqueues) ───────────────
    const intakeFn = new lambda.Function(this, 'IntakeFunction', {
      functionName: 'supt-ai-intake',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('../docker/intake'),
      memorySize: 128,
      timeout: cdk.Duration.seconds(10),
      architecture: lambda.Architecture.X86_64,
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        QUEUE_URL: reviewQueue.queueUrl,
        SECRETS_ARN: secrets.secretArn,
      },
    });

    // Intake needs to send messages and read the webhook secret
    reviewQueue.grantSendMessages(intakeFn);
    secrets.grantRead(intakeFn);

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

    // POST /webhook → Intake Lambda
    const intakeIntegration = new integrations.HttpLambdaIntegration(
      'IntakeIntegration',
      intakeFn,
    );

    httpApi.addRoutes({
      path: '/webhook',
      methods: [apigatewayv2.HttpMethod.POST],
      integration: intakeIntegration,
    });

    // Outputs
    new cdk.CfnOutput(this, 'WebhookUrl', {
      value: `${httpApi.apiEndpoint}/webhook`,
      description: 'Full webhook URL for GitHub',
    });
  }
}
