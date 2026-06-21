import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { SuptAiStack } from '../lib/supt-ai-stack';

test('Stack creates successfully', () => {
  const app = new cdk.App();
  const stack = new SuptAiStack(app, 'TestStack');
  const template = Template.fromStack(stack);

  // TODO: Add assertions
  expect(template).toBeDefined();
});
