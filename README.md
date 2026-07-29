# Gold & Fuel Price Tracker

A lightweight, **GitHub-only** Python project that scrapes daily **gold/silver** and **petrol/diesel** prices in India and publishes them as public JSON files — no database, no server, no API keys.

Every day, a GitHub Action runs the scrapers, updates the JSON files, and commits the result. Consumers can fetch prices from GitHub raw URLs.

## Features

- Scrapes **22K gold**, **24K gold**, and **silver** prices (INR per gram)
- Scrapes **Last 10 Days** and **Today / Yesterday / Change** for gold & silver
- Scrapes **petrol** and **diesel** prices for metro cities + all states (INR per litre)
- Stores latest prices + historical daily entries in JSON
- Avoids duplicate history for the same date
- Runs automatically every day at **9:00 AM IST** via GitHub Actions
- Exposes free public JSON APIs via GitHub raw content

## Project structure

```text
gold-price-tracker/
├── scraper.py              # Gold & silver scraper
├── fuel_scraper.py         # Petrol & diesel scraper
├── gold_price.json
├── fuel_price.json
├── requirements.txt
├── README.md
└── .github/
    └── workflows/
        └── update-gold.yml
```

## How it works

1. `scraper.py` fetches gold/silver rates from [Goodreturns gold](https://www.goodreturns.in/gold-rates/).
2. `fuel_scraper.py` fetches petrol/diesel rates from [petrol](https://www.goodreturns.in/petrol-price.html) and [diesel](https://www.goodreturns.in/diesel-price.html) pages.
3. Results are written to `gold_price.json` and `fuel_price.json`.
4. GitHub Actions commits and pushes both files when prices change.

## JSON format

```json
{
  "last_updated": "2026-07-18",
  "source": "Goodreturns",
  "source_url": "https://www.goodreturns.in/gold-rates/",
  "unit": "INR",
  "prices": {
    "22k_gold": 13135,
    "24k_gold": 14329,
    "silver": 230,
    "unit": "INR per gram"
  },
  "price_change": {
    "gold": [
      {
        "weight_gram": 1,
        "today": { "24k_gold": 14329, "22k_gold": 13135, "18k_gold": 10747 },
        "yesterday": { "24k_gold": 14253, "22k_gold": 13065, "18k_gold": 10690 },
        "change": { "24k_gold": 76, "22k_gold": 70, "18k_gold": 57 }
      }
    ],
    "silver": [
      { "weight_gram": 1, "today": 230, "yesterday": 230, "change": 0 }
    ]
  },
  "last_10_days": {
    "gold": [
      {
        "date": "2026-07-18",
        "24k_gold": 14329,
        "22k_gold": 13135,
        "change_24k": 76,
        "change_22k": 70
      }
    ],
    "silver": [
      {
        "date": "2026-07-18",
        "silver_1g": 230,
        "silver_10g": 2300,
        "silver_100g": 23000,
        "silver_1kg": 230000,
        "change_1kg": 0
      }
    ]
  },
  "history": [
    {
      "date": "2026-07-18",
      "22k_gold": 13135,
      "24k_gold": 14329,
      "silver": 230
    }
  ]
}
```

Useful fields for apps:

| Field | Use |
| --- | --- |
| `prices` | Latest 22K / 24K / silver per gram |
| `price_change` | Today vs yesterday by weight (1g, 8g, 10g, …) |
| `last_10_days` | Last 10 daily rows for gold & silver |
| `history` | Repo-owned cumulative history from each Action run |

## Fuel JSON format (`fuel_price.json`)

```json
{
  "last_updated": "2026-07-18",
  "source": "Goodreturns",
  "unit": "INR per litre",
  "prices": {
    "petrol": {
      "new_delhi": 102.12,
      "mumbai": 111.21,
      "chennai": 107.76,
      "kolkata": 113.51,
      "bangalore": 111.68,
      "hyderabad": 115.69
    },
    "diesel": {
      "new_delhi": 95.2,
      "mumbai": 97.83,
      "chennai": 99.55,
      "kolkata": 99.82,
      "bangalore": 99.56,
      "hyderabad": 103.82
    }
  },
  "cities": {
    "petrol": [
      { "city": "New Delhi", "price": 102.12, "yesterday": 102.12, "change": 0.0 }
    ],
    "diesel": [
      { "city": "New Delhi", "price": 95.2, "yesterday": 95.2, "change": 0.0 }
    ]
  },
  "states": {
    "petrol": [
      { "state": "Delhi", "price": 102.12, "yesterday": 102.12, "change": 0.0 }
    ],
    "diesel": [
      { "state": "Delhi", "price": 95.2, "yesterday": 95.2, "change": 0.0 }
    ]
  },
  "history": [
    {
      "date": "2026-07-18",
      "petrol_delhi": 102.12,
      "diesel_delhi": 95.2,
      "petrol_mumbai": 111.21,
      "diesel_mumbai": 97.83
    }
  ]
}
```

| Field | Use |
| --- | --- |
| `prices.petrol` / `prices.diesel` | Quick metro summary (Delhi, Mumbai, …) |
| `cities` | Full metro/city list with today / yesterday / change |
| `states` | All Indian states with today / yesterday / change |
| `history` | Repo-owned daily snapshot of major cities |

## Public JSON APIs

After publishing this repo to GitHub, replace `USERNAME` with your GitHub username:

```text
# Gold & silver
https://raw.githubusercontent.com/USERNAME/gold-price-tracker/main/gold_price.json

# Petrol & diesel
https://raw.githubusercontent.com/USERNAME/gold-price-tracker/main/fuel_price.json
```

### cURL

```bash
curl -s https://raw.githubusercontent.com/USERNAME/gold-price-tracker/main/gold_price.json
curl -s https://raw.githubusercontent.com/USERNAME/gold-price-tracker/main/fuel_price.json
```

### JavaScript / React

```javascript
async function fetchPrices(path) {
  const url = `https://raw.githubusercontent.com/USERNAME/gold-price-tracker/main/${path}`;
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`Failed: ${response.status}`);
  return response.json();
}

const gold = await fetchPrices("gold_price.json");
console.log("22K gold:", gold.prices["22k_gold"]);

const fuel = await fetchPrices("fuel_price.json");
console.log("Petrol Delhi:", fuel.prices.petrol.new_delhi);
console.log("Diesel Mumbai:", fuel.prices.diesel.mumbai);
```

### Flutter / Dart

```dart
import 'dart:convert';
import 'package:http/http.dart' as http;

Future<Map<String, dynamic>> fetchJson(String file) async {
  final uri = Uri.parse(
    'https://raw.githubusercontent.com/USERNAME/gold-price-tracker/main/$file',
  );
  final response = await http.get(uri);
  if (response.statusCode != 200) {
    throw Exception('Failed to load $file: ${response.statusCode}');
  }
  return jsonDecode(response.body) as Map<String, dynamic>;
}

final gold = await fetchJson('gold_price.json');
final fuel = await fetchJson('fuel_price.json');
print('Petrol Delhi: ${fuel['prices']['petrol']['new_delhi']}');
print('Diesel Delhi: ${fuel['prices']['diesel']['new_delhi']}');
```

> Tip: GitHub raw content may be cached. Add a cache-buster query if you need fresher client reads, e.g. `...?t=${Date.now()}`.

## GitHub Actions

Workflow file: [`.github/workflows/update-gold.yml`](./.github/workflows/update-gold.yml)

| Trigger | When |
| --- | --- |
| `schedule` | Every day at **9:00 AM IST** (`cron: 30 3 * * *` UTC) |
| `workflow_dispatch` | Manual run from the Actions tab |

What the workflow does:

1. Checks out the repository
2. Sets up **Python 3.12**
3. Installs dependencies from `requirements.txt`
4. Runs `python scraper.py` and `python fuel_scraper.py`
5. Commits and pushes `gold_price.json` + `fuel_price.json` when prices change

### Enable the Action after publishing

1. Push this repository to GitHub (public).
2. Open **Actions** and enable workflows if prompted.
3. Run **Update Prices** once with **Run workflow** to verify.
4. Ensure the default branch is `main` (or update the raw URL / workflow push target).

Scheduled workflows on GitHub can be delayed by a few minutes under load. That is normal.

## Local usage

Requires **Python 3.12+**.

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python scraper.py        # gold + silver
python fuel_scraper.py   # petrol + diesel
```

### Optional environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `GOLD_URL` | `https://www.goodreturns.in/gold-rates/` | Gold rates page |
| `SILVER_URL` | `https://www.goodreturns.in/silver-rates/` | Silver rates page |
| `PETROL_URL` | `https://www.goodreturns.in/petrol-price.html` | Petrol rates page |
| `DIESEL_URL` | `https://www.goodreturns.in/diesel-price.html` | Diesel rates page |
| `SOURCE_NAME` | `Goodreturns` | Value stored in JSON `source` |
| `OUTPUT_FILE` | `gold_price.json` | Gold scraper output path |
| `FUEL_OUTPUT_FILE` | `fuel_price.json` | Fuel scraper output path |
| `REQUEST_TIMEOUT` | `30` | HTTP timeout in seconds |

## Error handling

The scrapers are designed to fail safely in CI:

- Network / HTTP errors are caught and reported
- Missing or implausible values raise an error and exit non-zero
- Same-day re-runs update the existing history row instead of duplicating it

If the source website redesigns its markup, GitHub Actions will fail — check the Action logs and adjust selectors in `scraper.py` / `fuel_scraper.py`.

## Disclaimer

Prices are scraped from a third-party public website for informational purposes only. Gold rates may differ from local jewellers; fuel rates may differ by pump/city. Always verify before making financial decisions.

## License

MIT — free to use, modify, and publish.
