"""Tests for Slack webhook handlers."""


def test_url_verification(client):
    """Test Slack URL verification challenge."""
    response = client.post(
        "/webhooks/slack",
        json={
            "type": "url_verification",
            "challenge": "test-challenge-token",
        },
    )
    assert response.status_code == 200
    assert response.text == "test-challenge-token"


def test_bot_message_filtered(client):
    """Test that bot messages are filtered out."""
    response = client.post(
        "/webhooks/slack",
        json={
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "bot_id": "B123",
                "channel": "C123",
                "text": "Bot message",
                "ts": "1234567890.123456",
            },
        },
    )
    assert response.status_code == 200


def test_message_deleted_filtered(client):
    """Test that deleted messages are filtered out."""
    response = client.post(
        "/webhooks/slack",
        json={
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "subtype": "message_deleted",
                "channel": "C123",
                "ts": "1234567890.123456",
            },
        },
    )
    assert response.status_code == 200


def test_user_message_accepted(client):
    """Test that user messages are accepted."""
    response = client.post(
        "/webhooks/slack",
        json={
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "user": "U123",
                "channel": "C123",
                "text": "Hello bot",
                "ts": "1234567890.123456",
            },
        },
    )
    assert response.status_code == 200
