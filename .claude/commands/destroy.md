# /destroy — Tear Down TripAI AWS Stack

Destroys the `TripAIStack` CloudFormation stack and cleans up associated resources.

**WARNING: This terminates the EC2 instance and deletes the CloudFront distribution. DynamoDB tables are retained (RemovalPolicy.RETAIN) and must be deleted manually if desired.**

## Steps

### 1. Confirm with user

Before doing anything, ask:
> "This will terminate the EC2 instance and delete the CloudFront distribution. DynamoDB tables (tripai-runs, tripai-agent-calls, tripai-tool-calls) will be RETAINED. Type 'yes' to confirm."

Do not proceed until the user explicitly confirms.

### 2. Delete the stack

```bash
cd /Users/arpan/Downloads/Projects/Claude-Agentic-Workflow/infra && cdk destroy TripAIStack --force 2>&1
```

Wait for completion. This typically takes 3–5 minutes.

### 3. (Optional) Delete DynamoDB tables

If the user also wants to delete the DynamoDB tables, run:
```bash
aws dynamodb delete-table --table-name tripai-runs --region us-east-1 2>&1
aws dynamodb delete-table --table-name tripai-agent-calls --region us-east-1 2>&1
aws dynamodb delete-table --table-name tripai-tool-calls --region us-east-1 2>&1
```

Only do this if the user explicitly asks — tables are retained by default to preserve data.

### 4. Report result

- Exit code (0 = success)
- Confirm stack is gone: `aws cloudformation describe-stacks --stack-name TripAIStack 2>&1` should return an error (stack does not exist)
- Whether DynamoDB tables were deleted or retained
