"""
Maine YIMBY Housing Watch — Scraper
Targets Granicus, CivicPlus, and Municode CMS platforms used by Maine municipalities.
Run: python scraper.py  (or via cron / Docker)
"""

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
import pdfplumber
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from db import get_session, RawAgendaItem, Municipality
from classifier import classify_item

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Municipality registry
# Each entry: (town, county, cms, base_url, board_filter)
# board_filter: list of substrings to match board names (case-insensitive)
# ---------------------------------------------------------------------------
MUNICIPALITIES = [
    # --- Granicus ---
    ("Portland",      "Cumberland",   "granicus",  "https://portland.granicus.com",      ["city council","planning board","zoning"]),
    ("Bangor",        "Penobscot",    "granicus",  "https://bangor.granicus.com",         ["city council","planning board"]),
    ("Augusta",       "Kennebec",     "granicus",  "https://augusta.granicus.com",        ["city council","planning board"]),
    ("Lewiston",      "Androscoggin", "granicus",  "https://lewiston.granicus.com",       ["city council","planning board"]),
    ("South Portland","Cumberland",   "granicus",  "https://southportland.granicus.com",  ["city council","planning board"]),
    ("Westbrook",     "Cumberland",   "granicus",  "https://westbrook.granicus.com",      ["city council","planning board"]),
    ("Biddeford",     "York",         "granicus",  "https://biddeford.granicus.com",      ["city council","planning board"]),
    ("Sanford",       "York",         "granicus",  "https://sanford.granicus.com",        ["city council","planning board"]),
    ("Saco",          "York",         "granicus",  "https://saco.granicus.com",           ["city council","planning board"]),

    # --- CivicPlus ---
    ("Brunswick",     "Cumberland",   "civicplus", "https://www.brunswickme.org",         ["planning board","town council"]),
    ("Scarborough",   "Cumberland",   "civicplus", "https://www.scarboroughme.org",       ["town council","planning board"]),
    ("Falmouth",      "Cumberland",   "civicplus", "https://www.falmouthme.org",          ["town council","planning board"]),
    ("Yarmouth",      "Cumberland",   "civicplus", "https://www.yarmouth.me.us",          ["town council","planning board"]),
    ("Gorham",        "Cumberland",   "civicplus", "https://www.gorhamme.org",            ["town council","planning board"]),
    ("Windham",       "Cumberland",   "civicplus", "https://www.windhamme.org",           ["town council","planning board"]),
    ("Auburn",        "Androscoggin", "civicplus", "https://www.auburnmaine.gov",         ["city council","planning board"]),
    ("Bath",          "Sagadahoc",    "civicplus", "https://www.cityofbath.com",          ["city council","planning board"]),
    ("Waterville",    "Kennebec",     "civicplus", "https://www.waterville-me.gov",       ["city council","planning board"]),
    ("Rockland",      "Knox",         "civicplus", "https://www.rocklandmaine.gov",       ["city council","planning board"]),
    ("Belfast",       "Waldo",        "civicplus", "https://www.belfastmaine.org",        ["city council","planning board"]),
    ("Ellsworth",     "Hancock",      "civicplus", "https://www.ellsworthmaine.gov",      ["city council","planning board"]),

    # --- Municode ---
    ("Kennebunk",     "York",         "municode",  "https://www.kennebunkmaine.us",       ["select board","planning board"]),
    ("Freeport",      "Cumberland",   "municode",  "https://www.freeportmaine.com",       ["town council","planning board"]),
    ("Camden",        "Knox",         "municode",  "https://www.camdenmaine.gov",         ["select board","planning board"]),
    ("Falmouth",      "Cumberland",   "municode",  "https://www.falmouthme.org",          ["town council","planning board"]),

    # --- Generic HTML (direct website scrape) ---
    ("Brewer",        "Penobscot",    "generic",   "https://www.brewermaine.gov",         ["city council","planning board"]),
    ("Old Town",      "Penobscot",    "generic",   "https://www.old-town.org",            ["city council","planning board"]),
    ("Farmington",    "Franklin",     "generic",   "https://www.farmington-maine.org",    ["select board","planning board"]),
    ("Skowhegan",     "Somerset",     "generic",   "https://www.skowhegan.org",           ["select board","planning board"]),
    ("Machias",       "Washington",   "generic",   "https://www.machias.org",             ["select board","planning board"]),
]

HOUSING_KEYWORDS = [
    "zoning", "rezoning", "comprehensive plan", "comp plan",
    "affordable housing", "workforce housing", "density", "adu",
    "accessory dwelling", "subdivision", "inclusionary", "lihtc",
    "housing trust", "mixed use", "mixed-use", "overlay district",
    "land use", "growth management", "variance", "conditional use",
    "site plan", "housing element", "fair housing", "nimby",
    "downzone", "upzone", "setback", "lot coverage", "height limit",
    "multifamily", "multi-family", "apartment", "chapter 32",
    "title 30-a", "shoreland zoning",
]


def is_housing_relevant(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in HOUSING_KEYWORDS)


def pdf_fingerprint(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def extract_pdf_text(path: Path) -> str:
    try:
        with pdfplumber.open(path) as pdf:
            return "\n".join(
                page.extract_text() or "" for page in pdf.pages[:12]
            )
    except Exception as e:
        log.warning(f"PDF extraction failed for {path}: {e}")
        return ""


async def download_pdf(url: str, client: httpx.AsyncClient) -> Optional[Path]:
    try:
        r = await client.get(url, timeout=30, follow_redirects=True)
        r.raise_for_status()
        fp = DOWNLOAD_DIR / (hashlib.md5(url.encode()).hexdigest() + ".pdf")
        fp.write_bytes(r.content)
        return fp
    except Exception as e:
        log.warning(f"PDF download failed {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Granicus scraper
# ---------------------------------------------------------------------------
async def scrape_granicus(town: str, county: str, base_url: str, board_filter: list[str]) -> list[dict]:
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(f"{base_url}/ViewPublisher.php?view_id=1", timeout=20000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            items = await page.query_selector_all("table.listingTable tr")
            for item in items:
                title_el = await item.query_selector("td.listingTitle")
                date_el  = await item.query_selector("td.listingDate")
                link_el  = await item.query_selector("a[href*='AgendaViewer'], a[href*='MetaViewer']")
                if not title_el:
                    continue
                title = (await title_el.inner_text()).strip()
                date  = (await date_el.inner_text()).strip() if date_el else ""
                href  = await link_el.get_attribute("href") if link_el else None

                board_match = any(b in title.lower() for b in board_filter)
                if not board_match:
                    continue

                results.append({
                    "town": town, "county": county, "board": title,
                    "date": normalize_date(date), "pdf_url": href,
                    "source_url": f"{base_url}/ViewPublisher.php?view_id=1",
                })
        except Exception as e:
            log.error(f"Granicus scrape failed {town}: {e}")
        finally:
            await browser.close()
    return results


# ---------------------------------------------------------------------------
# CivicPlus scraper
# ---------------------------------------------------------------------------
async def scrape_civicplus(town: str, county: str, base_url: str, board_filter: list[str]) -> list[dict]:
    results = []
    agenda_url = f"{base_url}/AgendaCenter"
    try:
        async with httpx.AsyncClient(headers={"User-Agent": "MaineYIMBY/1.0"}) as client:
            r = await client.get(agenda_url, timeout=20, follow_redirects=True)
            soup = BeautifulSoup(r.text, "html.parser")
            for row in soup.select("ul.catAgendaRow li, div.listingBlock"):
                title = row.get_text(strip=True)
                link  = row.find("a", href=re.compile(r"\.pdf|AgendaViewer", re.I))
                date_m = re.search(r"\d{1,2}/\d{1,2}/\d{4}", title)

                if not any(b in title.lower() for b in board_filter):
                    continue

                results.append({
                    "town": town, "county": county, "board": extract_board_name(title),
                    "date": normalize_date(date_m.group() if date_m else ""),
                    "pdf_url": (base_url + link["href"]) if link else None,
                    "source_url": agenda_url,
                })
    except Exception as e:
        log.error(f"CivicPlus scrape failed {town}: {e}")
    return results


# ---------------------------------------------------------------------------
# Municode scraper (JS-heavy, use Playwright)
# ---------------------------------------------------------------------------
async def scrape_municode(town: str, county: str, base_url: str, board_filter: list[str]) -> list[dict]:
    results = []
    agenda_url = f"{base_url}/government/boards-committees-commissions"
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(agenda_url, timeout=20000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            links = await page.query_selector_all("a[href*='agenda'], a[href*='Agenda']")
            for link in links:
                text = (await link.inner_text()).strip()
                href = await link.get_attribute("href")
                if not any(b in text.lower() for b in board_filter):
                    continue
                date_m = re.search(r"\d{1,2}/\d{1,2}/\d{4}", text)
                results.append({
                    "town": town, "county": county, "board": extract_board_name(text),
                    "date": normalize_date(date_m.group() if date_m else ""),
                    "pdf_url": href if href and href.endswith(".pdf") else None,
                    "source_url": agenda_url,
                })
        except Exception as e:
            log.error(f"Municode scrape failed {town}: {e}")
        finally:
            await browser.close()
    return results


# ---------------------------------------------------------------------------
# Generic HTML scraper (fallback)
# ---------------------------------------------------------------------------
async def scrape_generic(town: str, county: str, base_url: str, board_filter: list[str]) -> list[dict]:
    results = []
    search_paths = [
        "/agendas", "/meetings", "/government/agendas-minutes",
        "/boards-committees", "/city-council/agendas", "/planning-board",
    ]
    async with httpx.AsyncClient(headers={"User-Agent": "MaineYIMBY/1.0"}) as client:
        for path in search_paths:
            try:
                r = await client.get(base_url + path, timeout=15, follow_redirects=True)
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=re.compile(r"\.pdf", re.I)):
                    text = a.get_text(strip=True)
                    href = a.get("href", "")
                    if not href.startswith("http"):
                        href = base_url + href
                    if not any(b in text.lower() for b in board_filter):
                        continue
                    date_m = re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text)
                    results.append({
                        "town": town, "county": county, "board": extract_board_name(text),
                        "date": normalize_date(date_m.group() if date_m else ""),
                        "pdf_url": href,
                        "source_url": base_url + path,
                    })
                if results:
                    break
            except Exception:
                continue
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def normalize_date(raw: str) -> str:
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def extract_board_name(text: str) -> str:
    for board in ["City Council", "Town Council", "Select Board", "Planning Board",
                  "Zoning Board", "Board of Appeals", "Project Review Board"]:
        if board.lower() in text.lower():
            return board
    return text[:60].strip()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
SCRAPERS = {
    "granicus":  scrape_granicus,
    "civicplus": scrape_civicplus,
    "municode":  scrape_municode,
    "generic":   scrape_generic,
}

async def process_item(raw: dict, client: httpx.AsyncClient, session) -> None:
    pdf_text = ""
    if raw.get("pdf_url"):
        pdf_path = await download_pdf(raw["pdf_url"], client)
        if pdf_path:
            pdf_text = extract_pdf_text(pdf_path)
            pdf_path.unlink(missing_ok=True)

    combined = f"{raw.get('board','')} {pdf_text[:3000]}"
    if not is_housing_relevant(combined):
        return

    fingerprint = hashlib.sha256(
        f"{raw['town']}{raw['date']}{raw['board']}".encode()
    ).hexdigest()

    existing = session.query(RawAgendaItem).filter_by(fingerprint=fingerprint).first()
    if existing:
        return

    classification = await classify_item(
        title=raw.get("board", ""),
        text=pdf_text[:4000],
        town=raw["town"],
        county=raw["county"],
        date=raw.get("date", ""),
    )
    if not classification.get("relevant"):
        return

    record = RawAgendaItem(
        fingerprint=fingerprint,
        town=raw["town"],
        county=raw["county"],
        board=raw.get("board", ""),
        date=raw.get("date", ""),
        title=classification.get("title", raw.get("board", "")),
        summary=classification.get("summary", ""),
        tags=",".join(classification.get("tags", [])),
        urgency=classification.get("urgency", "low"),
        yimby_opportunity=classification.get("yimby_opportunity", ""),
        source_url=raw.get("source_url", ""),
        pdf_url=raw.get("pdf_url", ""),
        scraped_at=datetime.utcnow(),
    )
    session.add(record)
    session.commit()
    log.info(f"  + Saved: {raw['town']} / {record.title[:60]}")


async def run_scraper():
    log.info(f"=== Maine YIMBY scraper run started at {datetime.utcnow()} ===")
    async with httpx.AsyncClient(headers={"User-Agent": "MaineYIMBY/1.0"}) as client:
        session = get_session()
        for town, county, cms, base_url, board_filter in MUNICIPALITIES:
            log.info(f"Scraping {town} ({cms})")
            try:
                scraper_fn = SCRAPERS[cms]
                raw_items = await scraper_fn(town, county, base_url, board_filter)
                log.info(f"  Found {len(raw_items)} raw items")
                for raw in raw_items:
                    await process_item(raw, client, session)
                await asyncio.sleep(2)  # polite delay between towns
            except Exception as e:
                log.error(f"  Failed {town}: {e}")
        session.close()
    log.info("=== Scraper run complete ===")


if __name__ == "__main__":
    asyncio.run(run_scraper())
