"""SQLite-lagring for snapshots av prosjekter og enheter."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "nybolig.db"


def _ensure_tables(conn):
    """Lager tabellene hvis de mangler. Kjøres ved hver tilkobling."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            municipality TEXT NOT NULL,
            finn_code TEXT NOT NULL,
            project_title TEXT,
            address TEXT,
            sales_stage TEXT,
            units_total INTEGER,
            units_for_sale INTEGER,
            units_sold INTEGER,
            avg_price_per_m2 REAL,
            min_price INTEGER,
            max_price INTEGER,
            project_url TEXT,
            scraped_at TEXT NOT NULL,
            UNIQUE(date, finn_code)
        );

        CREATE TABLE IF NOT EXISTS unit_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            finn_code TEXT NOT NULL,
            unit_id TEXT NOT NULL,
            floor INTEGER,
            bra_m2 INTEGER,
            bedrooms INTEGER,
            total_price INTEGER,
            sold INTEGER DEFAULT 0,
            UNIQUE(date, finn_code, unit_id)
        );

        CREATE INDEX IF NOT EXISTS idx_snap_date ON snapshots(date);
        CREATE INDEX IF NOT EXISTS idx_snap_muni ON snapshots(municipality);
        CREATE INDEX IF NOT EXISTS idx_unit_date ON unit_snapshots(date);
        CREATE INDEX IF NOT EXISTS idx_unit_finn ON unit_snapshots(finn_code);
    """)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_project_snapshot(municipality: str, project, project_url: str):
    """Lagrer dagens snapshot for et prosjekt + alle enheter."""
    today = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")

    units_for_sale = project.units_for_sale
    units_sold = project.units_sold

    prices_for_sale = [u.total_price for u in units_for_sale if u.total_price]

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO snapshots
              (date, municipality, finn_code, project_title, address, sales_stage,
               units_total, units_for_sale, units_sold, avg_price_per_m2,
               min_price, max_price, project_url, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, finn_code) DO UPDATE SET
                project_title = excluded.project_title,
                address = excluded.address,
                sales_stage = excluded.sales_stage,
                units_total = excluded.units_total,
                units_for_sale = excluded.units_for_sale,
                units_sold = excluded.units_sold,
                avg_price_per_m2 = excluded.avg_price_per_m2,
                min_price = excluded.min_price,
                max_price = excluded.max_price,
                project_url = excluded.project_url,
                scraped_at = excluded.scraped_at
        """, (
            today, municipality, project.finn_code, project.title,
            project.address, project.sales_stage,
            len(project.units), len(units_for_sale), len(units_sold),
            project.avg_price_per_m2,
            min(prices_for_sale) if prices_for_sale else None,
            max(prices_for_sale) if prices_for_sale else None,
            project_url, now,
        ))

        for u in project.units:
            conn.execute("""
                INSERT INTO unit_snapshots
                  (date, finn_code, unit_id, floor, bra_m2, bedrooms, total_price, sold)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, finn_code, unit_id) DO UPDATE SET
                    floor = excluded.floor,
                    bra_m2 = excluded.bra_m2,
                    bedrooms = excluded.bedrooms,
                    total_price = excluded.total_price,
                    sold = excluded.sold
            """, (
                today, project.finn_code, u.unit_id, u.floor, u.bra_m2,
                u.bedrooms, u.total_price, 1 if u.sold else 0,
            ))


def get_latest_snapshots() -> list[dict]:
    """Henter siste snapshot per prosjekt."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT s.* FROM snapshots s
            JOIN (
                SELECT finn_code, MAX(date) as max_date
                FROM snapshots
                GROUP BY finn_code
            ) latest ON s.finn_code = latest.finn_code AND s.date = latest.max_date
            ORDER BY s.municipality, s.project_title
        """).fetchall()
        return [dict(r) for r in rows]


def compute_sales_stats(finn_code: str, days_back: int) -> int:
    """
    Estimerer antall solgte enheter siste N dager ved diff mellom snapshots.

    Logikk: en enhet som var "for sale" for N dager siden men nå er enten
    forsvunnet fra siden eller markert "sold" regnes som solgt i perioden.
    """
    with get_conn() as conn:
        # Finn nyeste dato
        row = conn.execute(
            "SELECT MAX(date) FROM unit_snapshots WHERE finn_code = ?",
            (finn_code,)
        ).fetchone()
        latest_date = row[0] if row else None
        if not latest_date:
            return 0

        # Finn snapshot nærmest N dager siden. Vi vil ha snapshot fra
        # rundt baseline-datoen — primært fra eller etter target.
        target_date = conn.execute(
            "SELECT date(?, ?)", (latest_date, f"-{days_back} days")
        ).fetchone()[0]

        # Først: prøv eldste snapshot fra og med target_date (men før latest)
        row = conn.execute("""
            SELECT date FROM unit_snapshots
            WHERE finn_code = ?
              AND date >= ?
              AND date < ?
            ORDER BY date ASC LIMIT 1
        """, (finn_code, target_date, latest_date)).fetchone()

        # Hvis ingen, prøv nyeste snapshot eldre enn target_date
        if not row:
            row = conn.execute("""
                SELECT date FROM unit_snapshots
                WHERE finn_code = ?
                  AND date < ?
                ORDER BY date DESC LIMIT 1
            """, (finn_code, target_date)).fetchone()

        if not row:
            return 0
        baseline_date = row[0]

        if baseline_date == latest_date:
            return 0  # Ingen tidligere data å sammenligne med

        # Enheter som var for sale i baseline
        baseline_units = set(r[0] for r in conn.execute("""
            SELECT unit_id FROM unit_snapshots
            WHERE finn_code = ? AND date = ? AND sold = 0
        """, (finn_code, baseline_date)).fetchall())

        # Enheter som er for sale nå
        current_units = set(r[0] for r in conn.execute("""
            SELECT unit_id FROM unit_snapshots
            WHERE finn_code = ? AND date = ? AND sold = 0
        """, (finn_code, latest_date)).fetchall())

        # Solgt = i baseline men ikke i nåværende
        return len(baseline_units - current_units)


def get_project_history(finn_code: str, days: int = 90) -> list[dict]:
    """Tidsserie av units_for_sale per dag."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT date, units_for_sale, units_sold, avg_price_per_m2
            FROM snapshots
            WHERE finn_code = ?
              AND date >= date('now', ?)
            ORDER BY date
        """, (finn_code, f"-{days} days")).fetchall()
        return [dict(r) for r in rows]


def get_current_units(finn_code: str) -> list[dict]:
    """Henter alle enheter fra siste snapshot for et prosjekt."""
    with get_conn() as conn:
        latest = conn.execute(
            "SELECT MAX(date) FROM unit_snapshots WHERE finn_code = ?",
            (finn_code,)
        ).fetchone()[0]
        if not latest:
            return []
        rows = conn.execute("""
            SELECT unit_id, floor, bra_m2, bedrooms, total_price, sold
            FROM unit_snapshots
            WHERE finn_code = ? AND date = ?
            ORDER BY unit_id
        """, (finn_code, latest)).fetchall()
        return [dict(r) for r in rows]


def get_recent_changes(finn_code: str, days_back: int = 30) -> dict:
    """
    Returnerer både nylig solgte enheter OG enheter med prisendring siste N dager.

    {
      "sold": [{"unit_id", "last_seen_price", "bra_m2", "floor", "disappeared_on"}],
      "price_changes": [{"unit_id", "old_price", "new_price", "change_pct", "changed_on"}],
    }
    """
    with get_conn() as conn:
        # Nyeste dato
        latest_date = conn.execute(
            "SELECT MAX(date) FROM unit_snapshots WHERE finn_code = ?",
            (finn_code,)
        ).fetchone()[0]
        if not latest_date:
            return {"sold": [], "price_changes": []}

        # Baseline: nyeste snapshot fra eller før (now - days_back)
        target_date = conn.execute(
            "SELECT date(?, ?)", (latest_date, f"-{days_back} days")
        ).fetchone()[0]

        baseline_date = conn.execute("""
            SELECT date FROM unit_snapshots
            WHERE finn_code = ? AND date >= ? AND date < ?
            ORDER BY date ASC LIMIT 1
        """, (finn_code, target_date, latest_date)).fetchone()
        if not baseline_date:
            baseline_date = conn.execute("""
                SELECT date FROM unit_snapshots
                WHERE finn_code = ? AND date < ?
                ORDER BY date DESC LIMIT 1
            """, (finn_code, target_date)).fetchone()
        if not baseline_date:
            return {"sold": [], "price_changes": []}
        baseline_date = baseline_date[0]

        if baseline_date == latest_date:
            return {"sold": [], "price_changes": []}

        # Enheter til salgs i baseline (med pris og metadata)
        baseline_rows = conn.execute("""
            SELECT unit_id, total_price, bra_m2, floor, bedrooms
            FROM unit_snapshots
            WHERE finn_code = ? AND date = ? AND sold = 0
        """, (finn_code, baseline_date)).fetchall()
        baseline_map = {r["unit_id"]: dict(r) for r in baseline_rows}

        # Enheter til salgs nå
        current_rows = conn.execute("""
            SELECT unit_id, total_price, bra_m2, floor, bedrooms
            FROM unit_snapshots
            WHERE finn_code = ? AND date = ? AND sold = 0
        """, (finn_code, latest_date)).fetchall()
        current_map = {r["unit_id"]: dict(r) for r in current_rows}

        # Solgt = i baseline men ikke nå
        sold = []
        for uid, data in baseline_map.items():
            if uid not in current_map:
                sold.append({
                    "unit_id": uid,
                    "last_seen_price": data["total_price"],
                    "bra_m2": data["bra_m2"],
                    "floor": data["floor"],
                    "bedrooms": data["bedrooms"],
                    "disappeared_after": baseline_date,
                    "disappeared_before": latest_date,
                })

        # Prisendring = i begge, men annen pris
        price_changes = []
        for uid, current in current_map.items():
            base = baseline_map.get(uid)
            if not base:
                continue  # Ny enhet — ikke en endring
            old_price = base["total_price"]
            new_price = current["total_price"]
            if old_price and new_price and old_price != new_price:
                change_pct = (new_price - old_price) / old_price * 100
                price_changes.append({
                    "unit_id": uid,
                    "old_price": old_price,
                    "new_price": new_price,
                    "change_pct": change_pct,
                    "bra_m2": current["bra_m2"],
                    "floor": current["floor"],
                    "bedrooms": current["bedrooms"],
                    "since": baseline_date,
                    "changed_before": latest_date,
                })

        # Sorter solgt etter pris (høyest først), endringer etter størrelse
        sold.sort(key=lambda x: -(x["last_seen_price"] or 0))
        price_changes.sort(key=lambda x: -abs(x["change_pct"]))

        return {"sold": sold, "price_changes": price_changes}

