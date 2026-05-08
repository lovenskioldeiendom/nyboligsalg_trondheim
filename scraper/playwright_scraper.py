"""
Playwright-basert henting av enheter fra Finn-prosjektsider.

Bakgrunn: Finn paginerer enhetstabellen med JavaScript på klientsiden.
Hele datasettet er IKKE i HTML — det lastes dynamisk når brukeren klikker
"side 2", "side 3" osv. Derfor må vi bruke en headless nettleser.

Strategi:
1. Naviger til prosjektsiden
2. Vent til enhetstabellen er rendret
3. Les tabellen
4. Klikk "neste side"-knappen
5. Vent på at tabellen oppdateres
6. Gjenta til knappen forsvinner eller vi har sett alle enheter

Ved feil eller tomt resultat — fallback til vanlig HTML-parsing.
"""

import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)


def fetch_units_with_playwright(url: str, max_pages: int = 20,
                                page_timeout_ms: int = 20_000) -> Optional[list]:
    """
    Henter ALLE enheter fra et prosjekt ved å klikke gjennom paginering.

    Returnerer liste med dicts: {unit_id, floor, bra_m2, bedrooms, total_price, sold}
    eller None ved feil (kalleren bør falle tilbake til HTML-parsing).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright ikke installert — kan ikke pagine")
        return None

    units_by_id = {}  # unit_id -> dict; deduplisering på tvers av sider

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="nb-NO",
                viewport={"width": 1400, "height": 2400},  # Høy nok til at paginering ofte er synlig
            )
            page = context.new_page()

            try:
                page.goto(url, timeout=page_timeout_ms, wait_until="domcontentloaded")
            except Exception as e:
                logger.warning(f"Goto-feil for {url}: {e}")
                browser.close()
                return None

            # Aksepter cookies (Finn bruker Sourcepoint-iframe)
            _try_accept_cookies(page)

            # Vent på at enhetstabellen rendres
            try:
                page.wait_for_selector("table", timeout=10_000)
            except Exception:
                logger.info(f"Ingen tabell funnet for {url}")
                browser.close()
                return []

            # Loop gjennom sider
            for page_num in range(1, max_pages + 1):
                # Les nåværende tabell
                page_units = _extract_units_from_dom(page)
                if not page_units:
                    break

                new_count = 0
                for u in page_units:
                    if u["unit_id"] not in units_by_id:
                        units_by_id[u["unit_id"]] = u
                        new_count += 1

                logger.info(f"  Side {page_num}: {len(page_units)} enheter ({new_count} nye)")

                # Hvis ingen nye enheter på denne siden, vi er ferdige
                if new_count == 0:
                    break

                # Hvis side 1 har færre enn 15, paginering ikke aktiv
                if page_num == 1 and len(page_units) < 15:
                    break

                # Prøv å klikke "neste side"
                if not _click_next_page(page):
                    break

                # Vent på at tabellen oppdateres med ny data
                time.sleep(0.8)

            browser.close()
            return list(units_by_id.values())

    except Exception as e:
        logger.warning(f"Playwright-feil for {url}: {e}")
        return None


def _try_accept_cookies(page) -> None:
    """
    Aksepter cookie-banneren. Finn bruker Sourcepoint som lever i en iframe
    (sp_message_iframe_*). Vi må gå inn i iframen for å klikke.
    """
    # Vent litt på at banner laster
    page.wait_for_timeout(1500)

    # Strategi 1: Sourcepoint iframe
    try:
        for frame in page.frames:
            if "sourcepoint" in (frame.url or "").lower() or \
               "cmpv2.finn.no" in (frame.url or "") or \
               "consent" in (frame.url or "").lower():
                # Inne i iframen, finn aksepter-knappen
                for label in ["Godta alle", "Godta", "Aksepter alle", "Aksepter",
                              "Accept all", "Accept", "Tillat alle", "Tillat",
                              "Jeg godtar", "Samtykker"]:
                    try:
                        btn = frame.locator(f'button:has-text("{label}")').first
                        if btn.count() > 0:
                            btn.click(timeout=3000)
                            logger.info(f"    Cookie-banner: klikket '{label}' i iframe")
                            page.wait_for_timeout(800)
                            return
                    except Exception:
                        continue
    except Exception as e:
        logger.debug(f"Iframe cookie-håndtering feilet: {e}")

    # Strategi 2: vanlige selektorer på hovedsiden
    selectors = [
        'button:has-text("Godta alle")',
        'button:has-text("Godta")',
        'button:has-text("Aksepter")',
        'button:has-text("Accept")',
        'button[id*="cookie"]',
        '#didomi-notice-agree-button',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible(timeout=1000):
                btn.click(timeout=2000)
                logger.info(f"    Cookie-banner: klikket via {sel}")
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def _extract_units_from_dom(page) -> list[dict]:
    """
    Finn enhetstabellen i DOM-en og hent ut radene.

    Vi leter etter table med 'enhet' og 'totalpris' i headerne.
    """
    js = r"""
    () => {
      function parseInt_(s) {
        if (!s) return null;
        const cleaned = String(s).replace(/[^\d]/g, '');
        return cleaned ? parseInt(cleaned, 10) : null;
      }

      const tables = document.querySelectorAll('table');
      for (const table of tables) {
        const headers = Array.from(table.querySelectorAll('th'))
          .map(th => th.textContent.trim().toLowerCase());
        if (!headers.includes('enhet') || !headers.includes('totalpris')) continue;

        const colIdx = {};
        headers.forEach((h, i) => { colIdx[h] = i; });

        // Bruk tbody hvis det finnes — hvis ikke, alle tr som IKKE er i thead
        let rows;
        const tbody = table.querySelector('tbody');
        if (tbody) {
          rows = Array.from(tbody.querySelectorAll('tr'));
        } else {
          const thead = table.querySelector('thead');
          rows = Array.from(table.querySelectorAll('tr')).filter(r => !thead || !thead.contains(r));
        }
        rows = rows.filter(r => r.querySelectorAll('td').length > 0);

        const units = [];
        const seenInTable = new Set();
        for (const row of rows) {
          const cells = row.querySelectorAll('td, th');
          const cellText = (key) => {
            const i = colIdx[key];
            if (i === undefined || i >= cells.length) return '';
            return cells[i].textContent.trim();
          };

          const unitId = cellText('enhet');
          // Hopp over header-aktige rader og duplikater innenfor samme side
          if (!unitId || unitId.toLowerCase() === 'enhet') continue;
          if (seenInTable.has(unitId)) continue;
          seenInTable.add(unitId);

          const priceText = cellText('totalpris');
          const sold = priceText.toLowerCase().includes('solgt');

          units.push({
            unit_id: unitId,
            floor: parseInt_(cellText('etasje')),
            bra_m2: parseInt_(cellText('bra-i') || cellText('areal')),
            bedrooms: parseInt_(cellText('soverom')),
            total_price: sold ? null : parseInt_(priceText),
            sold: sold,
          });
        }
        return units;
      }
      return [];
    }
    """
    try:
        return page.evaluate(js) or []
    except Exception as e:
        logger.warning(f"DOM-uttrekk feilet: {e}")
        return []


def _click_next_page(page) -> bool:
    """
    Prøver å klikke 'neste side'-knappen for enhetstabellen.

    Finn-strukturen er typisk:
        <nav aria-labelledby="Enhetsvelger">
          <div>
            <button aria-current="page">1</button>
            <button aria-current="false">2</button>
            <button aria-current="false">3</button>
          </div>
          <button aria-label="Neste side">...</button>
        </nav>

    Returnerer True hvis vi klikket, False hvis ikke (= vi er på siste side).
    """
    # Diagnostikk: hva ser vi i nav?
    try:
        diag = page.evaluate(r"""
        () => {
          const nav = document.querySelector('nav[aria-labelledby="Enhetsvelger"]');
          if (!nav) return {found: false};
          const buttons = nav.querySelectorAll('button, a');
          return {
            found: true,
            count: buttons.length,
            buttons: Array.from(buttons).map(b => ({
              text: b.textContent.trim().slice(0, 20),
              aria_label: b.getAttribute('aria-label'),
              aria_current: b.getAttribute('aria-current'),
              disabled: b.disabled || b.hasAttribute('disabled'),
            }))
          };
        }
        """)
        logger.info(f"    nav-diag: {diag}")
    except Exception as e:
        logger.info(f"    nav-diag feilet: {e}")

    # Strategi 1: direkte nav-basert. Inne i nav[aria-labelledby="Enhetsvelger"]
    # finner vi den siste knappen — det er "Neste side"-knappen.
    try:
        nav = page.locator('nav[aria-labelledby="Enhetsvelger"]').first
        if nav.count() > 0:
            next_btn = nav.locator('button[aria-label="Neste side"]').first
            if next_btn.count() > 0:
                # Sjekk at den er enabled (på siste side er den disabled)
                is_disabled = next_btn.get_attribute("disabled")
                aria_disabled = next_btn.get_attribute("aria-disabled")
                if is_disabled is not None or aria_disabled == "true":
                    logger.info("    Strategi 1: Neste side er disabled, vi er på siste")
                    return False
                # Aktivt scroll til knappen via JS, deretter klikk med force
                next_btn.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'instant'})")
                page.wait_for_timeout(300)
                next_btn.click(timeout=3000, force=True)
                page.wait_for_timeout(700)
                logger.info("    Strategi 1: klikket Neste side")
                return True
    except Exception as e:
        logger.info(f"    Strategi 1 feilet: {e}")

    # Strategi 2: aria-label-basert utenfor nav. Bare på selve "Neste side"
    # (ikke generelle "Neste" som matcher galleri-pilen)
    selectors = [
        'button[aria-label="Neste side"]',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.count() == 0:
                continue
            if btn.get_attribute("disabled") is not None:
                continue
            if btn.get_attribute("aria-disabled") == "true":
                continue
            btn.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'instant'})")
            page.wait_for_timeout(300)
            btn.click(timeout=3000, force=True)
            page.wait_for_timeout(700)
            logger.info(f"    Strategi 2: klikket via '{sel}'")
            return True
        except Exception as e:
            logger.info(f"    Strategi 2 ({sel}) feilet: {e}")
            continue

    # Strategi 3: numerisk navigasjon — finn aktiv side, klikk neste tall.
    # Robust mot at aktiv knapp ikke alltid har aria-current.
    try:
        next_num = page.evaluate(r"""
        () => {
          const nav = document.querySelector('nav[aria-labelledby="Enhetsvelger"]');
          if (!nav) return null;
          // Hent alle knapper som er rene tall (ikke "Neste side", "Forrige" etc)
          const allButtons = nav.querySelectorAll('button, a');
          const numberedButtons = [];
          for (const b of allButtons) {
            const text = b.textContent.trim();
            if (/^\d+$/.test(text)) {
              numberedButtons.push({ el: b, num: parseInt(text, 10) });
            }
          }
          if (numberedButtons.length < 2) return null;

          const max = Math.max(...numberedButtons.map(x => x.num));

          // Aktiv side: enten aria-current="page", eller den som mangler
          // aria-current mens andre har aria-current="false"
          let current = null;
          let othersHaveFalse = false;
          for (const x of numberedButtons) {
            const ac = x.el.getAttribute('aria-current');
            if (ac === 'page' || ac === 'true') {
              current = x.num;
              break;
            }
            if (ac === 'false') {
              othersHaveFalse = true;
            }
          }
          // Hvis ingen er eksplisitt 'page', men noen er 'false', er den
          // uten attributt sannsynligvis aktiv
          if (current === null && othersHaveFalse) {
            for (const x of numberedButtons) {
              if (!x.el.hasAttribute('aria-current')) {
                current = x.num;
                break;
              }
            }
          }
          // Siste utvei: anta side 1
          if (current === null) current = 1;

          return current < max ? current + 1 : null;
        }
        """)
        if next_num:
            btn = page.locator(
                f'nav[aria-labelledby="Enhetsvelger"] button:has-text("{next_num}"), '
                f'nav[aria-labelledby="Enhetsvelger"] a:has-text("{next_num}")'
            ).first
            if btn.count() > 0:
                btn.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'instant'})")
                page.wait_for_timeout(300)
                btn.click(timeout=3000, force=True)
                page.wait_for_timeout(700)
                logger.info(f"    Strategi 3: klikket på nummer {next_num}")
                return True
            else:
                logger.info(f"    Strategi 3: fant next_num={next_num} men ingen knapp")
        else:
            logger.info("    Strategi 3: next_num=None (sannsynligvis siste side)")
    except Exception as e:
        logger.info(f"    Strategi 3 feilet: {e}")

    logger.info("    Ingen strategi virket — gir opp paginering")
    return False
