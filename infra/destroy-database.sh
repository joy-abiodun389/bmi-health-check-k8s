#!/usr/bin/env bash
# Delete the database pod, its EBS volume, and the credentials secret.
#
#   ./infra/destroy-database.sh
#
# This is deliberately not something CI can do: it permanently erases every
# registered client. Needs cluster-admin credentials.
set -euo pipefail

NAMESPACE="${NAMESPACE:-bmi-api}"
DB_STATEFULSET="${DB_STATEFULSET:-bmi-db}"
DB_SECRET_NAME="${DB_SECRET_NAME:-bmi-db-credentials}"

if kubectl -n "$NAMESPACE" get "statefulset/${DB_STATEFULSET}" >/dev/null 2>&1; then
  COUNT=$(kubectl -n "$NAMESPACE" exec -i "${DB_STATEFULSET}-0" -- sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM clients"' 2>/dev/null || echo "?")
  echo "About to erase ${COUNT} registered client(s) in ${NAMESPACE}."
fi

read -r -p "Type ERASE to continue: " CONFIRM
[ "$CONFIRM" = "ERASE" ] || { echo "Aborted."; exit 1; }

kubectl -n "$NAMESPACE" delete --ignore-not-found=true \
  "statefulset/${DB_STATEFULSET}" "service/${DB_STATEFULSET}"

# volumeClaimTemplates PVCs outlive their StatefulSet, and the EBS volume
# keeps billing until the claim goes away.
kubectl -n "$NAMESPACE" delete pvc --ignore-not-found=true \
  -l "app.kubernetes.io/name=${DB_STATEFULSET}"
kubectl -n "$NAMESPACE" delete pvc --ignore-not-found=true \
  "data-${DB_STATEFULSET}-0" 2>/dev/null || true

kubectl -n "$NAMESPACE" delete secret --ignore-not-found=true "$DB_SECRET_NAME"

echo "Database removed. Re-run infra/bootstrap.sh to recreate the credentials secret."
