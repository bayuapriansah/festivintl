#!/usr/bin/env python
# award_force_daily_export.py
"""
Creative Force (Award Force v2.3) → Excel → Telegram

• Endpoint  : https://api.us.cr4ce.com
• Output    : award_force_stage1_YYYYMMDD.xlsx  (new file each run)
• Logging   : logs.txt
• Schedule  : use GitHub Actions (.github/workflows/daily-award-force.yml)

Author   : Bayu ‹ChatGPT›   • 2025-07-22
Python   : ≥3.9  (uses zoneinfo from std-lib)
"""

from __future__ import annotations
import os
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List

import requests
import pandas as pd
from zoneinfo import ZoneInfo
from dotenv import load_dotenv   # ← loads .env if present

# ────────────────────────────────────────────────────────────
# 1. ENV + CONSTANTS
# ────────────────────────────────────────────────────────────
load_dotenv()                                  # .env in cwd (ignored in CI)

BASE_URL  = "https://api.us.cr4ce.com"
API_KEY   = os.getenv("CF_API_KEY", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")  # numeric

CATEGORY_SLUGS: Dict[str, str] = {
    "13_17":  "ZLgyzemp",   # 13 – 17 Years
    "above18": "Kgwrlowa",  # Above 18 Years
}

REGION_MAP: Dict[str, List[str]] = {
    "AMR": [
        "argentina", "brazil", "canada", "costa rica",
        "mexico", "united states of america",
    ],
    "PRC": ["china"],
    "APJ": [
        "bangladesh", "india", "indonesia", "japan", "malaysia",
        "singapore", "south korea", "taiwan", "thailand",
        "vietnam", "australia", "new zealand",
    ],
}

LOCAL_TZ   = ZoneInfo("Asia/Jakarta")
LOG_FILE   = Path("logs.txt")

HEADERS = {
    "Accept":         "application/vnd.Creative Force.v2.3+json",
    "x-api-key":      API_KEY,
    "x-api-language": "en_GB",
}

# ────────────────────────────────────────────────────────────
# 2. LOGGING
# ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, "a", "utf-8"),
        logging.StreamHandler(),
    ],
)

# ────────────────────────────────────────────────────────────
# 3. HELPER FUNCTIONS
# ────────────────────────────────────────────────────────────
def cf_get_all(path: str, params: dict | None = None) -> List[dict]:
    """GET and follow `next_page_url` until depleted; return combined data list."""
    url   = f"{BASE_URL}{path}"
    items: List[dict] = []
    while url:
        r   = requests.get(url, headers=HEADERS, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        items.extend(body["data"])
        url, params = body.get("next_page_url") or None, None
    return items


def chapter_to_region(name: str) -> str:
    lc = name.lower()
    if lc == "global festival":
        return "Other"
    for region, countries in REGION_MAP.items():
        if lc in countries:
            return region
    return "EMEA"


# ────────────────────────────────────────────────────────────
# 4. DATA COLLECTION
# ────────────────────────────────────────────────────────────
def fetch_chapters() -> Dict[str, Dict[str, str]]:
    chapters: Dict[str, Dict[str, str]] = {}
    for ch in cf_get_all("/chapter", {"status": "active", "per_page": 100}):
        nm = ch["name"]["en_GB"]
        chapters[ch["slug"]] = {"name": nm, "region": chapter_to_region(nm)}
    logging.info("Loaded %d chapters", len(chapters))
    return chapters


def gather_entries(category_slug: str) -> List[dict]:
    logging.info("Pulling entries for category %s …", category_slug)
    return cf_get_all("/entry", {"category": category_slug, "per_page": 100})


def build_counts(chapters: Dict[str, Dict[str, str]]) -> Dict[str, dict]:
    counts = {
        slug: {"13_sub": 0, "13_prog": 0, "18_sub": 0, "18_prog": 0}
        for slug in chapters
    }

    # 13–17
    for e in gather_entries(CATEGORY_SLUGS["13_17"]):
        ch, st = e["chapter"]["slug"], e["status"]
        if ch in counts:
            counts[ch]["13_sub" if st == "submitted" else "13_prog"] += 1

    # 18+
    for e in gather_entries(CATEGORY_SLUGS["above18"]):
        ch, st = e["chapter"]["slug"], e["status"]
        if ch in counts:
            counts[ch]["18_sub" if st == "submitted" else "18_prog"] += 1

    return counts


# ────────────────────────────────────────────────────────────
# 5. FILE CREATION + TELEGRAM SEND
# ────────────────────────────────────────────────────────────
def make_workbook(rows: List[dict]) -> Path:
    tag = datetime.now(LOCAL_TZ).strftime("%Y%m%d")
    file_path = Path(f"award_force_stage1_{tag}.xlsx")

    cols = [
        "No", "Region", "Chapter Name",
        "13–17 Years (Submitted)", "13–17 Years (In Progress)",
        "Above 18 Years (Submitted)", "Above 18 Years (In Progress)",
        "Total",
    ]
    pd.DataFrame(rows)[cols].to_excel(file_path, index=False, sheet_name="Stage 1")
    return file_path


def send_to_telegram(file_path: Path):
    if not (BOT_TOKEN and CHAT_ID):
        logging.error("BOT_TOKEN or TELEGRAM_CHAT_ID missing – not sending to Telegram.")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    with open(file_path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": CHAT_ID, "caption": file_path.name},
            files={
                "document": (
                    file_path.name,
                    f,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            timeout=60,
        )
    if resp.ok:
        logging.info("Sent %s to Telegram chat %s", file_path.name, CHAT_ID)
    else:
        logging.error("Telegram upload failed: %s", resp.text)


# ────────────────────────────────────────────────────────────
# 6. MAIN
# ────────────────────────────────────────────────────────────
def main() -> None:
    if not API_KEY:
        logging.error("CF_API_KEY is missing. Aborting.")
        return

    logging.info("=== RUN START ===")
    try:
        chapters = fetch_chapters()
        counts   = build_counts(chapters)

        # Compose & sort
        rows: List[dict] = []
        for i, (slug, meta) in enumerate(
                sorted(chapters.items(), key=lambda kv: (kv[1]["region"], kv[1]["name"].lower())),
                start=1):
            c = counts[slug]
            rows.append({
                "No": i,
                "Region": meta["region"],
                "Chapter Name": meta["name"],
                "13–17 Years (Submitted)":      c["13_sub"],
                "13–17 Years (In Progress)":    c["13_prog"],
                "Above 18 Years (Submitted)":   c["18_sub"],
                "Above 18 Years (In Progress)": c["18_prog"],
                "Total": sum(c.values()),
            })

        wb_path = make_workbook(rows)
        logging.info("Workbook saved → %s", wb_path)
        send_to_telegram(wb_path)

    except Exception as exc:
        logging.exception("Run failed: %s", exc)

    logging.info("=== RUN END ===\n")


if __name__ == "__main__":
    main()
