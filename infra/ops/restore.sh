#!/usr/bin/env bash
#
# Restore a backup produced by backup.sh.
#
#   ./restore.sh <backup-directory> --into-scratch    # verify, touching nothing real
#   ./restore.sh <backup-directory> --into "$URL"     # restore into a named database
#
# `--into-scratch` is the mode that matters and the one the runbook schedules.
# It restores into a throwaway Postgres container, counts the rows, and throws
# it away. A backup nobody has restored is not known to work -- the failure mode
# is silent, and it is discovered on the worst day.
#
# There is deliberately no "restore over production" convenience flag. Pointing
# this at a live database is possible with --into, and it should require typing
# the URL out.

set -euo pipefail

SOURCE="${1:?Usage: $0 <backup-directory> [--into-scratch | --into <database-url>]}"
MODE="${2:---into-scratch}"
TARGET_URL="${3:-}"

DUMP="${SOURCE}/database.dump"
[[ -f "${DUMP}" ]] || { echo "No database.dump in ${SOURCE}" >&2; exit 1; }

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

[[ -f "${SOURCE}/manifest.txt" ]] && { say "Manifest"; cat "${SOURCE}/manifest.txt"; }

case "${MODE}" in
  --into-scratch)
    CONTAINER="frugal-restore-check-$$"
    PASSWORD="restore-check"

    say "Starting a throwaway Postgres"
    docker run -d --rm --name "${CONTAINER}" \
      -e POSTGRES_PASSWORD="${PASSWORD}" -e POSTGRES_DB=restore_check \
      postgres:16-alpine >/dev/null

    # Removed however this script exits, including a failed restore.
    trap 'docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true' EXIT

    printf 'waiting for it to accept connections'
    until docker exec "${CONTAINER}" pg_isready -q -U postgres 2>/dev/null; do
      printf '.'; sleep 1
    done
    echo

    say "Restoring"
    docker cp "${DUMP}" "${CONTAINER}:/tmp/database.dump"
    # CITEXT and the other extensions come from the dump itself; --no-owner
    # because the scratch instance has no matching roles.
    docker exec "${CONTAINER}" pg_restore \
      --dbname="postgresql://postgres:${PASSWORD}@localhost/restore_check" \
      --no-owner --no-privileges /tmp/database.dump

    say "What came back"
    # Row counts, not "pg_restore exited 0". A dump can restore cleanly and be
    # empty -- which is exactly what a backup script pointed at the wrong
    # database produces, and it looks like success at every other step.
    docker exec "${CONTAINER}" psql \
      "postgresql://postgres:${PASSWORD}@localhost/restore_check" -tAc "
      SELECT format('%-22s %s', relname, n_live_tup)
      FROM pg_stat_user_tables
      WHERE n_live_tup > 0
      ORDER BY n_live_tup DESC
      LIMIT 12"

    USERS="$(docker exec "${CONTAINER}" psql \
      "postgresql://postgres:${PASSWORD}@localhost/restore_check" \
      -tAc 'SELECT count(*) FROM users' 2>/dev/null || echo 0)"

    if [[ "${USERS}" -eq 0 ]]; then
      say "FAILED: the restore produced zero users"
      echo "The dump restored without error and contains no accounts, which means"
      echo "the backup captured an empty or wrong database. Fix backup.sh before"
      echo "relying on this."
      exit 1
    fi

    say "Restore verified: ${USERS} users recovered"
    ;;

  --into)
    : "${TARGET_URL:?--into needs a database URL}"
    say "Restoring into ${TARGET_URL//:*@/:***@}"
    read -r -p "This overwrites that database. Type 'restore' to continue: " CONFIRM
    [[ "${CONFIRM}" == "restore" ]] || { echo "Aborted."; exit 1; }

    docker run --rm -i --add-host=host.docker.internal:host-gateway \
      -v "$(cd "${SOURCE}" && pwd):/in" postgres:16-alpine \
      pg_restore --dbname="${TARGET_URL/postgresql+asyncpg:/postgresql:}" \
      --no-owner --no-privileges --clean --if-exists /in/database.dump

    say "Restored. Now bring the schema to head, in case the dump predates a migration:"
    echo "  docker compose exec api alembic upgrade head"
    ;;

  *)
    echo "Unknown mode: ${MODE}" >&2
    exit 1
    ;;
esac
