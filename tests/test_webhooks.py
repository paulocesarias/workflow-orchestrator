"""Tests for Slack webhook handlers."""

from unittest.mock import patch


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


def test_channel_join_filtered(client):
    """Test that channel join messages are filtered out."""
    response = client.post(
        "/webhooks/slack",
        json={
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "subtype": "channel_join",
                "user": "U123",
                "channel": "C123",
                "ts": "1234567890.123456",
            },
        },
    )
    assert response.status_code == 200


@patch("orchestrator.tasks.slack.process_slack_message.delay")
def test_user_message_accepted(mock_task, client):
    """Test that user messages are accepted and task is queued."""
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
    mock_task.assert_called_once()


@patch("orchestrator.tasks.slack.process_slack_message.delay")
def test_file_share_accepted(mock_task, client):
    """Test that file_share messages are accepted."""
    response = client.post(
        "/webhooks/slack",
        json={
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "subtype": "file_share",
                "user": "U123",
                "channel": "C123",
                "text": "Check this image",
                "ts": "1234567890.123456",
                "files": [
                    {
                        "id": "F123",
                        "name": "screenshot.png",
                        "mimetype": "image/png",
                        "url_private": "https://files.slack.com/test.png",
                        "size": 1024,
                    }
                ],
            },
        },
    )
    assert response.status_code == 200
    mock_task.assert_called_once()


@patch("orchestrator.tasks.slack.process_slack_message.delay")
def test_mention_stripped(mock_task, client):
    """Test that @mentions are stripped from message text."""
    response = client.post(
        "/webhooks/slack",
        json={
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "user": "U123",
                "channel": "C123",
                "text": "<@U456> Hello bot",
                "ts": "1234567890.123456",
            },
        },
    )
    assert response.status_code == 200
    mock_task.assert_called_once()
    # Check that the event passed to task has stripped mentions
    call_args = mock_task.call_args
    event = call_args.kwargs.get("event") or call_args[1].get("event")
    assert event["text"] == "Hello bot"


def test_empty_message_filtered(client):
    """Test that empty messages (after stripping mentions) are filtered."""
    response = client.post(
        "/webhooks/slack",
        json={
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "user": "U123",
                "channel": "C123",
                "text": "<@U456>",  # Only a mention, becomes empty after strip
                "ts": "1234567890.123456",
            },
        },
    )
    assert response.status_code == 200


@patch("orchestrator.tasks.slack.process_slack_message.delay")
def test_thread_message(mock_task, client):
    """Test that thread messages include thread_ts."""
    response = client.post(
        "/webhooks/slack",
        json={
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "user": "U123",
                "channel": "C123",
                "text": "Reply in thread",
                "ts": "1234567890.999999",
                "thread_ts": "1234567890.123456",
            },
        },
    )
    assert response.status_code == 200
    mock_task.assert_called_once()
    call_args = mock_task.call_args
    event = call_args.kwargs.get("event") or call_args[1].get("event")
    assert event["thread_ts"] == "1234567890.123456"


def test_non_message_event_ignored(client):
    """Test that non-message events are ignored."""
    response = client.post(
        "/webhooks/slack",
        json={
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "reaction_added",
                "user": "U123",
                "reaction": "thumbsup",
            },
        },
    )
    assert response.status_code == 200
