#!/usr/bin/env python3
"""
Gold Price Tracker scraper.

Fetches daily 22K/24K gold and silver prices (INR) from a public website,
including last-10-days history and today/yesterday/change tables, then
updates gold_price.json.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup, Tag

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[misc, assignment]

# ---------------------------------------------------------------------------
# Configuration (override with environment variables)
# ---------------------------------------------------------------------------

GOLD_URL = os.getenv("GOLD_URL", "https://www.goodreturns.in/gold-rates/")
SILVER_URL = os.getenv("SILVER_URL", "https://www.goodreturns.in/silver-rates/")
SOURCE_NAME = os.getenv("SOURCE_NAME", "Goodreturns")
SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_OUTPUT = SCRIPT_DIR / "gold_price.json"
_OUTPUT_ENV = os.getenv("OUTPUT_FILE")
OUTPUT_FILE = Path(_OUTPUT_ENV) if _OUTPUT_ENV else _DEFAULT_OUTPUT
if not OUTPUT_FILE.is_absolute():
    OUTPUT_FILE = (Path.cwd() / OUTPUT_FILE).resolve()
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (compatible; GoldPriceTracker/1.0; +https://github.com/)",
)

try:
    IST = ZoneInfo("Asia/Kolkata") if ZoneInfo else timezone(timedelta(hours=5, minutes=30))
except Exception:  # noqa: BLE001
    IST = timezone(timedelta(hours=5, minutes=30))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("gold_price_tracker")


class ScrapeError(Exception):
    """Raised when prices cannot be extracted reliably."""


# ---------------------------------------------------------------------------
# HTTP / parsing helpers
# ---------------------------------------------------------------------------

def fetch_html(url: str) -> str:
    """Fetch a page and return its HTML. Raises ScrapeError on request failure."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        return response.text
    except requests.Timeout as exc:
        raise ScrapeError(f"Request timed out for {url}") from exc
    except requests.RequestException as exc:
        raise ScrapeError(f"Failed to fetch {url}: {exc}") from exc


def parse_inr_amount(text: str) -> Optional[int]:
    """Parse Indian-formatted currency into an integer rupee amount."""
    if not text:
        return None

    cleaned = re.sub(r"\([^)]*\)", "", text)
    cleaned = cleaned.replace("\xa0", " ").replace(",", "").replace("₹", "")
    cleaned = cleaned.replace("Rs.", "").replace("Rs", "").replace("INR", "")
    cleaned = cleaned.strip()

    match = re.search(r"(-?\d+(?:\.\d+)?)", cleaned)
    if not match:
        return None

    try:
        return int(round(float(match.group(1))))
    except ValueError:
        return None


def parse_change(text: str) -> Optional[int]:
    """Parse a price change from '(+76)', '(-11)', '0', or '+76'."""
    if text is None:
        return None

    paren = re.search(r"\(([+-]?\d[\d,]*)\)", text)
    raw = paren.group(1) if paren else text.strip()
    raw = raw.replace(",", "").replace("₹", "").strip()
    if not raw or raw in {"—", "-", "–"}:
        return None

    match = re.search(r"([+-]?\d+(?:\.\d+)?)", raw)
    if not match:
        return 0 if raw == "0" else None

    try:
        return int(round(float(match.group(1))))
    except ValueError:
        return None


def parse_display_date(text: str) -> Optional[str]:
    """Convert 'Jul 18, 2026' style dates to YYYY-MM-DD."""
    text = (text or "").strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def today_ist() -> str:
    """Return today's date in Asia/Kolkata as YYYY-MM-DD."""
    return datetime.now(IST).date().isoformat()


def _table_headers(table: Tag) -> list[str]:
    return [th.get_text(" ", strip=True) for th in table.select("thead th")]


def _find_table_by_heading(soup: BeautifulSoup, *needles: str) -> Optional[Tag]:
    """Find the first table under an h2 whose text contains all needles."""
    for h2 in soup.select("h2"):
        title = h2.get_text(" ", strip=True).lower()
        if all(n.lower() in title for n in needles):
            section = h2.find_parent("section") or h2.parent
            if section:
                table = section.find("table")
                if table:
                    return table
    return None


# ---------------------------------------------------------------------------
# Current price extractors
# ---------------------------------------------------------------------------

def _extract_from_price_cards(soup: BeautifulSoup) -> dict[str, Optional[int]]:
    result: dict[str, Optional[int]] = {"22k_gold": None, "24k_gold": None}

    for card in soup.select(".gr-price-card"):
        label_el = card.select_one(".gr-price-card-label")
        value_el = card.select_one(".gr-price-card-value")
        if not label_el or not value_el:
            continue

        label = label_el.get_text(" ", strip=True).upper()
        value = parse_inr_amount(value_el.get_text(" ", strip=True))
        if value is None:
            continue

        if "24K" in label or "24 K" in label:
            result["24k_gold"] = value
        elif "22K" in label or "22 K" in label:
            result["22k_gold"] = value

    return result


def _extract_from_gold_table(soup: BeautifulSoup) -> dict[str, Optional[int]]:
    result: dict[str, Optional[int]] = {"22k_gold": None, "24k_gold": None}
    table = _find_table_by_heading(soup, "today gold price per gram")
    tables = [table] if table else soup.select("table.gr-table, table")

    for candidate in tables:
        if candidate is None:
            continue
        headers = [h.upper() for h in _table_headers(candidate)]
        if not headers:
            continue
        try:
            col_24 = next(i for i, h in enumerate(headers) if "24K" in h)
            col_22 = next(i for i, h in enumerate(headers) if "22K" in h)
        except StopIteration:
            continue

        for row in candidate.select("tbody tr"):
            cells = row.find_all("td")
            if len(cells) <= max(col_24, col_22):
                continue
            if cells[0].get_text(" ", strip=True).replace(",", "") != "1":
                continue
            result["24k_gold"] = parse_inr_amount(cells[col_24].get_text(" ", strip=True))
            result["22k_gold"] = parse_inr_amount(cells[col_22].get_text(" ", strip=True))
            return result

    return result


def _extract_gold_from_text(html: str) -> dict[str, Optional[int]]:
    result: dict[str, Optional[int]] = {"22k_gold": None, "24k_gold": None}
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    patterns = {
        "24k_gold": [
            r"₹\s*([\d,]+)\s*per\s*gram\s*for\s*24\s*karat",
            r"24\s*karat[^₹]{0,40}₹\s*([\d,]+)",
        ],
        "22k_gold": [
            r"₹\s*([\d,]+)\s*per\s*gram\s*for\s*22\s*karat",
            r"22\s*karat[^₹]{0,40}₹\s*([\d,]+)",
        ],
    }
    for key, pats in patterns.items():
        for pat in pats:
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                result[key] = parse_inr_amount(match.group(1))
                break
    return result


def extract_gold_prices(html: str) -> dict[str, int]:
    soup = BeautifulSoup(html, "lxml")
    candidates = [
        _extract_from_price_cards(soup),
        _extract_from_gold_table(soup),
        _extract_gold_from_text(html),
    ]

    merged: dict[str, Optional[int]] = {"22k_gold": None, "24k_gold": None}
    for candidate in candidates:
        for key in merged:
            if merged[key] is None and candidate.get(key) is not None:
                merged[key] = candidate[key]

    missing = [k for k, v in merged.items() if v is None]
    if missing:
        raise ScrapeError(
            f"Could not extract gold prices. Missing: {', '.join(missing)}. "
            "The source HTML may have changed."
        )

    for key, value in merged.items():
        assert value is not None
        if value < 1000 or value > 100_000:
            raise ScrapeError(f"Implausible {key} value: {value}")

    return {"22k_gold": merged["22k_gold"], "24k_gold": merged["24k_gold"]}  # type: ignore[dict-item]


def _extract_silver_from_cards(soup: BeautifulSoup) -> Optional[int]:
    for card in soup.select(".gr-price-card"):
        label_el = card.select_one(".gr-price-card-label")
        value_el = card.select_one(".gr-price-card-value")
        if not label_el or not value_el:
            continue
        label = label_el.get_text(" ", strip=True).upper().replace(" ", "")
        if "SILVER" in label and "/G" in label:
            return parse_inr_amount(value_el.get_text(" ", strip=True))
    return None


def _extract_silver_from_table(soup: BeautifulSoup) -> Optional[int]:
    table = _find_table_by_heading(soup, "today silver price")
    if not table:
        return None
    headers = [h.upper() for h in _table_headers(table)]
    today_col = next((i for i, h in enumerate(headers) if "TODAY" in h), 1)
    for row in table.select("tbody tr"):
        cells = row.find_all("td")
        if len(cells) <= today_col:
            continue
        if cells[0].get_text(" ", strip=True).replace(",", "") != "1":
            continue
        return parse_inr_amount(cells[today_col].get_text(" ", strip=True))
    return None


def _extract_silver_from_text(html: str) -> Optional[int]:
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    patterns = [
        r"₹\s*([\d,]+)\s*per\s*gram",
        r"silver[^₹]{0,60}₹\s*([\d,]+)\s*per\s*gram",
    ]
    for pat in patterns:
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            value = parse_inr_amount(match.group(1))
            if value is not None and 10 <= value <= 10_000:
                return value
    return None


def extract_silver_price(html: str) -> int:
    soup = BeautifulSoup(html, "lxml")
    value = (
        _extract_silver_from_cards(soup)
        or _extract_silver_from_table(soup)
        or _extract_silver_from_text(html)
    )
    if value is None:
        raise ScrapeError(
            "Could not extract silver price. The source HTML may have changed."
        )
    if value < 10 or value > 10_000:
        raise ScrapeError(f"Implausible silver value: {value}")
    return value


# ---------------------------------------------------------------------------
# Last 10 days + Today / Yesterday / Change
# ---------------------------------------------------------------------------

def extract_gold_last_10_days(html: str) -> list[dict[str, Any]]:
    """Gold Rate in India for Last 10 Days (1 gram)."""
    soup = BeautifulSoup(html, "lxml")
    table = _find_table_by_heading(soup, "last 10 days")
    if table is None:
        logger.warning("Gold last-10-days table not found")
        return []

    headers = [h.upper() for h in _table_headers(table)]
    try:
        col_date = next(i for i, h in enumerate(headers) if "DATE" in h)
        col_24 = next(i for i, h in enumerate(headers) if "24K" in h)
        col_22 = next(i for i, h in enumerate(headers) if "22K" in h)
    except StopIteration:
        logger.warning("Unexpected gold last-10-days headers: %s", headers)
        return []

    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if len(cells) <= max(col_date, col_24, col_22):
            continue

        date_text = cells[col_date].get_text(" ", strip=True)
        date_iso = parse_display_date(date_text)
        text_24 = cells[col_24].get_text(" ", strip=True)
        text_22 = cells[col_22].get_text(" ", strip=True)
        price_24 = parse_inr_amount(text_24)
        price_22 = parse_inr_amount(text_22)
        if not date_iso or price_24 is None or price_22 is None:
            continue

        rows.append(
            {
                "date": date_iso,
                "date_label": date_text,
                "24k_gold": price_24,
                "22k_gold": price_22,
                "change_24k": parse_change(text_24),
                "change_22k": parse_change(text_22),
            }
        )

    return rows


def extract_silver_last_10_days(html: str) -> list[dict[str, Any]]:
    """Silver Rate in India for Last 10 Days (normalized to per gram too)."""
    soup = BeautifulSoup(html, "lxml")
    table = _find_table_by_heading(soup, "silver rate", "last 10 days")
    if table is None:
        # Fallback: heading may omit "Silver Rate"
        table = _find_table_by_heading(soup, "last 10 days")
    if table is None:
        logger.warning("Silver last-10-days table not found")
        return []

    headers = [h.upper() for h in _table_headers(table)]
    try:
        col_date = next(i for i, h in enumerate(headers) if "DATE" in h)
        col_10 = next(i for i, h in enumerate(headers) if "10" in h and "GRAM" in h)
        col_100 = next(i for i, h in enumerate(headers) if "100" in h and "GRAM" in h)
        col_kg = next(i for i, h in enumerate(headers) if "KG" in h or "1 KG" in h)
    except StopIteration:
        logger.warning("Unexpected silver last-10-days headers: %s", headers)
        return []

    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if len(cells) <= max(col_date, col_10, col_100, col_kg):
            continue

        date_text = cells[col_date].get_text(" ", strip=True)
        date_iso = parse_display_date(date_text)
        text_10 = cells[col_10].get_text(" ", strip=True)
        text_100 = cells[col_100].get_text(" ", strip=True)
        text_kg = cells[col_kg].get_text(" ", strip=True)
        per_10g = parse_inr_amount(text_10)
        per_100g = parse_inr_amount(text_100)
        per_kg = parse_inr_amount(text_kg)
        if not date_iso or per_10g is None:
            continue

        per_gram = int(round(per_10g / 10))
        rows.append(
            {
                "date": date_iso,
                "date_label": date_text,
                "silver_1g": per_gram,
                "silver_10g": per_10g,
                "silver_100g": per_100g,
                "silver_1kg": per_kg,
                "change_1kg": parse_change(text_kg),
            }
        )

    return rows


def extract_gold_price_change(html: str, last_10: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Today / Yesterday / Price Change for gold by weight.

    Source table has Gram + karat prices with inline change; yesterday is
    derived from the last-10-days 1g row when available.
    """
    soup = BeautifulSoup(html, "lxml")
    table = _find_table_by_heading(soup, "today gold price per gram")
    if table is None:
        logger.warning("Gold today/change table not found")
        return []

    headers = [h.upper() for h in _table_headers(table)]
    try:
        col_gram = next(i for i, h in enumerate(headers) if "GRAM" in h)
        col_24 = next(i for i, h in enumerate(headers) if "24K" in h)
        col_22 = next(i for i, h in enumerate(headers) if "22K" in h)
        col_18 = next((i for i, h in enumerate(headers) if "18K" in h), None)
    except StopIteration:
        logger.warning("Unexpected gold price-change headers: %s", headers)
        return []

    yesterday_1g_24 = last_10[1]["24k_gold"] if len(last_10) > 1 else None
    yesterday_1g_22 = last_10[1]["22k_gold"] if len(last_10) > 1 else None

    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        needed = max(col_gram, col_24, col_22 if col_18 is None else col_18)
        if len(cells) <= needed:
            continue

        weight = parse_inr_amount(cells[col_gram].get_text(" ", strip=True))
        if weight is None:
            continue

        text_24 = cells[col_24].get_text(" ", strip=True)
        text_22 = cells[col_22].get_text(" ", strip=True)
        today_24 = parse_inr_amount(text_24)
        today_22 = parse_inr_amount(text_22)
        change_24 = parse_change(text_24)
        change_22 = parse_change(text_22)
        if today_24 is None or today_22 is None:
            continue

        yesterday_24 = (
            yesterday_1g_24 * weight
            if yesterday_1g_24 is not None
            else (today_24 - change_24 if change_24 is not None else None)
        )
        yesterday_22 = (
            yesterday_1g_22 * weight
            if yesterday_1g_22 is not None
            else (today_22 - change_22 if change_22 is not None else None)
        )

        item: dict[str, Any] = {
            "weight_gram": weight,
            "today": {"24k_gold": today_24, "22k_gold": today_22},
            "yesterday": {"24k_gold": yesterday_24, "22k_gold": yesterday_22},
            "change": {"24k_gold": change_24, "22k_gold": change_22},
        }

        if col_18 is not None and len(cells) > col_18:
            text_18 = cells[col_18].get_text(" ", strip=True)
            today_18 = parse_inr_amount(text_18)
            change_18 = parse_change(text_18)
            item["today"]["18k_gold"] = today_18
            item["change"]["18k_gold"] = change_18
            item["yesterday"]["18k_gold"] = (
                today_18 - change_18 if today_18 is not None and change_18 is not None else None
            )

        rows.append(item)

    return rows


def extract_silver_price_change(html: str) -> list[dict[str, Any]]:
    """Today / Yesterday / Change table for silver by weight."""
    soup = BeautifulSoup(html, "lxml")
    table = _find_table_by_heading(soup, "today silver price")
    if table is None:
        logger.warning("Silver today/yesterday/change table not found")
        return []

    headers = [h.upper() for h in _table_headers(table)]
    try:
        col_gram = next(i for i, h in enumerate(headers) if "GRAM" in h)
        col_today = next(i for i, h in enumerate(headers) if "TODAY" in h)
        col_yday = next(i for i, h in enumerate(headers) if "YESTERDAY" in h)
        col_change = next(i for i, h in enumerate(headers) if "CHANGE" in h)
    except StopIteration:
        logger.warning("Unexpected silver price-change headers: %s", headers)
        return []

    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if len(cells) <= max(col_gram, col_today, col_yday, col_change):
            continue

        weight = parse_inr_amount(cells[col_gram].get_text(" ", strip=True))
        today = parse_inr_amount(cells[col_today].get_text(" ", strip=True))
        yesterday = parse_inr_amount(cells[col_yday].get_text(" ", strip=True))
        change = parse_change(cells[col_change].get_text(" ", strip=True))
        if weight is None or today is None:
            continue

        if change is None and yesterday is not None:
            change = today - yesterday

        rows.append(
            {
                "weight_gram": weight,
                "today": today,
                "yesterday": yesterday,
                "change": change,
            }
        )

    return rows


# ---------------------------------------------------------------------------
# JSON storage
# ---------------------------------------------------------------------------

def load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "last_updated": None,
            "source": SOURCE_NAME,
            "source_url": GOLD_URL,
            "unit": "INR",
            "prices": {"22k_gold": None, "24k_gold": None, "silver": None},
            "price_change": {"gold": [], "silver": []},
            "last_10_days": {"gold": [], "silver": []},
            "history": [],
        }

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise ScrapeError(f"Failed to read {path}: {exc}") from exc

    data.setdefault("history", [])
    data.setdefault("prices", {})
    data.setdefault("price_change", {"gold": [], "silver": []})
    data.setdefault("last_10_days", {"gold": [], "silver": []})
    return data


def update_store(data: dict[str, Any], scraped: dict[str, Any]) -> dict[str, Any]:
    """Update latest prices, lists, and append history (no duplicate dates)."""
    date = scraped["date"]
    gold_22k = scraped["22k_gold"]
    gold_24k = scraped["24k_gold"]
    silver = scraped["silver"]

    data["last_updated"] = date
    data["source"] = scraped["source"]
    data["source_url"] = scraped["source_url"]
    data["unit"] = "INR"
    data["prices"] = {
        "22k_gold": gold_22k,
        "24k_gold": gold_24k,
        "silver": silver,
        "unit": "INR per gram",
    }
    data["price_change"] = scraped["price_change"]
    data["last_10_days"] = scraped["last_10_days"]

    history: list[dict[str, Any]] = data.get("history", [])
    entry = {
        "date": date,
        "22k_gold": gold_22k,
        "24k_gold": gold_24k,
        "silver": silver,
    }

    existing_index = next(
        (i for i, item in enumerate(history) if item.get("date") == date),
        None,
    )
    if existing_index is None:
        history.append(entry)
        logger.info("Appended history entry for %s", date)
    else:
        history[existing_index] = entry
        logger.info("Updated existing history entry for %s (no duplicate)", date)

    history.sort(key=lambda item: item.get("date") or "")
    data["history"] = history
    return data


def save_store(path: Path, data: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp_path.replace(path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scrape_prices() -> dict[str, Any]:
    """Fetch and parse gold + silver prices and supporting tables."""
    logger.info("Fetching gold prices from %s", GOLD_URL)
    gold_html = fetch_html(GOLD_URL)
    gold = extract_gold_prices(gold_html)
    gold_last_10 = extract_gold_last_10_days(gold_html)
    gold_change = extract_gold_price_change(gold_html, gold_last_10)
    logger.info(
        "Gold: 22K=%s, 24K=%s | last_10=%s rows | change=%s rows",
        gold["22k_gold"],
        gold["24k_gold"],
        len(gold_last_10),
        len(gold_change),
    )

    logger.info("Fetching silver prices from %s", SILVER_URL)
    silver_html = fetch_html(SILVER_URL)
    silver = extract_silver_price(silver_html)
    silver_last_10 = extract_silver_last_10_days(silver_html)
    silver_change = extract_silver_price_change(silver_html)
    logger.info(
        "Silver: %s | last_10=%s rows | change=%s rows",
        silver,
        len(silver_last_10),
        len(silver_change),
    )

    if not gold_last_10:
        raise ScrapeError("Gold last-10-days list is empty (HTML may have changed).")
    if not silver_last_10:
        raise ScrapeError("Silver last-10-days list is empty (HTML may have changed).")
    if not gold_change:
        raise ScrapeError("Gold today/yesterday/change list is empty.")
    if not silver_change:
        raise ScrapeError("Silver today/yesterday/change list is empty.")

    return {
        "date": today_ist(),
        "22k_gold": gold["22k_gold"],
        "24k_gold": gold["24k_gold"],
        "silver": silver,
        "source": SOURCE_NAME,
        "source_url": GOLD_URL,
        "price_change": {
            "gold": gold_change,
            "silver": silver_change,
        },
        "last_10_days": {
            "gold": gold_last_10,
            "silver": silver_last_10,
        },
        "scraped_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def main() -> int:
    try:
        scraped = scrape_prices()
        store = load_store(OUTPUT_FILE)
        store = update_store(store, scraped)
        save_store(OUTPUT_FILE, store)
        logger.info("Wrote %s (last_updated=%s)", OUTPUT_FILE, store["last_updated"])
        return 0
    except ScrapeError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
