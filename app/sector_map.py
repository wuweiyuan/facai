from __future__ import annotations

import csv
from pathlib import Path


def load_sector_map(path: str | Path) -> dict[str, str]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Sector map not found: {file_path}")
    out: dict[str, str] = {}
    with file_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = {"symbol", "sector"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError("Sector map must include headers: symbol,sector")
        for row in reader:
            symbol = str(row.get("symbol", "")).strip().zfill(6)
            sector = str(row.get("sector", "")).strip()
            if not symbol or not sector:
                continue
            out[symbol] = sector
    return out


def summarize_sector_map(path: str | Path, cache_dir: str | Path) -> dict:
    mapping = load_sector_map(path)
    cache_path = Path(cache_dir) / "meta" / "stock_list.csv"
    cached_symbols: set[str] = set()
    if cache_path.exists():
        with cache_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = str(row.get("symbol", "")).strip().zfill(6)
                if symbol:
                    cached_symbols.add(symbol)
    sectors = sorted(set(mapping.values()))
    matched_symbols = sorted(set(mapping) & cached_symbols) if cached_symbols else []
    unmatched_symbols = sorted(set(mapping) - cached_symbols) if cached_symbols else []
    sector_sizes: dict[str, int] = {}
    for sector in mapping.values():
        sector_sizes[sector] = sector_sizes.get(sector, 0) + 1
    top_sectors = sorted(sector_sizes.items(), key=lambda item: (-item[1], item[0]))[:10]
    return {
        "path": str(Path(path)),
        "rows": len(mapping),
        "unique_sectors": len(sectors),
        "matched_cached_symbols": len(matched_symbols),
        "unmatched_symbols": len(unmatched_symbols),
        "top_sectors": [{"sector": name, "count": count} for name, count in top_sectors],
        "sample_matches": matched_symbols[:10],
        "sample_unmatched": unmatched_symbols[:10],
    }
