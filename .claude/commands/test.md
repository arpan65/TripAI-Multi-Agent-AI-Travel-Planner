# /test — E2E Test Against Live Cloud Backend

Run a full end-to-end pipeline test against the deployed EC2 backend.

## Steps

### 1. Get the EC2 IP
```bash
aws cloudformation describe-stacks --stack-name TripAIStack \
  --query "Stacks[0].Outputs[?OutputKey=='InstancePublicIP'].OutputValue" \
  --output text
```

### 2. Check backend health
```bash
curl -s http://<EC2_IP>:8000/health
```
If not `{"status":"ok"}`, stop and report: backend is down.

### 3. Run the pipeline
```bash
curl -s -X POST http://<EC2_IP>:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message":"Toronto to Montreal, 3 nights, 2 people, train, May 15-18 2026","test_mode":false}' \
  --no-buffer --max-time 300
```

### 4. Evaluate result
Parse the SSE stream output and report:
- Did all 4 phase events arrive? (Planning, Scouting, Calculating, Finalising)
- Did a `{"type":"result",...}` event arrive with valid JSON?
- In the result JSON, is `data_notes.fetch_failed` an empty array `[]`? (confirms live browser searches worked)
- What are the transport prices found?
- What are the 3 budget tier totals (economy/mid_range/comfort)?

Report PASS or FAIL with details.
