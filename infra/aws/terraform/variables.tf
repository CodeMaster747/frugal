variable "region" {
  description = "AWS region. ap-south-1 (Mumbai) is closest to the intended users and prices in the same currency the product displays."
  type        = string
  default     = "ap-south-1"
}

variable "instance_type" {
  description = <<-EOT
    Free-tier eligible instance type.

    Deliberately constrained by the validation below rather than left open. The
    difference between t3.micro and t3.small is not visible when typing it, and
    is the difference between free and roughly $15/month. Measured footprint is
    ~450 MB during a Prophet fit, so t3.micro (1 GB) plus the 2 GB swap
    configured in user-data.sh is sufficient.
  EOT
  type        = string
  default     = "t3.micro"

  validation {
    condition     = contains(["t2.micro", "t3.micro"], var.instance_type)
    error_message = "Only t2.micro and t3.micro are free-tier eligible. Anything larger bills from the first hour."
  }
}

variable "ssh_ingress_cidr" {
  description = <<-EOT
    Who may reach port 22. Your own address, as `x.x.x.x/32`.

    No default on purpose. A default of 0.0.0.0/0 is how instances get found and
    used for mining within hours of launch, and a leaked-credential incident is
    the one item on the cost-risk list that reaches four figures.
  EOT
  type        = string

  validation {
    condition     = var.ssh_ingress_cidr != "0.0.0.0/0"
    error_message = "Refusing to open SSH to the whole internet. Pass your own address as x.x.x.x/32 (`curl -s ifconfig.me`)."
  }
}

variable "ssh_public_key" {
  description = "Contents of your SSH public key (~/.ssh/id_ed25519.pub). AWS holds only the public half."
  type        = string
}

variable "domain_name" {
  description = "Domain for TLS. Leave empty to serve over the instance's public DNS name with a self-signed certificate."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = <<-EOT
    CloudWatch log retention.

    The free tier covers 5 GB of ingestion and 5 GB of storage per month.
    Retention defaults to "never expire" if unset, which is the quiet way logs
    become a recurring charge long after the instance is gone.
  EOT
  type        = number
  default     = 7
}

variable "receipt_expiry_days" {
  description = "Days before an uploaded receipt image is deleted from S3. The extracted fields live in Postgres; the image is only needed while a human might review it."
  type        = number
  default     = 90
}

variable "alert_email" {
  description = "Where CloudWatch alarms are sent. AWS emails a confirmation link that must be clicked before any alarm can reach you."
  type        = string
}
