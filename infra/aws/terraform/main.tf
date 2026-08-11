# Compute and network.
#
# The default VPC is used deliberately. A custom VPC with private subnets needs
# a NAT Gateway for outbound traffic, which is ~$32/month and bills whether or
# not anything uses it -- the single most common source of an unexpected AWS
# bill. Frugal has one public instance and no private tier, so a public subnet
# in the default VPC is both correct and free.
#
# There is no load balancer for the same reason: an idle ALB is ~$16/month.
# Caddy on the instance terminates TLS, which is what a single-instance
# deployment actually needs.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# Canonical's official AMI, resolved at plan time rather than pinned. A stale
# hardcoded AMI is a machine that launches years behind on security patches.
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

resource "aws_key_pair" "deploy" {
  key_name   = "frugal-deploy"
  public_key = var.ssh_public_key
}

resource "aws_security_group" "app" {
  name        = "frugal-app"
  description = "Frugal application instance"
  vpc_id      = data.aws_vpc.default.id

  # SSH, restricted to one address by the variable's validation.
  ingress {
    description = "SSH from the operator only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_ingress_cidr]
  }

  # 80 is not for serving. Caddy needs it to answer the ACME HTTP-01 challenge,
  # and it redirects everything else to 443.
  ingress {
    description = "HTTP, for the ACME challenge and the redirect to HTTPS"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Outbound is unrestricted: the instance reaches Neon, Upstash, and the
  # package mirrors, and narrowing this to their addresses would break the
  # moment any of them changes IP.
  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- identity ---------------------------------------------------------------
# An instance profile, never a long-lived access key. Credentials delivered this
# way are short-lived and rotated by AWS, so there is nothing durable to leak
# into a repository or a log -- which is the failure mode that produces the
# four-figure bills, not the instance itself.

resource "aws_iam_role" "app" {
  name = "frugal-app"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "app" {
  name = "frugal-app"
  role = aws_iam_role.app.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Scoped to the objects in one bucket. Not s3:* , and not "Resource":
        # "*" -- a compromised instance should not be able to reach anything
        # else in the account.
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.receipts.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.receipts.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = "${aws_cloudwatch_log_group.app.arn}:*"
      },
    ]
  })
}

resource "aws_iam_instance_profile" "app" {
  name = "frugal-app"
  role = aws_iam_role.app.name
}

# --- the instance -----------------------------------------------------------

resource "aws_instance" "app" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnets.default.ids[0]
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.app.name
  key_name               = aws_key_pair.deploy.key_name

  # The auto-assigned public IP, not an Elastic IP. Since February 2024 every
  # public IPv4 address bills hourly whether attached or not, and an EIP left
  # behind after the instance is gone keeps charging -- a classic forgotten
  # cost. This address is released with the instance. The trade-off is that it
  # changes on stop/start, which the runbook covers.
  associate_public_ip_address = true

  user_data                   = file("${path.module}/user-data.sh")
  user_data_replace_on_change = true

  root_block_device {
    # Free tier covers 30 GB of gp3. 20 leaves headroom for Docker images and
    # the swap file without approaching the limit.
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true

    # Deleted with the instance. Orphaned volumes bill quietly at $0.08/GB-month
    # and are invisible unless you go looking for them.
    delete_on_termination = true
  }

  metadata_options {
    # IMDSv2 required. IMDSv1 lets any SSRF in the application read the
    # instance's credentials with a plain GET.
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  credit_specification {
    # `standard`, not the t3 default of `unlimited`.
    #
    # Under `unlimited` a burst past the CPU baseline is billed as surplus
    # credits rather than throttled -- so a load test, a crawler, or a runaway
    # Celery task quietly produces a charge on an instance that is otherwise
    # free. `standard` throttles to the 10% baseline instead, which is the
    # stated preference: slow or stopped is acceptable, billed is not.
    cpu_credits = "standard"
  }

  tags = {
    Name = "frugal-app"
  }

  lifecycle {
    # The AMI data source resolves to a newer image whenever Canonical
    # publishes one. Without this, an unrelated `terraform apply` would destroy
    # and rebuild the running instance to adopt it.
    ignore_changes = [ami]
  }
}
