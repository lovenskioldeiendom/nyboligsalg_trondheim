"""
Henter prosjekter fra Finn-søkesidene for hver kommune, så går inn på
hver prosjektannonse og parser enhetstabellen. Lagrer alt i SQLite.

Kjøring:
    python -m scraper.run                  # alle kommuner
    python -m scraper.run --dry-run        # ikke skriv til DB
    python -m scraper.run --limit 3        # bare 3 prosjekter (for testing)
"""

import argparse
import logging
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs

from .config import (
    MUNICIPALITIES, USER_AGENT, DELAY_BETWEEN_REQUESTS_S, REQUEST_TIMEOUT_S,
)
from .parser import parse_project_page, extract_project_links_from_search
from .database import save_project_snapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def fetch(url: str) -> str | None:
    """Henter HTML fra en URL. Returnerer None ved feil."""
    req = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "nb-NO,nb;q=0.9",
    })
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            if resp.status != 200:
                logger.warning(f"HTTP {resp.status} for {url}")
                return None
            return resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        logger.warning(f"HTTPError {e.code} for {url}")
        return None
    except (URLError, TimeoutError) as e:
        logger.warning(f"Network error for {url}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error for {url}: {e}")
        return None


def fetch_all_unit_pages(base_url: str):
    """
    Henter alle enheter fra et prosjekt.

    Strategi:
    1. Hent HTML på vanlig måte for å få meta-info og side-1 enheter.
    2. Hvis HTML har < 14 enheter: ferdig (Finn viser 15 per side, så <14
       betyr garantert ingen paginering).
    3. Hvis HTML har ≥ 14 enheter: bruk Playwright for å klikke gjennom
       pagineringssidene og hente full liste.
    4. Hvis Playwright feiler/ikke installert: fall tilbake til HTML.

    Returnerer Project-objekt eller None ved feil.
    """
    from .parser import parse_project_page, Unit

    # Hent meta + side 1 fra HTML
    html = fetch(base_url)
    if not html:
        return None

    project = parse_project_page(html, source_url=base_url)
    html_units = list(project.units)

    # Hopp over Playwright hvis prosjektet er lite nok til at paginering
    # garantert ikke gjelder
    if len(html_units) < 14:
        logger.info(f"  {len(html_units)} enheter (under terskel — ingen paginering)")
        return project

    # Stor nok til at paginering kan være aktiv — bruk Playwright
    from .playwright_scraper import fetch_units_with_playwright
    pw_units_dicts = fetch_units_with_playwright(base_url)

    if pw_units_dicts is None:
        # Playwright feilet — bruk HTML-parsing (kun side 1)
        logger.info(f"  Playwright ga ingen data, bruker HTML-parsing ({len(html_units)} enheter)")
        return project

    if len(pw_units_dicts) <= len(html_units):
        logger.warning(
            f"  Playwright ga bare {len(pw_units_dicts)} enheter (samme som HTML). "
            f"Pagineringsknapp ble ikke klikket — kan mangle data."
        )
        return project

    # Playwright ga mer data — bytt ut units
    logger.info(f"  Playwright fant {len(pw_units_dicts)} enheter (vs {len(html_units)} i HTML)")
    project.units = [
        Unit(
            unit_id=u["unit_id"],
            floor=u.get("floor"),
            bra_m2=u.get("bra_m2"),
            bedrooms=u.get("bedrooms"),
            total_price=u.get("total_price"),
            sold=u.get("sold", False),
        )
        for u in pw_units_dicts
    ]
    return project


def gather_project_urls(municipality: dict) -> list[str]:
    """
    Henter alle project-URLer for en kommune ved å pagine gjennom søket.
    """
    base = "https://www.finn.no/realestate/newbuildings/search.html"
    urls = set()
    page = 1
    max_pages = 10  # Sikkerhet — Bærum har ~3 sider med ~50 annonser per side
    while page <= max_pages:
        url = f"{base}?location={municipality['finn_location']}&page={page}"
        logger.info(f"[{municipality['name']}] søkeside {page}: {url}")
        html = fetch(url)
        if not html:
            break
        page_urls = extract_project_links_from_search(html)
        if not page_urls:
            break
        new_count_before = len(urls)
        urls.update(page_urls)
        new_count = len(urls) - new_count_before
        logger.info(f"[{municipality['name']}] side {page}: {len(page_urls)} lenker ({new_count} nye)")
        if new_count == 0:
            break  # Ingen nye = vi har sett alle
        page += 1
        time.sleep(DELAY_BETWEEN_REQUESTS_S)
    return sorted(urls)


def scrape_municipality(municipality: dict, dry_run: bool = False,
                        limit: int | None = None) -> dict:
    """Scraper alle prosjekter for én kommune. Returnerer oppsummering."""
    summary = {"municipality": municipality["name"], "found": 0, "scraped": 0, "errors": 0}

    project_urls = gather_project_urls(municipality)
    summary["found"] = len(project_urls)
    logger.info(f"[{municipality['name']}] fant {len(project_urls)} prosjekter")

    if limit:
        project_urls = project_urls[:limit]

    for i, url in enumerate(project_urls, 1):
        logger.info(f"[{municipality['name']}] {i}/{len(project_urls)}: {url}")

        try:
            project = fetch_all_unit_pages(url)
        except Exception as e:
            logger.warning(f"Feil for {url}: {e}")
            summary["errors"] += 1
            continue

        if project is None:
            summary["errors"] += 1
            continue

        # Sett kommune (ikke automatisk fra paginering)
        project.municipality = municipality["name"]

        if not project.units:
            logger.info(f"[{municipality['name']}] ingen enheter funnet, hopper over")
            continue

        logger.info(f"[{municipality['name']}]   → {len(project.units)} enheter totalt")

        if not dry_run:
            try:
                save_project_snapshot(municipality["name"], project, url)
            except Exception as e:
                logger.warning(f"DB-feil for {url}: {e}")
                summary["errors"] += 1
                continue

        summary["scraped"] += 1
        time.sleep(DELAY_BETWEEN_REQUESTS_S)

    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Ikke skriv til DB")
    parser.add_argument("--limit", type=int, default=None,
                        help="Maks antall prosjekter per kommune (for testing)")
    parser.add_argument("--municipality", default=None,
                        help="Bare scrape én kommune (etter navn)")
    args = parser.parse_args()

    municipalities = MUNICIPALITIES
    if args.municipality:
        municipalities = [m for m in MUNICIPALITIES
                         if m["name"].lower() == args.municipality.lower()]
        if not municipalities:
            logger.error(f"Ukjent kommune: {args.municipality}")
            sys.exit(1)

    overall = []
    for muni in municipalities:
        try:
            s = scrape_municipality(muni, dry_run=args.dry_run, limit=args.limit)
            overall.append(s)
        except Exception as e:
            logger.exception(f"Uventet feil for {muni['name']}: {e}")
            overall.append({"municipality": muni["name"], "error": str(e)})

    logger.info("━━━ OPPSUMMERING ━━━")
    for s in overall:
        logger.info(f"  {s}")


if __name__ == "__main__":
    main()
