"""
Parser for Finn.no prosjektannonser (URL-format /realestate/project/ad.html).

Henter ut:
- Prosjekttittel og adresse
- Salgstrinn (fra tittel, hvis nevnt)
- Liste over enheter med pris, BRA, etasje, soverom, og solgt-status
"""

import re
from dataclasses import dataclass, field
from typing import Optional
from bs4 import BeautifulSoup


@dataclass
class Unit:
    unit_id: str
    floor: Optional[int] = None
    bra_m2: Optional[int] = None
    bedrooms: Optional[int] = None
    total_price: Optional[int] = None
    sold: bool = False
    finn_url: Optional[str] = None


@dataclass
class Project:
    finn_code: str
    title: str
    address: Optional[str] = None
    municipality: Optional[str] = None
    sales_stage: Optional[str] = None  # F.eks. "Salgstrinn 3"
    units: list[Unit] = field(default_factory=list)
    last_modified: Optional[str] = None

    @property
    def units_for_sale(self) -> list[Unit]:
        return [u for u in self.units if not u.sold]

    @property
    def units_sold(self) -> list[Unit]:
        return [u for u in self.units if u.sold]

    @property
    def avg_price_per_m2(self) -> Optional[float]:
        valid = [u for u in self.units_for_sale if u.total_price and u.bra_m2]
        if not valid:
            return None
        return sum(u.total_price / u.bra_m2 for u in valid) / len(valid)


def _parse_int(text: str) -> Optional[int]:
    """Henter første tall fra tekst. Håndterer mellomrom, NBSP, kr."""
    if not text:
        return None
    # Erstatt unicode-mellomrom med vanlig
    cleaned = text.replace("\xa0", " ").replace("\u2009", " ")
    cleaned = re.sub(r"[^\d]", "", cleaned)
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _extract_sales_stage(title: str) -> Optional[str]:
    """Plukker ut 'salgstrinn N' eller 'trinn N' fra tittel."""
    m = re.search(r"(salgstrinn|trinn)\s*(\d+)", title, re.IGNORECASE)
    if m:
        return f"Salgstrinn {m.group(2)}"
    return None


def _parse_units_table(soup: BeautifulSoup) -> list[Unit]:
    """
    Finner tabellen med enheter ved å lete etter en table med 'enhet' og
    'totalpris' i headerne.
    """
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if "enhet" in headers and "totalpris" in headers:
            return _extract_units_from_table(table, headers)
    return []


def _extract_units_from_table(table, headers: list[str]) -> list[Unit]:
    units = []
    col = {h: i for i, h in enumerate(headers)}

    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else table.find_all("tr")[1:]

    for row in rows:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        def cell_text(key: str) -> str:
            i = col.get(key)
            if i is None or i >= len(cells):
                return ""
            return cells[i].get_text(strip=True)

        unit_id = cell_text("enhet")
        if not unit_id:
            continue

        # Lenke til Finn-annonsen for enheten
        finn_url = None
        if "enhet" in col:
            link = cells[col["enhet"]].find("a")
            if link and link.get("href"):
                finn_url = link["href"]

        price_text = cell_text("totalpris")
        sold = "solgt" in price_text.lower()

        units.append(Unit(
            unit_id=unit_id,
            floor=_parse_int(cell_text("etasje")),
            bra_m2=_parse_int(cell_text("bra-i") or cell_text("areal")),
            bedrooms=_parse_int(cell_text("soverom")),
            total_price=None if sold else _parse_int(price_text),
            sold=sold,
            finn_url=finn_url,
        ))
    return units


def _extract_finn_code(soup: BeautifulSoup, fallback_url: Optional[str] = None) -> str:
    # Prøv <dt>FINN-kode</dt><dd>...</dd>
    for dt in soup.find_all("dt"):
        if "finn-kode" in dt.get_text(strip=True).lower():
            dd = dt.find_next_sibling("dd")
            if dd:
                code = re.sub(r"\D", "", dd.get_text())
                if code:
                    return code

    # Prøv tabell-rad: | FINN-kode | 12345 |
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2 and "finn-kode" in cells[0].get_text(strip=True).lower():
                code = re.sub(r"\D", "", cells[1].get_text())
                if code:
                    return code

    # Fallback: trekk ut fra URL
    if fallback_url:
        m = re.search(r"finnkode=(\d+)", fallback_url)
        if m:
            return m.group(1)
    return ""


def _extract_address(soup: BeautifulSoup) -> Optional[str]:
    """Adresselinjen — typisk en tekst med 4-sifret postnr."""
    for el in soup.find_all(["p", "div", "span", "address"]):
        text = el.get_text(" ", strip=True)
        if re.search(r"\b\d{4}\s+[A-ZÆØÅa-zæøå]", text) and 10 < len(text) < 200:
            # Strip "Kart" som ofte er prefiks
            text = re.sub(r"^Kart\s*", "", text)
            return text
    return None


def parse_project_page(html: str, source_url: Optional[str] = None,
                       municipality_hint: Optional[str] = None) -> Project:
    """Hovedfunksjon. Tar HTML, returnerer en Project."""
    soup = BeautifulSoup(html, "html.parser")

    # Tittel: prøv h1, så h2, så <title>
    title_el = soup.find("h1") or soup.find("h2") or soup.find("title")
    title = title_el.get_text(strip=True) if title_el else "Ukjent prosjekt"

    # For salgstrinn: sjekk all titteltekst (h1 + h2 + title), siden trinn-info
    # ofte ligger i h2 eller annonsens undertittel
    title_search_text = " ".join([
        (soup.find("h1").get_text(strip=True) if soup.find("h1") else ""),
        (soup.find("h2").get_text(strip=True) if soup.find("h2") else ""),
        (soup.find("title").get_text(strip=True) if soup.find("title") else ""),
    ])

    address = _extract_address(soup)
    finn_code = _extract_finn_code(soup, fallback_url=source_url)
    sales_stage = _extract_sales_stage(title_search_text)
    units = _parse_units_table(soup)

    # Sist endret
    last_modified = None
    for dt in soup.find_all("dt"):
        if "sist endret" in dt.get_text(strip=True).lower():
            dd = dt.find_next_sibling("dd")
            if dd:
                last_modified = dd.get_text(strip=True)

    if not last_modified:
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2 and "sist endret" in cells[0].get_text(strip=True).lower():
                    last_modified = cells[1].get_text(strip=True)
                    break

    return Project(
        finn_code=finn_code,
        title=title,
        address=address,
        municipality=municipality_hint,
        sales_stage=sales_stage,
        units=units,
        last_modified=last_modified,
    )


def extract_project_links_from_search(html: str) -> list[str]:
    """
    Fra en Finn-søkeside, returnerer alle URLer som peker til /realestate/project/.

    Vi tar bare 'project' (med enhetsliste), ikke 'projectsingle' (én bolig)
    eller 'planned' (interessemelding uten salgsstart).
    """
    soup = BeautifulSoup(html, "html.parser")
    urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/realestate/project/ad.html" in href and "finnkode=" in href:
            # Normaliser til absolutt URL
            if href.startswith("/"):
                href = "https://www.finn.no" + href
            urls.add(href)
    return sorted(urls)
