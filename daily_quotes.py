#!/usr/bin/env python3
"""
Daily Quotes Poster

Posts a single daily quote to a configured Discord channel using the Bot API.

Behavior:
- 95% chance: choose from regular quotes list
- 5% chance: choose from mention quotes list, replacing {user} with a random human user mention
- If mention flow fails (no members, API error), fall back to a regular quote

Required environment variables:
- DISCORD_BOT_TOKEN: Bot token with permission to post in the target channel
- CHANNEL_ID_QUOTES: Channel ID to post daily quotes
- DAILY_QUOTES_YAML: YAML list of regular quotes

Optional:
- MENTION_QUOTES_YAML: YAML list of mention quotes (contain {user} placeholder)
- DRY_RUN: "true" to log the message without posting
"""

import logging
import os
import random
from typing import Iterable, List, Optional, Tuple

import requests
import yaml

DISCORD_API_BASE = "https://discord.com/api/v10"
MENTION_PROBABILITY = 0.05
PLACEHOLDER = "{user}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("daily_quotes")


def _parse_yaml_list(raw: str, name: str) -> List[str]:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid {name}: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{name} must be a YAML list.")
    return [q for q in data if isinstance(q, str) and q.strip()]


def load_quotes_from_env() -> Tuple[List[str], List[str]]:
    raw_quotes = (os.environ.get("DAILY_QUOTES_YAML") or "").strip()
    if not raw_quotes:
        raise ValueError("Missing DAILY_QUOTES_YAML env var.")

    quotes = _parse_yaml_list(raw_quotes, "DAILY_QUOTES_YAML")
    if not quotes:
        raise ValueError("DAILY_QUOTES_YAML list is empty.")

    raw_mention = (os.environ.get("MENTION_QUOTES_YAML") or "").strip()
    mention_quotes: List[str] = []
    if raw_mention:
        mention_quotes = _parse_yaml_list(raw_mention, "MENTION_QUOTES_YAML")

    return quotes, mention_quotes


def bot_headers(bot_token: str) -> dict:
    return {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }


def get_channel_guild_id(channel_id: str, bot_token: str) -> Optional[str]:
    url = f"{DISCORD_API_BASE}/channels/{channel_id}"
    response = requests.get(url, headers=bot_headers(bot_token), timeout=15)
    if response.status_code != 200:
        logger.warning("Failed to fetch channel info (%d): %s", response.status_code, response.text[:200])
        return None
    return response.json().get("guild_id")


def fetch_guild_members(guild_id: str, bot_token: str) -> List[dict]:
    members: List[dict] = []
    after: Optional[str] = None
    while True:
        params = {"limit": 1000}
        if after:
            params["after"] = after
        url = f"{DISCORD_API_BASE}/guilds/{guild_id}/members"
        response = requests.get(url, headers=bot_headers(bot_token), params=params, timeout=20)
        if response.status_code != 200:
            logger.warning("Failed to fetch guild members (%d): %s", response.status_code, response.text[:200])
            return []
        batch = response.json()
        if not batch:
            break
        members.extend(batch)
        after = batch[-1]["user"]["id"]
        if len(batch) < 1000:
            break
    return members


def pick_random_human_user_id(members: Iterable[dict]) -> Optional[str]:
    humans = []
    for member in members:
        user = member.get("user") or {}
        if not user.get("bot"):
            uid = user.get("id")
            if uid:
                humans.append(uid)
    if not humans:
        return None
    return random.choice(humans)


def build_message(
    quotes: List[str],
    mention_quotes: List[str],
    channel_id: str,
    bot_token: str,
) -> Tuple[str, bool]:
    use_mention = bool(mention_quotes) and random.random() < MENTION_PROBABILITY
    if not use_mention:
        return random.choice(quotes), False

    guild_id = get_channel_guild_id(channel_id, bot_token)
    if not guild_id:
        logger.info("Mention path failed: could not resolve guild ID, falling back.")
        return random.choice(quotes), False

    members = fetch_guild_members(guild_id, bot_token)
    user_id = pick_random_human_user_id(members)
    if not user_id:
        logger.info("Mention path failed: no human users found, falling back.")
        return random.choice(quotes), False

    template = random.choice(mention_quotes)
    mention = f"<@{user_id}>"
    if PLACEHOLDER in template:
        return template.replace(PLACEHOLDER, mention), True

    logger.warning("Mention quote missing placeholder %s; appending mention.", PLACEHOLDER)
    return f"{template} {mention}", True


def post_message(channel_id: str, bot_token: str, content: str, dry_run: bool) -> bool:
    if dry_run:
        logger.info("[DRY RUN] Would post: %s", content)
        return True

    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    response = requests.post(url, headers=bot_headers(bot_token), json={"content": content}, timeout=15)
    if response.status_code in (200, 201):
        logger.info("Posted daily quote to channel %s", channel_id)
        return True
    logger.error("Failed to post quote (%d): %s", response.status_code, response.text[:300])
    return False


def main() -> int:
    bot_token = (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    channel_id = (os.environ.get("CHANNEL_ID_QUOTES") or "").strip()
    dry_run = (os.environ.get("DRY_RUN") or "false").lower() == "true"

    if not bot_token:
        logger.error("Missing DISCORD_BOT_TOKEN env var.")
        return 1
    if not channel_id:
        logger.error("Missing CHANNEL_ID_QUOTES env var.")
        return 1

    try:
        quotes, mention_quotes = load_quotes_from_env()
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    message, used_mention = build_message(quotes, mention_quotes, channel_id, bot_token)
    logger.info("Selected %s quote.", "mention" if used_mention else "regular")

    success = post_message(channel_id, bot_token, message, dry_run)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
