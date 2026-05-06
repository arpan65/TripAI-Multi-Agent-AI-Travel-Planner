import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';

export class InfraStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ── DYNAMODB TABLES ──────────────────────────────────────────────────────

    const runsTable = new dynamodb.Table(this, 'RunsTable', {
      tableName: 'tripai-runs',
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    runsTable.addGlobalSecondaryIndex({
      indexName: 'status-created-index',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'created_at', type: dynamodb.AttributeType.STRING },
    });

    const agentCallsTable = new dynamodb.Table(this, 'AgentCallsTable', {
      tableName: 'tripai-agent-calls',
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    agentCallsTable.addGlobalSecondaryIndex({
      indexName: 'run-id-index',
      partitionKey: { name: 'run_id', type: dynamodb.AttributeType.STRING },
    });

    const toolCallsTable = new dynamodb.Table(this, 'ToolCallsTable', {
      tableName: 'tripai-tool-calls',
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    toolCallsTable.addGlobalSecondaryIndex({
      indexName: 'run-id-index',
      partitionKey: { name: 'run_id', type: dynamodb.AttributeType.STRING },
    });

    // ── IAM ROLE FOR EC2 ─────────────────────────────────────────────────────
    // Allows the instance to access DynamoDB and SSM (for parameter/secret fetch)
    const instanceRole = new iam.Role(this, 'InstanceRole', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEC2ContainerRegistryReadOnly'),
      ],
    });
    [runsTable, agentCallsTable, toolCallsTable].forEach(t =>
      t.grantReadWriteData(instanceRole)
    );

    // ── VPC / SECURITY GROUP ─────────────────────────────────────────────────
    const vpc = ec2.Vpc.fromLookup(this, 'DefaultVPC', { isDefault: true });

    const sg = new ec2.SecurityGroup(this, 'BackendSG', {
      vpc,
      description: 'TripAI backend',
      allowAllOutbound: true,
    });
    // SSH (restrict to your IP in production)
    sg.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(22), 'SSH');
    // FastAPI
    sg.addIngressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(8000), 'FastAPI');

    // ── EC2 t2.micro ─────────────────────────────────────────────────────────
    const ecrImage = `809581003268.dkr.ecr.us-east-1.amazonaws.com/tripai:latest`;

    const userData = ec2.UserData.forLinux();
    userData.addCommands(
      // Install Docker
      'dnf install -y docker',
      'systemctl enable --now docker',

      // Allow ec2-user to use docker without sudo
      'usermod -aG docker ec2-user',

      // Write a systemd unit that pulls from ECR and runs the container.
      // The .env file must be placed at /home/ec2-user/app/.env before the
      // service starts (done once manually via SSM after first deploy).
      `cat > /etc/systemd/system/tripai.service << 'UNIT'
[Unit]
Description=TripAI backend (Docker)
After=docker.service network-online.target
Requires=docker.service

[Service]
Restart=always
RestartSec=5
ExecStartPre=/bin/bash -c 'aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 809581003268.dkr.ecr.us-east-1.amazonaws.com'
ExecStartPre=-/usr/bin/docker stop tripai
ExecStartPre=-/usr/bin/docker rm tripai
ExecStart=/usr/bin/docker run --name tripai --env-file /home/ec2-user/app/.env -p 8000:8000 -v ms-playwright:/ms-playwright ${ecrImage}
ExecStop=/usr/bin/docker stop tripai

[Install]
WantedBy=multi-user.target
UNIT`,

      'systemctl daemon-reload',
      'systemctl enable tripai',
      // Service starts automatically once .env is in place and systemctl start tripai is run
    );

    const instance = new ec2.Instance(this, 'BackendInstance', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T2, ec2.InstanceSize.MICRO),
      machineImage: ec2.MachineImage.latestAmazonLinux2023(),
      securityGroup: sg,
      role: instanceRole,
      userData,
      keyName: 'tripai-key',
    });

    // ── CLOUDFRONT DISTRIBUTION ───────────────────────────────────────────────
    // Single origin: EC2:8000 serves both the FastAPI backend and the React SPA
    // (static files baked into the Docker image). 180s read timeout keeps SSE
    // connections alive through CloudFront during long Playwright pricing phases.
    const ec2Origin = new origins.HttpOrigin(instance.instancePublicDnsName, {
      httpPort: 8000,
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
      readTimeout: cdk.Duration.seconds(60),
      keepaliveTimeout: cdk.Duration.seconds(60),
    });

    const distribution = new cloudfront.Distribution(this, 'CDN', {
      defaultBehavior: {
        origin: ec2Origin,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
    });

    // ── OUTPUTS ───────────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'InstancePublicIP', {
      value: instance.instancePublicIp,
      description: 'EC2 public IP — backend runs on port 8000',
    });
    new cdk.CfnOutput(this, 'InstancePublicDNS', {
      value: instance.instancePublicDnsName,
      description: 'EC2 public DNS',
    });
    new cdk.CfnOutput(this, 'BackendURL', {
      value: `http://${instance.instancePublicIp}:8000`,
      description: 'FastAPI base URL',
    });
    new cdk.CfnOutput(this, 'RunsTableName', { value: runsTable.tableName });
    new cdk.CfnOutput(this, 'AgentCallsTableName', { value: agentCallsTable.tableName });
    new cdk.CfnOutput(this, 'ToolCallsTableName', { value: toolCallsTable.tableName });
    new cdk.CfnOutput(this, 'CloudFrontURL', {
      value: `https://${distribution.distributionDomainName}`,
      description: 'Frontend URL',
    });
  }
}
