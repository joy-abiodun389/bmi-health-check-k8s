#!/usr/bin/env bash
# Print the registered clients from the database pod.
#
#   ./infra/list-clients.sh
#
# Password hashes are never selected.
set -euo pipefail

NAMESPACE="${NAMESPACE:-bmi-api}"
DB_POD="${DB_POD:-bmi-db-0}"

kubectl -n "$NAMESPACE" exec -i "$DB_POD" -- sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
\pset border 2
SELECT
  id,
  email,
  full_name,
  to_char(created_at,    'YYYY-MM-DD HH24:MI') AS registered,
  to_char(last_login_at, 'YYYY-MM-DD HH24:MI') AS last_login
FROM clients
ORDER BY id;

SELECT count(*) AS total_clients FROM clients;
SQL
