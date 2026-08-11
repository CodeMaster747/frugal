output "public_ip" {
  description = "The instance's public address. Changes on stop/start -- see the runbook."
  value       = aws_instance.app.public_ip
}

output "public_dns" {
  description = "Public DNS name. Point a CNAME here, or use it directly when no domain is configured."
  value       = aws_instance.app.public_dns
}

output "ssh" {
  description = "Ready-to-paste SSH command."
  value       = "ssh ubuntu@${aws_instance.app.public_ip}"
}

output "receipts_bucket" {
  description = "S3 bucket for receipt images. Goes into S3_BUCKET on the instance."
  value       = aws_s3_bucket.receipts.id
}

output "log_group" {
  description = "CloudWatch log group holding application logs."
  value       = aws_cloudwatch_log_group.app.name
}

output "next_steps" {
  description = "What to do after apply."
  value       = <<-EOT
    1. Confirm the SNS subscription — check ${var.alert_email} for "AWS Notification —
       Subscription Confirmation". Until that link is clicked, no alarm reaches you.
    2. Re-run ../setup-cost-guardrails.sh so the budget's stop action can pick up
       the new instance id.
    3. Verify an alarm actually fires (runbook §4), rather than assuming it will.
  EOT
}
