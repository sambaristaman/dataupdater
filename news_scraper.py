#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified News Scraper → Discord (HoYoLAB + Gryphline + Shadowverse)

Posts to WEBHOOK_URL_NEWS only, with per-game scheduling via ONLY_GAME env.
State is stored in news_state.json.
"""

import hashlib
import html as html_lib
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag

# ---------------- Config ----------------

STATE_PATH = Path("news_state.json")
WEBHOOK_ENV = "WEBHOOK_URL_NEWS"
ONLY_GAME = ""
DRY_RUN = False
RUN_LAST_HOURS_RAW = ""
IMAGE_EMBEDS = True

DEFAULT_LANGUAGE = "en-us"
CATEGORY_SIZE = 5

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# HoYoLAB games
HOYOLAB_GAMES = {
    "genshin": {"gids": 2, "categories": [1, 2, 3]},
    "starrail": {"gids": 6, "categories": [1, 2, 3]},
    "honkai3rd": {"gids": 1, "categories": [1, 2, 3]},
    "zzz": {"gids": 8, "categories": [1, 2, 3]},
}

# Gryphline games
GRYPHLINE_GAMES = {
    "endfield": {"categories": ["notices", "news"]},
}

# Shadowverse
SHADOWVERSE_GAME = "shadowverse"
SHADOWVERSE_BASE_URL = "https://shadowverse.gg/"
SHADOWVERSE_NEWS_URL = "https://shadowverse.gg/news/"
MIRROR_PREFIX = "https://r.jina.ai/http://"

CATEGORY_MAP_HOYOLAB = {1: "notices", 2: "events", 3: "info"}

COLOR_BY_GAME = {
    "genshin": 0x00DCDC,
    "starrail": 0xDDA000,
    "honkai3rd": 0x00BFFF,
    "zzz": 0x00FF7F,
    "endfield": 0xFF6347,
    "shadowverse": 0x7E57C2,
}

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }
)

# ---------------- Utilities ----------------


def _retry_sleep(attempt: int, base: float = 2.0) -> None:
    wait = base * attempt + random.uniform(0, 1)
    time.sleep(wait)


def log(level: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} [{level}] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        data = (line + "\n").encode("utf-8", errors="replace")
        try:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        except Exception:
            # Last-resort fallback
            print(line.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


def log_item(action: str, reason: str, item: Dict) -> None:
    title = (item.get("title") or "").strip()
    url = item.get("url") or ""
    platform = item.get("platform") or "unknown"
    game = item.get("game") or "unknown"
    log("INFO", f"{action} {platform}/{game}: {title} — {url} ({reason})")


def refresh_runtime_config() -> None:
    global ONLY_GAME, DRY_RUN, RUN_LAST_HOURS_RAW, STATE_PATH, IMAGE_EMBEDS
    ONLY_GAME = os.getenv("ONLY_GAME", "").strip().lower()
    DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() == "true"
    RUN_LAST_HOURS_RAW = os.getenv("RUN_LAST_HOURS", "").strip()
    STATE_PATH = Path(os.getenv("NEWS_STATE_PATH", "news_state.json"))
    IMAGE_EMBEDS = os.getenv("IMAGE_EMBEDS", "true").strip().lower() != "false"


def load_state() -> Dict[str, Dict]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: Dict[str, Dict]) -> None:
    if DRY_RUN:
        print("[DRY_RUN] Would write news_state.json")
        return
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def composite_key(platform: str, game: str, item_id: str) -> str:
    return f"{platform}:{game}:{item_id}"


def hash_item(item: Dict) -> str:
    payload = f"{item.get('title','')}|{item.get('url','')}|{item.get('content','')}|{item.get('updated','')}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_first_run_for_game(state: Dict[str, Dict], game: str) -> bool:
    prefix = f":{game}:"
    return not any(prefix in key for key in state.keys())


def to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def get_last_hours_cutoff() -> Optional[int]:
    if not RUN_LAST_HOURS_RAW:
        return None
    try:
        hours = int(RUN_LAST_HOURS_RAW)
    except ValueError:
        return None
    if hours <= 0:
        return None
    return int(time.time()) - (hours * 3600)


# ---------------- HTML to Plain Text ----------------


_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_P_OPEN_RE = re.compile(r"<p[^>]*>", re.IGNORECASE)
_P_CLOSE_RE = re.compile(r"</p>", re.IGNORECASE)
_LI_OPEN_RE = re.compile(r"<li[^>]*>", re.IGNORECASE)
_LI_CLOSE_RE = re.compile(r"</li>", re.IGNORECASE)
_ULOL_RE = re.compile(r"</?(ul|ol)[^>]*>", re.IGNORECASE)
_A_RE = re.compile(r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_IMG_RE = re.compile(r"<img[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    if not html:
        return ""

    text = html_lib.unescape(html)
    text = _BR_RE.sub("\n", text)
    text = _P_CLOSE_RE.sub("\n\n", text)
    text = _P_OPEN_RE.sub("", text)
    text = _LI_OPEN_RE.sub("• ", text)
    text = _LI_CLOSE_RE.sub("\n", text)
    text = _ULOL_RE.sub("", text)

    def _link_repl(match: re.Match) -> str:
        href = match.group(1).strip()
        label = _TAG_RE.sub("", match.group(2)).strip()
        if href and label:
            return f"{label} ({href})"
        return href or label

    text = _A_RE.sub(_link_repl, text)

    def _img_repl(match: re.Match) -> str:
        tag = match.group(0)
        src_m = re.search(r"src=[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
        alt_m = re.search(r"alt=[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
        src = src_m.group(1) if src_m else ""
        alt = alt_m.group(1) if alt_m else ""
        if alt:
            return f"[img: {alt} — {src}]"
        return f"[img: {src}]"

    text = _IMG_RE.sub(_img_repl, text)
    text = _TAG_RE.sub("", text)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


_HEADING_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.IGNORECASE | re.DOTALL)
_STRONG_RE = re.compile(r"<(strong|b)>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_EM_RE = re.compile(r"<(em|i)>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_IMG_SRC_RE = re.compile(r"<img[^>]*\ssrc=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)


def extract_images_from_html(html: str) -> List[str]:
    """Extract all image URLs from <img> tags in raw HTML, preserving order."""
    if not html:
        return []
    seen: set = set()
    urls: List[str] = []
    for m in _IMG_SRC_RE.finditer(html):
        url = m.group(1)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def html_to_discord_md(html: str, strip_images: bool = False) -> str:
    """Convert HTML to Discord-flavored Markdown."""
    if not html:
        return ""

    text = html_lib.unescape(html)

    # Headings → bold + newline
    text = _HEADING_RE.sub(lambda m: f"**{_TAG_RE.sub('', m.group(1)).strip()}**\n", text)

    # Bold
    text = _STRONG_RE.sub(lambda m: f"**{m.group(2)}**", text)

    # Italic
    text = _EM_RE.sub(lambda m: f"*{m.group(2)}*", text)

    # Links → [text](url)
    def _link_md(match: re.Match) -> str:
        href = match.group(1).strip()
        label = _TAG_RE.sub("", match.group(2)).strip()
        if href and label:
            return f"[{label}]({href})"
        return href or label

    text = _A_RE.sub(_link_md, text)

    # Images → [alt](url) or just url, or stripped entirely
    def _img_md(match: re.Match) -> str:
        if strip_images:
            return ""
        tag = match.group(0)
        src_m = re.search(r"src=[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
        alt_m = re.search(r"alt=[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
        src = src_m.group(1) if src_m else ""
        alt = alt_m.group(1) if alt_m else ""
        if alt and src:
            return f"[{alt}]({src})"
        return src

    text = _IMG_RE.sub(_img_md, text)

    # Block-level elements
    text = _BR_RE.sub("\n", text)
    text = _P_CLOSE_RE.sub("\n\n", text)
    text = _P_OPEN_RE.sub("", text)
    text = _LI_OPEN_RE.sub("• ", text)
    text = _LI_CLOSE_RE.sub("\n", text)
    text = _ULOL_RE.sub("", text)

    # Strip remaining HTML tags
    text = _TAG_RE.sub("", text)

    # Normalize whitespace
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def split_content(text: str, limit: int = 4096) -> List[str]:
    """Split text into chunks at word/newline boundaries."""
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while len(remaining) > limit:
        segment = remaining[:limit]
        # Prefer splitting at last newline
        nl = segment.rfind("\n")
        if nl > limit // 4:
            split_at = nl
        else:
            # Fall back to last space
            sp = segment.rfind(" ")
            split_at = sp if sp > limit // 4 else limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")
    if remaining.strip():
        chunks.append(remaining.strip())
    return chunks


def truncate_description(text: str, url: str, limit: int = 4096) -> str:
    if len(text) <= limit:
        return text
    suffix = f"\n\nRead more: {url}"
    max_len = limit - len(suffix)
    if max_len <= 0:
        return text[: limit - 1]
    cut = text[:max_len]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + suffix


# ---------------- Discord ----------------


def send_embeds(webhook_url: str, embeds: List[Dict]) -> None:
    max_per_message = 10
    batches: List[List[Dict]] = []
    for i in range(0, len(embeds), max_per_message):
        batches.append(embeds[i : i + max_per_message])

    for bi, batch in enumerate(batches):
        if DRY_RUN:
            titles = [e.get("title", "(image/continuation)") for e in batch]
            log("INFO", f"DRY_RUN would send message {bi+1}/{len(batches)} with {len(batch)} embed(s): {titles}")
            continue
        payload = {"embeds": batch}
        r = SESSION.post(f"{webhook_url}?wait=true", json=payload, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Discord webhook error {r.status_code}: {r.text[:300]}")
        if bi < len(batches) - 1:
            time.sleep(0.5)


def build_embed(item: Dict) -> List[Dict]:
    color = COLOR_BY_GAME.get(item["game"], 0x888888)
    footer = {"text": f"{item.get('category','news')} · {item['game']}"}
    timestamp = item.get("published")
    item_url = item["url"]
    cover_image = item.get("image")

    if not IMAGE_EMBEDS:
        # Legacy behavior: no image extraction
        md = html_to_discord_md(item.get("content", ""))
        chunks = split_content(md)
        embeds: List[Dict] = []
        for i, chunk in enumerate(chunks):
            embed: Dict = {"description": chunk, "color": color}
            if i == 0:
                embed["title"] = (item.get("title") or item_url)[:256]
                embed["url"] = item_url
                if item.get("author"):
                    embed["author"] = {"name": item["author"][:256]}
                if cover_image:
                    embed["image"] = {"url": cover_image}
            if i == len(chunks) - 1:
                embed["footer"] = footer
                if timestamp:
                    embed["timestamp"] = timestamp
            embeds.append(embed)
        return embeds

    # --- IMAGE_EMBEDS enabled ---
    content_html = item.get("content", "")
    extracted_images = extract_images_from_html(content_html)
    md = html_to_discord_md(content_html, strip_images=True)

    # Deduplicate: remove the cover image from extracted list if present
    if cover_image:
        extracted_images = [u for u in extracted_images if u != cover_image]

    # Pick one image: cover takes priority, else first extracted
    image = cover_image
    if not image and extracted_images:
        image = extracted_images[0]

    chunks = split_content(md)
    embeds = []

    for i, chunk in enumerate(chunks):
        embed: Dict = {"description": chunk, "color": color, "url": item_url}
        if i == 0:
            embed["title"] = (item.get("title") or item_url)[:256]
            if item.get("author"):
                embed["author"] = {"name": item["author"][:256]}
            if image:
                embed["image"] = {"url": image}
        embeds.append(embed)

    # Footer + timestamp on the very last embed
    if embeds:
        embeds[-1]["footer"] = footer
        if timestamp:
            embeds[-1]["timestamp"] = timestamp

    return embeds


# ---------------- HoYoLAB ----------------


HOYOLAB_BASE = "https://bbs-api-os.hoyolab.com/community/post/wapi/"


def hoyolab_headers(lang: str) -> Dict[str, str]:
    return {
        "Origin": "https://www.hoyolab.com",
        "X-Rpc-Language": lang,
    }


def hoyolab_get(endpoint: str, params: Dict, lang: str) -> Dict:
    url = HOYOLAB_BASE + endpoint
    for attempt in range(1, 4):
        try:
            r = SESSION.get(url, params=params, headers=hoyolab_headers(lang), timeout=30)
            r.raise_for_status()
            data = r.json()
            if data.get("retcode") != 0:
                raise RuntimeError(f"HoYoLAB API retcode {data.get('retcode')}: {data.get('message')}")
            return data.get("data", {})
        except Exception as e:
            if attempt == 3:
                raise
            print(f"[HoYoLAB] {type(e).__name__} attempt {attempt} failed; retrying")
            _retry_sleep(attempt)
    return {}


def parse_structured_content(raw: str) -> str:
    if not raw:
        return ""
    prepared = raw.replace("\\n", "<br>").replace("\n", "<br>")
    try:
        ops = json.loads(prepared)
    except Exception:
        return ""
    out = []
    for op in ops:
        ins = op.get("insert")
        attrs = op.get("attributes") or {}
        if isinstance(ins, str):
            text = ins
            if attrs.get("link"):
                out.append(f"<p><a href=\"{attrs['link']}\">{text}</a></p>")
            elif attrs.get("bold"):
                out.append(f"<p><strong>{text}</strong></p>")
            elif attrs.get("italic"):
                out.append(f"<p><em>{text}</em></p>")
            else:
                out.append(f"<p>{text}</p>")
        elif isinstance(ins, dict):
            if "image" in ins:
                out.append(f"<img src=\"{ins['image']}\">")
            if "video" in ins:
                out.append(f"<iframe src=\"{ins['video']}\"></iframe>")
    return "".join(out)


def hoyolab_transform_content(post: Dict) -> str:
    content = post.get("content") or ""
    structured = post.get("structured_content") or ""
    view_type = post.get("view_type")
    video = post.get("video")
    desc = post.get("desc") or ""

    if re.fullmatch(r"[a-z]{2}-[a-z]{2}", content.strip()):
        content = parse_structured_content(structured)

    if view_type == 5 and video:
        vurl = video.get("url") or ""
        vcover = video.get("cover") or ""
        content = (
            f"<video src=\"{vurl}\" poster=\"{vcover}\" controls playsinline>"
            f"Watch the video here: {vurl}</video><p>{desc}</p>"
        )

    if content.startswith("<p></p>") or content.startswith("<p>&nbsp;</p>") or content.startswith("<p><br></p>"):
        _, _, tail = content.partition("</p>")
        content = tail

    content = content.replace("hoyolab-upload-private", "upload-os-bbs")
    return content


def hoyolab_discover(game: str, gids: int, category: int, lang: str) -> List[Dict]:
    data = hoyolab_get(
        "getNewsList",
        params={"gids": gids, "type": category, "page_size": CATEGORY_SIZE},
        lang=lang,
    )
    return data.get("list", []) or []


def hoyolab_fetch_detail(gids: int, post_id: str, lang: str) -> Dict:
    data = hoyolab_get(
        "getPostFull",
        params={"gids": gids, "post_id": post_id},
        lang=lang,
    )
    return data.get("post", {}) or {}


def hoyolab_process(game: str, lang: str, state: Dict[str, Dict], cutoff_ts: Optional[int] = None) -> Tuple[List[Dict], List[Tuple[str, Dict]]]:
    gids = HOYOLAB_GAMES[game]["gids"]
    categories = HOYOLAB_GAMES[game]["categories"]

    discovered: List[Dict] = []
    to_fetch_map: Dict[str, Dict] = {}

    for cat in categories:
        for item in hoyolab_discover(game, gids, cat, lang):
            post = item.get("post", {})
            post_id = str(post.get("post_id"))
            created_at = int(post.get("created_at") or 0)
            last_mod = int(item.get("last_modify_time") or 0)
            effective_ts = max(created_at, last_mod)
            key = composite_key("hoyolab", game, post_id)
            discovered.append({"key": key, "effective_ts": effective_ts})

            prev = state.get(key)
            needs_fetch = prev is None or effective_ts > int(prev.get("last_modified", 0))
            if not needs_fetch and cutoff_ts and effective_ts >= cutoff_ts:
                needs_fetch = True
            if needs_fetch:
                existing = to_fetch_map.get(post_id)
                if not existing or effective_ts > existing["effective_ts"]:
                    to_fetch_map[post_id] = {"effective_ts": effective_ts, "gids": gids}

    to_fetch = [(pid, meta) for pid, meta in to_fetch_map.items()]
    return discovered, to_fetch


def hoyolab_build_item(game: str, detail: Dict, effective_ts: int) -> Dict:
    outer = detail
    inner = outer.get("post", {}) or {}
    post_id = str(inner.get("post_id"))
    title = inner.get("subject") or ""
    author = (outer.get("user") or {}).get("nickname") or ""
    post_for_transform = dict(inner)
    post_for_transform["video"] = outer.get("video")
    content = hoyolab_transform_content(post_for_transform)
    category = CATEGORY_MAP_HOYOLAB.get(inner.get("official_type"), "info")
    created_at = int(inner.get("created_at") or 0)
    last_mod = int(outer.get("last_modify_time") or 0)
    updated = to_iso(last_mod) if last_mod else None
    image = None
    covers = outer.get("cover_list") or []
    if covers:
        image = covers[0].get("url")
    summary = inner.get("desc") or None
    url = f"https://www.hoyolab.com/article/{post_id}"

    return {
        "id": post_id,
        "platform": "hoyolab",
        "game": game,
        "url": url,
        "title": title,
        "author": author,
        "content": content,
        "category": category,
        "published": to_iso(created_at) if created_at else to_iso(effective_ts),
        "updated": updated,
        "image": image,
        "summary": summary,
        "effective_ts": effective_ts,
    }


# ---------------- Gryphline ----------------


def extract_push_payloads(html: str) -> List[str]:
    payloads = []
    for m in re.finditer(r"self\.__next_f\.push\((\[[^\n]+?\])\)", html):
        payloads.append(m.group(1))
    return payloads


def find_json_object_in_string(s: str, needle: str) -> Optional[str]:
    idx = s.find(needle)
    if idx == -1:
        return None
    start = s.rfind("{", 0, idx)
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def extract_json_blocks(html: str, needle: str) -> List[Dict]:
    blocks = []
    for payload in extract_push_payloads(html):
        try:
            data = json.loads(payload)
        except Exception:
            continue
        for part in data:
            if isinstance(part, str) and needle in part:
                obj_str = find_json_object_in_string(part, needle)
                if obj_str:
                    try:
                        blocks.append(json.loads(obj_str))
                    except Exception:
                        continue
    return blocks


def gryphline_list(lang: str) -> List[Dict]:
    url = f"https://endfield.gryphline.com/{lang}/news"
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    blocks = extract_json_blocks(r.text, "\"bulletins\"")
    for b in blocks:
        if isinstance(b, dict) and "bulletins" in b:
            return b.get("bulletins") or []
    return []


def _concat_rsc_stream(html: str) -> str:
    """Concatenate all ``self.__next_f.push([1, "..."])`` string fragments
    into a single RSC stream.  Push payloads are chunked at ~2 KiB by
    Next.js, so bulletin JSON may span multiple push calls.

    Fragments are joined *without* extra separators — the T-blob parser
    in ``_parse_rsc_lines`` handles boundaries correctly.
    """
    parts: List[str] = []
    for p in extract_push_payloads(html):
        try:
            data = json.loads(p)
        except Exception:
            continue
        for item in data:
            if isinstance(item, str):
                parts.append(item)
    return "".join(parts)


def _resolve_rsc_text_blob(stream: str, ref_id: str) -> str:
    """Resolve an RSC ``$<ref_id>`` text-blob reference.

    Text blobs are encoded as ``<ref_id>:T<hex_byte_len>,<content>`` where
    *content* spans exactly *hex_byte_len* **UTF-8 bytes** and may contain
    newlines.  The blob may also be split across push-payload boundaries,
    so we search the concatenated stream.
    """
    # Look for the marker  ``\n<ref_id>:T``  or at stream start
    for prefix in (f"\n{ref_id}:T", f"{ref_id}:T"):
        idx = stream.find(prefix)
        if idx == -1:
            continue
        t_pos = idx + len(prefix) - 1  # position of 'T'
        after_t = stream[t_pos + 1:]   # after 'T'
        comma = after_t.find(",")
        if comma == -1:
            continue
        try:
            byte_len = int(after_t[:comma], 16)
        except ValueError:
            continue
        content_start = t_pos + 1 + comma + 1
        # Walk chars until we've consumed byte_len UTF-8 bytes
        byte_count = 0
        ci = content_start
        while ci < len(stream) and byte_count < byte_len:
            byte_count += len(stream[ci].encode("utf-8"))
            ci += 1
        return stream[content_start:ci]
    return ""


def _find_rsc_bulletin(stream: str, cid: str) -> Optional[Dict]:
    """Search the RSC stream for a bulletin matching *cid*.

    Uses a direct regex search for the bulletin wrapper JSON
    ``{"value":{"bulletin":{..."cid":"<cid>"...}}}`` anywhere in the
    concatenated stream.
    """
    pattern = r'\{"value":\s*\{"bulletin":\s*\{[^}]*"cid"\s*:\s*"' + re.escape(cid) + r'"'
    m = re.search(pattern, stream)
    if not m:
        return None
    start = m.start()
    # Extract the full wrapper object by brace-matching
    depth = 0
    for i in range(start, len(stream)):
        if stream[i] == "{":
            depth += 1
        elif stream[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    wrapper = json.loads(stream[start:i + 1])
                    return (wrapper.get("value") or {}).get("bulletin")
                except Exception:
                    return None
    return None


def _extract_rsc_bulletin(html: str, cid: str) -> Dict:
    """Parse the RSC stream to find the bulletin dict for *cid*.

    The bulletin lives inside an RSC component array:
    ``<line_id>:["$","$L<xx>",null,{"value":{"bulletin":{...}}}]``

    For long articles the ``"data"`` field may be an RSC ``$<ref>``
    reference pointing to a separate text blob in the stream.
    """
    stream = _concat_rsc_stream(html)

    bulletin = _find_rsc_bulletin(stream, cid)
    if not bulletin:
        return {}

    # Resolve RSC $-reference in 'data' field
    data = bulletin.get("data") or ""
    if isinstance(data, str) and re.fullmatch(r"\$[0-9a-f]+", data):
        resolved = _resolve_rsc_text_blob(stream, data[1:])
        if resolved:
            bulletin["data"] = resolved
    return bulletin


def gryphline_detail(lang: str, cid: str) -> Dict:
    url = f"https://endfield.gryphline.com/{lang}/news/{cid}"
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return _extract_rsc_bulletin(r.text, cid)


def gryphline_process(game: str, lang: str, state: Dict[str, Dict], cutoff_ts: Optional[int] = None) -> Tuple[List[Dict], List[Tuple[str, Dict]]]:
    discovered = []
    to_fetch = []

    allowed = set(GRYPHLINE_GAMES[game]["categories"])
    for item in gryphline_list(lang):
        if item.get("tab") not in allowed:
            continue
        cid = str(item.get("cid"))
        ts = int(item.get("displayTime") or 0)
        key = composite_key("gryphline", game, cid)
        discovered.append({"key": key, "effective_ts": ts})
        prev = state.get(key)
        if prev is None or ts > int(prev.get("last_modified", 0)):
            to_fetch.append((cid, {"effective_ts": ts, "listing": item}))
        elif cutoff_ts and ts >= cutoff_ts:
            to_fetch.append((cid, {"effective_ts": ts, "listing": item}))
    return discovered, to_fetch


def gryphline_build_item(game: str, lang: str, cid: str, detail: Dict, effective_ts: int, listing: Optional[Dict] = None) -> Dict:
    url = f"https://endfield.gryphline.com/{lang}/news/{cid}"
    listing = listing or {}
    title = detail.get("title") or listing.get("title") or ""
    author = detail.get("author") or listing.get("author") or "Arknights: Endfield"
    content = detail.get("data") or listing.get("data") or ""
    category = detail.get("tab") or listing.get("tab") or "news"
    published = int(detail.get("displayTime") or listing.get("displayTime") or effective_ts)
    image = detail.get("cover") or listing.get("cover") or None
    summary = detail.get("brief") or listing.get("brief") or None
    return {
        "id": cid,
        "platform": "gryphline",
        "game": game,
        "url": url,
        "title": title,
        "author": author,
        "content": content,
        "category": category,
        "published": to_iso(published),
        "updated": None,
        "image": image,
        "summary": summary,
        "effective_ts": effective_ts,
    }


# ---------------- Shadowverse ----------------


def direct_get(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def mirror_get(url: str) -> str:
    if url.startswith("https://"):
        target = url.replace("https://", "http://", 1)
    else:
        target = url
    mirror_url = MIRROR_PREFIX + target.split("://", 1)[1]
    r = SESSION.get(mirror_url, timeout=30)
    r.raise_for_status()
    return r.text


def fetch_html_or_text(url: str) -> Tuple[str, str]:
    for attempt in range(1, 3):
        try:
            return "html", direct_get(url)
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            if code in (403, 429, 503):
                try:
                    return "text", mirror_get(url)
                except Exception:
                    pass
            if attempt == 2:
                raise
            _retry_sleep(attempt)
        except Exception:
            if attempt == 2:
                raise
            _retry_sleep(attempt)
    return "html", ""


def is_shadowverse_article(url: str) -> bool:
    if not url.startswith(SHADOWVERSE_BASE_URL):
        return False
    path = url[len(SHADOWVERSE_BASE_URL) :].strip("/")
    if not path:
        return False
    if path.startswith("page/") or "/page/" in path:
        return False
    non_articles = {
        "cards",
        "decks",
        "collection",
        "builder",
        "tier-list",
        "events",
        "articles",
        "classes",
        "guides",
        "meta",
        "sets",
        "tournaments",
        "about",
        "contact",
        "privacy",
        "terms",
        "login",
        "news",
        "feed",
        "wp-json",
    }
    first = path.split("/")[0]
    if first.startswith("wp-"):
        return False
    if first in non_articles:
        return False
    return bool(re.match(r"^[a-z0-9-]+(?:/[a-z0-9-]+)?/?$", path))


def find_shadowverse_links_from_home_html(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    news_h = None
    for h in soup.find_all(["h2", "h3"]):
        if h.get_text(strip=True).lower() == "news":
            news_h = h
            break
    if news_h:
        started = False
        container = news_h.parent
        for el in container.descendants:
            if el is news_h:
                started = True
                continue
            if not started:
                continue
            if isinstance(el, Tag) and el.name == "a" and el.has_attr("href"):
                href = el["href"].split("?")[0].split("#")[0]
                if is_shadowverse_article(href):
                    links.append(href)
    else:
        for a in soup.find_all("a", href=True):
            href = a["href"].split("?")[0].split("#")[0]
            if "/page/" in href:
                continue
            if is_shadowverse_article(href):
                links.append(href)
    out, seen = [], set()
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def find_shadowverse_links_from_home_text(txt: str) -> List[str]:
    content = txt.replace("\r\n", "\n")
    m = re.search(r"(?im)^##\s*News\s*$", content)
    if m:
        start = m.end()
        n = re.search(r"(?im)^\s*##\s+\S", content[start:])
        block = content[start:start + n.start()] if n else content[start:]
    else:
        block = content
    candidates = re.findall(r"\((https?://shadowverse\.gg/[^\s)]+)\)", block)
    cleaned = [u.split("?")[0].split("#")[0] for u in candidates]
    links = [u for u in cleaned if is_shadowverse_article(u)]
    out, seen = [], set()
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def shadowverse_extract_article(content: str, kind: str, url: str) -> Dict:
    title = url
    date_str = None
    author = None
    body = ""
    if kind == "html":
        m = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip() or url
        text_for_date = re.sub(r"<[^>]+>", " ", content)
        dm = re.search(
            r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}",
            text_for_date,
        )
        if dm:
            date_str = dm.group(0)
        am = re.search(r"\bBy\s+([A-Za-z0-9_.\- ]{2,})\b", text_for_date)
        if am:
            author = am.group(1).strip()
        body = text_for_date.strip()
    else:
        m = re.search(r"(?m)^#\s+(.+)$", content)
        if m:
            title = m.group(1).strip()
        dm = re.search(
            r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}",
            content,
        )
        if dm:
            date_str = dm.group(0)
        am = re.search(r"\bBy\s+([A-Za-z0-9_.\- ]{2,})\b", content)
        if am:
            author = am.group(1).strip()
        body = content.strip()

    iso_ts = None
    published_ts = None
    if date_str:
        try:
            dt = datetime.strptime(date_str, "%B %d, %Y").replace(tzinfo=timezone.utc)
            iso_ts = dt.isoformat()
            published_ts = int(dt.timestamp())
        except Exception:
            pass

    summary = body[:3200].strip() if body else ""
    return {
        "title": title,
        "author": author,
        "summary": summary,
        "published": iso_ts,
        "published_ts": published_ts,
    }


def find_shadowverse_links_from_news_html(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    for article in soup.find_all("article"):
        a = article.find("a", href=True)
        if a:
            href = a["href"].split("?")[0].split("#")[0]
            if is_shadowverse_article(href):
                links.append(href)
    out, seen = [], set()
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def shadowverse_process(state: Dict[str, Dict], cutoff_ts: Optional[int] = None) -> Tuple[List[Dict], List[Tuple[str, Dict]]]:
    kind, home = fetch_html_or_text(SHADOWVERSE_NEWS_URL)
    if kind == "html":
        links = find_shadowverse_links_from_news_html(home)
        if not links:
            links = find_shadowverse_links_from_home_html(home)
    else:
        links = find_shadowverse_links_from_home_text(home)
    discovered, to_fetch = [], []
    for url in links:
        key = composite_key("shadowverse", SHADOWVERSE_GAME, url)
        discovered.append({"key": key, "effective_ts": 0})
        if key not in state:
            to_fetch.append((url, {"effective_ts": 0}))
        elif cutoff_ts:
            to_fetch.append((url, {"effective_ts": 0}))
    return discovered, to_fetch


# ---------------- Main Flow ----------------


def main() -> None:
    refresh_runtime_config()
    webhook_url = os.environ.get(WEBHOOK_ENV, "").strip()
    if not webhook_url:
        raise SystemExit("Missing env var WEBHOOK_URL_NEWS")

    state = load_state()
    first_run_for_game = bool(ONLY_GAME) and is_first_run_for_game(state, ONLY_GAME)

    cutoff_ts = get_last_hours_cutoff()
    log("INFO", f"Starting news scraper. ONLY_GAME={ONLY_GAME or 'all'} DRY_RUN={DRY_RUN}")
    if cutoff_ts:
        log("INFO", f"Time filter: last {RUN_LAST_HOURS_RAW}h (cutoff={to_iso(cutoff_ts)})")
    log("INFO", f"State path: {STATE_PATH}")
    log("INFO", f"Language: {DEFAULT_LANGUAGE}")

    target_games = set()
    if ONLY_GAME:
        target_games.add(ONLY_GAME)
    else:
        target_games.update(HOYOLAB_GAMES.keys())
        target_games.update(GRYPHLINE_GAMES.keys())
        target_games.add(SHADOWVERSE_GAME)

    all_items: List[Dict] = []
    totals = {"discovered": 0, "to_fetch": 0, "sent": 0, "skipped": 0, "failed": 0}

    # HoYoLAB
    for game in HOYOLAB_GAMES:
        if game not in target_games:
            continue
        discovered, to_fetch = hoyolab_process(game, DEFAULT_LANGUAGE, state, cutoff_ts)
        totals["discovered"] += len(discovered)
        totals["to_fetch"] += len(to_fetch)
        log("INFO", f"HoYoLAB/{game}: discovered={len(discovered)} to_fetch={len(to_fetch)}")
        for post_id, meta in to_fetch:
            detail = hoyolab_fetch_detail(HOYOLAB_GAMES[game]["gids"], post_id, DEFAULT_LANGUAGE)
            item = hoyolab_build_item(game, detail, meta["effective_ts"])
            all_items.append(item)
        # baseline state for discovered items even if unchanged
        for d in discovered:
            if d["key"] not in state:
                state[d["key"]] = {"last_modified": d["effective_ts"], "last_sent_hash": ""}

    # Gryphline
    for game in GRYPHLINE_GAMES:
        if game not in target_games:
            continue
        discovered, to_fetch = gryphline_process(game, DEFAULT_LANGUAGE, state, cutoff_ts)
        totals["discovered"] += len(discovered)
        totals["to_fetch"] += len(to_fetch)
        log("INFO", f"Gryphline/{game}: discovered={len(discovered)} to_fetch={len(to_fetch)}")
        for cid, meta in to_fetch:
            detail = gryphline_detail(DEFAULT_LANGUAGE, cid)
            if not detail:
                log("WARN", f"Gryphline/{game}: detail missing for cid={cid}; using listing fallback")
            item = gryphline_build_item(game, DEFAULT_LANGUAGE, cid, detail or {}, meta["effective_ts"], meta.get("listing"))
            all_items.append(item)
        for d in discovered:
            if d["key"] not in state:
                state[d["key"]] = {"last_modified": d["effective_ts"], "last_sent_hash": ""}

    # Shadowverse
    if SHADOWVERSE_GAME in target_games:
        discovered, to_fetch = shadowverse_process(state, cutoff_ts)
        totals["discovered"] += len(discovered)
        totals["to_fetch"] += len(to_fetch)
        log("INFO", f"Shadowverse: discovered={len(discovered)} to_fetch={len(to_fetch)}")
        for url, meta in to_fetch:
            kind, content = fetch_html_or_text(url)
            article = shadowverse_extract_article(content, kind, url)
            item = {
                "id": url,
                "platform": "shadowverse",
                "game": SHADOWVERSE_GAME,
                "url": url,
                "title": article.get("title") or url,
                "author": article.get("author") or "Shadowverse.gg",
                "content": article.get("summary") or "",
                "category": "news",
                "published": article.get("published") or datetime.now(timezone.utc).isoformat(),
                "updated": None,
                "image": None,
                "summary": article.get("summary") or "",
                "effective_ts": article.get("published_ts") or meta["effective_ts"],
            }
            all_items.append(item)
        for d in discovered:
            if d["key"] not in state:
                state[d["key"]] = {"last_modified": d["effective_ts"], "last_sent_hash": ""}

    # Determine per-game first run behavior
    if first_run_for_game:
        log("INFO", f"First run for {ONLY_GAME} — baseline only, no Discord messages sent.")
        log("INFO", f"Baseline items recorded: {len(all_items)}")
        save_state(state)
        return

    # Send new/updated items
    for item in all_items:
        allow_recent_resend = bool(cutoff_ts and item.get("effective_ts", 0) >= cutoff_ts)
        if cutoff_ts and item.get("effective_ts", 0) < cutoff_ts:
            totals["skipped"] += 1
            log_item("skip", f"older than cutoff ({RUN_LAST_HOURS_RAW}h)", item)
            continue
        key = composite_key(item["platform"], item["game"], item["id"])
        stored = state.get(key, {})
        new_hash = hash_item(item)
        if stored.get("last_sent_hash") == new_hash and not allow_recent_resend:
            totals["skipped"] += 1
            log_item("skip", "unchanged (hash match)", item)
            continue
        embeds = build_embed(item)
        try:
            reason = "within cutoff (resend)" if allow_recent_resend else "new or updated"
            log_item("send" if not DRY_RUN else "would-send", reason, item)
            send_embeds(webhook_url, embeds)
            state[key] = {"last_modified": item["effective_ts"], "last_sent_hash": new_hash}
            totals["sent"] += 1
            time.sleep(1.5)
        except Exception as e:
            totals["failed"] += 1
            log("ERROR", f"Failed to send {item.get('title')}: {e}")
            time.sleep(1.5)

    save_state(state)
    log(
        "INFO",
        "Summary: discovered={discovered} to_fetch={to_fetch} sent={sent} skipped={skipped} failed={failed}".format(
            **totals
        ),
    )
    if totals["sent"] == 0:
        log("INFO", "No items sent — all items unchanged or baseline-only.")


if __name__ == "__main__":
    main()
