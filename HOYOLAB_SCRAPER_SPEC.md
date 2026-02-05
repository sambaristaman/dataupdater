# Game News Scraper — Complete Specification

> **Purpose:** Everything needed to build a multi-platform gacha game news scraper from scratch, targeting Discord as the output channel.
>
> **Last verified:** 2026-02-05 (HoYoLAB API endpoints tested live; Gryphline RSC payloads confirmed working).

---

## Table of Contents

1. [Overview & Terminology](#1-overview--terminology)
2. [Configuration Model](#2-configuration-model)
3. [Platform: HoYoLAB](#3-platform-hoyolab)
   - 3.1 [API Reference](#31-api-reference)
   - 3.2 [Games, Categories & Languages](#32-games-categories--languages)
   - 3.3 [Scraping Strategy](#33-scraping-strategy)
   - 3.4 [Content Transformation Rules](#34-content-transformation-rules)
   - 3.5 [Article URL Pattern](#35-article-url-pattern)
   - 3.6 [Field Mapping → FeedItem](#36-field-mapping--feeditem)
4. [Platform: Gryphline](#4-platform-gryphline)
   - 4.1 [Overview](#41-overview)
   - 4.2 [Games & Categories](#42-games--categories)
   - 4.3 [Language Codes](#43-language-codes)
   - 4.4 [Scraping Strategy (RSC Payload)](#44-scraping-strategy-rsc-payload)
   - 4.5 [Content Notes](#45-content-notes)
   - 4.6 [Article URL Pattern](#46-article-url-pattern)
   - 4.7 [Field Mapping → FeedItem](#47-field-mapping--feeditem)
   - 4.8 [Pagination Limitations](#48-pagination-limitations)
5. [Unified Data Model](#5-unified-data-model)
6. [State Management & Deduplication](#6-state-management--deduplication)
7. [HTML-to-Plain-Text Conversion](#7-html-to-plain-text-conversion)
8. [Discord Embed Mapping](#8-discord-embed-mapping)
- [Appendix A: Quick-Start Pseudocode](#appendix-a-quick-start-pseudocode)
- [Appendix B: Adding a New Platform](#appendix-b-adding-a-new-platform)

---

## 1. Overview & Terminology

This specification describes a scraper that monitors **multiple game news platforms**, extracts structured article data, and delivers it to Discord via webhook embeds.

### Key Terms

| Term       | Definition                                                                                   |
|------------|----------------------------------------------------------------------------------------------|
| Platform   | A news publisher with a distinct scraping mechanism (e.g. HoYoLAB REST API, Gryphline RSC)   |
| Game       | A specific game title within a platform (e.g. Genshin Impact, Arknights: Endfield)           |
| Category   | A classification of posts within a game (e.g. "notices", "events", "info", "news")           |
| FeedItem   | The unified data model representing a single scraped article, regardless of source platform   |

### Supported Platforms

| Platform  | Publisher    | Mechanism   | Games                                               |
|-----------|-------------|-------------|-----------------------------------------------------|
| HoYoLAB   | HoYoverse   | REST API    | Honkai 3rd, Genshin, Themis, Star Rail, ZZZ, Nexus  |
| Gryphline | Hypergryph  | RSC parsing | Arknights: Endfield                                 |

---

## 2. Configuration Model

Configuration uses TOML with `[platform.game]` nesting. Each game can be independently enabled or disabled.

### Structure

```toml
# Global defaults
language = "en-us"
category_size = 5

# HoYoLAB games
[hoyolab.genshin]
categories = ["Notices", "Events"]

[hoyolab.starrail]
enabled = false

[hoyolab.honkai3rd]
categories = ["Notices", "Events", "Info"]

# Gryphline games
[gryphline.endfield]
categories = ["notices", "news"]
```

### Per-Game Settings

| Key             | Type       | Default          | Description                                        |
|-----------------|------------|------------------|----------------------------------------------------|
| `enabled`       | boolean    | `true`           | `false` = skip this game entirely                  |
| `categories`    | string[]   | platform default | Which categories to scrape                         |
| `language`      | string     | global default   | Language code override for this game               |
| `category_size` | integer    | global default   | Max posts to track per category                    |

### Rules

- **Present with no `enabled` key** → treated as enabled (`true`)
- **`enabled = false`** → game is skipped during scraping
- **Absent section** → game is not configured (not scraped)
- **Global defaults** apply to all games unless overridden at the game level

### Backward Compatibility

A flat `[genshin]` section (without the `hoyolab.` prefix) maps to `[hoyolab.genshin]`. This allows existing configuration files to continue working without modification.

---

## 3. Platform: HoYoLAB

### 3.1 API Reference

#### Base URL

```
https://bbs-api-os.hoyolab.com/community/post/wapi/
```

#### Required Headers

Every request **must** include these headers:

| Header            | Value                      | Notes                                      |
|-------------------|----------------------------|---------------------------------------------|
| `Origin`          | `https://www.hoyolab.com`  | Required or the API rejects the request     |
| `X-Rpc-Language`  | e.g. `en-us`               | Controls the language of the response text  |

No authentication or API key is needed.

#### Endpoint: `getNewsList`

Returns a **list of post summaries** for a given game and category. Used for discovery — finding which posts exist and when they were last modified.

**URL:** `GET https://bbs-api-os.hoyolab.com/community/post/wapi/getNewsList`

**Query Parameters:**

| Param       | Type | Required | Description                                  |
|-------------|------|----------|----------------------------------------------|
| `gids`      | int  | Yes      | Game ID (see [Games table](#game-ids))        |
| `type`      | int  | Yes      | Category type (see [Categories table](#category-types)) |
| `page_size` | int  | No       | Number of posts to return (default varies, recommended: 5–15) |

**Example Response (trimmed):**

```json
{
  "retcode": 0,
  "message": "OK",
  "data": {
    "list": [
      {
        "post": {
          "post_id": "12345678",
          "subject": "Version 5.4 Update Notice",
          "content": "",
          "desc": "Dear Travelers, below is the content of the Version 5.4 update...",
          "created_at": 1738000000,
          "view_type": 1,
          "official_type": 0
        },
        "last_modify_time": 1738100000,
        "cover_list": [
          {
            "url": "https://upload-os-bbs.hoyolab.com/upload/2025/01/image.jpg"
          }
        ],
        "user": {
          "nickname": "Genshin Impact"
        },
        "video": null
      }
    ]
  }
}
```

> **Important:** The `official_type` field in `getNewsList` responses is **always `0`** regardless of the post's actual category. The `type` query parameter controls which category you're requesting, but the response does not echo it back reliably. To get the true `official_type`, you must call `getPostFull`.

#### Endpoint: `getPostFull`

Returns the **full content** of a single post, including complete HTML body and accurate metadata.

**URL:** `GET https://bbs-api-os.hoyolab.com/community/post/wapi/getPostFull`

**Query Parameters:**

| Param     | Type | Required | Description              |
|-----------|------|----------|--------------------------|
| `gids`    | int  | Yes      | Game ID                  |
| `post_id` | int  | Yes      | The post ID to retrieve  |

**Example Response (trimmed):**

```json
{
  "retcode": 0,
  "message": "OK",
  "data": {
    "post": {
      "post": {
        "post_id": "12345678",
        "subject": "Version 5.4 Update Notice",
        "content": "<p>Dear Travelers,</p><p>Below are the details of the update...</p>",
        "structured_content": "[{\"insert\":\"Dear Travelers,\\n\"},{\"insert\":\"\\n\"}]",
        "desc": "Dear Travelers, below is the content of the Version 5.4 update...",
        "created_at": 1738000000,
        "view_type": 1,
        "official_type": 2
      },
      "last_modify_time": 1738100000,
      "cover_list": [
        {
          "url": "https://upload-os-bbs.hoyolab.com/upload/2025/01/image.jpg"
        }
      ],
      "user": {
        "nickname": "Genshin Impact"
      },
      "video": null
    }
  }
}
```

> **Note:** The response is nested as `data.post` (the outer post object), which itself contains `data.post.post` (the inner post metadata), `data.post.user`, `data.post.video`, `data.post.cover_list`, and `data.post.last_modify_time`.

#### Response Envelope

All API responses share the same envelope structure:

```json
{
  "retcode": 0,
  "message": "OK",
  "data": { ... }
}
```

- `retcode == 0` → success
- `retcode != 0` → error; `message` contains the reason (may be in Chinese)

#### Error Handling

1. **HTTP errors** — handle non-2xx status codes (network failures, rate limiting)
2. **JSON decode errors** — the API occasionally returns non-JSON responses
3. **`retcode != 0`** — API-level error, check the `message` field
4. **Missing `retcode` key** — unexpected response structure

### 3.2 Games, Categories & Languages

#### Game IDs

| Game                               | `gids` Value |
|------------------------------------|:------------:|
| Honkai Impact 3rd                  | 1            |
| Genshin Impact                     | 2            |
| Tears of Themis                    | 4            |
| Honkai: Star Rail                  | 6            |
| Zenless Zone Zero                  | 8            |
| Honkai Impact: Nexus (unreleased)  | 9            |

> Values 3, 5, and 7 are reserved/unused. Value 5 corresponds to HoYoLAB itself.

#### Category Types

| Category | `type` Value | Description                        |
|----------|:------------:|------------------------------------|
| Notices  | 1            | Maintenance, patch notes, updates  |
| Events   | 2            | In-game events, web events         |
| Info     | 3            | General information, guides        |

#### Language Codes

The `X-Rpc-Language` header accepts these values:

| Language             | Code    |
|----------------------|---------|
| German               | `de-de` |
| English              | `en-us` |
| Spanish              | `es-es` |
| French               | `fr-fr` |
| Indonesian           | `id-id` |
| Italian              | `it-it` |
| Japanese             | `ja-jp` |
| Korean               | `ko-kr` |
| Portuguese           | `pt-pt` |
| Russian              | `ru-ru` |
| Thai                 | `th-th` |
| Turkish              | `tr-tr` |
| Vietnamese           | `vi-vn` |
| Chinese (Simplified) | `zh-cn` |
| Chinese (Traditional)| `zh-tw` |

### 3.3 Scraping Strategy

#### Overview

The scraping process has three phases: **discovery**, **diff**, and **detail fetch**.

#### Phase 1: Discovery

For each game + category combination, call `getNewsList` to get the latest post summaries.

```
GET /getNewsList?gids={game_id}&type={category}&page_size={category_size}
```

From each item in the response, extract:
- `post.post_id` — unique post identifier
- `post.created_at` — Unix timestamp of creation
- `last_modify_time` — Unix timestamp of last modification

Compute the effective timestamp as: `max(created_at, last_modify_time)`.

#### Phase 2: Smart Update (Diff)

Compare the discovered posts against previously seen posts to avoid refetching unchanged content.

A post needs to be fetched if:
- Its `post_id` is **not** in the known set (new post), OR
- Its effective timestamp is **newer** than the stored timestamp (modified post)

```
for each post in latest_posts:
    if post.id not in known_ids:
        mark as NEW
    elif post.effective_timestamp > known_ids[post.id].timestamp:
        mark as MODIFIED
    else:
        skip (unchanged)
```

#### Phase 3: Detail Fetch

For every new or modified post, call `getPostFull` to retrieve the full content.

```
GET /getPostFull?gids={game_id}&post_id={post_id}
```

These requests are **independent** and can be parallelized (e.g. with `asyncio.gather` or `Promise.all`).

After fetching, apply all [Content Transformation Rules](#34-content-transformation-rules) before storing.

#### Category Size

The `category_size` parameter (default: **5**) caps how many posts to track per category. After fetching new items, sort all items for that category by ID descending and keep only the top N. This prevents unbounded growth.

#### Polling Interval Recommendations

| Use Case                | Interval      | Notes                                       |
|-------------------------|---------------|---------------------------------------------|
| Real-time notifications | 5–10 minutes  | Aggressive; watch for rate limiting          |
| Standard monitoring     | 15–30 minutes | Good balance of freshness and politeness     |
| Relaxed / archival      | 1–2 hours     | Minimal API load                             |

HoYoLAB does not publish rate limits. Be conservative and implement exponential backoff on errors.

### 3.4 Content Transformation Rules

These transformations **must** be applied to every post returned by `getPostFull`, in the order listed below. They fix known quirks in the HoYoLAB API responses.

#### Rule 1: Structured Content Fallback

**Condition:** The `content` field matches the regex `^[a-z]{2}-[a-z]{2}$` (e.g. `"en-us"`, `"zh-cn"`).

This is a known HoYoLAB bug where the content field contains only a language code instead of actual HTML. When this happens, parse the `structured_content` field instead.

**Action:** Replace `content` with the result of parsing `structured_content` (see [Structured Content Parser](#structured-content-parser) below).

> **This check must happen first** so the remaining rules can also apply to the reconstructed HTML.

#### Structured Content Parser

The `structured_content` field is a JSON string containing a Quill-delta-like array of operations. Each element has an `insert` key.

**Pre-processing:** Replace all `\\n` and `\n` sequences in the raw string with `<br>` before JSON parsing.

**Parsing rules:**

| `insert` type       | Attributes                    | Output HTML                                              |
|----------------------|-------------------------------|----------------------------------------------------------|
| String               | `attributes.link` present     | `<a href="{link}">{text}</a>` wrapped in `<p>`           |
| String               | `attributes.bold` present     | `<p><strong>{text}</strong></p>`                         |
| String               | `attributes.italic` present   | `<p><em>{text}</em></p>`                                 |
| String               | No special attributes         | `<p>{text}</p>`                                          |
| Object with `image`  | —                             | `<img src="{image_url}">`                                |
| Object with `video`  | —                             | `<iframe src="{video_url}"></iframe>`                     |

Concatenate all output fragments into a single HTML string.

**Example input:**
```json
[
  {"insert": "Hello ", "attributes": {"bold": true}},
  {"insert": "world"},
  {"insert": {"image": "https://example.com/img.png"}},
  {"insert": "\n"}
]
```

**Example output:**
```html
<p><strong>Hello </strong></p><p>world</p><img src="https://example.com/img.png"><p><br></p>
```

#### Rule 2: Video Posts

**Condition:** `view_type == 5` AND `video` is not `null`.

These are native video posts where the content is a video rather than an article.

**Action:** Replace `content` entirely with:

```html
<video src="{video.url}" poster="{video.cover}" controls playsinline>Watch the video here: {video.url}</video><p>{post.desc}</p>
```

#### Rule 3: Empty Leading Paragraph Removal

**Condition:** `content` starts with any of:
- `<p></p>`
- `<p>&nbsp;</p>`
- `<p><br></p>`

**Action:** Remove everything up to and including the first `</p>`. Specifically, partition the string on `</p>` and keep only the part after it.

#### Rule 4: Private Link Fix

**Condition:** `content` contains the substring `hoyolab-upload-private`.

**Action:** Replace all occurrences of `hoyolab-upload-private` with `upload-os-bbs`.

This fixes image/asset URLs that use a private CDN domain, making them publicly accessible.

### 3.5 Article URL Pattern

Construct the web URL for any HoYoLAB post as:

```
https://www.hoyolab.com/article/{id}
```

### 3.6 Field Mapping → FeedItem

| FeedItem Field | HoYoLAB Source                    | Notes                                        |
|----------------|-----------------------------------|----------------------------------------------|
| `id`           | `post.post_id`                    | String (numeric)                             |
| `platform`     | `"hoyolab"` (constant)            | —                                            |
| `url`          | `https://www.hoyolab.com/article/{id}` | Pre-computed                            |
| `title`        | `post.subject`                    | —                                            |
| `author`       | `user.nickname`                   | —                                            |
| `content`      | `post.content`                    | After applying transformation rules          |
| `category`     | `post.official_type`              | Mapped to canonical string (see [Section 5](#5-unified-data-model)) |
| `published`    | `post.created_at`                 | Unix timestamp → datetime                    |
| `updated`      | `last_modify_time`                | Only set if `> 0`; Unix timestamp            |
| `image`        | `cover_list[0].url`               | First cover image, if any                    |
| `summary`      | `post.desc`                       | Only if non-empty after stripping whitespace |
| `game`         | Known from request context        | e.g. `"genshin"`, `"starrail"`               |

> **Important:** `category` (`official_type`) is only reliable from `getPostFull`, not from `getNewsList`.

---

## 4. Platform: Gryphline

### 4.1 Overview

Gryphline is Hypergryph's news portal for **Arknights: Endfield**. Unlike HoYoLAB, Gryphline does **not** expose a public REST API. Data is delivered via **Next.js React Server Components (RSC)**, serialized in `self.__next_f.push()` arrays embedded within page HTML.

> **Brittleness warning:** RSC payload parsing depends on the internal structure of a Next.js application. Site rebuilds or framework upgrades can change the serialization format without notice. Implement defensive parsing and log warnings on parse failures.

### 4.2 Games & Categories

#### Games

| Game                  | Config Slug  |
|-----------------------|-------------|
| Arknights: Endfield   | `endfield`  |

#### Categories

| Category | Native Value | Description                       |
|----------|-------------|-----------------------------------|
| Notices  | `"notices"` | Maintenance, patch notes, updates |
| News     | `"news"`    | General news, announcements       |

Only 2 categories (vs. HoYoLAB's 3).

### 4.3 Language Codes

Gryphline uses the same locale format as HoYoLAB (e.g. `en-us`, `ja-jp`, `ko-kr`, `zh-cn`). The language code appears as a path segment in the URL.

### 4.4 Scraping Strategy (RSC Payload)

#### Discovery

**Listing URL:**

```
https://endfield.gryphline.com/{lang}/news
```

Fetch the page HTML and extract `self.__next_f.push()` arrays. Parse the serialized data to find the `bulletins` array and `total` count.

**Payload structure:**

```json
{
  "bulletins": [
    {
      "cid": "5215",
      "tab": "notices",
      "sticky": false,
      "title": "The [Over the Frontier] themed gallery is now live",
      "displayTime": 1770264000,
      "cover": "https://web-static.hg-cdn.com/...",
      "brief": "...",
      "author": "",
      "extraCover": "",
      "data": "<p>HTML content...</p>"
    }
  ],
  "total": 41
}
```

The listing page returns **8 items** per load.

#### Diff

Compare discovered posts against the state store (see [Section 6](#6-state-management--deduplication)):
- A post is **new** if its `cid` is not in the store
- A post is **modified** if its `displayTime` is newer than the stored timestamp

> **Limitation:** Gryphline has no `last_modify_time` equivalent. Diff uses `displayTime` only. Edits to existing articles that do not change `displayTime` will **not** be detected.

#### Detail Fetch

**Article URL:**

```
https://endfield.gryphline.com/{lang}/news/{cid}
```

Fetch the article page HTML and extract RSC payload. The `data` field in the payload contains the full HTML article body.

### 4.5 Content Notes

- The `data` field contains **clean HTML** (standard `<p>` tags, links, Unicode entities)
- **No HoYoLAB-style quirks** — no structured content fallback, no private link fixes, no video post handling needed
- The standard [HTML-to-Plain-Text conversion](#7-html-to-plain-text-conversion) pipeline applies without platform-specific pre-processing
- The `author` field is **always empty** in observed data — fall back to the game name (`"Arknights: Endfield"`)

### 4.6 Article URL Pattern

Construct the web URL for any Gryphline article as:

```
https://endfield.gryphline.com/{lang}/news/{cid}
```

Where `{lang}` is the configured language code (e.g. `en-us`).

### 4.7 Field Mapping → FeedItem

| FeedItem Field | Gryphline Source                  | Notes                                        |
|----------------|-----------------------------------|----------------------------------------------|
| `id`           | `cid`                             | String (numeric)                             |
| `platform`     | `"gryphline"` (constant)          | —                                            |
| `url`          | `https://endfield.gryphline.com/{lang}/news/{cid}` | Pre-computed with language       |
| `title`        | `title`                           | —                                            |
| `author`       | `author`                          | Falls back to `"Arknights: Endfield"` if empty |
| `content`      | `data`                            | HTML body; no platform-specific transforms   |
| `category`     | `tab`                             | Already a canonical string (`"notices"` / `"news"`) |
| `published`    | `displayTime`                     | Unix timestamp → datetime                    |
| `updated`      | —                                 | Always `null` (no modification tracking)     |
| `image`        | `cover`                           | Cover image URL                              |
| `summary`      | `brief`                           | Pre-truncated by Gryphline                   |
| `game`         | `"endfield"` (constant)           | —                                            |

### 4.8 Pagination Limitations

Gryphline returns **8 items** per page load. There is no documented REST pagination (no offset/limit parameters). If `category_size > 8`, only the first 8 items are available per scrape cycle.

The `total` field in the payload indicates the total number of articles, but accessing items beyond the first 8 would require client-side pagination simulation, which is not recommended due to brittleness.

---

## 5. Unified Data Model

After fetching and transforming a post from any platform, extract these fields into a unified `FeedItem`:

| Field       | Type              | Description                                              |
|-------------|-------------------|----------------------------------------------------------|
| `id`        | string            | Unique post identifier (platform-specific)               |
| `platform`  | string enum       | `"hoyolab"` or `"gryphline"`                             |
| `url`       | string (URL)      | Pre-computed article URL (pattern differs per platform)  |
| `title`     | string            | Post title                                               |
| `author`    | string            | Author display name                                     |
| `content`   | string (HTML)     | Full HTML body (after any transformations)               |
| `category`  | string enum       | Canonical category (see table below)                     |
| `published` | datetime          | Publication timestamp                                    |
| `updated`   | datetime or null  | Last modification timestamp (null if unavailable)        |
| `image`     | URL or null       | Cover/thumbnail image, if any                            |
| `summary`   | string or null    | Short description, if available                          |
| `game`      | string enum       | Game slug (e.g. `"genshin"`, `"endfield"`)               |

### Changes from Previous Model

| Change        | Before               | After                              | Reason                          |
|---------------|----------------------|------------------------------------|---------------------------------|
| `id` type     | `int`                | `string`                           | Endfield uses string CIDs       |
| Add `platform`| —                    | `enum: "hoyolab" \| "gryphline"`   | Multi-platform support          |
| Add `url`     | —                    | Pre-computed article URL           | URL patterns differ per platform|
| `category`    | `int (1, 2, 3)`      | `string enum`                      | Gryphline uses string tabs      |
| `game` values | HoYoLAB-only         | + `"endfield"`                     | New game added                  |

### Category Normalization

All platforms map their native category values to canonical lowercase strings:

| Platform  | Native Value         | Canonical     |
|-----------|---------------------|---------------|
| HoYoLAB   | `official_type = 1` | `"notices"`   |
| HoYoLAB   | `official_type = 2` | `"events"`    |
| HoYoLAB   | `official_type = 3` | `"info"`      |
| Gryphline | `tab = "notices"`   | `"notices"`   |
| Gryphline | `tab = "news"`      | `"news"`      |

### Composite Key

To uniquely identify a post across platforms, use the composite key:

```
{platform}:{game}:{id}
```

Examples: `hoyolab:genshin:12345678`, `gryphline:endfield:5215`

---

## 6. State Management & Deduplication

### Seen-Posts Store

A persistent JSON file (e.g. `state.json`) tracks which posts have been seen and sent to Discord.

**Format:**

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

The key is the [composite key](#composite-key) `"{platform}:{game}:{id}"` to avoid collisions across platforms.

### Poll Cycle Flow

On each poll cycle:

1. **Discovery** — fetch listing data from each enabled platform/game/category
2. **Diff** — compare discovered posts against the state store
3. **Detail fetch** — retrieve full content for new/modified posts only
4. **Send** — deliver to Discord via webhook
5. **Record** — update the state store with sent posts

### Deduplication Rules

| Condition                                                  | Action            |
|------------------------------------------------------------|-------------------|
| Post ID **not** in the store                               | Treat as **new**  |
| Post ID in store, `last_modified > stored_last_modified`   | Treat as **modified** |
| Post ID in store, timestamps match                         | **Skip** (unchanged) |

### First-Run Baseline

On the very first run (i.e. `state.json` does not exist or is empty):

1. Execute discovery + detail fetch as normal for all enabled games
2. Populate the state store with all discovered posts
3. **Do NOT send anything to Discord**
4. Log: `"First run — recorded N posts as baseline, no Discord messages sent"`

This prevents flooding a Discord channel with dozens of historical posts when the scraper is first deployed.

Subsequent runs operate normally — only new or modified posts are sent.

### Error Handling

- If a Discord webhook request **fails**, do **not** mark the post as sent in the state store. It will be retried on the next poll cycle.
- If the state file is corrupted or unreadable, treat it as a first run (re-baseline).

---

## 7. HTML-to-Plain-Text Conversion

For Discord embeds you need plain text, not HTML. Apply these transformations in order:

### Step 1: Decode HTML Entities

```
&nbsp;  →  (space)
&amp;   →  &
&lt;    →  <
&gt;    →  >
```

### Step 2: Line Break Tags

```
<br>, <br/>, <br /> (any case)  →  \n
```

### Step 3: Paragraphs

```
</p>  →  \n\n
<p>, <p ...>  →  (remove)
```

### Step 4: Lists

```
<li>, <li ...>  →  "• " (bullet prefix)
</li>           →  \n
<ul>, </ul>, <ol>, </ol>  →  (remove)
```

### Step 5: Links

Convert anchor tags to `text (url)` format:

```
<a href="https://example.com">Click here</a>  →  Click here (https://example.com)
```

If only href or only text exists, use whichever is available.

### Step 6: Images

Convert image tags to markers:

```
<img alt="photo" src="https://example.com/img.png">  →  [img: photo — https://example.com/img.png]
<img src="https://example.com/img.png">               →  [img: https://example.com/img.png]
```

### Step 7: Strip Remaining Tags

Remove all remaining HTML tags:

```
<anything>  →  (remove)
```

### Step 8: Normalize Whitespace

```
\r\n, \r     →  \n
3+ newlines  →  \n\n
2+ spaces    →  (single space)
```

Trim leading and trailing whitespace from the final result.

---

## 8. Discord Embed Mapping

### Embed Template

```json
{
  "title": "{item.title}",
  "url": "{item.url}",
  "description": "{plain_text_content (truncated)}",
  "color": "{COLOR_BY_GAME[item.game]}",
  "thumbnail": {
    "url": "{item.image}"
  },
  "author": {
    "name": "{item.author}"
  },
  "footer": {
    "text": "{category} · {game}"
  },
  "timestamp": "{item.published (ISO 8601)}"
}
```

If `updated` exists and is different from `published`, consider including it in the footer:
```
"Events · Genshin Impact · Updated: 2025-01-28"
```

### Concrete Examples

**HoYoLAB — Genshin Impact notice:**

```json
{
  "title": "Version 5.4 Update Notice",
  "url": "https://www.hoyolab.com/article/12345678",
  "description": "Dear Travelers,\n\nBelow are the details of the update...\n\n[Read more](https://www.hoyolab.com/article/12345678)",
  "color": 56540,
  "thumbnail": {
    "url": "https://upload-os-bbs.hoyolab.com/upload/2025/01/image.jpg"
  },
  "author": {
    "name": "Genshin Impact"
  },
  "footer": {
    "text": "Notices · Genshin Impact"
  },
  "timestamp": "2025-01-28T00:00:00Z"
}
```

**Gryphline — Arknights: Endfield news:**

```json
{
  "title": "Arknights: Endfield Pre-Download Now Available",
  "url": "https://endfield.gryphline.com/en-us/news/9574",
  "description": "The pre-download for Arknights: Endfield is now available...",
  "color": 16737095,
  "thumbnail": {
    "url": "https://web-static.hg-cdn.com/upload/endfield/cover.png"
  },
  "author": {
    "name": "Arknights: Endfield"
  },
  "footer": {
    "text": "News · Arknights: Endfield"
  },
  "timestamp": "2026-02-05T12:00:00Z"
}
```

> Note: Colors above are decimal integers (Discord's format). `0x00DCDC` = `56540`, `0xFF6347` = `16737095`.

### Discord Limits

| Field              | Max Length  | Truncation Strategy                           |
|--------------------|:----------:|-----------------------------------------------|
| Embed title        | 256 chars  | Truncate with `...` at 253 chars              |
| Embed description  | 4096 chars | Truncate with `\n\n[Read more]({url})` suffix |
| Message content    | 2000 chars | Use embeds instead of plain messages          |
| Footer text        | 2048 chars | Should never be an issue                      |
| Total embed size   | 6000 chars | Sum of all fields; trim description first     |

**Truncation strategy for long posts:**

1. Convert HTML content to plain text (see [Section 7](#7-html-to-plain-text-conversion))
2. If the result exceeds ~4000 characters, truncate at a word boundary near the limit
3. Append `\n\n[Read more]({item.url})` after truncation
4. Ensure the total (truncated text + "Read more" link) stays under 4096

### Suggested Color Coding

Use different embed colors per game for visual distinction:

| Game                  | Suggested Color | Hex        |
|-----------------------|-----------------|------------|
| Honkai Impact 3rd     | Blue            | `0x00BFFF` |
| Genshin Impact        | Teal            | `0x00DCDC` |
| Tears of Themis       | Pink            | `0xFF77A8` |
| Honkai: Star Rail     | Gold            | `0xDDA000` |
| Zenless Zone Zero     | Green           | `0x00FF7F` |
| Nexus                 | Purple          | `0xAA77FF` |
| Arknights: Endfield   | Orange-Red      | `0xFF6347` |

Alternatively, you can color-code by category:

| Category | Suggested Color | Hex        |
|----------|-----------------|------------|
| Notices  | Red / Orange    | `0xFF6B35` |
| Events   | Green           | `0x00C853` |
| Info     | Blue            | `0x448AFF` |
| News     | Amber           | `0xFFB300` |

---

## Appendix A: Quick-Start Pseudocode

```python
# --- Platform registry ---
PLATFORMS = {
    "hoyolab":   HoYoLABPlatform,
    "gryphline": GryphlinePlatform,
}

# --- Main loop ---
def scrape_cycle(config, state_store):
    is_first_run = state_store.is_empty()
    all_new_items = []

    for platform_name, platform_cls in PLATFORMS.items():
        platform = platform_cls(config)

        for game in platform.enabled_games():
            for category in game.categories:
                # Phase 1: Discovery
                posts = platform.discover(game, category)

                # Phase 2: Diff
                for post in posts:
                    key = f"{platform_name}:{game.slug}:{post.id}"
                    stored = state_store.get(key)

                    if stored is None:
                        post.status = NEW
                    elif post.effective_timestamp > stored.last_modified:
                        post.status = MODIFIED
                    else:
                        continue  # unchanged

                    # Phase 3: Detail fetch
                    item = platform.fetch_detail(game, post)
                    all_new_items.append((key, item))

    # Phase 4: Send or baseline
    if is_first_run:
        log("First run — recorded {len(all_new_items)} posts as baseline, "
            "no Discord messages sent")
    else:
        for key, item in all_new_items:
            plain_text = html_to_plaintext(item.content)
            embed = build_discord_embed(item, plain_text)
            success = send_to_discord(webhook_url, embed)
            if not success:
                continue  # will retry next cycle

    # Phase 5: Record state
    for key, item in all_new_items:
        if is_first_run or item.was_sent:
            state_store.set(key, {
                "last_modified": item.effective_timestamp,
                "last_sent_hash": hash(item),
            })

    state_store.save()
```

---

## Appendix B: Adding a New Platform

To integrate a new game publisher or news source, follow this checklist:

1. **Choose a platform slug** — lowercase, no spaces (e.g. `"gryphline"`, `"hoyolab"`)

2. **Document the scraping mechanism** — REST API, HTML scraping, RSC parsing, GraphQL, etc.

3. **Map native fields → FeedItem** — create a field mapping table (see [Section 3.6](#36-field-mapping--feeditem) or [Section 4.7](#47-field-mapping--feeditem) for examples)

4. **Document categories + canonical mappings** — list all native category values and their canonical string equivalents (see [Category Normalization](#category-normalization))

5. **Document the article URL pattern** — how to construct a direct link to any article

6. **Document content transformations** — any platform-specific HTML fixups needed before the standard pipeline (HoYoLAB needs 4 rules; Gryphline needs none)

7. **Add a TOML config section** — define `[newplatform.gameslug]` with supported settings

8. **Add a Discord embed color** — choose a distinctive color for the game and add it to the [color table](#suggested-color-coding)

9. **Register the platform** — add to the `PLATFORMS` registry so the main loop picks it up

10. **Test the full pipeline** — discovery → diff → detail fetch → transform → embed → Discord send

---

*This specification is self-contained and can be used to build a multi-platform game news scraper without access to any other codebase or documentation.*
