#!/usr/bin/env bash
#
# Cost guardrails for a Frugal AWS account.
#
#   ./setup-cost-guardrails.sh you@example.com [deploy-iam-username]
#
# Creates:
#   1. A zero-spend budget      -> emails you the moment the bill exceeds $0.01
#   2. A $5 forecast budget     -> emails you before it happens
#   3. A $5 actual budget with an ACTION that attaches a deny-all policy
#
# Read COST-SAFETY.md first. In particular: budgets ALERT, they do not STOP.
# Even the action in step 3 reacts on a delay of hours, because AWS billing data
# lags. This is damage limitation, not a guarantee.
#
# Requires: awscli v2, configured with credentials that can manage budgets.

set -euo pipefail

EMAIL="${1:?Usage: $0 <email> [deploy-iam-username]}"
DEPLOY_USER="${2:-}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
POLICY_NAME="FrugalCostCircuitBreaker"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

say "Account: ${ACCOUNT_ID}   Alerts to: ${EMAIL}"

# --- 1. zero-spend budget --------------------------------------------------
# $0.01 is the earliest possible signal that something outside the free tier
# has started running.

say "1/3  Zero-spend budget (alerts above \$0.01)"

aws budgets create-budget \
  --account-id "${ACCOUNT_ID}" \
  --budget '{
    "BudgetName": "frugal-zero-spend",
    "BudgetLimit": {"Amount": "0.01", "Unit": "USD"},
    "TimeUnit": "MONTHLY",
    "BudgetType": "COST"
  }' \
  --notifications-with-subscribers "[{
    \"Notification\": {
      \"NotificationType\": \"ACTUAL\",
      \"ComparisonOperator\": \"GREATER_THAN\",
      \"Threshold\": 1,
      \"ThresholdType\": \"PERCENTAGE\"
    },
    \"Subscribers\": [{\"SubscriptionType\": \"EMAIL\", \"Address\": \"${EMAIL}\"}]
  }]" 2>/dev/null || echo "     (already exists — skipping)"

# --- 2. forecast warning ---------------------------------------------------
# Forecast fires *before* the money is spent, which is the only alert that gives
# you time to act.

say "2/3  Forecast budget (warns before \$5 is reached)"

aws budgets create-budget \
  --account-id "${ACCOUNT_ID}" \
  --budget '{
    "BudgetName": "frugal-forecast-warning",
    "BudgetLimit": {"Amount": "5", "Unit": "USD"},
    "TimeUnit": "MONTHLY",
    "BudgetType": "COST"
  }' \
  --notifications-with-subscribers "[
    {
      \"Notification\": {
        \"NotificationType\": \"FORECASTED\",
        \"ComparisonOperator\": \"GREATER_THAN\",
        \"Threshold\": 100,
        \"ThresholdType\": \"PERCENTAGE\"
      },
      \"Subscribers\": [{\"SubscriptionType\": \"EMAIL\", \"Address\": \"${EMAIL}\"}]
    },
    {
      \"Notification\": {
        \"NotificationType\": \"ACTUAL\",
        \"ComparisonOperator\": \"GREATER_THAN\",
        \"Threshold\": 50,
        \"ThresholdType\": \"PERCENTAGE\"
      },
      \"Subscribers\": [{\"SubscriptionType\": \"EMAIL\", \"Address\": \"${EMAIL}\"}]
    }
  ]" 2>/dev/null || echo "     (already exists — skipping)"

# --- 3. circuit breaker ----------------------------------------------------
# The only step that *does* something. Needs a target IAM user, because a budget
# action works by attaching a policy to a principal.

say "3/3  Circuit breaker (attaches a deny-all policy at \$5 actual)"

if [[ -z "${DEPLOY_USER}" ]]; then
  cat <<'EOF'
     Skipped: no IAM username given.

     A budget action works by attaching a deny policy to a principal, so it
     needs a target. Create a deploy user, then re-run with its name:

       aws iam create-user --user-name frugal-deploy
       ./setup-cost-guardrails.sh you@example.com frugal-deploy

     Until then you have alerts but no automatic stop.
EOF
else
  POLICY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${POLICY_NAME}"

  aws iam create-policy \
    --policy-name "${POLICY_NAME}" \
    --policy-document "file://$(dirname "$0")/deny-all-policy.json" \
    --description "Attached automatically by a budget action when spend exceeds the limit" \
    >/dev/null 2>&1 || echo "     (policy already exists — reusing)"

  # The budget service needs permission to attach the policy on your behalf.
  ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/AWSBudgetsActionsWithAWSResourceControlAccess"

  aws budgets create-budget-action \
    --account-id "${ACCOUNT_ID}" \
    --budget-name "frugal-forecast-warning" \
    --notification-type ACTUAL \
    --action-type APPLY_IAM_POLICY \
    --action-threshold '{"ActionThresholdValue": 100, "ActionThresholdType": "PERCENTAGE"}' \
    --definition "{\"IamActionDefinition\": {\"PolicyArn\": \"${POLICY_ARN}\", \"Users\": [\"${DEPLOY_USER}\"]}}" \
    --execution-role-arn "${ROLE_ARN}" \
    --approval-model AUTOMATIC \
    --subscribers "[{\"SubscriptionType\": \"EMAIL\", \"Address\": \"${EMAIL}\"}]" \
    2>/dev/null || cat <<EOF
     Could not create the budget action.

     It needs the role AWSBudgetsActionsWithAWSResourceControlAccess, which AWS
     creates the first time you configure an action in the console. Create one
     action there once, then re-run this script.
EOF
fi

# --- 4. stop the instance --------------------------------------------------
# The deny-all policy in step 3 blocks *new* resources. It does not stop the one
# that is already running and burning credits, which on this deployment is the
# only thing that can.
#
# This is the action that matches the stated preference: the app going offline
# is an acceptable outcome, a bill is not. It stops every instance tagged
# Project=frugal at 100% of the $5 budget.

say "4/4  Stop EC2 instances tagged Project=frugal at the budget limit"

aws budgets create-budget-action \
  --account-id "${ACCOUNT_ID}" \
  --budget-name "frugal-forecast-warning" \
  --notification-type ACTUAL \
  --action-type RUN_SSM_DOCUMENTS \
  --action-threshold '{"ActionThresholdValue": 100, "ActionThresholdType": "PERCENTAGE"}' \
  --definition "{
    \"SsmActionDefinition\": {
      \"ActionSubType\": \"STOP_EC2_INSTANCES\",
      \"Region\": \"${AWS_REGION:-ap-south-1}\",
      \"InstanceIds\": [$(
        aws ec2 describe-instances \
          --filters "Name=tag:Project,Values=frugal" "Name=instance-state-name,Values=running" \
          --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null \
          | tr '\t' '\n' | sed 's/.*/"&"/' | paste -sd, -
      )]
    }
  }" \
  --execution-role-arn "${ROLE_ARN:-arn:aws:iam::${ACCOUNT_ID}:role/AWSBudgetsActionsWithAWSResourceControlAccess}" \
  --approval-model AUTOMATIC \
  --subscribers "[{\"SubscriptionType\": \"EMAIL\", \"Address\": \"${EMAIL}\"}]" \
  2>/dev/null || cat <<'EOF'
     Skipped. Either no running instance is tagged Project=frugal (normal
     before the first deploy -- re-run this after `terraform apply`), or the
     budget-actions role does not exist yet. See step 3 for that role.
EOF

cat <<EOF

---------------------------------------------------------------
Done. Still to do by hand (COST-SAFETY.md has the detail):

  * Confirm the budget emails — AWS sends a subscription confirmation.
  * Enable MFA on the root account.
  * Turn on Free Tier usage alerts:
      Billing -> Billing preferences -> Alert preferences
  * Check which free tier plan you are on:
      Billing -> Account overview -> Free tier plan
    On the Free Plan the account PAUSES instead of billing you, which is
    stronger than anything configured here.

Remember: budgets alert, they do not stop. The reliable protection is not
having billable resources — see section 3 of COST-SAFETY.md.
---------------------------------------------------------------
EOF
