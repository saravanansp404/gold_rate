#!/usr/bin/env python3
"""
Petrol & Diesel Price Tracker scraper.

Fetches daily petrol and diesel prices (INR per litre) for Indian metro cities
and states from a public website, then updates fuel_price.json.
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
# Configuration
# ---------------------------------------------------------------------------

PETROL_URL = os.getenv("PETROL_URL", "https://www.goodreturns.in/petrol-price.html")
DIESEL_URL = os.getenv("DIESEL_URL", "https://www.goodreturns.in/diesel-price.html")
SOURCE_NAME = os.getenv("SOURCE_NAME", "Goodreturns")
SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_OUTPUT = SCRIPT_DIR / "fuel_price.json"
_OUTPUT_ENV = os.getenv("FUEL_OUTPUT_FILE")
OUTPUT_FILE = Path(_OUTPUT_ENV) if _OUTPUT_ENV else _DEFAULT_OUTPUT
if not OUTPUT_FILE.is_absolute():
    OUTPUT_FILE = (Path.cwd() / OUTPUT_FILE).resolve()
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (compatible; FuelPriceTracker/1.0; +https://github.com/)",
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
logger = logging.getLogger("fuel_price_tracker")


class ScrapeError(Exception):
    """Raised when fuel prices cannot be extracted reliably."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_html(url: str) -> str:
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


def parse_price(text: str) -> Optional[float]:
    """Parse values like '₹102.12', '111.21' into float INR."""
    if not text:
        return None
    cleaned = re.sub(r"\([^)]*\)", "", text)
    cleaned = cleaned.replace("\xa0", " ").replace(",", "").replace("₹", "")
    cleaned = cleaned.replace("Rs.", "").replace("Rs", "").replace("/ Ltr", "")
    cleaned = cleaned.replace("/Ltr", "").replace("INR", "").strip()
    match = re.search(r"(-?\d+(?:\.\d+)?)", cleaned)
    if not match:
        return None
    try:
        return round(float(match.group(1)), 2)
    except ValueError:
        return None


def parse_change(text: str) -> Optional[float]:
    """Parse change values like '+0.03', '-1.20', '0.00'."""
    if text is None:
        return None
    cleaned = text.replace(",", "").replace("₹", "").strip()
    if not cleaned or cleaned in {"—", "-", "–"}:
        return None
    match = re.search(r"([+-]?\d+(?:\.\d+)?)", cleaned)
    if not match:
        return None
    try:
        return round(float(match.group(1)), 2)
    except ValueError:
        return None


def today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def _table_headers(table: Tag) -> list[str]:
    return [th.get_text(" ", strip=True) for th in table.select("thead th")]


def _find_table_by_heading(soup: BeautifulSoup, *needles: str) -> Optional[Tag]:
    for h2 in soup.select("h2"):
        title = h2.get_text(" ", strip=True).lower()
        if all(n.lower() in title for n in needles):
            section = h2.find_parent("section") or h2.parent
            if section:
                table = section.find("table")
                if table:
                    return table
    return None


def extract_page_date(html: str) -> Optional[str]:
    """Parse dates like '18th Jul, 2026' from the page title/intro."""
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    match = re.search(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9}),?\s+(\d{4})",
        text,
    )
    if not match:
        return None
    day, month, year = match.groups()
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(f"{int(day)} {month} {year}", fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

def _extract_location_table(
    soup: BeautifulSoup,
    *,
    heading_needles: tuple[str, ...],
    location_key: str,
) -> list[dict[str, Any]]:
    table = _find_table_by_heading(soup, *heading_needles)
    if table is None:
        return []

    headers = [h.upper() for h in _table_headers(table)]
    try:
        col_loc = next(
            i
            for i, h in enumerate(headers)
            if location_key.upper() in h or h in {"CITY", "STATE"}
        )
        col_price = next(i for i, h in enumerate(headers) if "PRICE" in h and "CHANGE" not in h)
        col_change = next(i for i, h in enumerate(headers) if "CHANGE" in h)
    except StopIteration:
        logger.warning("Unexpected fuel table headers: %s", headers)
        return []

    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if len(cells) <= max(col_loc, col_price, col_change):
            continue

        location = cells[col_loc].get_text(" ", strip=True)
        price = parse_price(cells[col_price].get_text(" ", strip=True))
        change = parse_change(cells[col_change].get_text(" ", strip=True))
        if not location or price is None:
            continue

        yesterday = round(price - change, 2) if change is not None else None
        rows.append(
            {
                location_key: location,
                "price": price,
                "yesterday": yesterday,
                "change": change if change is not None else 0.0,
            }
        )

    return rows


def extract_fuel_prices(html: str, fuel_type: str) -> dict[str, Any]:
    """Extract metro cities + state-wise prices for petrol or diesel."""
    soup = BeautifulSoup(html, "lxml")
    fuel = fuel_type.lower()

    cities = _extract_location_table(
        soup,
        heading_needles=("metro cities",),
        location_key="city",
    )
    if not cities:
        cities = _extract_location_table(
            soup,
            heading_needles=("state capitals",),
            location_key="city",
        )

    states = _extract_location_table(
        soup,
        heading_needles=("state-wise",),
        location_key="state",
    )
    if not states:
        states = _extract_location_table(
            soup,
            heading_needles=("state", fuel),
            location_key="state",
        )

    if not cities:
        raise ScrapeError(f"Could not extract {fuel} city prices (HTML may have changed).")
    if not states:
        raise ScrapeError(f"Could not extract {fuel} state prices (HTML may have changed).")

    # Sanity: Indian pump prices are typically 60–150 INR/L
    for row in cities + states:
        if not (50.0 <= float(row["price"]) <= 200.0):
            raise ScrapeError(f"Implausible {fuel} price for {row}: {row['price']}")

    page_date = extract_page_date(html)
    return {
        "fuel": fuel,
        "date": page_date,
        "cities": cities,
        "states": states,
    }


def build_summary(cities: list[dict[str, Any]]) -> dict[str, Optional[float]]:
    """Map major cities to a compact prices object."""
    by_name = {row["city"].strip().lower(): row["price"] for row in cities}
    aliases = {
        "new_delhi": ["new delhi", "delhi"],
        "mumbai": ["mumbai"],
        "chennai": ["chennai"],
        "kolkata": ["kolkata"],
        "bangalore": ["bangalore", "bengaluru"],
        "hyderabad": ["hyderabad"],
    }

    summary: dict[str, Optional[float]] = {}
    for key, names in aliases.items():
        summary[key] = next((by_name[n] for n in names if n in by_name), None)
    return summary


# ---------------------------------------------------------------------------
# JSON storage
# ---------------------------------------------------------------------------

def load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "last_updated": None,
            "source": SOURCE_NAME,
            "unit": "INR per litre",
            "prices": {"petrol": {}, "diesel": {}},
            "cities": {"petrol": [], "diesel": []},
            "states": {"petrol": [], "diesel": []},
            "history": [],
        }

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise ScrapeError(f"Failed to read {path}: {exc}") from exc

    data.setdefault("history", [])
    data.setdefault("prices", {"petrol": {}, "diesel": {}})
    data.setdefault("cities", {"petrol": [], "diesel": []})
    data.setdefault("states", {"petrol": [], "diesel": []})
    return data


def update_store(data: dict[str, Any], scraped: dict[str, Any]) -> dict[str, Any]:
    date = scraped["date"]
    petrol = scraped["petrol"]
    diesel = scraped["diesel"]

    data["last_updated"] = date
    data["source"] = SOURCE_NAME
    data["source_url"] = {
        "petrol": PETROL_URL,
        "diesel": DIESEL_URL,
    }
    data["unit"] = "INR per litre"
    data["prices"] = {
        "petrol": petrol["summary"],
        "diesel": diesel["summary"],
    }
    data["cities"] = {
        "petrol": petrol["cities"],
        "diesel": diesel["cities"],
    }
    data["states"] = {
        "petrol": petrol["states"],
        "diesel": diesel["states"],
    }

    history: list[dict[str, Any]] = data.get("history", [])
    entry = {
        "date": date,
        "petrol_delhi": petrol["summary"].get("new_delhi"),
        "diesel_delhi": diesel["summary"].get("new_delhi"),
        "petrol_mumbai": petrol["summary"].get("mumbai"),
        "diesel_mumbai": diesel["summary"].get("mumbai"),
        "petrol_chennai": petrol["summary"].get("chennai"),
        "diesel_chennai": diesel["summary"].get("chennai"),
        "petrol_bangalore": petrol["summary"].get("bangalore"),
        "diesel_bangalore": diesel["summary"].get("bangalore"),
    }

    existing_index = next(
        (i for i, item in enumerate(history) if item.get("date") == date),
        None,
    )
    if existing_index is None:
        history.append(entry)
        logger.info("Appended fuel history entry for %s", date)
    else:
        history[existing_index] = entry
        logger.info("Updated existing fuel history entry for %s (no duplicate)", date)

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

def scrape_fuel_prices() -> dict[str, Any]:
    logger.info("Fetching petrol prices from %s", PETROL_URL)
    petrol_html = fetch_html(PETROL_URL)
    petrol_raw = extract_fuel_prices(petrol_html, "petrol")
    petrol_summary = build_summary(petrol_raw["cities"])
    logger.info(
        "Petrol: cities=%s states=%s delhi=%s",
        len(petrol_raw["cities"]),
        len(petrol_raw["states"]),
        petrol_summary.get("new_delhi"),
    )

    logger.info("Fetching diesel prices from %s", DIESEL_URL)
    diesel_html = fetch_html(DIESEL_URL)
    diesel_raw = extract_fuel_prices(diesel_html, "diesel")
    diesel_summary = build_summary(diesel_raw["cities"])
    logger.info(
        "Diesel: cities=%s states=%s delhi=%s",
        len(diesel_raw["cities"]),
        len(diesel_raw["states"]),
        diesel_summary.get("new_delhi"),
    )

    date = petrol_raw["date"] or diesel_raw["date"] or today_ist()

    if petrol_summary.get("new_delhi") is None:
        raise ScrapeError("Missing New Delhi petrol price in summary.")
    if diesel_summary.get("new_delhi") is None:
        raise ScrapeError("Missing New Delhi diesel price in summary.")

    return {
        "date": date,
        "petrol": {
            "summary": petrol_summary,
            "cities": petrol_raw["cities"],
            "states": petrol_raw["states"],
        },
        "diesel": {
            "summary": diesel_summary,
            "cities": diesel_raw["cities"],
            "states": diesel_raw["states"],
        },
        "scraped_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def main() -> int:
    try:
        scraped = scrape_fuel_prices()
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
