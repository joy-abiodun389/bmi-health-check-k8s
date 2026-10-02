#!/usr/bin/env bash
# Posts a pipeline status message to Slack.
# Usage: slack-notify.sh <pass|fail|success|failure|cancelled>
# No-ops when SLACK_WEBHOOK_URL is unset so forks and local runs stay quiet.
set -euo pipefail

STATUS="${1:-}"

if [ -z "${SLACK_WEBHOOK_URL:-}" ]; then
  echo "SLACK_WEBHOOK_URL not set; skipping Slack notification"
  exit 0
fi

case "$STATUS" in
  pass)
    EMOJI=":shield:"
    HEADLINE="Trivy scan passed — image clean of fixable CRITICAL/HIGH"
    COLOR="#2f7d4a"
    ;;
  fail)
    EMOJI=":rotating_light:"
    HEADLINE="Trivy scan FAILED — vulnerable image blocked before push"
    COLOR="#b85c38"
    ;;
  success)
    EMOJI=":rocket:"
    HEADLINE="Deployed to EKS successfully"
    COLOR="#2f7d4a"
    ;;
  failure)
    EMOJI=":x:"
    HEADLINE="Deploy to EKS failed"
    COLOR="#b85c38"
    ;;
  *)
    EMOJI=":warning:"
    HEADLINE="Pipeline finished with status: ${STATUS}"
    COLOR="#d8c3a5"
    ;;
esac

RUN_URL="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
COMMIT_SHORT="${GITHUB_SHA:0:7}"
DETAILS="*Repo:* ${GITHUB_REPOSITORY}\n*Commit:* \`${COMMIT_SHORT}\` by ${GITHUB_ACTOR}\n*Trigger:* ${GITHUB_EVENT_NAME} on \`${GITHUB_REF_NAME}\`"

if [ -n "${IMAGE_TAG:-}" ]; then
  DETAILS="${DETAILS}\n*Image tag:* \`${IMAGE_TAG:0:12}\`"
fi

if [ -n "${APP_URL:-}" ]; then
  DETAILS="${DETAILS}\n*App:* http://${APP_URL}/"
fi

PAYLOAD=$(
  EMOJI="$EMOJI" HEADLINE="$HEADLINE" COLOR="$COLOR" DETAILS="$DETAILS" RUN_URL="$RUN_URL" \
  python3 <<'PY'
import json
import os

details = os.environ["DETAILS"].replace("\\n", "\n")

payload = {
    "attachments": [
        {
            "color": os.environ["COLOR"],
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{os.environ['EMOJI']} *{os.environ['HEADLINE']}*\n{details}",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View workflow run"},
                            "url": os.environ["RUN_URL"],
                        }
                    ],
                },
            ],
        }
    ]
}
print(json.dumps(payload))
PY
)

HTTP_CODE=$(curl -sS -o /tmp/slack-response.txt -w '%{http_code}' \
  -X POST -H 'Content-Type: application/json' \
  --data "$PAYLOAD" "$SLACK_WEBHOOK_URL" || echo "000")

if [ "$HTTP_CODE" = "200" ]; then
  echo "Slack notified (${STATUS})"
else
  # A broken webhook should not fail an otherwise good deploy.
  echo "Slack notification failed with HTTP ${HTTP_CODE}: $(cat /tmp/slack-response.txt)"
fi
