from __future__ import annotations

import argparse
import csv
from pathlib import Path

import akshare as ak
import pandas as pd


def _load_symbols(stock_list_path: Path) -> list[str]:
    symbols: list[str] = []
    with stock_list_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            symbol = str(row.get("symbol", "")).strip().zfill(6)
            if symbol:
                symbols.append(symbol)
    return symbols


def _load_existing(output_path: Path) -> dict[str, dict]:
    if not output_path.exists():
        return {}
    out: dict[str, dict] = {}
    with output_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            symbol = str(row.get("symbol", "")).strip().zfill(6)
            if symbol:
                out[symbol] = row
    return out


def _pick_latest_sector(df: pd.DataFrame, standard: str, sector_col: str) -> dict | None:
    if df.empty:
        return None
    working = df.copy()
    if "分类标准" in working.columns:
        working = working[working["分类标准"] == standard].copy()
    if working.empty:
        return None
    working["变更日期"] = pd.to_datetime(working["变更日期"], errors="coerce")
    working = working.sort_values("变更日期")
    latest = working.iloc[-1]
    sector = str(latest.get(sector_col, "") or "").strip()
    if not sector or sector.lower() == "nan":
        # fallback to 行业大类 if 行业中类缺失
        sector = str(latest.get("行业大类", "") or "").strip()
    if not sector or sector.lower() == "nan":
        return None
    return {
        "symbol": str(latest.get("证券代码", "")).strip().zfill(6),
        "sector": sector,
        "sector_standard": standard,
        "change_date": latest.get("变更日期").strftime("%Y-%m-%d") if pd.notna(latest.get("变更日期")) else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build resumable sector map from CNInfo per-stock industry mapping.")
    parser.add_argument("--stock-list", default=".cache/akshare/meta/stock_list.csv", help="Path to stock list CSV")
    parser.add_argument("--output", default="data/sector_map.csv", help="Output sector map CSV path")
    parser.add_argument("--standard", default="申银万国行业分类标准", help="Industry standard name used in CNInfo")
    parser.add_argument("--sector-col", default="行业中类", help="Preferred column to use as sector name")
    parser.add_argument("--limit", type=int, default=0, help="Optional limit on number of stocks to process")
    args = parser.parse_args()

    stock_list_path = Path(args.stock_list)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    symbols = _load_symbols(stock_list_path)
    existing = _load_existing(output_path)
    rows: list[dict] = list(existing.values())
    existing_symbols = set(existing)
    processed = 0
    added = 0
    failures: list[str] = []

    for symbol in symbols:
        if args.limit > 0 and processed >= args.limit:
            break
        if symbol in existing_symbols:
            continue
        processed += 1
        try:
            df = ak.stock_industry_change_cninfo(symbol=symbol, start_date="20000101", end_date="20260331")
            picked = _pick_latest_sector(df, args.standard, args.sector_col)
            if picked is None:
                failures.append(symbol)
                continue
            rows.append(picked)
            existing_symbols.add(symbol)
            added += 1
        except Exception:
            failures.append(symbol)
            continue

        if added % 50 == 0:
            pd.DataFrame(rows).sort_values("symbol").to_csv(output_path, index=False)
            print(f"saved checkpoint: rows={len(rows)} processed={processed} added={added}")

    if rows:
        pd.DataFrame(rows).sort_values("symbol").to_csv(output_path, index=False)
    print(f"saved: {output_path}")
    print(f"rows: {len(rows)}")
    print(f"processed: {processed}")
    print(f"added: {added}")
    print(f"failed: {len(failures)}")
    if failures:
        print(f"failed_preview: {', '.join(failures[:20])}")


if __name__ == "__main__":
    main()
