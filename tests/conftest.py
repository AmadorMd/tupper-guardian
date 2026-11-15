import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.update(
    {
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "TABLE_NAME": "Tuppers",
        "SHELF_TABLE_NAME": "ShelfLife",
        "BOT_TOKEN_PARAM": "/test/bot-token",
        "BOT_USERNAME": "guardian_test_bot",
        "MODEL_ID": "test-model",
        "SECONDS_PER_DAY": "86400",
        "ASK_TIMEOUT_SECONDS": "600",
        "VOICE_ENABLED": "false",
    }
)

NOW = 1_800_000_000  # fixed clock for deterministic timestamps


@pytest.fixture
def tables(monkeypatch):
    """Fresh in-memory DynamoDB tables wired into the inventory and notify modules."""
    from guardian import inventory, notify

    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        tuppers = resource.create_table(
            TableName="Tuppers",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "chat_id", "AttributeType": "S"},
                {"AttributeName": "item_id", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "chat_id", "KeyType": "HASH"},
                {"AttributeName": "item_id", "KeyType": "RANGE"},
            ],
        )
        shelf = resource.create_table(
            TableName="ShelfLife",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "category", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "category", "KeyType": "HASH"}],
        )
        shelf.put_item(Item={"category": "cooked_soup_stew", "days": 3})
        shelf.put_item(Item={"category": "guacamole", "days": 1})
        monkeypatch.setattr(inventory, "_resource", resource)
        monkeypatch.setattr(notify, "_dynamodb", resource)
        monkeypatch.setattr(inventory, "_now", lambda: NOW)
        yield tuppers
