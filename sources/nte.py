# -*- coding: utf-8 -*-
"""Neverness to Everness: boards (banners) from the Fandom wiki, events from official notices."""

import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from news_scraper import nte_fetch_article, nte_list
from sources.common import Item, SourceResult, parse_local, parse_offset
from sources.mediawiki import category_members, page_wikitexts, parse_template

API = "https://neverness-to-everness.fandom.com/api.php"
WIKI = "https://neverness-to-everness.fandom.com/wiki/"
WIKI_SOURCE = "neverness-to-everness.fandom.com"
OFFICIAL_SOURCE = "nte.perfectworld.com"
OFFICIAL_URL = "https://nte.perfectworld.com/en/main.html"
CAT_BOARDS = "Category:Boards_by_Date_Descending"

RELEVANT_TITLE_RE = re.compile(r"Patch Notes|Maintenance Notice|Update Notice", re.I)
LOOKBACK_DAYS = 45
AFTER_UPDATE_TIME = "11:00"
SERVER_OFFSET = 8

_DATE_SUFFIX_RE = re.compile(r"/\d{4}-\d{2}-\d{2}$")
_EVENT_RE = re.compile(r'^\W*["“”](?P<name>.+?)["“”]\s+Limited-Time Event\s*$')
_DURATION_RE = re.compile(
    r"(?:Duration|Available):\s*"
    r"(?P<m1>[A-Z][a-z]+) (?P<d1>\d{1,2})(?:, (?P<y1>\d{4}))?"
    r"(?:, (?P<t1>\d{2}:\d{2})| \(after update\))"
    r"\s*[–-]\s*"
    r"(?P<m2>[A-Z][a-z]+) (?P<d2>\d{1,2})(?:, (?P<y2>\d{4}))?, (?P<t2>\d{2}:\d{2})"
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"],
        1,
    )
}


# ---------------- Fandom boards ----------------


def _offset(value: Optional[str]) -> float:
    value = (value or "").strip()
    if not value:
        return SERVER_OFFSET
    parsed = parse_offset(value.replace("GMT", "UTC"), default=float("nan"))
    return SERVER_OFFSET if parsed != parsed else parsed


def _date(value: Optional[str], offset: float) -> Optional[datetime]:
    try:
        return parse_local((value or "").strip(), offset)
    except ValueError:
        return None


def _names(value: Optional[str]) -> List[str]:
    return [v.strip() for v in (value or "").split(";") if v.strip()]


def nte_fair_items(wikitexts: Dict[str, str]) -> List[Item]:
    items: List[Item] = []
    for title, text in wikitexts.items():
        fair = parse_template(text, "Fair")
        if fair is None:
            continue
        if (fair.get("type") or "").strip().lower() == "standard":
            continue
        start_off = _offset(fair.get("time_start_offset"))
        end_off = _offset(fair.get("time_end_offset")) if (fair.get("time_end_offset") or "").strip() else start_off
        start = _date(fair.get("time_start"), start_off)
        end = _date(fair.get("time_end"), end_off)
        if start is None and end is None:
            continue
        pool = parse_template(text, "Board Pool") or {}
        feat = _names(pool.get("character_S_F"))
        if not feat:
            # Arc Research Programs list "Name*Display" pairs
            feat = [n.split("*")[-1].strip() for n in _names(pool.get("arc_S_F"))]
        details = f"S: {', '.join(feat)}" if feat else None
        link = (fair.get("link") or "").strip() or WIKI + title.replace(" ", "_")
        items.append(
            Item(
                label=_DATE_SUFFIX_RE.sub("", title),
                start=start,
                end=end,
                link=link,
                details=details,
            )
        )
    return items


def nte_gacha(now: datetime) -> SourceResult:
    titles = category_members(API, CAT_BOARDS)
    texts = page_wikitexts(API, titles) if titles else {}
    return SourceResult(items=nte_fair_items(texts), source_name=WIKI_SOURCE, source_url=WIKI)


# ---------------- Official notices ----------------


def _when(month: str, day: str, year: int, hhmm: str) -> datetime:
    return parse_local(f"{year:04d}-{_MONTHS[month]:02d}-{int(day):02d} {hhmm}", SERVER_OFFSET)


def nte_parse_article_events(article_html: str, article_day: str) -> List[Item]:
    soup = BeautifulSoup(article_html, "html.parser")
    body = soup.select_one("div.articleContent")
    if body is None:
        return []
    lines = [ln.strip() for ln in body.get_text("\n").split("\n")]
    base = datetime.strptime(article_day, "%Y-%m-%d")
    items: List[Item] = []
    for idx, line in enumerate(lines):
        m = _EVENT_RE.match(line)
        if not m:
            continue
        for nxt in lines[idx + 1 : idx + 4]:
            d = _DURATION_RE.search(nxt)
            if not d:
                continue
            try:
                y1 = int(d["y1"]) if d["y1"] else base.year
                start_year = y1
                start = _when(d["m1"], d["d1"], y1, d["t1"] or AFTER_UPDATE_TIME)
                if not d["y1"]:
                    # a date months before the article is the next year's (Dec article, Jan event)
                    naive = datetime(y1, _MONTHS[d["m1"]], int(d["d1"]))
                    if naive < base - timedelta(days=180):
                        start = _when(d["m1"], d["d1"], y1 + 1, d["t1"] or AFTER_UPDATE_TIME)
                        start_year = y1 + 1
                y2 = int(d["y2"]) if d["y2"] else start_year
                end = _when(d["m2"], d["d2"], y2, d["t2"])
                if not d["y2"] and end < start:
                    end = _when(d["m2"], d["d2"], y2 + 1, d["t2"])
            except (KeyError, ValueError):
                break
            items.append(Item(label=m["name"].strip(), start=start, end=end))
            break
    return items


def nte_events(now: datetime) -> SourceResult:
    cutoff = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    listing = [
        a
        for a in nte_list()
        if RELEVANT_TITLE_RE.search(a.get("title", "")) and a.get("time", "") >= cutoff
    ]
    by_label: Dict[str, Item] = {}
    # oldest first so the newest article overwrites older duplicates
    for art in sorted(listing, key=lambda a: a["time"]):
        try:
            found = nte_parse_article_events(nte_fetch_article(art["url"]), art["time"])
        except Exception:
            continue
        for it in found:
            by_label[it.label] = Item(
                label=it.label, start=it.start, end=it.end, link=art["url"], details=it.details
            )
    return SourceResult(items=list(by_label.values()), source_name=OFFICIAL_SOURCE, source_url=OFFICIAL_URL)
