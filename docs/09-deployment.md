# 09 — Deployment

A first deployment, step by step. Every command block says **where** it runs and
in **which directory**.

Read [`infra/aws/COST-SAFETY.md`](../infra/aws/COST-SAFETY.md) before Step 12 —
that is the first step that can cost money, and the guardrails go in before the
infrastructure on purpose.

Budget about 2.5 hours. Most of it is waiting: cloud-init, a Docker build on a
`t3.micro`, and a first Render build.

---

## Where commands run

| Marker | Means |
|---|---|
| 💻 **Mac** | Your own Terminal. The directory is given above each block. |
| 🖥️ **Server** | Inside `ssh ubuntu@<IP>`. Step 17 gets you there. |
| 🌐 **Browser** | A web console. Clicks, not commands. |

`REPO` below means `~/Documents/Rahul/Projects/Deployed/Frugal` — wherever you
cloned this. Nothing is destructive until Step 13 (`terraform apply`).

---

## What runs where, when it's done

| Layer | Host | At the free-tier limit |
|---|---|---|
| Frontend + `/api` proxy | Render web service | Sleeps after 15 min idle; 750 h/month |
| Caddy · API · worker · beat · Redis | AWS EC2 `t3.micro` | Account pauses (Free Plan); no bill |
| Postgres | Neon free | Paused, not billed |
| Receipt images | AWS S3 | Free tier; account pauses |
| Logs and alarms | CloudWatch | 5 GB/month, 7-day retention |

Three design points, because each one is the reason a step below looks odd:

- **The backend is not on Render.** Render's free instance type covers web
  services and static sites only — Background Workers and Cron Jobs are paid,
  and a free instance is 512 MB against a worker that measures ~450 MB during a
  Prophet fit (ADR-006). The `t3.micro` has 1 GB plus the 2 GB swap file
  `user-data.sh` creates.
- **Redis is on the instance, not Upstash.** Upstash free is 500k commands per
  *month*; an idle Celery worker BRPOPs its queues about once a second, roughly
  2.6M. The broker would stop answering a week into every month and the symptom
  would be tasks silently not running. This Redis holds only regenerable state,
  so it is capped at 64 MB and unpersisted.
- **The frontend proxies `/api`.** The refresh token is an httpOnly cookie with
  `SameSite=Lax`, which browsers send only on same-site requests, and
  `onrender.com` is on the Public Suffix List — so even two Render subdomains
  are two different sites. A direct cross-site call would carry no cookie and
  every session would die at the 15-minute access-token expiry.

---

## Before you start

Open a scratch note. You will collect seven values across these steps and paste
them into one file at Step 18. Referred to below as **① … ⑦**.

| | Value | Comes from |
|---|---|---|
| ① | Neon **pooled** URL, edited | Step 7 |
| ② | Neon **direct** URL, edited | Step 7 |
| ③ | JWT secret | Step 8 |
| ④ | S3 bucket name | Step 14 |
| ⑤ | EC2 public IP | Step 14 |
| ⑥ | DuckDNS hostname | Step 15 |
| ⑦ | Render URL | Step 21 |

Accounts you need, all free: GitHub, [Neon](https://neon.tech),
[DuckDNS](https://duckdns.org), [Render](https://render.com), and an AWS account
**on the Free Plan**.

---

# Phase 1 — Tooling and GitHub

### Step 1 — Install the CLIs · 💻 Mac

Directory doesn't matter.

```bash
brew install awscli terraform gitleaks
```

`gh` is already installed. Verify all four:

```bash
aws --version && terraform version | head -1 && gitleaks version && gh --version | head -1
```

### Step 2 — Scan for secrets before the first commit · 💻 Mac · in `REPO`

```bash
cd ~/Documents/Rahul/Projects/Deployed/Frugal
gitleaks detect --no-git --config .gitleaks.toml --redact -v
```

Expect `no leaks found`. A secret committed here stays in the history even after
you delete it, which is why this runs before `git commit` and not after.

### Step 3 — Check what will be committed · 💻 Mac · in `REPO`

```bash
git add -A
git status --short | grep -iE '\.env$|node_modules|test-results|\.next/' || echo "clean"
```

Must print `clean`. If it lists anything, stop and add it to `.gitignore`.

### Step 4 — Commit and push · 💻 Mac · in `REPO`

```bash
git commit -m "Frugal: milestones 0-10"
gh repo create frugal --private --source=. --remote=origin --push
```

> Use `--public` if this is going in a portfolio, and decide now — flipping a
> repository to public later exposes its entire history, not just its current
> state.

### Step 5 — Watch CI · 💻 Mac · in `REPO`

```bash
gh run watch
```

Wait for green. Step 19 deploys this same code, so a red build here is a failed
deploy later.

---

# Phase 2 — Database

### Step 6 — Create the Neon project · 🌐 Browser

1. [neon.tech](https://neon.tech) → sign in → **New Project**.
2. Name: `frugal`.
3. **Region — decide this now, it constrains Step 13.** Every API request makes
   several database round-trips and only one browser round-trip, so the API
   belongs next to the database, not next to the user.
   - Neon offers Mumbai → pick it, and keep Terraform's default `ap-south-1`.
   - Otherwise → pick **Singapore**, and pass `-var="region=ap-southeast-1"` at
     Step 13.
4. **Create Project**.

### Step 7 — Copy and fix both connection strings · 🌐 Browser → scratch note

On the project dashboard → **Connection Details**. You need two strings:

- **Pooled** — its hostname contains `-pooler`. Toggle **Connection pooling** on.
- **Direct** — the same, with pooling toggled off.

Neon gives you something like:

```
postgresql://frugal_owner:PASS@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/frugal?sslmode=require&channel_binding=require
```

**Make two edits to each**, or the app will not start:

1. `postgresql://` → `postgresql+asyncpg://`
2. **Delete `?sslmode=require&channel_binding=require` entirely.**

> Why deleting rather than converting: SQLAlchemy's asyncpg dialect does not
> translate `sslmode` — it forwards it to `asyncpg.connect()`, which rejects it
> as an unknown keyword. Replacing it with `?ssl=require` fixes the API and
> breaks the **worker**, which reaches the same URL through psycopg
> ([`sync_database_url`](../backend/app/core/config.py)) and understands
> `sslmode` but not `ssl`. Both drivers default to `prefer` and Neon requires
> TLS, so with no query string at all the connection is still encrypted.

Save to your note as ① and ②:

```
① DATABASE_URL=postgresql+asyncpg://frugal_owner:PASS@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/frugal
② DATABASE_DIRECT_URL=postgresql+asyncpg://frugal_owner:PASS@ep-xxx.ap-southeast-1.aws.neon.tech/frugal
```

They are not interchangeable. Pooled runs the application; direct runs Alembic,
because a migration through a connection pooler can fail partway and leave the
schema in a state no migration file describes.

### Step 8 — Generate the JWT secret · 💻 Mac

```bash
openssl rand -hex 32
```

Copy the 64-character output to your note as ③. Anything shorter than 32
characters is rejected at boot.

---

# Phase 3 — AWS guardrails (before any resource exists)

### Step 9 — Confirm you are on the Free Plan · 🌐 Browser

AWS console → **Billing and Cost Management** → **Account overview** → **Free
tier plan**. It must read **Free Plan**.

On that plan the account *pauses* when credits or the six months run out. On the
Paid Plan it bills instead. From here on, treat every "upgrade to continue"
banner as a decision, not a formality.

### Step 10 — Secure the root account · 🌐 Browser

1. Root account → **Security credentials** → **Assign MFA device**.
2. Same page → confirm there are **zero** root access keys. Delete any.
3. **Account settings** → **IAM user and role access to Billing Information** →
   **Activate**.

### Step 11 — Create a deploy user and log in as it · 🌐 Browser, then 💻 Mac

In the browser: **IAM → Users → Create user**

- Name: `frugal-deploy`
- **Attach policies directly** → `AdministratorAccess` (narrowed after the first
  apply)
- Create → open the user → **Security credentials** → **Create access key** →
  **Command Line Interface (CLI)** → copy the key and secret.

💻 **Mac** (directory doesn't matter):

```bash
aws configure
# AWS Access Key ID     -> paste
# AWS Secret Access Key -> paste
# Default region name   -> ap-south-1   (or ap-southeast-1, matching Step 6)
# Default output format -> json

aws sts get-caller-identity
```

The ARN printed must end in `user/frugal-deploy`. **If it says `root`, stop and
fix it** — the circuit breaker in the next step works by attaching a deny policy
to a principal, and no policy can restrain root.

### Step 12 — Install the cost guardrails · 💻 Mac · in `REPO/infra/aws`

```bash
cd ~/Documents/Rahul/Projects/Deployed/Frugal/infra/aws
./setup-cost-guardrails.sh you@example.com frugal-deploy
```

Creates a $0.01 zero-spend alert, a $5 forecast warning, and a $5 budget action
that attaches `deny-all-policy.json` to `frugal-deploy`.

🌐 **Browser**, then: **Billing → Billing preferences → Alert preferences** →
tick **Receive AWS Free Tier alerts** → enter your email → **Save**.

---

# Phase 4 — Infrastructure

### Step 13 — Apply the Terraform · 💻 Mac · in `REPO/infra/aws/terraform`

```bash
cd ~/Documents/Rahul/Projects/Deployed/Frugal/infra/aws/terraform
terraform init
terraform validate
```

> `terraform validate` is not a formality here. This configuration has never
> been applied — it passes `fmt`, but provider schemas were never verified
> because the registry was unreachable where it was written
> ([RUNBOOK §7](../infra/aws/RUNBOOK.md)). **Expect to fix something**, and fix
> it before applying.

```bash
terraform apply \
  -var="ssh_ingress_cidr=$(curl -s ifconfig.me)/32" \
  -var="ssh_public_key=$(cat ~/.ssh/id_ed25519.pub)" \
  -var="alert_email=you@example.com"
```

Add `-var="region=ap-southeast-1"` if you chose Singapore at Step 6.

**Read the plan before typing `yes`.** It should create one `t3.micro`, one S3
bucket, one IAM role and instance profile, one security group, one log group,
and some alarms. If you see **NAT Gateway**, **Load Balancer**, or **Elastic
IP**, type `no` — those three are what produce surprise bills.

Type `yes`. Takes about two minutes.

> No SSH key yet? `ssh-keygen -t ed25519 -C "frugal-deploy"`, accept the
> defaults, then re-run.

### Step 14 — Capture the outputs · 💻 Mac · same directory

```bash
export FRUGAL_IP="$(terraform output -raw public_ip)"
export FRUGAL_BUCKET="$(terraform output -raw receipts_bucket)"
echo "IP=$FRUGAL_IP  BUCKET=$FRUGAL_BUCKET"
```

Copy both into your note as ⑤ and ④. **Keep this Terminal tab open** — those two
variables are used through Step 23, and they vanish if you close it.

Now re-run the guardrails so the budget action can see the new instance:

```bash
cd .. && ./setup-cost-guardrails.sh you@example.com frugal-deploy
```

📧 **Check your email and click the SNS confirmation link.** Terraform reports
success whether or not anyone confirms the subscription, and until it is
confirmed every alarm fires into nothing.

### Step 15 — Point a hostname at the instance · 🌐 Browser, then 💻 Mac

The instance needs a real hostname: Let's Encrypt will not issue a certificate
for `*.compute.amazonaws.com`, and the proxy hop from Render crosses the public
internet carrying bearer tokens, so plain HTTP is not acceptable.

🌐 [duckdns.org](https://duckdns.org) → sign in with GitHub → type a subdomain
such as `frugal-api` → **add domain**. Copy the **token** shown at the top.

💻 **Mac**, same tab as Step 14:

```bash
export DUCKDNS_TOKEN="paste-your-token"
export FRUGAL_HOST="frugal-api.duckdns.org"

curl -s "https://www.duckdns.org/update?domains=frugal-api&token=${DUCKDNS_TOKEN}&ip=${FRUGAL_IP}"
echo
dig +short "${FRUGAL_HOST}"
```

The `curl` prints `OK`, and `dig` must print exactly your `FRUGAL_IP`. Save the
hostname as ⑥.

> **The public IP changes on stop/start.** There is no Elastic IP, deliberately
> — an idle one bills hourly. After any stop/start, re-run that `curl` before
> wondering why TLS broke.

---

# Phase 5 — Deploy the backend

### Step 16 — Wait for cloud-init · 💻 Mac

```bash
ssh ubuntu@${FRUGAL_IP} 'test -f /opt/frugal/.bootstrapped && echo ready'
```

Prints `ready` once the instance has installed Docker and built its swap file.
Give it 2–3 minutes after `terraform apply`; retry until it prints. Accept the
host fingerprint when SSH asks.

### Step 17 — Get onto the server · 💻 Mac

```bash
ssh ubuntu@${FRUGAL_IP}
```

Your prompt changes to `ubuntu@ip-...`. The next step runs there.

### Step 18 — Write the secrets file · 🖥️ Server

Secrets are created by hand on the instance and never deployed from the
repository — cloud-init user-data is readable by anything on the box that can
reach the metadata service, so it is not a place for them either.

```bash
nano /opt/frugal/.env
```

Paste this, replacing each ① … ⑥ with the value from your note:

```
DATABASE_URL=①
DATABASE_DIRECT_URL=②
REDIS_URL=redis://redis:6379/0
JWT_SECRET=③
S3_BUCKET=④
AWS_REGION=ap-south-1
DOMAIN=⑥
CORS_ORIGINS=https://frugal-web.onrender.com
```

Save with **Ctrl+O**, **Enter**, then exit with **Ctrl+X**.

- `AWS_REGION` must match Step 13.
- `CORS_ORIGINS` is a guess until Step 21 tells you the real Render URL. Step 22
  corrects it.
- Note what is **absent**: no `S3_ACCESS_KEY`, no `S3_SECRET_KEY`. boto3 reads
  the instance profile's rotating credentials from the metadata service, so no
  long-lived key exists on this machine to leak — the one item on the cost-risk
  list that reaches four figures.

Lock it down, confirm it, and leave:

```bash
chmod 600 /opt/frugal/.env
cat /opt/frugal/.env
exit
```

`exit` returns you to your Mac.

### Step 19 — Deploy · 💻 Mac · in `REPO/infra/aws`

```bash
cd ~/Documents/Rahul/Projects/Deployed/Frugal/infra/aws
./deploy.sh "${FRUGAL_IP}"
```

This rsyncs the source, builds the images **on the instance**, runs
`alembic upgrade head` against the direct endpoint, starts all five containers,
and polls `/health/ready` until it answers.

**The first run takes 10–20 minutes** — a `t3.micro` compiling OpenCV and
Prophet wheels is genuinely slow. It ends with `Deployed`.

### Step 20 — Verify the API from outside · 💻 Mac

```bash
curl -fsS "https://${FRUGAL_HOST}/health/ready"
```

Expect:

```json
{"status":"ready","dependencies":{"database":true,"redis":true}}
```

Run it from your Mac, not from the instance — this also proves the security
group and the certificate, which a request from localhost would not.

- **Hangs or TLS error?** Caddy is still fetching its certificate. Wait a minute,
  then:
  `ssh ubuntu@${FRUGAL_IP} 'cd /opt/frugal/infra/aws && docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env logs caddy'`
- **`"database":false`?** ① or ② is wrong — most likely the query string is still
  on the end. Re-read Step 7.

---

# Phase 6 — Deploy the frontend

### Step 21 — Create the Render service · 🌐 Browser

1. [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**.
2. **Connect** your GitHub account and pick the `frugal` repository.
3. Render reads [`render.yaml`](../render.yaml) and proposes one free web
   service called **frugal-web**. Give the blueprint any name.
4. It prompts for the two variables marked `sync: false`:

   | Key | Value |
   |---|---|
   | `BACKEND_ORIGIN` | `https://frugal-api.duckdns.org` — your ⑥, with `https://` |
   | `NEXT_PUBLIC_API_URL` | `https://frugal-web.onrender.com` — a placeholder for now |

5. **Apply** / **Create resources**. The first build takes 5–10 minutes.
6. When it goes live, copy the URL from the top of the service page. Save as ⑦.

### Step 22 — Reconcile the real URL · 🌐 Browser, then 💻 Mac

Render appends a suffix when a service name is taken, so ⑦ is often
`frugal-web-a1b2.onrender.com` rather than what you guessed. If ⑦ is **not**
exactly `https://frugal-web.onrender.com`, fix both ends.

🌐 **Browser** — Render → **frugal-web** → **Environment** → edit
`NEXT_PUBLIC_API_URL` to ⑦ → **Save changes** → **Manual Deploy** → **Deploy
latest commit**.

> A restart is not enough. `NEXT_PUBLIC_*` values are inlined into the client
> bundle at build time, so this needs a full rebuild.

💻 **Mac** — point the backend's CORS at the real origin:

```bash
ssh ubuntu@${FRUGAL_IP} \
  "sed -i 's|^CORS_ORIGINS=.*|CORS_ORIGINS=https://YOUR-REAL-RENDER-HOST|' /opt/frugal/.env"

cd ~/Documents/Rahul/Projects/Deployed/Frugal/infra/aws
./deploy.sh "${FRUGAL_IP}"
```

This second deploy is fast — the images are cached.

---

# Phase 7 — Verify

Not "the page loaded". These are the failures specific to this topology, and
each one is silent.

### Step 23 — Port 8000 is not public · 💻 Mac

```bash
curl -fsS "https://${FRUGAL_HOST}/health/ready" && echo "OK: TLS works"
curl -fsS --max-time 5 "http://${FRUGAL_IP}:8000/health/ready" && echo "LEAK: port 8000 is public"
```

The first must succeed, the second must fail. The API is never published to the
host — only Caddy reaches it.

### Step 24 — Requests are same-origin · 🌐 Browser

Open ⑦ → DevTools (**F12**) → **Network** → register an account.

Every API request must go to **`https://<your-render-host>/api/v1/...`**. If you
see `duckdns.org` in that column, `NEXT_PUBLIC_API_URL` is wrong — sessions will
not survive. Redo Step 22.

### Step 25 — The refresh cookie survives · 🌐 Browser

The whole reason for the proxy. With the app open:

DevTools → **Application** → **Cookies** → select your Render origin. There must
be a `frugal_refresh` cookie, `HttpOnly` ticked, path `/api/v1/auth`.

Then leave the tab open **more than 15 minutes** and click to another page. The
access token expires at 15 minutes; if you are bounced to the login screen, the
refresh cookie is not travelling.

### Step 26 — Rate limiting sees real clients · 💻 Mac

Every request now arrives via Render. If the original client address is lost,
all users are throttled as one.

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code} " -X POST \
    "https://YOUR-REAL-RENDER-HOST/api/v1/auth/login" \
    -H 'Content-Type: application/json' \
    -d '{"email":"nobody@example.com","password":"wrong-password"}'
done; echo
```

Expect `401`s turning into `429`s. **Then open the site on a phone using mobile
data** and confirm you can still log in. If the phone is blocked too, the
left-most `X-Forwarded-For` entry reaching
[`client_ip`](../backend/app/core/dependencies.py) is Render's address rather
than the client's.

### Step 27 — Backups restore · 💻 Mac · in `REPO/infra/ops`

A backup that has never been restored is a hypothesis.

```bash
cd ~/Documents/Rahul/Projects/Deployed/Frugal/infra/ops
export DATABASE_DIRECT_URL='②'
export S3_BUCKET="${FRUGAL_BUCKET}"
./backup.sh ~/frugal-backups
./restore.sh ~/frugal-backups/<newest-directory> --into-scratch
```

`--into-scratch` restores into a throwaway container, counts rows, and **exits
non-zero if the restore is clean but empty** — which is what a backup pointed at
the wrong database looks like at every other step.

### Step 28 — Fire an alarm on purpose

Follow [RUNBOOK §4](../infra/aws/RUNBOOK.md). An alarm nobody has triggered is a
configuration, not a safety net.

---

## Afterwards

- **Weekly:** Billing → Bills. Two minutes, and it is the control that has
  actually caught things for most people.
- **Narrow `frugal-deploy`.** `AdministratorAccess` was for the first apply.
- **Re-run the load test against the deployed URL.**
  `infra/load/api-load-test.js` measured a laptop; NFR-1's 6× headroom says
  nothing about a `t3.micro` talking to Neon across a network.
- **The first request after idle takes ~1 minute.** Render free services sleep
  after 15 minutes. The 750 instance-hours are a workspace-wide budget, so a
  second free service shares them.
- **Redeploying after a code change:** push to GitHub — Render rebuilds the
  frontend by itself. The backend needs
  `cd REPO/infra/aws && ./deploy.sh "${FRUGAL_IP}"`.

Logs, restarts, migration status, and teardown are in
[RUNBOOK §3](../infra/aws/RUNBOOK.md).
