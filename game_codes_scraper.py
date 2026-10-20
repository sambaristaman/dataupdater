#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Game codes -> Discord webhook notifier (Pocket Tactics, with PocketGamer as fallback).
"""

import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup, NavigableString, Tag

CODE_RE = re.compile(r"^[A-Za-z0-9]{4,24}$")


def normalize_code(code: str) -> str:
    return re.sub(r"\s+", "", code).upper()


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _first_child(li: Tag):
    for child in li.children:
        if isinstance(child, NavigableString) and not child.strip():
            continue
        return child
    return None


def parse_codes(html: str) -> List[Dict[str, Optional[str]]]:
    """
    Extract codes from a "bold code - reward" bullet list.
    Stops at the first heading that mentions "expired".
    """
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("article") or soup

    items: List[Dict[str, Optional[str]]] = []
    seen = set()
    for el in root.find_all(["h2", "h3", "h4", "li"]):
        if el.name != "li":
            if "expired" in el.get_text(" ").lower():
                break
            continue
        if el.find_parent(["s", "del"]) or el.find(["s", "del"]):
            continue
        first = _first_child(el)
        if not isinstance(first, Tag) or first.name not in ("strong", "b"):
            continue
        code = _clean_text(first.get_text(" ")).rstrip(":-–—").strip()
        if not CODE_RE.match(code):
            continue
        key = normalize_code(code)
        if key in seen:
            continue
        seen.add(key)
        rest = _clean_text(el.get_text(" "))[len(_clean_text(first.get_text(" "))):]
        reward = rest.lstrip(" :-–—").strip()
        items.append({"code": code, "reward": reward or None})
    return items
