#!/usr/bin/env bash
# deploy.sh — build, push to ECR, then redeploy on EC2
#
# Usage:
#   ./deploy.sh          — push new image + restart container on existing EC2
#   ./deploy.sh --infra  — also run cdk deploy (replaces EC2 instance)
#
set -euo pipefail

AWS_ACCOUNT="809581003268"
AWS_REGION="us-east-1"
ECR_IMAGE="$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/tripai"
EC2_INSTANCE="i-0c820d1eb493d0a5c"
STACK="TripAIStack"

# ── 1. Build & push ──────────────────────────────────────────────────────────
echo "==> Building and pushing amd64 image to ECR..."
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
docker buildx build --platform linux/amd64 -t "$ECR_IMAGE:latest" --push .

# ── 2. (Optional) CDK deploy — replaces EC2 instance ────────────────────────
if [[ "${1:-}" == "--infra" ]]; then
  echo "==> Running CDK deploy..."
  cd infra && cdk deploy "$STACK" --require-approval never
  cd ..

  EC2_INSTANCE=$(aws cloudformation describe-stack-resources \
    --stack-name "$STACK" --logical-resource-id BackendInstance \
    --query "StackResources[0].PhysicalResourceId" --output text)
  echo "New instance: $EC2_INSTANCE"

  echo ""
  echo ">>> IMPORTANT: SSH/SSM into the new instance and create:"
  echo "    /home/ec2-user/app/.env"
  echo "    with ANTHROPIC_API_KEY and USE_DYNAMODB=true"
  echo "    Then run: sudo systemctl start tripai"
  echo ""
  exit 0
fi

# ── 3. Redeploy on existing EC2 (pull new image + restart container) ─────────
echo "==> Deploying to EC2 ($EC2_INSTANCE)..."
python3 - << PYEOF
import json
payload = {
  "InstanceIds": ["$EC2_INSTANCE"],
  "DocumentName": "AWS-RunShellScript",
  "Parameters": {
    "commands": [
      "aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com",
      "docker pull $ECR_IMAGE:latest",
      "docker stop tripai 2>/dev/null || true",
      "docker rm tripai 2>/dev/null || true",
      "docker run -d --name tripai --restart unless-stopped --env-file /home/ec2-user/app/.env -p 8000:8000 -v ms-playwright:/ms-playwright $ECR_IMAGE:latest",
      "docker ps --filter name=tripai"
    ]
  }
}
with open("/tmp/ssm-deploy.json", "w") as f:
    json.dump(payload, f)
PYEOF
CMD_ID=$(aws ssm send-command \
  --cli-input-json file:///tmp/ssm-deploy.json \
  --query "Command.CommandId" --output text)

echo "SSM command: $CMD_ID"
until aws ssm get-command-invocation \
    --command-id "$CMD_ID" --instance-id "$EC2_INSTANCE" \
    --query "Status" --output text 2>/dev/null | grep -qE "^Success$|^Failed$|^TimedOut$"; do
  printf "."; sleep 3
done
echo

STATUS=$(aws ssm get-command-invocation \
  --command-id "$CMD_ID" --instance-id "$EC2_INSTANCE" \
  --query "Status" --output text)
aws ssm get-command-invocation \
  --command-id "$CMD_ID" --instance-id "$EC2_INSTANCE" \
  --query "StandardOutputContent" --output text

if [ "$STATUS" != "Success" ]; then
  echo "ERROR: deployment failed"
  aws ssm get-command-invocation \
    --command-id "$CMD_ID" --instance-id "$EC2_INSTANCE" \
    --query "StandardErrorContent" --output text
  exit 1
fi

# ── 4. Health check ──────────────────────────────────────────────────────────
echo "==> Health check..."
sleep 5
curl -sf "http://54.163.82.43:8000/health" && echo " — OK"
echo "==> Done. App at https://d1uwf93gqyfftz.cloudfront.net"
