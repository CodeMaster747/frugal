# Cost safety

Written for the case where a surprise bill would genuinely hurt. Read the first
section before creating a single AWS resource.

---

## 1. What AWS does and does not protect you from

**There is no hard spending cap on AWS.** This is the single most important
thing to understand, and it is not obvious from the console.

| Mechanism | What it actually does |
|---|---|
| AWS Budgets | Sends an email when spend crosses a threshold. **Does not stop anything.** |
| Billing alarms (CloudWatch) | Same — a notification, not a brake. |
| Budget **Actions** | Can attach a deny-all IAM policy or stop EC2 instances automatically. The closest thing to a kill switch. |
| Free Tier usage alerts | Emails you at 85% of a free-tier limit. Still just an email. |

Even Budget Actions are not instant: budgets evaluate roughly three times a day
and billing data lags 8–12 hours. A runaway cost can accrue for several hours
before anything reacts. Treat every control here as **damage limitation, not a
guarantee**.

Because of that, the layers below are ordered deliberately: the ones that remove
exposure come first, and the ones that merely react come last.

---

## 2. First: check which plan your account is on

AWS changed its free tier in mid-2025. New accounts choose between two plans,
and which one you are on decides whether a surprise bill is even possible.

| Plan | Behaviour when credits or 6 months run out |
|---|---|
| **Free Plan** | The account is **paused**. Resources stop, and you are not charged. You must actively upgrade to keep going. |
| **Paid Plan** | Pay-as-you-go begins. Free-tier allowances still apply, but anything beyond them is billed. |

**This account is on the Free Plan** (confirmed at setup). Charges cannot
accrue: when the credits are exhausted or the six months elapse, the account is
paused. That is a stronger protection than anything configured later in this
document.

Verify at any time: **Billing and Cost Management → Account overview → Free
tier plan**.

Three things can still undo it:

1. **Upgrading to the Paid Plan.** This is the only action that re-exposes you.
   AWS prompts for it when you hit a limit or reach for an excluded service,
   sometimes as a console banner. Treat every "upgrade to continue" prompt as a
   decision, not a formality — with autopay approved, accepting it makes real
   charges possible immediately.
2. **Purchases that are not metered usage.** Route 53 domain registration, AWS
   Marketplace subscriptions, and Reserved Instances or Savings Plans are
   one-off or committed spend rather than usage. Do not buy anything through
   AWS and rely on the plan to stop you. Everything Frugal needs -- EC2, S3,
   CloudWatch -- is ordinary metered usage and is covered.
3. **The clock.** See below.

### The six-month clock is a schedule constraint, not just a cost one

The Free Plan ends after six months regardless of how little you have spent.
AWS deployment is M11, at the *end* of the roadmap, and the remaining
milestones run longer than that. So the likely outcome is the plan expiring
before the deployment exists -- and then the account pauses.

This is the practical reason to deploy on the stack in section 3 as you go:
it keeps a live URL from M4 onward, and AWS becomes an addition you make when
you actually want it rather than a deadline you are racing.

---

## 3. Remove the exposure (do this before anything else)

Frugal is designed so that **AWS is optional**. The `ObjectStore` port (ADR-004)
means the same code runs against MinIO, Cloudflare R2, or S3 with a config
change. The architecture already puts the database and cache on Neon and
Upstash, not AWS.

So the zero-risk deployment is:

| Concern | Instead of | Use | Risk if it overruns |
|---|---|---|---|
| API + worker | EC2 | **Fly.io** or **Render** free tier | Service stops. No bill. |
| Object storage | S3 | **Cloudflare R2** (10 GB, no egress fees) | Hard-capped on the free plan. |
| Database | RDS | **Neon** free tier | Paused, not billed. |
| Cache / broker | ElastiCache | **Upstash** free tier | Requests rejected, not billed. |
| Frontend | CloudFront | **Vercel** Hobby | Bandwidth capped. |
| Logs | CloudWatch | stdout + the platform's own log view | Free. |

Every one of these *stops working* rather than *bills you* when you exceed the
free allowance. That is the property you want, and AWS does not offer it on the
Paid Plan.

**Recommendation: deploy on this stack. Add AWS later, deliberately, when a bill
would not hurt.** You lose nothing architecturally — the ports are already
there, and "deployed on Fly + R2 + Neon" is not a weaker portfolio story than
"deployed on EC2 + S3".

R2 needs no code change at all. Placeholders below are written as shell
variable references rather than `<angle-bracket>` blanks, because the
angle-bracket form is shaped like a real credential and makes secret scanners
report the documentation as a leak:

```bash
STORAGE_BACKEND=s3
S3_ENDPOINT_URL="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
S3_ACCESS_KEY="${R2_TOKEN_ID}"
S3_SECRET_KEY="${R2_TOKEN_SECRET}"
S3_BUCKET=frugal-receipts
S3_REGION=auto
```

---

## 4. If you do use AWS: what actually causes big bills

Student horror stories are almost never "my t3.micro cost too much". A
t3.micro is about **$8/month** after the free year — bounded and predictable.
The bills that reach four figures come from a short list:

| Cause | Typical cost | Guard |
|---|---|---|
| **Leaked IAM access key** (public repo, mining crypto) | Thousands, in hours | Never create long-lived keys. Use an instance profile. Enable gitleaks (already in CI). |
| **NAT Gateway** | ~$32/month + data | Never create one. Frugal does not need a private subnet. |
| **Load balancer (ALB/NLB)** | ~$16/month idle | Not needed — Caddy on the instance terminates TLS. |
| **Unattached Elastic IP** | ~$3.60/month each | Release any EIP you are not using. |
| **Forgotten EBS volumes / snapshots** | Grows quietly | Delete volumes when you terminate an instance. |
| **Data egress** | $0.09/GB after 100 GB | Serve the frontend from Vercel, not from EC2. |
| **CloudWatch custom metrics** | $0.30/metric/month | Use the default metrics only. |

Frugal's architecture already avoids every one of these by design. The
deployment is: one t3.micro, one S3 bucket, default CloudWatch logs. Nothing
that scales with traffic.

---

## 5. The guardrails, in the order you should apply them

Run these once, before deploying. `setup-cost-guardrails.sh` in this directory
does steps 2–4 in one go.

### 5.1 Secure the root account (manual, 5 minutes)

Do this first — a compromised root account defeats every other control.

1. **Enable MFA on root.** Billing → Security credentials → MFA.
2. **Delete any root access keys.** There should be zero.
3. **Enable IAM access to billing data**, so a non-root user can see spend:
   Account settings → IAM user and role access to Billing Information → Activate.

### 5.2 Zero-spend alert

Emails you the moment your bill goes above **$0.01** — the earliest possible
warning that something outside the free tier started running.

```bash
./setup-cost-guardrails.sh you@example.com
```

### 5.3 Free-tier usage alerts

Billing → Billing preferences → **Alert preferences** → tick *Receive AWS Free
Tier alerts*, enter your email. AWS emails you at 85% and 100% of any free-tier
limit.

### 5.4 A budget with a real stop action

This is the only mechanism that *does* something rather than telling you about
it. At $5 of forecast spend it attaches `deny-all-policy.json` to your deploy
user, which blocks the creation of anything new.

Understand its limits: it does not stop *already-running* resources, and it
reacts on a delay. It is a circuit breaker, not a fuse.

### 5.5 Check the bill weekly

Set a recurring reminder. Billing → Bills. Two minutes, and it is the control
that has actually caught things for most people.

---

## 6. Residual risk, stated plainly

With every control above in place, you can still be billed if:

- A leaked credential is abused faster than budgets evaluate.
- You launch something expensive and it runs for the 8–12 hours before billing
  data catches up.
- A service outside the budget's scope accrues charges.

The only way to reduce this to genuinely zero is **not to have billable AWS
resources**. That is why section 3 comes before section 5.

For a student building a portfolio project, the honest recommendation is:
deploy on the free-tier stack in section 3, keep the AWS account closed or
empty, and add AWS in a later milestone when you want the deployment story and
can absorb ten dollars going wrong.

---

## 7. If you get an unexpected bill anyway

AWS routinely waives first-time accidental charges for students and new
accounts. Do not ignore it:

1. Billing → Bills → identify the service and region.
2. Delete the resource immediately (check **all regions** — a forgotten
   instance in `us-west-2` is a classic).
3. Open a **Billing support case** (free on every plan): explain it was
   accidental, that you are a student, and that you have since deleted the
   resource and added budgets.

The success rate on a polite first-time request is high.
