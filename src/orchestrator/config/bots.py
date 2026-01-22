"""Bot configuration model and registry."""

from dataclasses import dataclass
from functools import cache

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class BotConfig:
    """Configuration for a single Slack bot."""

    name: str
    channel_id: str
    working_dir: str
    slack_token: str | None = None  # If None, uses default from settings


# Bot registry - channel_id -> BotConfig
# Tokens will be loaded at runtime from environment/secrets
BOT_CONFIGS = {
    # TayTay
    "C0A6L1QBT36": BotConfig(
        name="TayTay",
        channel_id="C0A6L1QBT36",
        working_dir="/home/popy",
    ),
    # Jarvis
    "C0A3NCSDDMF": BotConfig(
        name="Jarvis",
        channel_id="C0A3NCSDDMF",
        working_dir="/home/paulo",
    ),
    # Friday
    "C0A6MHHCCHE": BotConfig(
        name="Friday",
        channel_id="C0A6MHHCCHE",
        working_dir="/home/alejo",
    ),
    # TP
    "C0A2V99L4KZ": BotConfig(
        name="TP",
        channel_id="C0A2V99L4KZ",
        working_dir="/home/paulo-tp",
    ),
    # BL
    "C0A3C7N7EDU": BotConfig(
        name="BL",
        channel_id="C0A3C7N7EDU",
        working_dir="/home/paulo-bl",
    ),
    # SW
    "C0A3HRZKZ8C": BotConfig(
        name="SW",
        channel_id="C0A3HRZKZ8C",
        working_dir="/home/paulo-sw",
    ),
    # KK
    "C0A3E6PL5S6": BotConfig(
        name="KK",
        channel_id="C0A3E6PL5S6",
        working_dir="/home/paulo-kk",
    ),
    # White-Vision (DEV bot)
    "C0A8CCS2FT2": BotConfig(
        name="White-Vision",
        channel_id="C0A8CCS2FT2",
        working_dir="/home/paulo",
    ),
    # Testing Channel
    "C0A3K9JUK8V": BotConfig(
        name="Testing",
        channel_id="C0A3K9JUK8V",
        working_dir="/home/paulo",
    ),
}


@cache
def get_bot_config(channel_id: str) -> BotConfig | None:
    """Get bot configuration for a channel.

    Returns None if the channel is not configured (unknown bot).
    """
    config = BOT_CONFIGS.get(channel_id)
    if config is None:
        logger.warning("Unknown channel - no bot configured", channel_id=channel_id)
    return config


def get_all_bots() -> list[BotConfig]:
    """Get all configured bots."""
    return list(BOT_CONFIGS.values())
