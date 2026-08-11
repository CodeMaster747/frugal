# Logs, metrics, and alarms.
#
# Everything here uses metrics AWS already publishes. Custom CloudWatch metrics
# are $0.30 each per month and are the usual way an observability setup starts
# costing more than the instance it watches.

resource "aws_cloudwatch_log_group" "app" {
  name = "/frugal/app"

  # Never omit this. Log groups default to retaining forever, and the storage
  # keeps billing long after the instance that produced it is gone.
  retention_in_days = var.log_retention_days
}

# --- alarms -----------------------------------------------------------------
# Delivered by email through SNS. No PagerDuty, no Slack app: one subscriber and
# an address that is already being read.

resource "aws_sns_topic" "alerts" {
  name = "frugal-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email

  # AWS emails a confirmation link; the subscription is inactive until it is
  # clicked. Terraform reports success either way, so an unconfirmed
  # subscription is a silent way to have alarms that never reach anyone. The
  # runbook's verification step exists to catch exactly that.
}

resource "aws_cloudwatch_metric_alarm" "instance_unhealthy" {
  alarm_name        = "frugal-instance-unhealthy"
  alarm_description = "The instance failed its status checks. Usually a kernel panic or an out-of-memory kill."

  namespace   = "AWS/EC2"
  metric_name = "StatusCheckFailed"
  dimensions  = { InstanceId = aws_instance.app.id }

  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 60
  evaluation_periods  = 2

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]

  # Missing data means the instance stopped reporting, which is itself the
  # condition being watched for. The default (ignore) would stay silent through
  # exactly the outage this alarm exists to catch.
  treat_missing_data = "breaching"
}

resource "aws_cloudwatch_metric_alarm" "cpu_credits_exhausted" {
  alarm_name        = "frugal-cpu-credits-low"
  alarm_description = "Burstable CPU credits are nearly gone. Below zero the instance is throttled to its 10% baseline and the API becomes very slow."

  namespace   = "AWS/EC2"
  metric_name = "CPUCreditBalance"
  dimensions  = { InstanceId = aws_instance.app.id }

  statistic           = "Minimum"
  comparison_operator = "LessThanThreshold"
  threshold           = 20
  period              = 300
  evaluation_periods  = 2

  alarm_actions = [aws_sns_topic.alerts.arn]

  # This one matters on t3: unlimited mode is the default on t3 instances and
  # bills for surplus credits. The runbook sets standard mode so the instance
  # throttles instead of charging, which makes this alarm the warning that it is
  # about to get slow.
  treat_missing_data = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "disk_nearly_full" {
  alarm_name        = "frugal-root-volume-full"
  alarm_description = "The root volume is nearly full. Docker images and logs are the usual cause."

  namespace   = "CWAgent"
  metric_name = "disk_used_percent"
  dimensions = {
    InstanceId = aws_instance.app.id
    path       = "/"
    fstype     = "ext4"
  }

  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 85
  period              = 300
  evaluation_periods  = 1

  alarm_actions = [aws_sns_topic.alerts.arn]

  # Depends on the CloudWatch agent, which user-data.sh installs. If the agent
  # is not running there is no data, and "no data" here means "unknown", not
  # "full" -- unlike the status check above, where silence is the symptom.
  treat_missing_data = "missing"
}

# Errors are found by pattern-matching the structured logs the application
# already emits, rather than by publishing a custom metric from the app. Same
# signal, no per-metric charge, and nothing to keep in sync in the code.
resource "aws_cloudwatch_log_metric_filter" "server_errors" {
  name           = "frugal-5xx"
  log_group_name = aws_cloudwatch_log_group.app.name

  # Matches the JSON the logging configuration produces (app/core/logging.py).
  pattern = "{ $.status_code >= 500 }"

  metric_transformation {
    name          = "ServerErrors"
    namespace     = "Frugal"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "server_errors" {
  alarm_name        = "frugal-5xx-errors"
  alarm_description = "The API is returning 500s. One is noise; a sustained rate is an incident."

  namespace   = "Frugal"
  metric_name = aws_cloudwatch_log_metric_filter.server_errors.metric_transformation[0].name

  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 5
  period              = 300
  evaluation_periods  = 1

  alarm_actions      = [aws_sns_topic.alerts.arn]
  treat_missing_data = "notBreaching"
}
