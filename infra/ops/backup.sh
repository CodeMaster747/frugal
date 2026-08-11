#!/usr/bin/env bash
#
# Back up everything that is not reproducible from the repository.
#
#   ./backup.sh [destination-directory]
#
# Destination defaults to ./backups. **Local by design, not S3.**
#
# The obvious place for a backup is the S3 bucket the application already has.
# That is wrong here: the AWS account is on the Free Plan and will pause when
# its credits or six months run out, and a backup stored inside the account that
# stopped is not a backup. The two things worth keeping -- the database and the
# receipt images -- must land somewhere AWS cannot switch off.
#
# What is *not* backed up, deliberately:
#   * Redis (Upstash) holds only caches, rate-limit counters, and the Celery
#     queue. All of it is derived or transient; losing it costs a cold cache.
#   * The application image, which rebuilds from the Dockerfile.
#   * Terraform state, which describes infrastructure that is recreated rather
#     than restored -- see ACCOUNT-MIGRATION.md.

set -euo pipefail

DEST="${1:-./backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN="${DEST}/${STAMP}"

: "${DATABASE_DIRECT_URL:?Set DATABASE_DIRECT_URL to the direct, non-pooled Neon endpoint. pg_dump against the pooled endpoint fails partway through a large dump}"

mkdir -p "${RUN}"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# --- database ---------------------------------------------------------------
# Custom format (-Fc): compressed, and restorable selectively with pg_restore,
# which plain SQL is not.

say "1/3  Database"

# Run pg_dump in a container matching the major version of the server. A
# pg_dump older than the server refuses to run, and the version on the host is
# Homebrew last installed.
docker run --rm -i \
  --add-host=host.docker.internal:host-gateway \
  -v "$(cd "${RUN}" && pwd):/out" \
  postgres:16-alpine \
  pg_dump --dbname="${DATABASE_DIRECT_URL/postgresql+asyncpg:/postgresql:}" \
  --format=custom --compress=9 --no-owner --no-privileges \
  --file="/out/database.dump"

printf '     %s\n' "$(du -h "${RUN}/database.dump" | cut -f1) written"

# --- receipts ---------------------------------------------------------------
# The only user data that lives in AWS. Everything else about a receipt -- the
# extracted fields, the confidence scores, the transaction it became -- is in
# Postgres and is covered by the dump above. This is the image itself.

say "2/3  Receipt images"

if [[ -n "${S3_BUCKET:-}" ]] && command -v aws >/dev/null 2>&1; then
  aws s3 sync "s3://${S3_BUCKET}" "${RUN}/receipts/" --only-show-errors
  printf '     %s objects\n' "$(find "${RUN}/receipts" -type f 2>/dev/null | wc -l | tr -d ' ')"
else
  echo "     skipped: set S3_BUCKET and install the AWS CLI to include receipt images"
fi

# --- manifest ---------------------------------------------------------------
# So a restore knows what it is looking at. A dump with no record of which
# schema version produced it is a dump you have to guess about.

say "3/3  Manifest"

MIGRATION="$(docker run --rm -i --add-host=host.docker.internal:host-gateway postgres:16-alpine \
  psql "${DATABASE_DIRECT_URL/postgresql+asyncpg:/postgresql:}" \
  -tAc 'SELECT version_num FROM alembic_version' 2>/dev/null || echo 'unknown')"

# Credentials stripped before anything is written to a file that will sit on
# disk next to the data it describes.
REDACTED_URL="$(printf '%s' "${DATABASE_DIRECT_URL}" | sed -E 's#//[^@]+@#//***@#')"

cat >"${RUN}/manifest.txt" <<EOF
taken_at:        ${STAMP}
alembic_version: ${MIGRATION}
database:        ${REDACTED_URL}
s3_bucket:       ${S3_BUCKET:-(not backed up)}
restore_with:    infra/ops/restore.sh ${RUN}
EOF

cat "${RUN}/manifest.txt"

say "Backup complete: ${RUN}"
echo "Verify it by actually restoring it. An untested backup is a hope, not a plan:"
echo "  ./restore.sh ${RUN} --into-scratch"
