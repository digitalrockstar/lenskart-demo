from __future__ import annotations

import argparse
import json
import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "lenskart.db"
STATIC_DIR = BASE_DIR / "static"
DATA_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

CHANNELS = ["Organic Search", "Paid Search", "Paid Social"]
DEVICES = ["Android App", "iOS App", "Web"]
PAYMENT_METHODS = ["UPI", "Card", "COD"]
CHANNEL_SHARES_PREV = {"Organic Search": 0.43, "Paid Search": 0.32, "Paid Social": 0.25}
CHANNEL_SHARES_CURR = {"Organic Search": 0.39, "Paid Search": 0.26, "Paid Social": 0.35}
DEVICE_SHARES = {"Android App": 0.62, "iOS App": 0.23, "Web": 0.15}
PAYMENT_SHARES = {"UPI": 0.65, "Card": 0.25, "COD": 0.10}

app = FastAPI(title="Lenskart Growth Intelligence", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
_background_started = False
_background_lock = threading.Lock()


def now_local() -> datetime:
    # Local naive timestamps keep the demo aligned with the machine running it.
    return datetime.now().replace(tzinfo=None)


def iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat(sep=" ")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 15000")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init_db() -> None:
    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS traffic_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                bucket_type TEXT NOT NULL CHECK(bucket_type IN ('actual','forecast')),
                channel TEXT NOT NULL,
                device TEXT NOT NULL,
                payment_method TEXT NOT NULL,
                sessions INTEGER NOT NULL,
                product_views INTEGER NOT NULL,
                add_to_cart INTEGER NOT NULL,
                checkout_started INTEGER NOT NULL,
                payment_attempts INTEGER NOT NULL,
                orders INTEGER NOT NULL,
                revenue REAL NOT NULL,
                page_load_ms REAL NOT NULL,
                payment_failures INTEGER NOT NULL,
                out_of_stock_sessions INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_facts_time_type ON traffic_facts(bucket_type, occurred_at);
            CREATE INDEX IF NOT EXISTS idx_facts_segments ON traffic_facts(channel, device, payment_method);
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_type TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                summary TEXT,
                hypotheses_json TEXT
            );
            """
        )


def make_fact(
    stamp: datetime,
    channel: str,
    device: str,
    payment_method: str,
    sessions: int,
    recent: bool,
    forecast: bool = False,
    rng: random.Random | None = None,
) -> tuple[Any, ...] | None:
    rng = rng or random
    if sessions <= 0:
        return None

    channel_view_rate = {
        "Organic Search": 0.78,
        "Paid Search": 0.72,
        "Paid Social": 0.56,
    }[channel]
    device_view_factor = {"Android App": 0.96, "iOS App": 1.03, "Web": 1.00}[device]
    view_rate = min(0.92, channel_view_rate * device_view_factor)
    product_views = min(sessions, max(0, round(sessions * view_rate)))

    cart_rate = 0.30
    if recent and device == "Android App":
        cart_rate = 0.285  # small app UX/performance drag in the recent period
    add_to_cart = min(product_views, round(product_views * cart_rate))
    checkout_started = min(add_to_cart, round(add_to_cart * 0.56))
    payment_attempts = min(checkout_started, round(checkout_started * 0.85))

    success_rate = {"UPI": 0.86, "Card": 0.82, "COD": 0.76}[payment_method]
    if recent and device == "Android App" and payment_method == "UPI":
        success_rate = 0.46  # seeded gateway anomaly: diagnostic should surface this
    orders = min(payment_attempts, round(payment_attempts * success_rate))

    base_load = {"Android App": 2250, "iOS App": 1850, "Web": 2050}[device]
    if recent and device == "Android App":
        base_load = 3450
    page_load_ms = max(700, rng.gauss(base_load, 220))
    out_of_stock_sessions = round(product_views * (0.035 if channel != "Paid Social" else 0.045))
    aov = rng.uniform(2900, 4300)
    return (
        iso(stamp), "forecast" if forecast else "actual", channel, device, payment_method,
        sessions, product_views, add_to_cart, checkout_started, payment_attempts, orders,
        round(orders * aov, 2), round(page_load_ms, 1),
        max(0, payment_attempts - orders), out_of_stock_sessions,
    )


def seed_database(force: bool = False) -> dict[str, int]:
    init_db()
    now = now_local()
    start = (now - timedelta(days=60)).replace(minute=0, second=0, microsecond=0)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    end = (now + timedelta(days=7)).replace(minute=0, second=0, microsecond=0)

    with connect() as con:
        existing = con.execute("SELECT COUNT(*) FROM traffic_facts").fetchone()[0]
        if existing and not force:
            forecast_count = con.execute(
                "SELECT COUNT(*) FROM traffic_facts WHERE bucket_type='forecast'"
            ).fetchone()[0]
            return {"rows": existing, "forecast_rows": forecast_count, "seeded": 0}
        if force:
            con.execute("DELETE FROM traffic_facts")
            con.execute("DELETE FROM agent_runs")

        rows: list[tuple[Any, ...]] = []
        stamp = start
        while stamp <= end:
            forecast = stamp >= current_hour
            recent = stamp >= now - timedelta(days=30)
            channel_shares = CHANNEL_SHARES_CURR if recent or forecast else CHANNEL_SHARES_PREV
            # Keep the seed deterministic while making each hour look naturally different.
            seed = int(stamp.timestamp()) // 3600
            rng = random.Random(seed)
            hourly_sessions = rng.randint(1050, 1280)
            for channel in CHANNELS:
                for device in DEVICES:
                    for method in PAYMENT_METHODS:
                        expected = (
                            hourly_sessions
                            * channel_shares[channel]
                            * DEVICE_SHARES[device]
                            * PAYMENT_SHARES[method]
                        )
                        sessions = max(0, round(expected + rng.gauss(0, max(1, expected * 0.035))))
                        record = make_fact(
                            stamp, channel, device, method, sessions, recent,
                            forecast=forecast, rng=rng,
                        )
                        if record:
                            rows.append(record)
            stamp += timedelta(hours=1)

        con.executemany(
            """
            INSERT INTO traffic_facts (
                occurred_at, bucket_type, channel, device, payment_method,
                sessions, product_views, add_to_cart, checkout_started,
                payment_attempts, orders, revenue, page_load_ms,
                payment_failures, out_of_stock_sessions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        actual_count = con.execute(
            "SELECT COUNT(*) FROM traffic_facts WHERE bucket_type='actual'"
        ).fetchone()[0]
        forecast_count = con.execute(
            "SELECT COUNT(*) FROM traffic_facts WHERE bucket_type='forecast'"
        ).fetchone()[0]
    return {"rows": len(rows), "actual_rows": actual_count, "forecast_rows": forecast_count, "seeded": 1}


def insert_live_event() -> None:
    rng = random.Random(time.time_ns())
    channel = rng.choices(CHANNELS, weights=[0.39, 0.26, 0.35], k=1)[0]
    device = rng.choices(DEVICES, weights=[0.62, 0.23, 0.15], k=1)[0]
    method = rng.choices(PAYMENT_METHODS, weights=[0.65, 0.25, 0.10], k=1)[0]
    recent = True
    record = make_fact(now_local(), channel, device, method, rng.randint(3, 7), recent, rng=rng)
    if record:
        with connect() as con:
            con.execute(
                """
                INSERT INTO traffic_facts (
                    occurred_at, bucket_type, channel, device, payment_method,
                    sessions, product_views, add_to_cart, checkout_started,
                    payment_attempts, orders, revenue, page_load_ms,
                    payment_failures, out_of_stock_sessions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                record,
            )


def aggregate(start: datetime, end: datetime, filters: dict[str, str] | None = None) -> dict[str, float]:
    fields = {
        "channel": "channel",
        "device": "device",
        "payment_method": "payment_method",
    }
    where = ["bucket_type='actual'", "occurred_at >= ?", "occurred_at < ?"]
    values: list[Any] = [iso(start), iso(end)]
    for key, value in (filters or {}).items():
        if key not in fields:
            raise ValueError(f"Unsupported filter: {key}")
        where.append(f"{fields[key]} = ?")
        values.append(value)
    sql = f"""
        SELECT
          COALESCE(SUM(sessions),0) sessions,
          COALESCE(SUM(product_views),0) product_views,
          COALESCE(SUM(add_to_cart),0) add_to_cart,
          COALESCE(SUM(checkout_started),0) checkout_started,
          COALESCE(SUM(payment_attempts),0) payment_attempts,
          COALESCE(SUM(orders),0) orders,
          COALESCE(SUM(revenue),0) revenue,
          COALESCE(SUM(payment_failures),0) payment_failures,
          COALESCE(SUM(page_load_ms * sessions),0) weighted_load,
          COALESCE(SUM(out_of_stock_sessions),0) out_of_stock_sessions
        FROM traffic_facts WHERE {' AND '.join(where)}
    """
    with connect() as con:
        row = con.execute(sql, values).fetchone()
    result = dict(row)
    result["avg_page_load_ms"] = result.pop("weighted_load") / result["sessions"] if result["sessions"] else 0
    return result


def pct(numerator: float, denominator: float) -> float:
    return (numerator / denominator * 100) if denominator else 0.0


def conversion(metrics: dict[str, float]) -> float:
    return pct(metrics["orders"], metrics["sessions"])


def make_hypotheses(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or now_local()
    current_start = now - timedelta(days=30)
    previous_start = now - timedelta(days=60)
    current = aggregate(current_start, now)
    previous = aggregate(previous_start, current_start)

    pay_now = aggregate(current_start, now, {"device": "Android App", "payment_method": "UPI"})
    pay_prev = aggregate(previous_start, current_start, {"device": "Android App", "payment_method": "UPI"})
    pay_now_rate = pct(pay_now["orders"], pay_now["payment_attempts"])
    pay_prev_rate = pct(pay_prev["orders"], pay_prev["payment_attempts"])
    pay_delta = pay_now_rate - pay_prev_rate
    pay_share = pct(pay_now["payment_attempts"], current["payment_attempts"])
    payment_sample_ok = pay_now["payment_attempts"] >= 300 and pay_prev["payment_attempts"] >= 300
    payment_signal = payment_sample_ok and pay_delta <= -8.0
    payment_score = min(99, round(30 + max(0, -pay_delta) * 1.35)) if payment_sample_ok else 10
    payment_confidence = "High" if payment_signal and pay_delta <= -20 else "Medium" if payment_signal else "Low"

    app_now = aggregate(current_start, now, {"device": "Android App"})
    app_prev = aggregate(previous_start, current_start, {"device": "Android App"})
    load_change = app_now["avg_page_load_ms"] - app_prev["avg_page_load_ms"]
    cart_now = pct(app_now["add_to_cart"], app_now["product_views"])
    cart_prev = pct(app_prev["add_to_cart"], app_prev["product_views"])
    app_sample_ok = app_now["sessions"] >= 500 and app_prev["sessions"] >= 500
    cart_drop = cart_prev - cart_now
    app_signal = app_sample_ok and load_change >= 500 and cart_drop >= 1.0
    app_score = min(95, round(30 + max(0, load_change) / 30 + max(0, cart_drop) * 5)) if app_sample_ok else 10
    app_confidence = "High" if app_signal and load_change >= 1000 else "Medium" if app_signal else "Low"

    social_now = aggregate(current_start, now, {"channel": "Paid Social"})
    social_prev = aggregate(previous_start, current_start, {"channel": "Paid Social"})
    organic_now = aggregate(current_start, now, {"channel": "Organic Search"})
    search_now = aggregate(current_start, now, {"channel": "Paid Search"})
    social_share_now = pct(social_now["sessions"], current["sessions"])
    social_share_prev = pct(social_prev["sessions"], previous["sessions"])
    social_view_now = pct(social_now["product_views"], social_now["sessions"])
    non_social_view_now = pct(
        organic_now["product_views"] + search_now["product_views"],
        organic_now["sessions"] + search_now["sessions"],
    )
    social_mix_change = social_share_now - social_share_prev
    intent_gap = non_social_view_now - social_view_now
    social_sample_ok = social_now["sessions"] >= 500 and social_prev["sessions"] >= 500
    social_signal = social_sample_ok and social_mix_change >= 5.0 and intent_gap >= 8.0
    social_score = min(95, round(30 + max(0, social_mix_change) * 2 + max(0, intent_gap) * 1.5)) if social_sample_ok else 10
    social_confidence = "High" if social_signal and social_mix_change >= 10 and intent_gap >= 15 else "Medium" if social_signal else "Low"

    items = [
        {
            "key": "payment",
            "title": "UPI payment success has fallen on Android",
            "category": "PAYMENT",
            "confidence": payment_confidence,
            "signal_detected": payment_signal,
            "score": payment_score,
            "evidence": (
                f"Android + UPI payment completion is {pay_now_rate:.1f}% vs "
                f"{pay_prev_rate:.1f}% in the previous 30 days ({pay_delta:+.1f} pp). "
                f"This segment represents {pay_share:.1f}% of current payment attempts."
            ),
            "confirm_if": (
                "Gateway response data shows a matching rise in UPI DECLINED / TIMEOUT "
                "responses on Android, concentrated by app version or gateway route."
            ),
            "metric": f"{pay_now_rate:.1f}% success",
            "sample_size": int(pay_now["payment_attempts"]),
        },
        {
            "key": "latency",
            "title": "Android app latency is weakening cart intent",
            "category": "APP EXPERIENCE",
            "confidence": app_confidence,
            "signal_detected": app_signal,
            "score": app_score,
            "evidence": (
                f"Android average page load is {app_now['avg_page_load_ms'] / 1000:.2f}s "
                f"vs {app_prev['avg_page_load_ms'] / 1000:.2f}s previously "
                f"({load_change / 1000:+.2f}s). Add-to-cart per product view moved "
                f"from {cart_prev:.1f}% to {cart_now:.1f}%."
            ),
            "confirm_if": (
                "App performance traces show the slowdown on product/listing screens, "
                "and slow sessions have a materially lower add-to-cart rate."
            ),
            "metric": f"{app_now['avg_page_load_ms'] / 1000:.2f}s avg load",
            "sample_size": int(app_now["sessions"]),
        },
        {
            "key": "mix",
            "title": "Paid Social is contributing more low-intent sessions",
            "category": "CHANNEL MIX",
            "confidence": social_confidence,
            "signal_detected": social_signal,
            "score": social_score,
            "evidence": (
                f"Paid Social share moved from {social_share_prev:.1f}% to "
                f"{social_share_now:.1f}% of sessions. Its product-view rate is "
                f"{social_view_now:.1f}% vs {non_social_view_now:.1f}% for non-social traffic."
            ),
            "confirm_if": (
                "Campaign/ad-set and landing-page cuts show the incremental paid-social "
                "sessions have lower product-view and add-to-cart rates than other channels."
            ),
            "metric": f"{social_share_now:.1f}% traffic share",
            "sample_size": int(social_now["sessions"]),
        },
    ]
    items.sort(key=lambda item: item["score"], reverse=True)
    for index, item in enumerate(items, start=1):
        item["rank"] = index
        item["signal_label"] = "Signal detected" if item["signal_detected"] else "Watch only"
    return items[:3]


def execute_diagnostic(trigger_type: str = "manual") -> dict[str, Any]:
    started = now_local()
    hypotheses = make_hypotheses(started)
    current = aggregate(started - timedelta(days=30), started)
    previous = aggregate(started - timedelta(days=60), started - timedelta(days=30))
    delta = conversion(current) - conversion(previous)
    summary = (
        f"30-day conversion is {conversion(current):.2f}% vs {conversion(previous):.2f}% "
        f"in the previous 30 days ({delta:+.2f} pp). Highest-ranked signal: "
        f"{hypotheses[0]['title']}."
    )
    completed = now_local()
    with connect() as con:
        cursor = con.execute(
            """
            INSERT INTO agent_runs (trigger_type, status, started_at, completed_at, summary, hypotheses_json)
            VALUES (?, 'completed', ?, ?, ?, ?)
            """,
            (trigger_type, iso(started), iso(completed), summary, json.dumps(hypotheses)),
        )
        run_id = cursor.lastrowid
    return {
        "id": run_id,
        "trigger_type": trigger_type,
        "status": "completed",
        "started_at": iso(started),
        "completed_at": iso(completed),
        "summary": summary,
        "hypotheses": hypotheses,
    }


def latest_diagnostic() -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM agent_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    item = dict(row)
    item["hypotheses"] = json.loads(item.pop("hypotheses_json") or "[]")
    return item


def live_loop() -> None:
    while True:
        try:
            insert_live_event()
        except Exception as exc:  # keep the stream alive if a single write fails
            print(f"[live-stream] {exc}", flush=True)
        time.sleep(8)


def diagnostic_loop() -> None:
    time.sleep(8)  # first autonomous run shortly after the app starts
    while True:
        try:
            result = execute_diagnostic("scheduled")
            print(f"[diagnostic-agent] run #{result['id']} completed", flush=True)
        except Exception as exc:
            print(f"[diagnostic-agent] {exc}", flush=True)
        time.sleep(90)


def start_background_tasks() -> None:
    global _background_started
    with _background_lock:
        if _background_started:
            return
        _background_started = True
        threading.Thread(target=live_loop, name="lenskart-live-stream", daemon=True).start()
        threading.Thread(target=diagnostic_loop, name="lenskart-diagnostic-agent", daemon=True).start()


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    seed_database(force=False)
    start_background_tasks()


@app.get("/")
def home() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=500, detail="Frontend files are missing from /static")
    return FileResponse(index)


@app.get("/api/health")
def health() -> dict[str, Any]:
    with connect() as con:
        actual = con.execute("SELECT COUNT(*) FROM traffic_facts WHERE bucket_type='actual'").fetchone()[0]
        forecast = con.execute("SELECT COUNT(*) FROM traffic_facts WHERE bucket_type='forecast'").fetchone()[0]
        runs = con.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
    return {"status": "ok", "database": str(DB_PATH), "actual_rows": actual, "forecast_rows": forecast, "diagnostic_runs": runs}


@app.post("/api/seed")
def reseed() -> dict[str, Any]:
    result = seed_database(force=True)
    return {"ok": True, **result}


@app.get("/api/overview")
def overview(days: int = Query(30, ge=7, le=60)) -> dict[str, Any]:
    now = now_local()
    start = now - timedelta(days=days)
    prior_start = start - timedelta(days=days)
    current = aggregate(start, now)
    previous = aggregate(prior_start, start)

    names = [
        ("Visits", "sessions"),
        ("Product views", "product_views"),
        ("Add to cart", "add_to_cart"),
        ("Checkout started", "checkout_started"),
        ("Payment attempts", "payment_attempts"),
        ("Orders", "orders"),
    ]
    funnel = []
    previous_count = None
    for label, key in names:
        value = int(current[key])
        funnel.append({
            "stage": label,
            "count": value,
            "rate_from_previous": round(pct(value, previous_count), 1) if previous_count else 100.0,
            "drop_from_previous": round(100 - pct(value, previous_count), 1) if previous_count else 0.0,
            "share_of_visits": round(pct(value, current["sessions"]), 1) if current["sessions"] else 0,
        })
        previous_count = value

    with connect() as con:
        forecast = con.execute(
            """
            SELECT COALESCE(SUM(sessions),0) sessions,
                   COALESCE(SUM(orders),0) orders,
                   COALESCE(SUM(revenue),0) revenue
            FROM traffic_facts
            WHERE bucket_type='forecast' AND occurred_at >= ? AND occurred_at < ?
            """,
            [iso(now), iso(now + timedelta(days=7))],
        ).fetchone()
        last_event = con.execute(
            "SELECT MAX(occurred_at) FROM traffic_facts WHERE bucket_type='actual'"
        ).fetchone()[0]

    return {
        "as_of": iso(now),
        "window_days": days,
        "current": {
            "sessions": int(current["sessions"]),
            "orders": int(current["orders"]),
            "revenue": round(current["revenue"], 2),
            "conversion_rate": round(conversion(current), 2),
            "avg_page_load_ms": round(current["avg_page_load_ms"]),
            "payment_failures": int(current["payment_failures"]),
        },
        "previous": {
            "sessions": int(previous["sessions"]),
            "orders": int(previous["orders"]),
            "revenue": round(previous["revenue"], 2),
            "conversion_rate": round(conversion(previous), 2),
        },
        "conversion_delta_pp": round(conversion(current) - conversion(previous), 2),
        "sessions_delta_pct": round(
            ((current["sessions"] / previous["sessions"]) - 1) * 100, 1
        ) if previous["sessions"] else 0,
        "funnel": funnel,
        "forecast": {
            "sessions": int(forecast["sessions"]),
            "orders": int(forecast["orders"]),
            "revenue": round(forecast["revenue"], 2),
        },
        "last_event_at": last_event,
    }


@app.get("/api/trend")
def trend(days: int = Query(30, ge=7, le=60)) -> dict[str, Any]:
    now = now_local()
    start = now - timedelta(days=days)
    with connect() as con:
        rows = con.execute(
            """
            SELECT substr(occurred_at,1,10) day,
                   SUM(sessions) sessions, SUM(orders) orders
            FROM traffic_facts
            WHERE bucket_type='actual' AND occurred_at >= ? AND occurred_at < ?
            GROUP BY substr(occurred_at,1,10)
            ORDER BY day
            """,
            [iso(start), iso(now)],
        ).fetchall()
    return {"days": days, "points": [
        {"date": row["day"], "sessions": int(row["sessions"]), "orders": int(row["orders"]),
         "conversion_rate": round(pct(row["orders"], row["sessions"]), 2)}
        for row in rows
    ]}


@app.get("/api/diagnostics")
def diagnostics() -> dict[str, Any]:
    latest = latest_diagnostic()
    if latest is None:
        latest = execute_diagnostic("on_open")
    return latest


@app.post("/api/diagnostics/run")
def run_diagnostic() -> dict[str, Any]:
    return execute_diagnostic("manual")


@app.get("/api/runs")
def runs(limit: int = Query(10, ge=1, le=50)) -> dict[str, Any]:
    with connect() as con:
        rows = con.execute(
            "SELECT id, trigger_type, status, started_at, completed_at, summary FROM agent_runs ORDER BY id DESC LIMIT ?",
            [limit],
        ).fetchall()
    return {"runs": [dict(row) for row in rows]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lenskart traffic funnel demo")
    parser.add_argument("--seed", action="store_true", help="create or refresh the synthetic dataset")
    parser.add_argument("--reset", action="store_true", help="clear existing rows before seeding")
    args = parser.parse_args()
    if args.seed:
        result = seed_database(force=args.reset)
        print(json.dumps(result, indent=2))
