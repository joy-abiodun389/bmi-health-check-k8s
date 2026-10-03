#!/usr/bin/env bash
# Open an interactive psql prompt inside the database pod.
#
#   ./infra/db-shell.sh                      # interactive session
#   ./infra/db-shell.sh "SELECT * FROM clients;"   # run one statement
#
# Credentials are read from the pod's own environment, so nothing sensitive
# passes through your shell history.
set -euo pipefail

NAMESPACE="${NAMESPACE:-bmi-api}"
DB_POD="${DB_POD:-bmi-db-0}"

if [ "$#" -gt 0 ]; then
  kubectl -n "$NAMESPACE" exec -i "$DB_POD" -- sh -c \
    "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"$1\""
else
  echo "Connecting to ${DB_POD} in ${NAMESPACE} (\\dt lists tables, \\q quits)"
  kubectl -n "$NAMESPACE" exec -it "$DB_POD" -- sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
fi
