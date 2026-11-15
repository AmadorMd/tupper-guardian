"""Notify Lambda: every outbound Telegram message.

A notification goes to `chat_id`, or to every chat in `chat_ids` when the
other members of a shared fridge must hear about an unanswered item.

When the workflow calls it with a task token (the "what happened?" question),
the token is saved on the tupper item *before* the message goes out, so a
button press can never arrive ahead of the token it needs to release.
"""

import logging
import os

import boto3
from botocore.exceptions import ClientError

from guardian import messages, telegram

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_dynamodb = None
_polly = None


def _save_token(fridge_key, item_id, token):
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    try:
        _dynamodb.Table(os.environ["TABLE_NAME"]).update_item(
            Key={"chat_id": str(fridge_key), "item_id": item_id},
            UpdateExpression="SET task_token = :token",
            ConditionExpression="attribute_exists(item_id)",
            ExpressionAttributeValues={":token": token},
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        logger.warning("Tupper %s no longer exists; token not saved", item_id)


def _synthesize(speech):
    global _polly
    if _polly is None:
        _polly = boto3.client("polly")
    request = {
        "Text": speech,
        "OutputFormat": "mp3",
        "VoiceId": os.environ.get("POLLY_VOICE", "Mia"),
        "LanguageCode": "es-MX",
    }
    try:
        response = _polly.synthesize_speech(Engine="neural", **request)
    except ClientError:
        response = _polly.synthesize_speech(Engine="standard", **request)
    return response["AudioStream"].read()


def _send_voice(chat_id, speech):
    """Voice is an accessibility extra: a failure must not fail the message."""
    try:
        telegram.send_voice(chat_id, _synthesize(speech))
    except Exception:  # noqa: BLE001 - best effort by design
        logger.exception("Voice note failed")


def handler(event, _context=None):
    recipients = event.get("chat_ids") or [event["chat_id"]]
    data = event.get("data") or {}

    if event.get("callback_query_id"):
        try:
            telegram.call("answerCallbackQuery", {"callback_query_id": event["callback_query_id"]})
        except telegram.TelegramError:
            logger.warning("answerCallbackQuery failed (query probably expired)")

    message = messages.build(event["kind"], data)

    if event.get("task_token"):
        _save_token(data["fridge_key"], data["item_id"], event["task_token"])

    speak = message.speech and os.environ.get("VOICE_ENABLED", "true").lower() == "true"
    for chat_id in recipients:
        payload = {"chat_id": chat_id, "text": message.text}
        if message.buttons:
            payload["reply_markup"] = {"inline_keyboard": message.buttons}
        try:
            telegram.call("sendMessage", payload)
        except telegram.TelegramError:
            # One member who blocked the bot must not silence the others.
            if len(recipients) == 1:
                raise
            logger.warning("Message to one member failed")
            continue
        if speak:
            _send_voice(chat_id, message.speech)
    return {"ok": True}
