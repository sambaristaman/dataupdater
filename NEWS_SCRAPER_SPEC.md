# Game News Scraper — News Spec (HoYoLAB + Gryphline)

> **Purpose:** Define the news-only scraper spec for Discord delivery via `WEBHOOK_URL_NEWS`, scoped to specific games and scheduled daily per game.
>
> **Source basis:** `HOYOLAB_SCRAPER_SPEC.md` (2026-02-05)

---

## 1. Scope & Goals

- **Platforms:** HoYoLAB (HoYoverse) + Gryphline (Hypergryph)
- **Games (enabled):** `genshin`, `starrail`, `honkai3rd`, `zzz`, `endfield`
- **Language:** `en-us`
- **Output:** Discord webhook only (`WEBHOOK_URL_NEWS`), no bot-token fallback
- **Scheduling:** **Separate daily GitHub Actions workflow per game** (5 workflows total)
- **State:** Dedicated state file for news (e.g. `news_state.json`)
- **Mentions:** none

Non-goals:
- No event/banner scraping
- No codes scraping
- No role pings
- No Discord bot-token posting

---

## 2. Configuration Model

Use TOML-style config (same as `HOYOLAB_SCRAPER_SPEC.md`). Only the five games below are enabled.

```toml
# Global defaults
language = "en-us"
category_size = 5

# HoYoLAB games
[hoyolab.genshin]
categories = ["Notices", "Events", "Info"]

[hoyolab.starrail]
categories = ["Notices", "Events", "Info"]

[hoyolab.honkai3rd]
categories = ["Notices", "Events", "Info"]

[hoyolab.zzz]
categories = ["Notices", "Events", "Info"]

# Gryphline games
[gryphline.endfield]
categories = ["notices", "news"]
```

**Rules:**
- Sections present with no `enabled` key are treated as enabled.
- Only the sections above are enabled; all others are not configured.

---

## 3. Platform: HoYoLAB

### 3.1 API Reference

**Base URL**
```
https://bbs-api-os.hoyolab.com/community/post/wapi/
```

**Required headers**
- `Origin: https://www.hoyolab.com`
- `X-Rpc-Language: en-us`

**Endpoints**
- `getNewsList` (discovery)
- `getPostFull` (detail fetch)

### 3.2 Games & Categories

**Game IDs**
- Genshin Impact: `2`
- Honkai: Star Rail: `6`
- Honkai Impact 3rd: `1`
- Zenless Zone Zero: `8`

**Category types**
- Notices: `1`
- Events: `2`
- Info: `3`

### 3.3 Scraping Strategy

**Phase 1: Discovery**
```
GET /getNewsList?gids={game_id}&type={category}&page_size={category_size}
```
Extract:
- `post.post_id`
- `post.created_at`
- `last_modify_time`

Effective timestamp = `max(created_at, last_modify_time)`.

**Phase 2: Diff**
Fetch if:
- post ID is unseen, or
- effective timestamp is newer than stored

**Phase 3: Detail Fetch**
```
GET /getPostFull?gids={game_id}&post_id={post_id}
```

### 3.4 Content Transform Rules (must apply in order)
1. **Structured Content Fallback** when `content` is a language code like `en-us`.
2. **Video Posts**: if `view_type == 5` and `video` exists, synthesize a `<video>` block.
3. **Empty Leading Paragraph Removal**: remove leading `<p></p>` / `<p>&nbsp;</p>` / `<p><br></p>`.
4. **Private Link Fix**: replace `hoyolab-upload-private` with `upload-os-bbs`.

### 3.5 Article URL
```
https://www.hoyolab.com/article/{id}
```

### 3.6 Field Mapping → FeedItem
See Section 5 (Unified Data Model).

---

## 4. Platform: Gryphline

### 4.1 Overview
Gryphline is served via Next.js RSC payloads embedded in HTML. Scrape `self.__next_f.push()` arrays.

### 4.2 Game & Categories
- Game: `endfield`
- Categories: `notices`, `news`

### 4.3 Listing URL
```
https://endfield.gryphline.com/{lang}/news
```

### 4.4 Detail URL
```
https://endfield.gryphline.com/{lang}/news/{cid}
```

### 4.5 Content Notes
- `data` field contains clean HTML.
- No HoYoLAB-style transforms needed.
- Author is usually empty; fallback to game name.

---

## 5. Unified Data Model

All items are normalized to `FeedItem`:

| Field       | Type                | Description |
|------------|---------------------|-------------|
| `id`        | string              | Platform-specific post ID |
| `platform`  | "hoyolab" or "gryphline" | Platform slug |
| `url`       | string (URL)        | Article URL |
| `title`     | string              | Post title |
| `author`    | string              | Author display name |
| `content`   | string (HTML)       | Full HTML body |
| `category`  | string enum         | Canonical category |
| `published` | datetime            | Publish time |
| `updated`   | datetime or null    | Update time if available |
| `image`     | URL or null         | Cover/thumbnail |
| `summary`   | string or null      | Short description |
| `game`      | string enum         | Game slug |

**Canonical categories**
- HoYoLAB: `1 -> notices`, `2 -> events`, `3 -> info`
- Gryphline: `notices -> notices`, `news -> news`

**Composite key**
```
{platform}:{game}:{id}
```

---

## 6. State Management & Deduplication

Use a **dedicated state file** for news (e.g. `news_state.json`).

```json
{
  "hoyolab:genshin:12345678": {
    "last_modified": 1738100000,
    "last_sent_hash": "abc123"
  },
  "gryphline:endfield:5215": {
    "last_modified": 1770264000,
    "last_sent_hash": "def456"
  }
}
```

**First run behavior**
- Populate state with discovered items
- **Do not** send any Discord messages

**Webhook failure**
- Do not update state for that item
- It will be retried next run

---

## 7. HTML → Plain Text (for Discord)

Follow the standard pipeline from `HOYOLAB_SCRAPER_SPEC.md`:
- Decode HTML entities
- Convert `<br>` to `\n`
- Convert paragraphs to blank-line separation
- Convert lists to `•` bullets
- Convert links to `text (url)`
- Convert images to `[img: ...]`
- Strip remaining tags
- Normalize whitespace

---

## 8. Discord Embed Mapping

Send **embeds** only (no message content).

```json
{
  "title": "{item.title}",
  "url": "{item.url}",
  "description": "{plain_text_content (truncated)}",
  "color": "{COLOR_BY_GAME[item.game]}",
  "thumbnail": { "url": "{item.image}" },
  "author": { "name": "{item.author}" },
  "footer": { "text": "{category} · {game}" },
  "timestamp": "{item.published (ISO 8601)}"
}
```

**Truncation**
- Title: 256 chars (truncate to 253 + `...`)
- Description: 4096 chars with `Read more` suffix if truncated
- Total embed size: 6000 chars

---

## 9. Scheduling (GitHub Actions)

**Split into 5 daily workflows** (one per game) to avoid limits and reduce peak load:
- `daily-news-genshin.yml`
- `daily-news-starrail.yml`
- `daily-news-honkai3rd.yml`
- `daily-news-zzz.yml`
- `daily-news-endfield.yml`

Each workflow runs once per day and sets a per-game filter (e.g. `ONLY_GAME=genshin`).

Rationale:
- Lower per-run API volume
- Predictable load, avoids rate limits
- Each game isolated for retries and failures

---

## 10. Operational Notes

- Use exponential backoff on network errors
- Log retcode/message for HoYoLAB API errors
- Gryphline parsing is brittle; fail gracefully and retry next run
- If `news_state.json` is corrupted, treat as first run (baseline-only)

---

## Appendix: Color Coding (Suggested)

| Game         | Hex       | Decimal  |
|--------------|-----------|----------|
| genshin      | `0x00DCDC`| `56540`  |
| starrail     | `0xDDA000`| `14556160` |
| honkai3rd    | `0x00BFFF`| `49151`  |
| zzz          | `0x00FF7F`| `65407`  |
| endfield     | `0xFF6347`| `16737095` |

---

**Delivery Requirement:** All news posts must be sent via `WEBHOOK_URL_NEWS` only.
