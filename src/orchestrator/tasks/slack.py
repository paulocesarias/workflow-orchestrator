"""Slack message processing tasks."""

import time

import structlog
from celery import shared_task

from orchestrator.api.metrics import ACTIVE_TASKS, TASK_DURATION
from orchestrator.config import get_settings
from orchestrator.services.claude_parser import ClaudeStreamParser, EventType
from orchestrator.services.slack_client import (
    Reaction,
    SlackClient,
    StreamingSlackUpdater,
)
from orchestrator.services.ssh_executor import SSHExecutor
from orchestrator.tasks.base import BaseTask

logger = structlog.get_logger()


@shared_task(bind=True, base=BaseTask)
def process_slack_message(
    self,
    event: dict,
    session_id: str,
    working_dir: str = "/home/paulo",
    bot_name: str = "unknown",
) -> dict:
    """Process an incoming Slack message by executing Claude on remote host.

    Flow:
    1. Add hourglass reaction to original message
    2. Post initial "..." message in thread
    3. SSH to VPS and run Claude CLI with stream-json output
    4. Parse streaming output and update Slack in real-time
    5. Finalize with stats and summary
    6. Replace hourglass with checkmark/x

    Args:
        event: Slack event data (channel_id, user_id, text, ts, thread_ts, files)
        session_id: Deterministic session UUID for conversation continuity
        working_dir: Working directory for Claude CLI execution on remote host
        bot_name: Name of the bot for metrics tracking

    Returns:
        Processing result dictionary
    """
    settings = get_settings()
    slack_token = settings.slack_bot_token

    if not slack_token:
        logger.error("SLACK_BOT_TOKEN not configured")
        return {"status": "error", "error": "SLACK_BOT_TOKEN not configured"}

    channel_id = event.get("channel_id", "")
    user_id = event.get("user_id", "")
    text = event.get("text", "")
    ts = event.get("ts", "")
    thread_ts = event.get("thread_ts")
    files = event.get("files", [])

    # Use thread_ts if in thread, otherwise use message ts as thread parent
    reply_ts = thread_ts or ts

    # Determine SSH user from working directory
    ssh_user = working_dir.split("/")[-1] if working_dir.startswith("/home/") else "paulo"

    logger.info(
        "Processing Slack message",
        task_id=self.request.id,
        bot_name=bot_name,
        channel_id=channel_id,
        user_id=user_id,
        session_id=session_id,
        text_length=len(text),
        file_count=len(files),
        is_thread=thread_ts is not None,
        working_dir=working_dir,
        ssh_user=ssh_user,
    )

    # Track active task
    ACTIVE_TASKS.labels(bot=bot_name).inc()
    start_time = time.time()

    slack_client = SlackClient(slack_token)
    executor = SSHExecutor(user=ssh_user)
    parser = ClaudeStreamParser()

    try:
        # Add hourglass reaction
        slack_client.add_reaction(channel_id, ts, Reaction.HOURGLASS)

        # Start streaming updater with initial "..." message
        updater = StreamingSlackUpdater(
            client=slack_client,
            channel=channel_id,
            thread_ts=reply_ts,
            update_interval=1.0,
        )
        updater.start("...")

        # Track if we should try resuming vs new session
        use_resume = False  # Start with new session, fallback to resume if needed

        # Execute Claude via SSH and stream output
        try:
            for line in executor.execute_claude_streaming(
                message=text,
                working_dir=working_dir,
                session_id=session_id if not use_resume else None,
                resume_session=session_id if use_resume else None,
            ):
                parsed = parser.parse_line(line)
                if not parsed:
                    continue

                # Handle different event types
                if parsed.type == EventType.ASSISTANT:
                    if parsed.text:
                        # Build message with tool notifications inline
                        display_text = parsed.text
                        if parser.state.tool_notifications:
                            tools_text = "\n".join(parser.state.tool_notifications[-3:])  # Show last 3
                            display_text = f"{parsed.text}\n\n{tools_text}"
                        updater.update(display_text)

                elif parsed.type == EventType.RESULT:
                    # Final result received
                    pass

        except Exception as e:
            # Check if error is "session already in use" - retry with resume
            error_str = str(e).lower()
            if "already in use" in error_str and not use_resume:
                logger.info("Session already exists, retrying with resume", session_id=session_id)
                use_resume = True
                # Retry with resume
                for line in executor.execute_claude_streaming(
                    message=text,
                    working_dir=working_dir,
                    resume_session=session_id,
                ):
                    parsed = parser.parse_line(line)
                    if not parsed:
                        continue

                    if parsed.type == EventType.ASSISTANT and parsed.text:
                        display_text = parsed.text
                        if parser.state.tool_notifications:
                            tools_text = "\n".join(parser.state.tool_notifications[-3:])
                            display_text = f"{parsed.text}\n\n{tools_text}"
                        updater.update(display_text)
            else:
                raise

        # Build final message
        final_text = parser.state.current_text or "_(No response)_"

        # Add summary if there were actions
        summary = parser.get_summary()
        if summary:
            final_text += f"\n\n_{summary}_"

        # Add stats
        stats_line = parser.get_stats_line()
        if stats_line:
            final_text += f"\n_{stats_line}_"

        # Finalize message
        updater.finalize(final_text)

        # Update reactions
        slack_client.remove_reaction(channel_id, ts, Reaction.HOURGLASS)
        if parser.state.is_error:
            slack_client.add_reaction(channel_id, ts, Reaction.ERROR)
        else:
            slack_client.add_reaction(channel_id, ts, Reaction.CHECKMARK)

        duration_ms = int((time.time() - start_time) * 1000)

        # Track duration metric
        TASK_DURATION.labels(bot=bot_name, task_type="claude_message").observe(
            duration_ms / 1000
        )

        response = {
            "status": "completed" if not parser.state.is_error else "error",
            "channel_id": channel_id,
            "user_id": user_id,
            "session_id": session_id,
            "reply_ts": reply_ts,
            "bot_name": bot_name,
            "duration_ms": duration_ms,
        }

        if parser.state.stats:
            response["cost_usd"] = parser.state.stats.total_cost_usd
            response["tokens"] = {
                "input": parser.state.stats.input_tokens,
                "output": parser.state.stats.output_tokens,
            }

        if parser.state.is_error:
            response["error"] = parser.state.error_message

        logger.info("Slack message processed", result=response)
        return response

    except Exception as e:
        logger.error("Failed to process Slack message", error=str(e))

        # Try to update Slack with error
        try:
            slack_client.remove_reaction(channel_id, ts, Reaction.HOURGLASS)
            slack_client.add_reaction(channel_id, ts, Reaction.ERROR)
            slack_client.post_message(
                channel=channel_id,
                text=f"_Error: {str(e)[:200]}_",
                thread_ts=reply_ts,
            )
        except Exception:
            pass

        return {
            "status": "error",
            "error": str(e),
            "channel_id": channel_id,
            "user_id": user_id,
            "session_id": session_id,
        }

    finally:
        ACTIVE_TASKS.labels(bot=bot_name).dec()
        slack_client.close()
