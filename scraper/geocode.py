"""
Geocoding av prosjektadresser via OpenStreetMap Nominatim.

Resultater caches i SQLite slik at vi bare slår opp hver adresse én gang.
"""

import json
import logging
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .database import get_conn

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "nybolig-monitor/1.0 (github.com/nybolig-monitor)"
RATE_LIMIT_S = 1.1  # Nominatim krever max 1 req/s — vi tar litt margin


def _ensure_cache_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geocode_cache (
            address TEXT PRIMARY KEY,
            lat REAL,
            lng REAL,
            display_name TEXT,
            geocoded_at TEXT NOT NULL
        )
    """)


_NOT_FOUND = object()  # Sentinel: vi har slått opp og ikke funnet noe


def get_cached(address: str):
    """
    Returner cachede koordinater.

    - dict {'lat', 'lng', 'display_name'} hvis funnet
    - _NOT_FOUND hvis vi har slått opp og ikke fant noe (ikke prøv igjen)
    - None hvis adressen ikke er i cachen (må slås opp)
    """
    if not address:
        return _NOT_FOUND
    with get_conn() as conn:
        _ensure_cache_table(conn)
        row = conn.execute(
            "SELECT lat, lng, display_name FROM geocode_cache WHERE address = ?",
            (address,)
        ).fetchone()
        if row is None:
            return None  # Ikke i cache
        if row["lat"] is None:
            return _NOT_FOUND  # I cache, men ikke funnet
        return {"lat": row["lat"], "lng": row["lng"], "display_name": row["display_name"]}


def _store_result(address: str, lat: float | None, lng: float | None,
                  display_name: str | None) -> None:
    from datetime import datetime
    with get_conn() as conn:
        _ensure_cache_table(conn)
        conn.execute("""
            INSERT INTO geocode_cache (address, lat, lng, display_name, geocoded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                lat = excluded.lat,
                lng = excluded.lng,
                display_name = excluded.display_name,
                geocoded_at = excluded.geocoded_at
        """, (address, lat, lng, display_name, datetime.now().isoformat(timespec="seconds")))


def _query_nominatim(address: str) -> dict | None:
    """Returnerer {'lat', 'lng', 'display_name'} eller None ved feil."""
    params = f"q={quote(address)}&format=json&limit=1&countrycodes=no"
    url = f"{NOMINATIM_URL}?{params}"
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "nb"})
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data:
                return None
            r = data[0]
            return {
                "lat": float(r["lat"]),
                "lng": float(r["lon"]),
                "display_name": r.get("display_name", ""),
            }
    except (HTTPError, URLError, TimeoutError) as e:
        logger.warning(f"Nominatim-feil for '{address}': {e}")
        return None
    except Exception as e:
        logger.warning(f"Uventet feil for '{address}': {e}")
        return None


def geocode_address(address: str) -> dict | None:
    """
    Returnerer koordinater for en adresse, fra cache eller via Nominatim.

    Returverdi: {'lat', 'lng', 'display_name'} eller None hvis ikke funnet.
    """
    cached = get_cached(address)
    if cached is _NOT_FOUND:
        return None
    if cached is not None:
        return cached

    logger.info(f"  Geocoder: {address}")
    result = _query_nominatim(address)
    time.sleep(RATE_LIMIT_S)

    if result is None:
        _store_result(address, None, None, None)
        return None

    _store_result(address, result["lat"], result["lng"], result["display_name"])
    return result


def geocode_all_pending() -> dict:
    """
    Slår opp koordinater for alle prosjekter i siste snapshot som ennå ikke
    er cachet. Returnerer summary.
    """
    summary = {"total": 0, "already_cached": 0, "newly_geocoded": 0, "failed": 0}
    with get_conn() as conn:
        _ensure_cache_table(conn)
        rows = conn.execute("""
            SELECT DISTINCT s.address
            FROM snapshots s
            JOIN (
                SELECT finn_code, MAX(date) as max_date
                FROM snapshots GROUP BY finn_code
            ) latest ON s.finn_code = latest.finn_code AND s.date = latest.max_date
            WHERE s.address IS NOT NULL AND s.address != ''
        """).fetchall()

    addresses = [r["address"] for r in rows]
    summary["total"] = len(addresses)

    for addr in addresses:
        cached = get_cached(addr)
        if cached is not None:  # Enten dict eller _NOT_FOUND
            summary["already_cached"] += 1
            continue
        result = geocode_address(addr)
        if result:
            summary["newly_geocoded"] += 1
        else:
            summary["failed"] += 1

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    s = geocode_all_pending()
    logger.info(f"Geocoding ferdig: {s}")
