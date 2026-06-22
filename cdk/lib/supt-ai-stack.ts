import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigatewayv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export class SuptAiStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Lambda function (Docker image built from local Dockerfile)
    const reviewerFn = new lambda.DockerImageFunction(this, 'ReviewerFunction', {
      functionName: 'supt-ai-reviewer',
      code: lambda.DockerImageCode.fromImageAsset('../docker'),
      memorySize: 512,
      timeout: cdk.Duration.seconds(90),
      architecture: lambda.Architecture.ARM_64,
      logRetention: logs.RetentionDays.TWO_WEEKS,
      environment: {
        // Populated manually in console or via CLI after first deploy.
        // Will move to Secrets Manager in a future iteration.
        GITHUB__USER_TOKEN: '',
        WEBHOOK_SECRET: '',
        XAI_API_KEY: '',
        DISCORD_WEBHOOK_URL: '',
        CONFIG__MODEL: 'xai/grok-4.3',
        CONFIG__CUSTOM_MODEL_MAX_TOKENS: '131072',
        CONFIG__FALLBACK_MODELS: '["xai/grok-4.3"]',
        OPENAI__KEY: 'none',
      },
    });

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
  }
}
