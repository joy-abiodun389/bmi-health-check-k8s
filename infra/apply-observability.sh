#!/usr/bin/env bash
# Publishes the CloudWatch dashboard and alarms for the app.
# Load balancer widgets are added only when the Service has provisioned an NLB.
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-2}"
K8S_NAMESPACE="${K8S_NAMESPACE:-bmi-api}"
SERVICE_NAME="${SERVICE_NAME:-bmi-api}"
DASHBOARD_NAME="${DASHBOARD_NAME:-bmi-health-check}"
METRICS_NAMESPACE="${METRICS_NAMESPACE:-BMI/HealthCheck}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The NLB metric dimension is the tail of its ARN (net/<name>/<id>), which we
# can only get by matching the Service's hostname against the ELB API.
LB_DIMENSION=""
LB_HOSTNAME="$(kubectl -n "$K8S_NAMESPACE" get svc "$SERVICE_NAME" \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"

if [ -n "$LB_HOSTNAME" ]; then
  LB_ARN="$(aws elbv2 describe-load-balancers --region "$AWS_REGION" \
    --query "LoadBalancers[?DNSName=='${LB_HOSTNAME}'].LoadBalancerArn | [0]" \
    --output text 2>/dev/null || true)"
  if [ -n "$LB_ARN" ] && [ "$LB_ARN" != "None" ]; then
    LB_DIMENSION="${LB_ARN#*:loadbalancer/}"
    echo "Load balancer dimension: ${LB_DIMENSION}"
  fi
fi

BODY="$(
  AWS_REGION="$AWS_REGION" \
  LB_DIMENSION="$LB_DIMENSION" \
  TEMPLATE="${SCRIPT_DIR}/cloudwatch-dashboard.json" \
  python3 <<'PY'
import json
import os

region = os.environ["AWS_REGION"]
lb = os.environ.get("LB_DIMENSION", "")

with open(os.environ["TEMPLATE"]) as handle:
    dashboard = json.loads(handle.read().replace("__REGION__", region))

if lb:
    dashboard["widgets"].append(
        {
            "type": "metric",
            "x": 0,
            "y": 14,
            "width": 24,
            "height": 6,
            "properties": {
                "title": "Network Load Balancer",
                "view": "timeSeries",
                "region": region,
                "period": 60,
                "metrics": [
                    ["AWS/NetworkELB", "HealthyHostCount", "LoadBalancer", lb,
                     {"stat": "Average", "label": "Healthy targets"}],
                    [".", "UnHealthyHostCount", ".", ".",
                     {"stat": "Average", "label": "Unhealthy targets"}],
                    [".", "NewFlowCount", ".", ".",
                     {"stat": "Sum", "label": "New connections", "yAxis": "right"}],
                    [".", "ProcessedBytes", ".", ".",
                     {"stat": "Sum", "label": "Processed bytes", "yAxis": "right"}],
                ],
                "yAxis": {"left": {"label": "Targets", "showUnits": False, "min": 0},
                          "right": {"showUnits": False, "min": 0}},
            },
        }
    )

print(json.dumps(dashboard))
PY
)"

echo "==> Dashboard ${DASHBOARD_NAME}"
aws cloudwatch put-dashboard \
  --region "$AWS_REGION" \
  --dashboard-name "$DASHBOARD_NAME" \
  --dashboard-body "$BODY" >/dev/null
echo "    published"

echo "==> Alarms"
aws cloudwatch put-metric-alarm \
  --region "$AWS_REGION" \
  --alarm-name "${DASHBOARD_NAME}-5xx-errors" \
  --alarm-description "BMI API returned 5xx responses" \
  --namespace "$METRICS_NAMESPACE" \
  --metric-name ErrorCount \
  --dimensions "Name=Service,Value=${SERVICE_NAME}" \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 5 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching >/dev/null

aws cloudwatch put-metric-alarm \
  --region "$AWS_REGION" \
  --alarm-name "${DASHBOARD_NAME}-slow-requests" \
  --alarm-description "BMI API request latency above 2s" \
  --namespace "$METRICS_NAMESPACE" \
  --metric-name LatencyMs \
  --dimensions "Name=Service,Value=${SERVICE_NAME}" \
  --statistic Maximum \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 2000 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching >/dev/null
echo "    5xx-errors and slow-requests configured"

echo
echo "Dashboard: https://${AWS_REGION}.console.aws.amazon.com/cloudwatch/home?region=${AWS_REGION}#dashboards:name=${DASHBOARD_NAME}"
