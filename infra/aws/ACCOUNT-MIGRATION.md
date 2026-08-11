# When the AWS account stops

Read this the day it happens, or the week before. It assumes the deliberate
choice recorded in [COST-SAFETY.md](COST-SAFETY.md): the account is on the AWS
**Free Plan**, it will pause rather than bill, and that is the accepted outcome.

**Nothing here is an emergency.** The design keeps almost everything outside
AWS, so an account pausing costs you a server and some image files, not your
data.

---

## 1. What actually stops

| What | Where it lives | Survives the account pausing? |
|---|---|---|
| **All financial data** — accounts, transactions, budgets, goals, categories, insights, forecasts, notifications | **Neon** (Postgres) | **Yes.** Not an AWS service. |
| Cache, rate limits, Celery queue | **Upstash** (Redis) | **Yes.** Also not AWS. |
| Frontend | **Vercel** | **Yes.** |
| API + worker + beat | EC2 instance | No — the instance stops. |
| Receipt **images** | S3 bucket | No — inaccessible while paused. |

So the loss is: one server that Terraform recreates in minutes, and the receipt
photographs. Everything extracted *from* a receipt — merchant, amount, date,
line items, confidence scores, the transaction it became — is in Postgres and is
unaffected. The image is the only thing that is genuinely gone if it was not
backed up.

That is why [`infra/ops/backup.sh`](../ops/backup.sh) writes to a **local**
directory and not to S3. A backup stored in the account that stops is not a
backup.

---

## 2. Before it stops

You get warning. The Free Plan ends on whichever comes first: credits
exhausted, or six months from account creation. Both are visible at
**Billing and Cost Management → Account overview → Free tier plan**, and AWS
emails you as the credits run down.

Do this while the account is still alive:

```bash
export DATABASE_DIRECT_URL='<the Neon direct endpoint>'
export S3_BUCKET='<terraform output receipts_bucket>'

cd infra/ops
./backup.sh ~/frugal-backups
./restore.sh ~/frugal-backups/<newest> --into-scratch   # prove it works
```

The second command is not optional ceremony. It restores into a throwaway
container and counts the rows, and it exits non-zero if the dump restores
cleanly but empty — which is exactly what a backup pointed at the wrong
database looks like at every other step.

**Do not click "upgrade to continue".** It is the only action that makes a
charge possible, autopay is approved on this account, and AWS presents it as a
routine banner rather than a decision. If a prompt blocks something you were
doing, the answer is to stop doing that thing.

---

## 3. Standing it back up on a new account

Roughly thirty minutes, most of it waiting.

### 3.1 Create the account and re-secure it

1. New AWS account, **Free Plan** again. Confirm the plan before anything else:
   Billing → Account overview → Free tier plan.
2. Enable MFA on root; confirm there are zero root access keys.
3. Billing → Billing preferences → Alert preferences → turn on Free Tier alerts.

### 3.2 Guardrails first, infrastructure second

```bash
cd infra/aws
./setup-cost-guardrails.sh you@example.com
```

Before, not after. The order is the point: the brakes exist before anything
that can spend.

### 3.3 Recreate the infrastructure

Terraform state from the old account is worthless now — it describes resources
that no longer exist, in an account you no longer use. Start clean:

```bash
cd infra/aws/terraform
rm -rf .terraform terraform.tfstate*      # old account's state, not a backup

terraform init
terraform apply \
  -var="ssh_ingress_cidr=$(curl -s ifconfig.me)/32" \
  -var="ssh_public_key=$(cat ~/.ssh/id_ed25519.pub)" \
  -var="alert_email=you@example.com"
```

Then re-run the guardrails script, so the budget's stop action picks up the new
instance id:

```bash
cd .. && ./setup-cost-guardrails.sh you@example.com
```

### 3.4 Point the application at the new bucket

The database and cache need no attention — Neon and Upstash never stopped, and
their URLs are unchanged. Only the bucket name moves, because it carries the
account id:

```bash
terraform output receipts_bucket        # new name
# update S3_BUCKET in /opt/frugal/.env on the instance
```

### 3.5 Restore the receipt images

```bash
aws s3 sync ~/frugal-backups/<newest>/receipts/ "s3://$(terraform output -raw receipts_bucket)/"
```

If there is no backup, receipts uploaded before the pause are gone. The
application handles this correctly rather than breaking: the transaction and its
extracted fields are intact, and the image simply 404s. Worth knowing so you do
not go looking for a bug.

### 3.6 Deploy and verify

```bash
cd infra/aws && ./deploy.sh "$(terraform -chdir=terraform output -raw public_ip)"
```

Then work through §4 of [RUNBOOK.md](RUNBOOK.md) — confirm the SNS subscription
email, and *trigger* an alarm rather than assuming it works.

---

## 4. If you would rather not do this again

Every repetition of §3 exists because the API runs on EC2. Nothing else in the
stack needs migrating, because nothing else is on AWS.

Moving the API to Fly.io or Render — both of which stop rather than bill, like
everything else here — removes the six-month cycle entirely, and the
`ObjectStore` port (ADR-004) means Cloudflare R2 replaces S3 with a config
change and no code change. §3 of [COST-SAFETY.md](COST-SAFETY.md) has the
details.

That is a real option, not a criticism of the current choice: running on EC2 is
a deliberate decision to have the AWS deployment story, and the cost of it is
this document, once every six months.
