import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigatewayv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

export class SuptAiStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Secrets Manager — create empty secrets, populate manually once
    const secrets = new secretsmanager.Secret(this, 'SuptAiSecrets', {
      secretName: 'supt-ai/config',
      description: 'supt-ai secrets (GitHub token, webhook secret, xAI key, Discord URL)',
      secretObjectValue: {
        GITHUB__USER_TOKEN: cdk.SecretValue.unsafePlainText(''),
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
      architecture: lambda.Architecture.ARM_64,
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
  }
}
