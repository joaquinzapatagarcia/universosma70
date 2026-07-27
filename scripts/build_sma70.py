#!/usr/bin/env python3
"""Construye el mapa SMA70 semanal de UNIVERSO SMA70.

Fuente primaria operativa: endpoint público de series históricas de Yahoo Finance.
No modifica HTML ni publica ediciones. Solo genera datos auditables en JSON/Markdown.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "markets.json"
OUTPUT_JSON = ROOT / "data" / "sma70-latest.json"
OUTPUT_MD = ROOT / "data" / "sma70-latest.md"
USER_AGENT = "Mozilla/5.0 UNIVERSO-SMA70/1.0"


@dataclass(frozen=True)
class DailyClose:
    date: dt.date
    close: float


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def yahoo_chart(symbol: str, years: int = 4, retries: int = 3) -> tuple[list[DailyClose], dict[str, Any]]:
    period2 = int(time.time())
    period1 = period2 - years * 366 * 24 * 3600
    encoded = urllib.parse.quote(symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history"
        "&includeAdjustedClose=true"
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = json.load(response)
            result = payload.get("chart", {}).get("result")
            error = payload.get("chart", {}).get("error")
            if error or not result:
                raise RuntimeError(f"respuesta sin serie: {error}")
            item = result[0]
            meta = item.get("meta", {})
            timezone_name = meta.get("exchangeTimezoneName") or "UTC"
            try:
                market_tz = ZoneInfo(timezone_name)
            except Exception:
                market_tz = ZoneInfo("UTC")
            timestamps = item.get("timestamp") or []
            quote = (item.get("indicators", {}).get("quote") or [{}])[0]
            closes = quote.get("close") or []
            rows: list[DailyClose] = []
            for timestamp, close in zip(timestamps, closes):
                if close is None:
                    continue
                value = float(close)
                if not math.isfinite(value) or value <= 0:
                    continue
                date = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).astimezone(market_tz).date()
                rows.append(DailyClose(date=date, close=value))
            if len(rows) < 300:
                raise RuntimeError(f"histórico insuficiente: {len(rows)} cierres diarios")
            return rows, {
                "symbol": symbol,
                "currency": meta.get("currency"),
                "exchange": meta.get("exchangeName"),
                "timezone": timezone_name,
                "instrument_type": meta.get("instrumentType"),
                "source_url": url,
            }
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"fallo al obtener {symbol}: {last_error}")


def week_period(date: dt.date, week_end: int) -> tuple[dt.date, dt.date]:
    # week_end usa weekday de Python: lunes=0 ... domingo=6.
    delta_to_end = (week_end - date.weekday()) % 7
    end = date + dt.timedelta(days=delta_to_end)
    start = end - dt.timedelta(days=6)
    return start, end


def completed_weekly_closes(rows: list[DailyClose], week_end: int, today: dt.date) -> list[DailyClose]:
    by_period: dict[dt.date, DailyClose] = {}
    for row in rows:
        _, period_end = week_period(row.date, week_end)
        current = by_period.get(period_end)
        if current is None or row.date > current.date:
            by_period[period_end] = row
    completed = [value for period_end, value in by_period.items() if period_end <= today]
    completed.sort(key=lambda item: item.date)
    return completed


def state_for(distance: float, zone: float) -> str:
    if distance > zone:
        return "above"
    if distance < -zone:
        return "below"
    return "zone"


def crossing(previous: str, current: str) -> str | None:
    if previous == current:
        return None
    direction = {
        ("below", "zone"): "entra en zona desde abajo",
        ("above", "zone"): "entra en zona desde arriba",
        ("zone", "above"): "sale de zona hacia arriba",
        ("zone", "below"): "sale de zona hacia abajo",
        ("below", "above"): "cruce directo hacia arriba",
        ("above", "below"): "cruce directo hacia abajo",
    }
    return direction.get((previous, current), f"cambia de {previous} a {current}")


def compute_market(market: dict[str, Any], method: dict[str, Any], today: dt.date) -> dict[str, Any]:
    errors: list[str] = []
    rows: list[DailyClose] | None = None
    metadata: dict[str, Any] | None = None
    selected_symbol: str | None = None
    for symbol in market["symbols"]:
        try:
            rows, metadata = yahoo_chart(symbol)
            selected_symbol = symbol
            break
        except Exception as exc:
            errors.append(str(exc))
    if rows is None or metadata is None or selected_symbol is None:
        return {
            **market,
            "status": "unavailable",
            "errors": errors,
            "source": "Yahoo Finance chart API",
        }

    weekly = completed_weekly_closes(rows, int(market.get("week_end", 4)), today)
    required = int(method["minimum_weekly_points"])
    length = int(method["sma_weeks"])
    if len(weekly) < required:
        return {
            **market,
            "symbol_used": selected_symbol,
            "status": "insufficient_history",
            "weekly_points": len(weekly),
            "required_weekly_points": required,
            "errors": errors,
            "source": "Yahoo Finance chart API",
            "source_metadata": metadata,
        }

    latest_window = weekly[-length:]
    previous_window = weekly[-length - 1:-1]
    latest = latest_window[-1]
    prior = weekly[-2]
    sma = fmean(item.close for item in latest_window)
    prior_sma = fmean(item.close for item in previous_window)
    distance = (latest.close / sma - 1.0) * 100.0
    prior_distance = (prior.close / prior_sma - 1.0) * 100.0
    weekly_change = (latest.close / prior.close - 1.0) * 100.0
    zone = float(method["zone_percent"])
    current_state = state_for(distance, zone)
    previous_state = state_for(prior_distance, zone)

    return {
        **market,
        "symbol_used": selected_symbol,
        "status": "verified",
        "source": "Yahoo Finance chart API",
        "source_metadata": metadata,
        "weekly_points": len(weekly),
        "weekly_close_date": latest.date.isoformat(),
        "weekly_close": round(latest.close, 6),
        "previous_weekly_close_date": prior.date.isoformat(),
        "previous_weekly_close": round(prior.close, 6),
        "sma70": round(sma, 6),
        "distance_percent": round(distance, 4),
        "weekly_change_percent": round(weekly_change, 4),
        "state": current_state,
        "previous_state": previous_state,
        "crossing": crossing(previous_state, current_state),
        "calculation": "SMA70 = media simple de los 70 cierres semanales completos más recientes; distancia = ((cierre/SMA70)-1)*100",
        "errors_from_fallbacks": errors,
    }


def build_summary(markets: list[dict[str, Any]], world_id: str) -> dict[str, Any]:
    verified = [market for market in markets if market.get("status") == "verified"]
    counts = {
        "above": sum(market["state"] == "above" for market in verified),
        "zone": sum(market["state"] == "zone" for market in verified),
        "below": sum(market["state"] == "below" for market in verified),
        "verified": len(verified),
        "unavailable": len(markets) - len(verified),
    }
    strongest = sorted(verified, key=lambda item: item["distance_percent"], reverse=True)[:5]
    weakest = sorted(verified, key=lambda item: item["distance_percent"])[:5]
    nearest = sorted(verified, key=lambda item: abs(item["distance_percent"]))[:5]
    crossings = [market for market in verified if market.get("crossing")]
    world = next((market for market in markets if market["id"] == world_id), None)
    non_world = [market for market in verified if market["id"] != world_id]
    breadth = None
    if non_world:
        breadth = round(sum(market["state"] == "above" for market in non_world) / len(non_world) * 100.0, 2)
    return {
        "counts": counts,
        "breadth_above_percent_ex_world": breadth,
        "strongest": [rank_item(item) for item in strongest],
        "weakest": [rank_item(item) for item in weakest],
        "nearest": [rank_item(item) for item in nearest],
        "crossings": [rank_item(item) | {"crossing": item["crossing"]} for item in crossings],
        "world": world,
        "publication_ready": len(verified) == len(markets) and world is not None and world.get("status") == "verified",
    }


def rank_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "country": item["country"],
        "name": item["name"],
        "distance_percent": item["distance_percent"],
        "weekly_change_percent": item["weekly_change_percent"],
        "state": item["state"],
    }


def markdown_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# MAPA SMA70 — SALIDA DE CÁLCULO",
        "",
        f"Generado: {payload['generated_at']}  ",
        f"Fecha de referencia: {payload['as_of_date']}  ",
        f"Estado de publicación: {'APTO' if summary['publication_ready'] else 'NO APTO'}",
        "",
        "## Resumen",
        "",
        f"- Por encima: {summary['counts']['above']}",
        f"- Zona ±5 %: {summary['counts']['zone']}",
        f"- Por debajo: {summary['counts']['below']}",
        f"- Verificados: {summary['counts']['verified']} de {len(payload['markets'])}",
        f"- Amplitud por encima, excluido MUNDO: {summary['breadth_above_percent_ex_world']} %",
        "",
        "## Mercados",
        "",
        "| Mercado | Cierre semanal | SMA70 | Distancia | Cambio semanal | Estado | Cruce |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for market in payload["markets"]:
        if market.get("status") == "verified":
            lines.append(
                f"| {market['country']} · {market['name']} | {market['weekly_close']:.4f} "
                f"({market['weekly_close_date']}) | {market['sma70']:.4f} | "
                f"{market['distance_percent']:+.2f} % | {market['weekly_change_percent']:+.2f} % | "
                f"{market['state']} | {market.get('crossing') or '—'} |"
            )
        else:
            lines.append(f"| {market['country']} · {market['name']} | — | — | — | — | {market['status']} | — |")
    lines.extend([
        "",
        "## Regla de seguridad",
        "",
        "El SITE y el informe diario no deben presentar este mapa como actualizado si `publication_ready` es falso.",
        "MUNDO se obtiene de ACWI como referencia global ponderada y nunca como media simple del resto.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Fecha de cálculo YYYY-MM-DD; por defecto, hoy en Europe/Madrid")
    parser.add_argument("--strict", action="store_true", help="Devuelve error si falta cualquier mercado")
    args = parser.parse_args()

    config = read_json(CONFIG_PATH)
    timezone = ZoneInfo(config["method"]["timezone"])
    now = dt.datetime.now(timezone)
    today = dt.date.fromisoformat(args.date) if args.date else now.date()

    results = [compute_market(market, config["method"], today) for market in config["markets"]]
    summary = build_summary(results, config["method"]["world_id"])
    payload = {
        "schema_version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "as_of_date": today.isoformat(),
        "timezone": config["method"]["timezone"],
        "method": config["method"],
        "summary": summary,
        "markets": results,
        "warnings": [
            "Fuente operativa pública no oficial; contrastar cierres críticos antes de publicación editorial.",
            "No publicar si summary.publication_ready es falso.",
            "Los datos de la semana en curso solo se incluyen cuando ha concluido el día de cierre configurado.",
        ],
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD.write_text(markdown_report(payload), encoding="utf-8")

    print(json.dumps({
        "publication_ready": summary["publication_ready"],
        "verified": summary["counts"]["verified"],
        "total": len(results),
        "output": str(OUTPUT_JSON.relative_to(ROOT)),
    }, ensure_ascii=False))

    if args.strict and not summary["publication_ready"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
