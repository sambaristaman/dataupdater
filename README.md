# Game Data Updater

Discord bot that scrapes game events, banners, redemption codes, and news from various sources and posts updates to Discord channels via webhooks.

## Features

- **Event & Gacha Scraper** (`scraper.py`): Scrapes Game8 for events and banners for multiple gacha games
- **Code Scrapers**: Individual scrapers for redemption codes (Arknights Endfield, MTG Arena, Disney Speedstorm)
- **News Scraper**: HoYoLAB + Gryphline + Shadowverse news to a single channel
- **Channel Purge Bot** (`purge_channels.py`): Cleans up orphaned messages from Discord channels
- **Daily Quotes Bot** (`daily_quotes.py`): Posts a daily quote to a configured channel

## Supported Games

| Game | Events | Gacha/Banners | Codes |
|------|--------|---------------|-------|
| Genshin Impact | Yes | Yes | - |
| Honkai: Star Rail | Yes | Yes | - |
| Wuthering Waves | Yes | Yes | - |
| Umamusume | Yes | - | - |
| Arknights: Endfield | Yes | Yes | Yes |
| MTG Arena | - | - | Yes |
| Disney Speedstorm | - | - | Yes |
| Shadowverse | - | - | News |

News feeds: Genshin Impact, Honkai: Star Rail, Honkai Impact 3rd, Zenless Zone Zero, Arknights: Endfield, Shadowverse.

---

## GitHub Configuration

### Secrets

#### Discord Bot Token (Purge Bot + Daily Quotes)

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_BOT_TOKEN` | Yes* | Bot token with `MANAGE_MESSAGES` and `READ_MESSAGE_HISTORY` permissions. *Required for purge bot and daily quotes. |

#### Webhooks - Game Channels

Used by the main scraper (`scraper.py`) to post events/gacha updates, and by the purge bot to derive channel IDs.

| Variable | Used By | Description |
|----------|---------|-------------|
| `WEBHOOK_URL_HSR` | scraper, purge | Honkai: Star Rail channel webhook |
| `WEBHOOK_URL_WUWA` | scraper, purge | Wuthering Waves channel webhook |
| `WEBHOOK_URL_GI` | scraper, purge | Genshin Impact channel webhook |
| `WEBHOOK_URL_UMA` | scraper, purge | Umamusume channel webhook |
| `WEBHOOK_URL_ENDFIELD` | scraper, purge | Arknights: Endfield channel webhook |

#### Webhooks - Special Channels

| Variable | Used By | Description |
|----------|---------|-------------|
| `WEBHOOK_URL_SUMMARY` | scraper, codes | Summary/log channel for scraper reports and health pings |
| `WEBHOOK_URL_LEDGER` | purge | Ledger channel for purge bot summaries |
| `WEBHOOK_URL_CODEX` | codes | Channel for redemption code alerts (Endfield, MTGA, Speedstorm) |
| `WEBHOOK_URL_NEWS` | news | Channel for HoYoLAB + Gryphline + Shadowverse news posts |

#### Role IDs (Optional)

Used for @mentioning Discord roles when new content is detected.

| Variable | Description |
|----------|-------------|
| `ROLE_ID_HSR` | Honkai: Star Rail role ID |
| `ROLE_ID_WUWA` | Wuthering Waves role ID |
| `ROLE_ID_GI` | Genshin Impact role ID |
| `ROLE_ID_UMA` | Umamusume role ID |
| `ROLE_ID_ARKNIGHTS_ENDFIELD` | Arknights: Endfield role ID |
| `ROLE_ID_MTGA` | MTG Arena role ID |
| `ROLE_ID_SPEEDSTORM` | Disney Speedstorm role ID |

#### Channel IDs (Optional - Purge Bot)

The purge bot can automatically derive channel IDs from webhook URLs. These are only needed as explicit overrides.

| Variable | Description |
|----------|-------------|
| `CHANNEL_ID_HSR` | Override: Honkai: Star Rail channel ID |
| `CHANNEL_ID_WUWA` | Override: Wuthering Waves channel ID |
| `CHANNEL_ID_GI` | Override: Genshin Impact channel ID |
| `CHANNEL_ID_UMA` | Override: Umamusume channel ID |
| `CHANNEL_ID_ENDFIELD` | Override: Arknights: Endfield channel ID |

#### Channel IDs (Daily Quotes)

| Variable | Description |
|----------|-------------|
| `CHANNEL_ID_QUOTES` | Target channel ID for daily quotes |

#### Runtime Flags (Workflow Inputs)

These are set via workflow dispatch inputs, not as repository secrets.

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `false` | Preview mode - no messages sent, no files written |
| `ONLY_KEY` | _(empty)_ | Run only for specific game key (e.g., `genshin-impact`) |
| `FORCE_NEW` | `false` | Force posting new messages instead of editing existing |
| `ONLY_CHANNEL` | _(empty)_ | Purge bot: specific channel to purge |

#### Runtime Flags (News Scraper)

| Variable | Default | Description |
|----------|---------|-------------|
| `ONLY_GAME` | _(empty)_ | Run news scraper for a single game (`genshin`, `starrail`, `honkai3rd`, `zzz`, `endfield`, `shadowverse`) |
| `DRY_RUN` | `false` | Preview mode - no Discord posts, no state writes |
| `NEWS_STATE_PATH` | `news_state.json` | Override the news state file path (useful for tests) |
| `RUN_LAST_HOURS` | _(empty)_ | Only send items updated within the last N hours (e.g., `24`). Items within the window are sent even if already tracked. |

### Variables (Repository Settings > Variables)

These are non-sensitive configuration values. Set them in GitHub repository settings under **Settings > Secrets and variables > Actions > Variables**.

#### Ledger Message Templates (Purge Bot)

| Variable | Default | Description |
|----------|---------|-------------|
| `LEDGER_MSG_DELETED` | `🧹 **Channel Purge Complete**\n{details}\n\n_Total: {total} messages deleted_` | Template for deletion summary. Placeholders: `{details}`, `{total}`, `{channel_count}` |
| `LEDGER_MSG_CLEAN` | `✨ **All channels clean** — no orphaned messages found.` | Fallback message when no deletions needed (used if `clean_messages.yaml` is missing) |
| `LEDGER_MSG_CHANNEL_LINE` | `• {channel}: {count} messages` | Template for each channel line. Placeholders: `{channel}`, `{count}` |
| `DISABLE_UMA_EVENTS` | `false` | Disable Umamusume events scraping entirely |

#### Clean Messages Configuration (Purge Bot)

When no messages need to be purged, the bot randomly selects a message from the `CLEAN_MESSAGES_YAML` variable:

| Variable | Description |
|----------|-------------|
| `CLEAN_MESSAGES_YAML` | YAML content with multiple clean messages (see format below) |

Example value for `CLEAN_MESSAGES_YAML`:
```yaml
clean_messages:
  - "✨ **All channels clean** — no orphaned messages found."
  - "🧹 **Nothing to purge** — everything looks spotless!"
  - "🎉 **Channels are pristine** — no cleanup needed today."
```

If `CLEAN_MESSAGES_YAML` is not set or invalid, the bot falls back to the `LEDGER_MSG_CLEAN` environment variable.

#### Daily Quotes Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `DAILY_QUOTES_YAML` | Yes | YAML list of regular quotes |
| `MENTION_QUOTES_YAML` | No | YAML list of mention quotes (contain `{user}` placeholder) |

Example value for `DAILY_QUOTES_YAML`:
```yaml
- "Consistency beats intensity."
- "Make it simple, then make it great."
```

Example value for `MENTION_QUOTES_YAML`:
```yaml
- "Keep going, {user}."
- "{user}, your future self will thank you."
```
Note: mention quotes require the Discord Server Members Intent to be enabled for the bot.

---

## Channel ID Resolution (Purge Bot)

The purge bot resolves channel IDs in this priority order:

1. **Cached value** from `channel_ids_cache.json` (fastest, no API call)
2. **`CHANNEL_ID_*` env var** (explicit override, backwards compatible)
3. **Webhook URL lookup** (fetches from Discord API and caches result)

This means you only need to configure `WEBHOOK_URL_*` secrets - channel IDs are derived automatically.

---

## GitHub Actions Workflows

| Workflow | Schedule (BRT) | Description |
|----------|----------------|-------------|
| `update.yml` | 09:00 | Main scraper (events + gacha) |
| `daily-arknights-endfield.yml` | 08:30 | Arknights Endfield codes |
| `daily-mtga.yml` | 08:00 | MTG Arena codes |
| `daily-news-genshin.yml` | 12:00 | Genshin news |
| `daily-news-starrail.yml` | 12:30 | Star Rail news |
| `daily-news-honkai3rd.yml` | 13:00 | Honkai 3rd news |
| `daily-news-zzz.yml` | 13:30 | ZZZ news |
| `daily-news-endfield.yml` | 14:00 | Endfield news |
| `daily-news-shadowverse.yml` | 14:30 | Shadowverse news |
| `daily-speedstorm.yml` | 10:00 | Disney Speedstorm codes |
| `daily-quotes.yml` | 12:00 | Daily quotes |
| `purge-channels.yml` | 11:00 | Channel purge bot |

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# (Optional) Install dev test dependencies
pip install -r requirements-dev.txt

# Run with dry run mode
DRY_RUN=true python scraper.py

# Run for specific game only
ONLY_KEY=genshin-impact DRY_RUN=true python scraper.py

# Run news scraper for a single game
ONLY_GAME=genshin WEBHOOK_URL_NEWS=... python news_scraper.py
```

## Tests

News scraper tests run against live sources and mock Discord. They write state to a temp path via `NEWS_STATE_PATH`.

```bash
pytest -q
```

---

## Files

| File | Description |
|------|-------------|
| `message_ids.json` | Tracked Discord message IDs for editing |
| `state.json` | Scraper state for change detection |
| `news_scraper.py` | Unified news scraper (HoYoLAB + Gryphline + Shadowverse) |
| `news_state.json` | News scraper state for change detection |
| `requirements-dev.txt` | Dev-only dependencies (pytest) |
| `tests/test_news_scraper_live.py` | Live integration tests for news scraper |
| `*_state.json` | Per-scraper state files |
| `channel_ids_cache.json` | Cached channel IDs (purge bot) |
| `purge_state.json` | Purge bot resumable state |
