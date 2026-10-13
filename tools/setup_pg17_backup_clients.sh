#!/usr/bin/env bash
# Operator-only PG17 CLI wrappers; never called by Discord bot startup.
set -euo pipefail

destination="${1:?private wrapper directory required}"
if [[ "${destination}" != /* || -L "${destination}" ]]; then
  echo "Expected an absolute, non-symlink private wrapper directory." >&2
  exit 2
fi
install -d -m 0700 "${destination}"

for cmd in pg_dump pg_restore; do
  {
    printf '#!/usr/bin/env bash\nset -euo pipefail\n'
    printf 'exec docker run --rm -i -e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGDATABASE -e PGSSLMODE -e PGCONNECT_TIMEOUT postgres:17 %s "$@"\n' "${cmd}"
  } > "${destination}/${cmd}"
  chmod 0700 "${destination}/${cmd}"
done

# Generates shell scripts only. No database connection is attempted.
