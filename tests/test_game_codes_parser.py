import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from game_codes_scraper import parse_codes

FIXTURES = ROOT / "tests" / "fixtures"


def _fx(name): return (FIXTURES / "codes" / f"{name}.html").read_text(encoding="utf-8")


def test_pockettactics_nte():
    codes = [c["code"] for c in parse_codes(_fx("pockettactics_nte"))]
    assert len(codes) == 9
    assert codes[0] == "WONDERLOGGIFT" and "NTEKokushi" in codes


def test_pockettactics_aniimo_preserves_case():
    items = parse_codes(_fx("pockettactics_aniimo"))
    codes = [c["code"] for c in items]
    assert len(codes) == 16
    assert {"aniimazingfroyo", "Aniimo2026", "ANIIMOGIFT"} <= set(codes)
    assert items[0]["reward"] == "20 glimmer (US servers only) (new!)"


def test_pockettactics_endfield():
    assert [c["code"] for c in parse_codes(_fx("pockettactics_endfield"))] == [
        "AKENDFIELDSUB", "ENDFIELDRENEW", "ZAU2SYXHWX5L4ZH", "ENDFIELDGIFT", "ENDFIELD4PC"]


def test_pocketgamer_skips_howto_steps():
    codes = [c["code"] for c in parse_codes(_fx("pocketgamer_aniimo"))]
    assert len(codes) == 14 and not any(c.lower().startswith("step") for c in codes)


def test_stops_at_expired_heading():
    html = "<article><h2>Active</h2><ul><li><strong>NEWONE</strong> - 10 gems</li></ul>" \
           "<h3>Expired codes</h3><ul><li><strong>OLDONE</strong> - 5 gems</li></ul></article>"
    assert [c["code"] for c in parse_codes(html)] == ["NEWONE"]


def test_skips_struck_through():
    html = "<article><ul><li><s><strong>DEADCODE</strong> - x</s></li><li><strong>LIVECODE</strong> - y</li></ul></article>"
    assert [c["code"] for c in parse_codes(html)] == ["LIVECODE"]


def test_dedupes_case_insensitive():
    html = "<article><ul><li><strong>aniimoidyll</strong> - a</li><li><strong>aniimoidyLL</strong> - a</li></ul></article>"
    assert [c["code"] for c in parse_codes(html)] == ["aniimoidyll"]


def test_no_list_returns_empty():
    assert parse_codes("<html><body>Just a moment...</body></html>") == []


def test_stops_at_expired_paragraph():
    html = "<article><ul><li><strong>NEWONE</strong> - 10 gems</li></ul>" \
           "<p>Expired codes:</p><ul><li><strong>OLDONE</strong> - 5 gems</li></ul></article>"
    assert [c["code"] for c in parse_codes(html)] == ["NEWONE"]
