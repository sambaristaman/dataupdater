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
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

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
        print(f"Warning: Could not save channel IDs cache: {e}")


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
        print(f"  Fetching channel ID from webhook for {channel_key}...")
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
            print(f"Warning: Could not load state file: {e}")
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
        print(f"Warning: Could not save state file: {e}")


def clear_state():
    """Clear the state file after successful completion."""
    if STATE_FILE.exists():
        try:
            STATE_FILE.unlink()
            print("State file cleared.")
        except IOError as e:
            print(f"Warning: Could not clear state file: {e}")


def get_messages_to_keep() -> set[str]:
    """Load message IDs from message_ids.json that should NOT be deleted."""
    message_ids_path = Path(__file__).parent / "message_ids.json"

    if not message_ids_path.exists():
        print(f"Warning: {message_ids_path} not found, no messages will be kept")
        return set()

    with open(message_ids_path) as f:
        data = json.load(f)

    keep_ids = set()
    for key, value in data.items():
        if isinstance(value, list):
            keep_ids.update(value)
        elif isinstance(value, str):
            keep_ids.add(value)

    print(f"Loaded {len(keep_ids)} message IDs to keep from message_ids.json")
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

    while retries < max_retries:
        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages?limit=100"
        if before:
            url += f"&before={before}"

        response = requests.get(url, headers=headers)

        if response.status_code == 429:
            retry_after = handle_rate_limit(response)
            print(f"  Rate limited, waiting {retry_after:.1f}s...")
            time.sleep(retry_after)
            retries += 1
            continue

        if response.status_code != 200:
            print(f"  Error fetching messages: {response.status_code} - {response.text}")
            retries += 1
            time.sleep(2)
            continue

        retries = 0  # Reset on success
        messages = response.json()
        if not messages:
            break

        all_messages.extend(messages)
        before = messages[-1]["id"]
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
            print(f"    Rate limited on bulk delete, waiting {retry_after:.1f}s...")
            time.sleep(retry_after)
            continue

        if response.status_code == 204:
            return True, message_ids

        if response.status_code == 400:
            # Some messages might be too old, fall back to individual delete
            print(f"    Bulk delete failed (some messages too old?): {response.text}")
            return False, []

        print(f"    Bulk delete error: {response.status_code} - {response.text}")
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
            print(f"    Rate limited, waiting {retry_after:.1f}s...")
            time.sleep(retry_after)
            continue

        if response.status_code == 204:
            return True

        if response.status_code == 404:
            # Message already deleted
            return True

        print(f"    Error deleting message {message_id}: {response.status_code} - {response.text}")
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
    print(f"\nProcessing {channel_name} (channel {channel_id})...")

    # Get already deleted IDs for this channel from state
    already_deleted = set(state["deleted_ids"].get(channel_id, []))
    if already_deleted:
        print(f"  Resuming: {len(already_deleted)} messages already deleted in previous run")

    messages = fetch_channel_messages(channel_id, bot_token)
    print(f"  Found {len(messages)} total messages in channel")

    # Filter: not in keep list, not already deleted
    to_delete = [
        msg for msg in messages
        if msg["id"] not in keep_ids and msg["id"] not in already_deleted
    ]
    print(f"  {len(to_delete)} messages to delete, {len(messages) - len(to_delete)} to keep/already deleted")

    if dry_run:
        for msg in to_delete[:10]:  # Show first 10 in dry run
            content_preview = msg.get("content", "")[:50]
            if len(msg.get("content", "")) > 50:
                content_preview += "..."
            print(f"    [DRY RUN] Would delete: {msg['id']} - {content_preview!r}")
        if len(to_delete) > 10:
            print(f"    [DRY RUN] ... and {len(to_delete) - 10} more")
        return len(to_delete)

    deleted_count = 0

    # Separate messages into bulk-deletable (< 14 days) and individual delete (>= 14 days)
    bulk_deletable = [msg["id"] for msg in to_delete if is_message_bulk_deletable(msg["id"])]
    individual_delete = [msg["id"] for msg in to_delete if not is_message_bulk_deletable(msg["id"])]

    print(f"  {len(bulk_deletable)} messages eligible for bulk delete, {len(individual_delete)} require individual delete")

    # Bulk delete in chunks of 100
    if bulk_deletable:
        print(f"  Starting bulk delete...")
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
                print(f"    Bulk deleted {len(deleted_ids)} messages (total: {deleted_count})")
            else:
                # Fall back to individual delete for this chunk
                individual_delete.extend(chunk)

            time.sleep(BULK_DELETE_DELAY)

    # Individual delete for old messages or failed bulk deletes
    if individual_delete:
        print(f"  Starting individual delete for {len(individual_delete)} messages...")
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
                    print(f"    Deleted {deleted_count} messages so far...")

            time.sleep(SINGLE_DELETE_DELAY)

    # Final state save for this channel
    save_state(state)
    print(f"  Completed: {deleted_count} messages deleted")

    return deleted_count


def post_ledger_summary(results: dict[str, int], webhook_url: str, dry_run: bool) -> bool:
    """
    Post summary to ledger channel using configured message templates.
    Returns True if successful (or no webhook configured).
    """
    if not webhook_url:
        print("\nNo WEBHOOK_URL_LEDGER configured, skipping summary post")
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
            print(f"\nPosted summary to ledger channel")
            return True
        else:
            print(f"\nFailed to post summary: {response.status_code} - {response.text}")
            return False
    except requests.RequestException as e:
        print(f"\nFailed to post summary: {e}")
        return False


def main():
    bot_token = os.environ.get("DISCORD_BOT_TOKEN")
    if not bot_token:
        print("Error: DISCORD_BOT_TOKEN environment variable is required")
        return 1

    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    only_channel = os.environ.get("ONLY_CHANNEL", "").strip()
    webhook_url = os.environ.get("WEBHOOK_URL_LEDGER", "")

    if dry_run:
        print("=== DRY RUN MODE - No messages will be deleted ===\n")

    # Load state for resumability
    state = load_state()

    # Convert deleted_ids lists back to sets
    state["deleted_ids"] = {k: set(v) for k, v in state["deleted_ids"].items()}

    # Check if this is a resumed run
    if state["last_run"]:
        print(f"Resuming from previous run at {state['last_run']}")

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
            print(f"\nSkipping {display_name}: no channel ID (checked cache, {channel_id_env}, {webhook_url_env})")
            continue

        deleted = purge_channel(channel_id, display_name, keep_ids, bot_token, dry_run, state)
        session_results[display_name] = deleted

        # Update cumulative results in state
        state["results"][display_name] = state["results"].get(display_name, 0) + deleted
        state["completed_channels"].append(channel_key)
        save_state(state)

    # Print summary
    print("\n" + "=" * 50)
    print("PURGE SUMMARY (this session)")
    print("=" * 50)

    total = 0
    for name, count in session_results.items():
        if count > 0:
            print(f"  {name}: {count} messages {'would be ' if dry_run else ''}deleted")
            total += count

    if total == 0:
        print("  All channels clean - no orphaned messages found")
    else:
        print(f"\n  Total: {total} messages {'would be ' if dry_run else ''}deleted")

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
        print("All channels processed successfully.")
    elif dry_run:
        print("Dry run complete - state not modified.")
    else:
        print("Some channels may not have been fully processed. State preserved for next run.")

    return 0


if __name__ == "__main__":
    exit(main())
