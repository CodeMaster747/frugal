# Runbook

Operating Frugal on AWS. Read [COST-SAFETY.md](COST-SAFETY.md) first, and
[ACCOUNT-MIGRATION.md](ACCOUNT-MIGRATION.md) when the account pauses.

---

## 1. First deployment

In order. The guardrails come before the infrastructure on purpose — the brakes
should exist before anything that can spend.

```bash
# 1. Guardrails
cd infra/aws
./setup-cost-guardrails.sh you@example.com

# 2. Infrastructure
cd terraform
terraform init
terraform validate                      # not optional: see §7
terraform apply \
  -var="ssh_ingress_cidr=$(curl -s ifconfig.me)/32" \
  -var="ssh_public_key=$(cat ~/.ssh/id_ed25519.pub)" \
  -var="alert_email=you@example.com"

# 3. Re-run the guardrails so the stop action can see the new instance
cd .. && ./setup-cost-guardrails.sh you@example.com

# 4. Secrets, once, by hand (§2)

# 5. Deploy
./deploy.sh "$(terraform -chdir=terraform output -raw public_ip)"
```

---

## 2. Secrets

`/opt/frugal/.env` on the instance, `chmod 600`, created by hand and never
deployed from the repository. cloud-init user-data is stored unencrypted and
readable by anything on the box that can reach the metadata service, so it is
not a place for credentials either.

```
DATABASE_URL=postgresql+asyncpg://...        # Neon POOLED endpoint
DATABASE_DIRECT_URL=postgresql+asyncpg://... # Neon DIRECT endpoint, migrations only
REDIS_URL=rediss://...                       # Upstash
JWT_SECRET=                                  # openssl rand -hex 32
S3_BUCKET=                                   # terraform output receipts_bucket
AWS_REGION=ap-south-1
CORS_ORIGINS=https://<your-app>.vercel.app
```

There is no `S3_ACCESS_KEY`. boto3 reads the instance profile's rotating
credentials from the metadata service, so no long-lived key exists on the
machine to leak — which is the one item on the cost-risk list that reaches four
figures.

**Neon has two endpoints and they are not interchangeable.** The pooled one for
the application, the direct one for Alembic: a migration through the pooler can
fail partway and leave the schema in a state no migration file describes.

---

## 3. Routine operations

| Task | Command |
|---|---|
| Deploy | `./deploy.sh <ip>` |
| Logs | `ssh ubuntu@<ip> 'cd /opt/frugal/infra/aws && docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env logs -f api'` |
| Restart | `... docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env restart` |
| Migration status | `... exec api alembic current` |
| Stop everything, keep the disk | `aws ec2 stop-instances --instance-ids <id>` |
| Destroy everything | `terraform destroy` |

**The public IP changes on stop/start.** There is no Elastic IP, deliberately —
every public IPv4 address bills hourly whether attached or not, and one left
behind after an instance is gone is a classic forgotten charge. After a
stop/start, re-read `terraform output public_ip` and update any DNS record.

---

## 4. Verify the alarms — by triggering them

An alarm nobody has fired is a configuration, not a safety net. Do this once
after the first deploy, and after any change to `observability.tf`.

**First, confirm the subscription.** Terraform reports success when it creates
an SNS email subscription, whether or not anyone clicked the confirmation link.
Until it is confirmed, every alarm below fires into nothing.

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn "$(aws sns list-topics --query "Topics[?contains(TopicArn,'frugal-alerts')].TopicArn" --output text)" \
  --query 'Subscriptions[].SubscriptionArn' --output text
```

`PendingConfirmation` means go and click the link in your email.

**Then fire one for real:**

```bash
# Fill the disk to trip frugal-root-volume-full (threshold 85%)
ssh ubuntu@<ip> 'fallocate -l 16G /tmp/ballast'
# ... wait ~10 minutes for the CloudWatch agent's 5-minute interval, twice ...
ssh ubuntu@<ip> 'rm /tmp/ballast'
```

You should receive an email. If not, the fault is in the subscription, the
agent, or the alarm — and finding that out now is the entire point.

The `frugal-5xx-errors` alarm depends on the log metric filter matching what the
application actually logs. Confirm the pattern still matches after any change to
`app/core/logging.py`:

```bash
aws logs filter-log-events --log-group-name /frugal/app \
  --filter-pattern '{ $.status_code >= 500 }' --max-items 5
```

---

## 5. Backups

Local, not S3 — a backup inside an account that pauses is not a backup.

```bash
export DATABASE_DIRECT_URL='...' S3_BUCKET='...'
cd infra/ops
./backup.sh ~/frugal-backups
./restore.sh ~/frugal-backups/<newest> --into-scratch
```

Run both. `--into-scratch` restores into a throwaway container, counts the rows,
and **exits non-zero if the restore is clean but empty** — which is what a
backup pointed at the wrong database looks like at every other step.

Verified working: a 405-transaction database dumped and restored with every
table intact.

Weekly is enough for a portfolio project; before any risky migration is the
other time that matters.

---

## 6. When something is wrong

**API not responding.** Check the containers before the instance:
`docker compose ps`. A crash-looped worker will not affect the API; an
out-of-memory kill affects both. `dmesg | grep -i oom` settles it — the instance
has 1 GB and 2 GB of swap, and a Prophet fit peaks near 450 MB.

**Everything is suddenly slow.** Look at `CPUCreditBalance` first. The instance
runs in `standard` credit mode, so exhausting credits throttles it to a 10%
baseline rather than billing for surplus — slow is the designed failure. The
`frugal-cpu-credits-low` alarm warns before it bites.

**Certificate errors.** Caddy renews automatically. If it cannot, check that
port 80 is still open — the ACME HTTP-01 challenge needs it, and it is easy to
close on the assumption that only 443 matters. Let's Encrypt rate-limits at five
failures per week, so read the logs before retrying in a loop.

**Migration failed halfway.** Check `alembic current` against
`alembic history`. Migration 0016 in particular deletes rows before adding
constraints; if it fails, it rolls back whole, because Postgres does DDL
transactionally.

**A bill appeared.** Follow §7 of [COST-SAFETY.md](COST-SAFETY.md). Delete the
resource first, check every region, then open a billing support case — the
success rate on a polite first-time request is high.

---

## 7. Known gaps

- **Terraform has never been `apply`d.** It passes `fmt`; `terraform validate`
  could not run where it was written, because the provider registry was
  unreachable, so provider schemas are unverified. Run `terraform validate`
  before the first `apply` and expect to fix something.
- **The load test measured a laptop.** NFR-1 passed with 6× headroom
  (read p95 47 ms against a 300 ms budget) against the local Docker stack on
  Apple silicon — not a t3.micro talking to Neon across a network. Re-run
  `infra/load/api-load-test.js` against the deployed URL before believing the
  numbers.
- **No CDN, no autoscaling, no multi-AZ.** One instance. It is a portfolio
  deployment and the architecture says so honestly rather than pretending
  otherwise.
