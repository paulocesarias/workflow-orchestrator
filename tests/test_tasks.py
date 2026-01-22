"""Tests for Celery tasks."""

import pytest


def test_add_task_direct():
    """Test add task runs correctly when called directly."""
    from orchestrator.tasks.sample import add
    
    # Call the task directly (not via Celery)
    result = add(2, 3)
    assert result == 5


def test_process_message_direct():
    """Test process_message task runs correctly when called directly."""
    from orchestrator.tasks.sample import process_message
    
    result = process_message("Hello world", "C12345", "1234567890.123456")
    
    assert result["status"] == "processed"
    assert result["channel_id"] == "C12345"
    assert result["thread_ts"] == "1234567890.123456"
    assert result["message_length"] == 11


def test_process_message_no_thread():
    """Test process_message without thread timestamp."""
    from orchestrator.tasks.sample import process_message
    
    result = process_message("Test", "C12345")
    
    assert result["status"] == "processed"
    assert result["thread_ts"] is None
