#!/usr/bin/env bash
# Slack Incoming Webhook 으로 메시지를 전송한다.
#
# 필요한 환경변수:
#   SLACK_WEBHOOK_URL  — Slack Incoming Webhook URL (secret)
#   SLACK_COLOR        — attachment 색상 (예: #36a64f, #ff0000)
#   SLACK_TITLE        — attachment 제목
#   SLACK_MESSAGE      — attachment 본문 (markdown)
#
# 사용 예 (workflow step):
#   - run: bash .github/workflows/templates/notify-slack.sh
#     env:
#       SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
#       SLACK_COLOR: '#36a64f'
#       SLACK_TITLE: '배포 결과'
#       SLACK_MESSAGE: ${{ env.SLACK_MESSAGE }}
#
# 의존성: bash, curl, jq (ubuntu-latest 와 일반 self-hosted runner에 모두 기본 포함)

set -euo pipefail

: "${SLACK_WEBHOOK_URL:?SLACK_WEBHOOK_URL 미설정}"
: "${SLACK_COLOR:?SLACK_COLOR 미설정}"
: "${SLACK_TITLE:?SLACK_TITLE 미설정}"
: "${SLACK_MESSAGE:?SLACK_MESSAGE 미설정}"

PAYLOAD=$(jq -nc \
  --arg color "$SLACK_COLOR" \
  --arg title "$SLACK_TITLE" \
  --arg text  "$SLACK_MESSAGE" \
  '{attachments: [{color: $color, title: $title, text: $text, mrkdwn_in: ["text"]}]}')

curl --silent --show-error --fail \
     -X POST \
     -H 'Content-type: application/json' \
     --data "$PAYLOAD" \
     "$SLACK_WEBHOOK_URL"
