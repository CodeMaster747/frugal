#!/usr/bin/env bash
#
# Deploy Frugal to the instance.
#
#   ./deploy.sh <public-ip>
#
# Builds nothing locally and pushes no images. The instance builds from source,
# which on a t3.micro is slow (several minutes for the worker image) but avoids
# a registry: ECR outside the free tier is a recurring charge, and Docker Hub
# needs credentials on the box. For a single instance, building in place is the
# cheaper trade.
#
# Secrets are not in this script and not in the repository. They live in
# /opt/frugal/.env on the instance, created once by hand — see RUNBOOK.md §2.

set -euo pipefail

HOST="${1:?Usage: $0 <public-ip>   (terraform -chdir=terraform output -raw public_ip)}"
SSH_USER="${SSH_USER:-ubuntu}"
REMOTE="${SSH_USER}@${HOST}"
APP_DIR="/opt/frugal"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

say "Deploying to ${REMOTE}"

# --- preflight --------------------------------------------------------------

ssh -o ConnectTimeout=10 "${REMOTE}" 'test -f /opt/frugal/.bootstrapped' || {
  echo "The instance has not finished cloud-init. Check /var/log/cloud-init-output.log" >&2
  exit 1
}

ssh "${REMOTE}" "test -f ${APP_DIR}/.env" || {
  cat >&2 <<EOF
No ${APP_DIR}/.env on the instance.

Secrets are never deployed from here — cloud-init user-data is readable by
anything on the box that can reach the metadata service, and this repository is
not a place for credentials. Create it once by hand:

  ssh ${REMOTE}
  sudo -u ubuntu tee ${APP_DIR}/.env >/dev/null <<'ENVFILE'
  DATABASE_URL=postgresql+asyncpg://...    # Neon pooled endpoint
  DATABASE_DIRECT_URL=postgresql+asyncpg://...   # Neon direct, for migrations
  REDIS_URL=rediss://...                   # Upstash
  JWT_SECRET=<openssl rand -hex 32>
  S3_BUCKET=<terraform output receipts_bucket>
  AWS_REGION=ap-south-1
  CORS_ORIGINS=https://<your-app>.vercel.app
  ENVFILE
  chmod 600 ${APP_DIR}/.env

Then run this again.
EOF
  exit 1
}

# --- ship the source --------------------------------------------------------
# rsync rather than git clone: no deploy key on the instance, and no
# requirement that the commit be pushed before it can be tested.

say "Copying source"
rsync -az --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.next' \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude 'backups' \
  --exclude '.env' \
  "${REPO_ROOT}/backend" "${REPO_ROOT}/infra" \
  "${REMOTE}:${APP_DIR}/"

# --- migrate, then restart --------------------------------------------------
# Migrations run before the new code starts, against the direct endpoint:
# Alembic through Neon's pooler can fail partway and leave the schema in a state
# no migration describes.

say "Applying migrations"
ssh "${REMOTE}" bash -euo pipefail <<'REMOTE_SCRIPT'
cd /opt/frugal/infra/aws
set -a; . /opt/frugal/.env; set +a

docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env build api

docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env \
  run --rm -e DATABASE_URL="${DATABASE_DIRECT_URL}" api \
  alembic upgrade head
REMOTE_SCRIPT

say "Starting services"
ssh "${REMOTE}" bash -euo pipefail <<'REMOTE_SCRIPT'
cd /opt/frugal/infra/aws
docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env up -d --build

echo "waiting for the API to report ready"
for _ in $(seq 1 30); do
  if docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env \
       exec -T api python -c "
import sys, urllib.request
sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health/ready', timeout=5).status == 200 else 1)
" 2>/dev/null; then
    echo "ready"; exit 0
  fi
  sleep 5
done

echo "The API did not become ready. Recent logs:" >&2
docker compose -f docker-compose.prod.yml --env-file /opt/frugal/.env logs --tail=40 api >&2
exit 1
REMOTE_SCRIPT

say "Deployed"
echo "Check it from here, not from the instance — that also proves the security"
echo "group and TLS are right, which a request from localhost does not:"
echo "  curl -fsS https://${HOST}/health/ready"
