#!/usr/bin/env python3
"""
Discord Channel Purge Bot

Purges orphaned messages from Discord channels that aren't tracked in message_ids.json.
Uses Discord Bot API (not webhooks) to fetch and delete messages.

Features:
- Resumable: Saves state after each deletion, can continue after being killed
- Rate limit aware: Proactively respects Discord rate limits
- Bulk delete: Uses bulk delete API for messages < 14 days old (much faster)
- Fault tolerant: Reporting failures don't affect deletion progress

Required environment variables:
- DISCORD_BOT_TOKEN: Bot token with MANAGE_MESSAGES and READ_MESSAGE_HISTORY permissions
- WEBHOOK_URL_LEDGER: Webhook URL for posting purge summary

Channel ID resolution (in priority order):
1. Cached channel ID from channel_ids_cache.json (fastest, no API call)
2. CHANNEL_ID_* env var (explicit override, backwards compatible)
3. WEBHOOK_URL_* lookup (fetches from Discord API and caches result)

Optional environment variables:
- DRY_RUN: Set to 'true' to preview deletions without actually deleting
- ONLY_CHANNEL: Specific channel key to purge (e.g., 'honkai-star-rail')
- LEDGER_MSG_DELETED: Template for deletion summary message
- LEDGER_MSG_CLEAN: Message when no deletions needed
- LEDGER_MSG_CHANNEL_LINE: Template for each channel line in the summary
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("purge_channels")

# Channel configuration: key -> (display_name, channel_id_env, webhook_url_env)
CHANNELS = {
    "honkai-star-rail": ("Honkai Star Rail", "CHANNEL_ID_HSR", "WEBHOOK_URL_HSR"),
    "wuthering-waves": ("Wuthering Waves", "CHANNEL_ID_WUWA", "WEBHOOK_URL_WUWA"),
    "genshin-impact": ("Genshin Impact", "CHANNEL_ID_GI", "WEBHOOK_URL_GI"),
    "umamusume": ("Umamusume", "CHANNEL_ID_UMA", "WEBHOOK_URL_UMA"),
    "arknights-endfield": ("Arknights Endfield", "CHANNEL_ID_ENDFIELD", "WEBHOOK_URL_ENDFIELD"),
}

DISCORD_API_BASE = "https://discord.com/api/v10"

# Discord rate limits (being conservative)
# Single message delete: 5/sec per channel, we'll do 3/sec to be safe
# Bulk delete: 1/sec, up to 100 messages per request (messages must be < 14 days old)
SINGLE_DELETE_DELAY = 0.35  # ~3 requests per second
BULK_DELETE_DELAY = 1.1  # Just over 1 second between bulk deletes
FETCH_DELAY = 0.5  # Delay between fetch requests

# Discord snowflake epoch (2015-01-01)
DISCORD_EPOCH = 1420070400000

# Messages older than 14 days cannot be bulk deleted
BULK_DELETE_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000

STATE_FILE = Path(__file__).parent / "purge_state.json"
CHANNEL_IDS_CACHE_FILE = Path(__file__).parent / "channel_ids_cache.json"


class GatewayPresence:
    """
    Minimal Discord Gateway connection to show the bot as online.
    Runs in a background thread, sends heartbeats, and disconnects on close.
    """

    GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self._ws = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._heartbeat_interval: float = 41.25  # default, overridden by Hello

    def start(self):
        """Connect to gateway in a background thread."""
        try:
            import websocket
        except ImportError:
            logger.warning("websocket-client not installed, bot will not appear online. pip install websocket-client")
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Disconnect from gateway."""
        self._stop_event.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Gateway connection closed, bot is offline.")

    def _run(self):
        try:
            import websocket
        except ImportError:
            return

        try:
            self._ws = websocket.create_connection(self.GATEWAY_URL, timeout=30)
            logger.info("Connected to Discord Gateway.")

            # 1. Receive Hello (opcode 10)
            hello = json.loads(self._ws.recv())
            if hello.get("op") != 10:
                logger.error("Expected Hello (op 10), got op %s", hello.get("op"))
                return
            self._heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000.0
            logger.debug("Gateway heartbeat interval: %.1fs", self._heartbeat_interval)

            # 2. Send Identify (opcode 2)
            identify = {
                "op": 2,
                "d": {
                    "token": self.bot_token,
                    "intents": 0,  # No intents needed, just presence
                    "properties": {
                        "os": "linux",
                        "browser": "purge_channels",
                        "device": "purge_channels",
                    },
                    "presence": {
                        "status": "online",
                        "activities": [{
                            "name": "Purging channels...",
                            "type": 0,  # Playing
                        }],
                    },
                },
            }
            self._ws.send(json.dumps(identify))
            logger.info("Sent Identify, bot should appear online.")

            # 3. Read Ready and heartbeat loop
            self._ws.settimeout(self._heartbeat_interval)
            last_sequence = None

            while not self._stop_event.is_set():
                # Send heartbeat
                heartbeat = {"op": 1, "d": last_sequence}
                try:
                    self._ws.send(json.dumps(heartbeat))
                except Exception:
                    break

                # Wait for messages until next heartbeat
                deadline = time.monotonic() + self._heartbeat_interval
                while time.monotonic() < deadline and not self._stop_event.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._ws.settimeout(max(remaining, 0.1))
                    try:
                        raw = self._ws.recv()
                        msg = json.loads(raw)
                        if msg.get("s") is not None:
                            last_sequence = msg["s"]
                    except websocket.WebSocketTimeoutException:
                        continue
                    except Exception:
                        self._stop_event.set()
                        break

        except Exception as e:
            logger.warning("Gateway connection error: %s", e)
        finally:
            if self._ws:
                try:
                    self._ws.close()
                except Exception:
                    pass


def load_channel_ids_cache() -> dict[str, str]:
    """Load cached channel IDs from file."""
    if CHANNEL_IDS_CACHE_FILE.exists():
        try:
            with open(CHANNEL_IDS_CACHE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_channel_ids_cache(cache: dict[str, str]):
    """Save channel IDs cache to file."""
    try:
        with open(CHANNEL_IDS_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except IOError as e:
        logger.warning("Could not save channel IDs cache: %s", e)


def get_channel_id_from_webhook(webhook_url: str) -> str | None:
    """Fetch channel ID from Discord webhook URL."""
    try:
        response = requests.get(webhook_url, timeout=10)
        if response.status_code == 200:
            return response.json().get("channel_id")
    except requests.RequestException:
        pass
    return None


def resolve_channel_id(channel_key: str, channel_id_env: str, webhook_url_env: str, cache: dict[str, str]) -> str | None:
    """
    Resolve channel ID with priority:
    1. Cached value (fastest)
    2. CHANNEL_ID_* env var (explicit override)
    3. Webhook URL lookup (fallback, caches result)
    """
    # 1. Check cache first
    if channel_key in cache:
        return cache[channel_key]

    # 2. Check explicit CHANNEL_ID env var
    channel_id = os.environ.get(channel_id_env)
    if channel_id:
        cache[channel_key] = channel_id
        save_channel_ids_cache(cache)
        return channel_id

    # 3. Fall back to webhook URL lookup
    webhook_url = os.environ.get(webhook_url_env)
    if webhook_url:
        logger.info("Fetching channel ID from webhook for %s...", channel_key)
        channel_id = get_channel_id_from_webhook(webhook_url)
        if channel_id:
            cache[channel_key] = channel_id
            save_channel_ids_cache(cache)
            return channel_id

    return None


def snowflake_to_timestamp(snowflake: str) -> int:
    """Convert Discord snowflake ID to Unix timestamp in milliseconds."""
    return (int(snowflake) >> 22) + DISCORD_EPOCH


def is_message_bulk_deletable(message_id: str) -> bool:
    """Check if message is young enough for bulk delete (< 14 days)."""
    message_timestamp = snowflake_to_timestamp(message_id)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return (now_ms - message_timestamp) < BULK_DELETE_MAX_AGE_MS


def load_state() -> dict:
    """Load purge state from file."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Could not load state file: %s", e)
    return {
        "deleted_ids": {},  # channel_id -> set of deleted message IDs
        "results": {},  # channel_name -> count of deleted messages
        "last_run": None,
        "completed_channels": [],  # Channels fully processed this session
    }


def save_state(state: dict):
    """Save purge state to file."""
    # Convert sets to lists for JSON serialization
    serializable = {
        "deleted_ids": {k: list(v) for k, v in state["deleted_ids"].items()},
        "results": state["results"],
        "last_run": state["last_run"],
        "completed_channels": state["completed_channels"],
    }
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(serializable, f, indent=2)
    except IOError as e:
        logger.warning("Could not save state file: %s", e)


def clear_state():
    """Clear the state file after successful completion."""
    if STATE_FILE.exists():
        try:
            STATE_FILE.unlink()
            logger.info("State file cleared.")
        except IOError as e:
            logger.warning("Could not clear state file: %s", e)


def get_messages_to_keep() -> set[str]:
    """Load message IDs from message_ids.json that should NOT be deleted."""
    message_ids_path = Path(__file__).parent / "message_ids.json"

    if not message_ids_path.exists():
        logger.warning("%s not found, no messages will be kept", message_ids_path)
        return set()

    with open(message_ids_path) as f:
        data = json.load(f)

    keep_ids = set()
    for key, value in data.items():
        if isinstance(value, list):
            keep_ids.update(value)
        elif isinstance(value, str):
            keep_ids.add(value)

    logger.info("Loaded %d message IDs to keep from message_ids.json", len(keep_ids))
    return keep_ids


def handle_rate_limit(response: requests.Response) -> float:
    """Handle rate limit response, return seconds to wait."""
    if response.status_code == 429:
        try:
            retry_after = response.json().get("retry_after", 5)
        except (json.JSONDecodeError, KeyError):
            retry_after = 5
        return retry_after
    return 0


def fetch_channel_messages(channel_id: str, bot_token: str) -> list[dict]:
    """Fetch all messages from a channel using Discord Bot API."""
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    all_messages = []
    before = None
    retries = 0
    max_retries = 5
    page = 0
    max_pages = 100  # Safety limit: 100 pages * 100 messages = 10,000 messages max

    while retries < max_retries:
        if page >= max_pages:
            logger.warning("Reached max page limit (%d pages, %d messages). Stopping fetch.", max_pages, len(all_messages))
            break

        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages?limit=100"
        if before:
            url += f"&before={before}"

        logger.debug("Fetching page %d (before=%s, %d messages so far)...", page + 1, before, len(all_messages))
        response = requests.get(url, headers=headers)

        if response.status_code == 429:
            retry_after = handle_rate_limit(response)
            logger.warning("Rate limited while fetching page %d, waiting %.1fs...", page + 1, retry_after)
            time.sleep(retry_after)
            retries += 1
            continue

        if response.status_code != 200:
            logger.error("Error fetching page %d: %d - %s", page + 1, response.status_code, response.text)
            retries += 1
            time.sleep(2)
            continue

        retries = 0  # Reset on success
        messages = response.json()
        if not messages:
            logger.debug("Page %d returned empty, done fetching.", page + 1)
            break

        all_messages.extend(messages)
        new_before = messages[-1]["id"]
        if new_before == before:
            logger.warning("Pagination stuck at message ID %s, stopping fetch.", before)
            break
        before = new_before
        page += 1
        logger.debug("Page %d: got %d messages (total: %d)", page, len(messages), len(all_messages))
        time.sleep(FETCH_DELAY)

    return all_messages


def bulk_delete_messages(channel_id: str, message_ids: list[str], bot_token: str) -> tuple[bool, list[str]]:
    """
    Bulk delete messages (2-100 messages, must be < 14 days old).
    Returns (success, list of deleted IDs).
    """
    if len(message_ids) < 2:
        return False, []

    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages/bulk-delete"
    payload = {"messages": message_ids}

    max_retries = 5
    for attempt in range(max_retries):
        response = requests.post(url, headers=headers, json=payload)

        if response.status_code == 429:
            retry_after = handle_rate_limit(response)
            logger.warning("Rate limited on bulk delete, waiting %.1fs...", retry_after)
            time.sleep(retry_after)
            continue

        if response.status_code == 204:
            return True, message_ids

        if response.status_code == 400:
            # Some messages might be too old, fall back to individual delete
            logger.warning("Bulk delete failed (some messages too old?): %s", response.text)
            return False, []

        logger.error("Bulk delete error: %d - %s", response.status_code, response.text)
        time.sleep(2)

    return False, []


def delete_message(channel_id: str, message_id: str, bot_token: str) -> bool:
    """Delete a single message via Discord Bot API. Returns True if successful."""
    headers = {
        "Authorization": f"Bot {bot_token}",
    }

    url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}"

    max_retries = 5
    for attempt in range(max_retries):
        response = requests.delete(url, headers=headers)

        if response.status_code == 429:
            retry_after = handle_rate_limit(response)
            logger.warning("Rate limited on single delete, waiting %.1fs...", retry_after)
            time.sleep(retry_after)
            continue

        if response.status_code == 204:
            return True

        if response.status_code == 404:
            logger.debug("Message %s already deleted (404)", message_id)
            return True

        logger.error("Error deleting message %s: %d - %s", message_id, response.status_code, response.text)
        if attempt < max_retries - 1:
            time.sleep(2)

    return False


def purge_channel(
    channel_id: str,
    channel_name: str,
    keep_ids: set[str],
    bot_token: str,
    dry_run: bool,
    state: dict,
) -> int:
    """
    Delete all messages in channel except those in keep_ids.
    Saves state after each deletion for resumability.
    Returns count of messages deleted this run.
    """
    logger.info("Processing %s (channel %s)...", channel_name, channel_id)

    # Get already deleted IDs for this channel from state
    already_deleted = set(state["deleted_ids"].get(channel_id, []))
    if already_deleted:
        logger.info("Resuming: %d messages already deleted in previous run", len(already_deleted))

    logger.debug("Fetching messages for channel %s...", channel_id)
    messages = fetch_channel_messages(channel_id, bot_token)
    logger.info("Found %d total messages in channel %s", len(messages), channel_name)

    # Filter: not in keep list, not already deleted
    to_delete = [
        msg for msg in messages
        if msg["id"] not in keep_ids and msg["id"] not in already_deleted
    ]
    kept_count = len(messages) - len(to_delete)
    logger.info("%d messages to delete, %d to keep/already deleted", len(to_delete), kept_count)

    if dry_run:
        for msg in to_delete[:10]:  # Show first 10 in dry run
            content_preview = msg.get("content", "")[:50]
            if len(msg.get("content", "")) > 50:
                content_preview += "..."
            logger.info("[DRY RUN] Would delete: %s - %r", msg["id"], content_preview)
        if len(to_delete) > 10:
            logger.info("[DRY RUN] ... and %d more", len(to_delete) - 10)
        return len(to_delete)

    deleted_count = 0

    # Separate messages into bulk-deletable (< 14 days) and individual delete (>= 14 days)
    bulk_deletable = [msg["id"] for msg in to_delete if is_message_bulk_deletable(msg["id"])]
    individual_delete = [msg["id"] for msg in to_delete if not is_message_bulk_deletable(msg["id"])]

    logger.info("%d messages eligible for bulk delete, %d require individual delete", len(bulk_deletable), len(individual_delete))

    # Bulk delete in chunks of 100
    if bulk_deletable:
        logger.info("Starting bulk delete of %d messages...", len(bulk_deletable))
        for i in range(0, len(bulk_deletable), 100):
            chunk = bulk_deletable[i:i + 100]
            if len(chunk) < 2:
                # Bulk delete requires at least 2 messages
                individual_delete.extend(chunk)
                continue

            success, deleted_ids = bulk_delete_messages(channel_id, chunk, bot_token)
            if success:
                deleted_count += len(deleted_ids)
                # Update state immediately
                if channel_id not in state["deleted_ids"]:
                    state["deleted_ids"][channel_id] = set()
                state["deleted_ids"][channel_id].update(deleted_ids)
                save_state(state)
                logger.info("Bulk deleted %d messages (total: %d)", len(deleted_ids), deleted_count)
            else:
                # Fall back to individual delete for this chunk
                individual_delete.extend(chunk)

            time.sleep(BULK_DELETE_DELAY)

    # Individual delete for old messages or failed bulk deletes
    if individual_delete:
        logger.info("Starting individual delete for %d messages...", len(individual_delete))
        for msg_id in individual_delete:
            if delete_message(channel_id, msg_id, bot_token):
                deleted_count += 1
                # Update state immediately after each deletion
                if channel_id not in state["deleted_ids"]:
                    state["deleted_ids"][channel_id] = set()
                state["deleted_ids"][channel_id].add(msg_id)

                # Save state every 10 deletions to balance I/O vs resumability
                if deleted_count % 10 == 0:
                    save_state(state)
                    logger.info("Deleted %d / %d messages so far...", deleted_count, len(individual_delete))

            time.sleep(SINGLE_DELETE_DELAY)

    # Final state save for this channel
    save_state(state)
    logger.info("Completed %s: %d messages deleted", channel_name, deleted_count)

    return deleted_count


def post_ledger_summary(results: dict[str, int], webhook_url: str, dry_run: bool) -> bool:
    """
    Post summary to ledger channel using configured message templates.
    Returns True if successful (or no webhook configured).
    """
    if not webhook_url:
        logger.info("No WEBHOOK_URL_LEDGER configured, skipping summary post")
        return True

    total = sum(results.values())

    if total == 0:
        message = os.environ.get("LEDGER_MSG_CLEAN", "✨ **All channels clean** — no orphaned messages found.")
    else:
        # Get channel line template
        channel_line_template = os.environ.get("LEDGER_MSG_CHANNEL_LINE", "• {channel}: {count} messages")

        # Format each channel line
        channel_lines = [
            channel_line_template.format(channel=name, count=count)
            for name, count in results.items() if count > 0
        ]
        details = "\n".join(channel_lines)
        channel_count = len(channel_lines)

        # Format main message
        template = os.environ.get(
            "LEDGER_MSG_DELETED",
            "🧹 **Channel Purge Complete**\n{details}\n\n_Total: {total} messages deleted_",
        )
        message = template.format(details=details, total=total, channel_count=channel_count)

    if dry_run:
        message = f"[DRY RUN]\n{message}"

    payload = {"content": message}

    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        if response.status_code in (200, 204):
            logger.info("Posted summary to ledger channel")
            return True
        else:
            logger.error("Failed to post summary: %d - %s", response.status_code, response.text)
            return False
    except requests.RequestException as e:
        logger.error("Failed to post summary: %s", e)
        return False


def main():
    bot_token = os.environ.get("DISCORD_BOT_TOKEN")
    if not bot_token:
        logger.error("DISCORD_BOT_TOKEN environment variable is required")
        return 1

    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    only_channel = os.environ.get("ONLY_CHANNEL", "").strip()
    webhook_url = os.environ.get("WEBHOOK_URL_LEDGER", "")

    if dry_run:
        logger.info("=== DRY RUN MODE - No messages will be deleted ===")

    # Connect to gateway so bot appears online during purge
    gateway = GatewayPresence(bot_token)
    gateway.start()

    # Load state for resumability
    state = load_state()

    # Convert deleted_ids lists back to sets
    state["deleted_ids"] = {k: set(v) for k, v in state["deleted_ids"].items()}

    # Check if this is a resumed run
    if state["last_run"]:
        logger.info("Resuming from previous run at %s", state["last_run"])

    state["last_run"] = datetime.now(timezone.utc).isoformat()

    keep_ids = get_messages_to_keep()

    # Load channel IDs cache
    channel_ids_cache = load_channel_ids_cache()

    # Track results for this session (merge with previous if resuming)
    session_results = {}

    for channel_key, (display_name, channel_id_env, webhook_url_env) in CHANNELS.items():
        # Skip if filtering to specific channel
        if only_channel and channel_key != only_channel:
            continue

        channel_id = resolve_channel_id(channel_key, channel_id_env, webhook_url_env, channel_ids_cache)
        if not channel_id:
            logger.warning("Skipping %s: no channel ID (checked cache, %s, %s)", display_name, channel_id_env, webhook_url_env)
            continue

        deleted = purge_channel(channel_id, display_name, keep_ids, bot_token, dry_run, state)
        session_results[display_name] = deleted

        # Update cumulative results in state
        state["results"][display_name] = state["results"].get(display_name, 0) + deleted
        state["completed_channels"].append(channel_key)
        save_state(state)

    # Print summary
    logger.info("=" * 50)
    logger.info("PURGE SUMMARY (this session)")
    logger.info("=" * 50)

    total = 0
    for name, count in session_results.items():
        if count > 0:
            action = "would be deleted" if dry_run else "deleted"
            logger.info("  %s: %d messages %s", name, count, action)
            total += count

    if total == 0:
        logger.info("All channels clean - no orphaned messages found")
    else:
        action = "would be deleted" if dry_run else "deleted"
        logger.info("Total: %d messages %s", total, action)

    # Post to ledger (use session results, not cumulative)
    # This is done after all deletions, so even if it fails, deletions are saved
    post_ledger_summary(session_results, webhook_url, dry_run)

    # Clear state only if all channels were processed successfully
    # A channel is considered "handled" if it was processed OR if it had no configuration
    all_channels_processed = all(
        channel_key in state["completed_channels"] or (
            channel_key not in channel_ids_cache
            and not os.environ.get(channel_id_env)
            and not os.environ.get(webhook_url_env)
        )
        for channel_key, (_, channel_id_env, webhook_url_env) in CHANNELS.items()
        if not only_channel or channel_key == only_channel
    )

    if all_channels_processed and not dry_run:
        clear_state()
        logger.info("All channels processed successfully.")
    elif dry_run:
        logger.info("Dry run complete - state not modified.")
    else:
        logger.warning("Some channels may not have been fully processed. State preserved for next run.")

    # Disconnect from gateway
    gateway.stop()

    return 0


if __name__ == "__main__":
    exit(main())
