#!/usr/bin/env bash
# One-command deploy: package, deploy the stack, seed shelf-life data, and
# register the Telegram webhook. Needs only the AWS CLI and python3.
#
#   ./scripts/deploy.sh                                   # real timings
#   SECONDS_PER_DAY=20 ASK_TIMEOUT_SECONDS=45 ./scripts/deploy.sh   # demo timings
set -euo pipefail
cd "$(dirname "$0")/.."

STACK="${STACK_NAME:-tupper-guardian}"
REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"
REGION="${REGION:-us-east-1}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
export BOT_TOKEN_PARAM="${BOT_TOKEN_PARAM:-/tupper-guardian/bot-token}"
MODEL_ID="${MODEL_ID:-us.amazon.nova-lite-v1:0}"
SECONDS_PER_DAY="${SECONDS_PER_DAY:-86400}"
ASK_TIMEOUT_SECONDS="${ASK_TIMEOUT_SECONDS:-21600}"
VOICE_ENABLED="${VOICE_ENABLED:-true}"

echo "==> Checking prerequisites (region $REGION)"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
if ! aws ssm get-parameter --name "$BOT_TOKEN_PARAM" >/dev/null 2>&1; then
  echo "Bot token not found. Run ./scripts/store_bot_token.sh first." >&2
  exit 1
fi
if ! aws bedrock-runtime converse --model-id "$MODEL_ID" \
    --messages '[{"role":"user","content":[{"text":"ok"}]}]' \
    --inference-config '{"maxTokens":5}' >/dev/null; then
  echo "Bedrock model $MODEL_ID is not usable in $REGION." >&2
  echo "List options with: aws bedrock list-inference-profiles --region $REGION" >&2
  echo "Then rerun with MODEL_ID=<id> (it must accept image input)." >&2
  exit 1
fi
BOT_USERNAME="$(python3 scripts/telegram_admin.py username)"
echo "    Bot: @$BOT_USERNAME"

if [[ ! -f .webhook-secret ]]; then
  (umask 077 && python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > .webhook-secret)
fi

BUCKET="tupper-guardian-artifacts-$ACCOUNT-$REGION"
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "==> Creating artifact bucket $BUCKET"
  aws s3 mb "s3://$BUCKET" >/dev/null
fi

echo "==> Packaging"
aws cloudformation package --template-file template.yaml --s3-bucket "$BUCKET" \
  --output-template-file .packaged.yaml >/dev/null

echo "==> Deploying stack $STACK"
aws cloudformation deploy --template-file .packaged.yaml --stack-name "$STACK" \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  --parameter-overrides \
    "WebhookSecret=$(cat .webhook-secret)" \
    "BotTokenParam=$BOT_TOKEN_PARAM" \
    "BotUsername=$BOT_USERNAME" \
    "ModelId=$MODEL_ID" \
    "SecondsPerDay=$SECONDS_PER_DAY" \
    "AskTimeoutSeconds=$ASK_TIMEOUT_SECONDS" \
    "VoiceEnabled=$VOICE_ENABLED"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

echo "==> Seeding shelf-life data"
python3 scripts/seed_shelf_life.py "$(output ShelfLifeTableName)"

echo "==> Registering Telegram webhook"
python3 scripts/telegram_admin.py set-webhook "$(output WebhookBaseUrl)"

echo
echo "Done. Open https://t.me/$BOT_USERNAME and send: Guardé caldo de pollo en el refri"
echo "A day lasts $SECONDS_PER_DAY s; unanswered alerts escalate after $ASK_TIMEOUT_SECONDS s."
