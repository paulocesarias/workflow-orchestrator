"""Bot configuration model."""

from pydantic import BaseModel


class BotConfig(BaseModel):
    """Configuration for a Slack bot."""

    name: str
    channel_id: str
    working_dir: str
    token_env: str  # Environment variable name containing the Slack token

    @property
    def token(self) -> str:
        """Get the bot token from environment."""
        import os

        token = os.environ.get(self.token_env, "")
        if not token:
            raise ValueError(f"Bot token not found in environment: {self.token_env}")
        return token
