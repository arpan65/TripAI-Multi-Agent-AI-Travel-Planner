# /deploy — Deploy TripAI to AWS

Build the Docker image (frontend baked in), push to ECR, and restart the container on EC2 via SSM.

Optionally pass `--infra` to also run `cdk deploy` (replaces the EC2 instance — only needed for infra changes).

## Steps

Run these in order. Stop and report any failure immediately.

### 1. Determine deploy mode

If the user said `--infra` or "infra deploy" or "deploy infra", run:
```bash
cd /Users/arpan/Downloads/Projects/Claude-Agentic-Workflow && ./deploy.sh --infra 2>&1
```

Otherwise (normal code deploy), run:
```bash
cd /Users/arpan/Downloads/Projects/Claude-Agentic-Workflow && ./deploy.sh 2>&1
```

The script will:
- Build and push the amd64 Docker image to ECR (includes the built React frontend)
- SSM into EC2 to pull the new image and restart the `tripai` container
- Run a health check against `http://54.163.82.43:8000/health`

### 2. Report result

After `deploy.sh` exits, report:
- Exit code (0 = success, non-zero = failure)
- The health check line from the output
- The final line: `==> Done. App at https://d1uwf93gqyfftz.cloudfront.net`
- If failed: show the SSM StandardErrorContent from the output

### Notes

- `--infra` mode exits after CDK deploy and prompts for manual `.env` setup — do not attempt to continue past that exit.
- The Playwright Chromium binary lives in the `ms-playwright` Docker volume on EC2; it persists across image updates automatically.
- After a normal deploy, the new container is live within ~30 seconds of the SSM command completing.
