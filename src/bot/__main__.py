"""Run the Discord bot: python -m src.bot"""
import sys

from src.platform.config import settings

if not settings.discord_token:
    print(
        "[bot] ERROR: DISCORD_TOKEN is not set. "
        "Add it to your .env file and restart.",
        file=sys.stderr,
    )
    sys.exit(1)

from src.bot.app import run  # noqa: E402

if __name__ == "__main__":
    run()
