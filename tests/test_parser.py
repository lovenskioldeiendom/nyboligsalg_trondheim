"""Tester for parser. Kjøres med: python tests/test_parser.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper.parser import parse_project_page, extract_project_links_from_search


FIXTURES = Path(__file__).parent / "fixtures"


def test_helgerud_with_sold():
    html = (FIXTURES / "helgerud_with_sold.html").read_text(encoding="utf-8")
    p = parse_project_page(
        html,
        source_url="https://www.finn.no/realestate/project/ad.html?finnkode=397833531",
        municipality_hint="Bærum",
    )

    assert p.finn_code == "397833531", f"finn_code={p.finn_code}"
    assert p.municipality == "Bærum"
    assert "Helgerudkvartalet" in p.title
    assert p.address and "Sandvika" in p.address
    assert len(p.units) == 11
    assert len(p.units_for_sale) == 10
    assert len(p.units_sold) == 1
    assert p.units_sold[0].unit_id == "A1104"
    assert p.avg_price_per_m2 is not None and 100_000 < p.avg_price_per_m2 < 200_000

    # Spot-check første enhet
    a0703 = next(u for u in p.units if u.unit_id == "A0703")
    assert a0703.floor == 7
    assert a0703.bra_m2 == 117
    assert a0703.bedrooms == 3
    assert a0703.total_price == 14_085_520
    assert not a0703.sold

    print(f"✓ helgerud_with_sold: {len(p.units_for_sale)} til salgs, {len(p.units_sold)} solgt, snitt {p.avg_price_per_m2:,.0f} kr/m²")


def test_storoykilen_with_stage():
    html = (FIXTURES / "storoykilen.html").read_text(encoding="utf-8")
    p = parse_project_page(
        html,
        source_url="https://www.finn.no/realestate/project/ad.html?finnkode=368269668",
        municipality_hint="Bærum",
    )

    assert p.finn_code == "368269668", f"finn_code={p.finn_code}"
    assert p.sales_stage == "Salgstrinn 3", f"sales_stage={p.sales_stage}"
    assert len(p.units) == 4
    assert p.last_modified == "1. mai 2026 09:15"
    print(f"✓ storoykilen: salgstrinn={p.sales_stage}, {len(p.units)} enheter")


def test_search_extraction():
    html = (FIXTURES / "search_results.html").read_text(encoding="utf-8")
    urls = extract_project_links_from_search(html)
    # Vi forventer 3 unike project-URLer (ikke projectsingle, ikke planned)
    assert len(urls) == 3, f"Forventet 3 unike URLer, fikk {len(urls)}: {urls}"
    for u in urls:
        assert "/realestate/project/ad.html" in u
        assert u.startswith("https://")
    print(f"✓ search_extraction: fant {len(urls)} unike project-URLer")


if __name__ == "__main__":
    test_helgerud_with_sold()
    test_storoykilen_with_stage()
    test_search_extraction()
    print("\n✓ Alle tester passerer")
