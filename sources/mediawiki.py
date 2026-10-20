# -*- coding: utf-8 -*-
"""Minimal MediaWiki API client and template parser (Fandom wikis)."""

import re
from typing import Dict, List, Optional

from sources.common import http_get_json

BATCH = 50


def category_members(api: str, category: str, limit: int = 20) -> List[str]:
    """Newest-first titles (main namespace only) in a category."""
    data = http_get_json(
        api,
        {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": category,
            "cmlimit": limit,
            "cmsort": "timestamp",
            "cmdir": "desc",
            "cmnamespace": 0,
            "format": "json",
            "formatversion": 2,
        },
    )
    return [m["title"] for m in data.get("query", {}).get("categorymembers", [])]


def page_wikitexts(api: str, titles: List[str]) -> Dict[str, str]:
    """Fetch the wikitext of pages, one request per batch of 50 titles."""
    out: Dict[str, str] = {}
    for i in range(0, len(titles), BATCH):
        batch = titles[i : i + BATCH]
        data = http_get_json(
            api,
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "formatversion": 2,
                "format": "json",
                "titles": "|".join(batch),
            },
        )
        for page in data.get("query", {}).get("pages", []):
            revs = page.get("revisions") or []
            if not revs:
                continue
            content = (revs[0].get("slots") or {}).get("main", {}).get("content")
            if content is not None:
                out[page["title"]] = content
    return out


def parse_template(wikitext: str, name: str) -> Optional[Dict[str, str]]:
    """Params of the first {{name ...}} (case-insensitive), nested {{...}}/[[...]] kept intact."""
    start_re = re.compile(r"\{\{\s*" + re.escape(name) + r"\s*(?=[|}\n]|$)", re.I)
    m = start_re.search(wikitext)
    if not m:
        return None

    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    i = m.end()
    n = len(wikitext)
    closed = False
    while i < n:
        two = wikitext[i : i + 2]
        if two in ("{{", "[["):
            depth += 1
            buf.append(two)
            i += 2
        elif two in ("}}", "]]"):
            if depth == 0:
                if two == "}}":
                    closed = True
                    break
                buf.append("]")
                i += 1
                continue
            depth -= 1
            buf.append(two)
            i += 2
        elif wikitext[i] == "|" and depth == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(wikitext[i])
            i += 1
    if not closed:
        return None
    parts.append("".join(buf))

    params: Dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key:
            params[key] = value.strip()
    return params
