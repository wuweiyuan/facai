from __future__ import annotations

from datetime import date

from app.models import StockInfo


def _excluded_board(symbol: str, exclude_star: bool, exclude_bj: bool, exclude_gem: bool) -> bool:
    if exclude_star and symbol.startswith("688"):
        return True
    if bool(exclude_gem) and symbol.startswith("300"):
        return True
    if exclude_bj and symbol.startswith(("4", "8")):
        return True
    return False


def filter_universe(stocks: list[StockInfo], cfg: dict, as_of_date: date) -> list[StockInfo]:
    _ = as_of_date
    filt = cfg.get("filters", {})
    exclude_star = bool(filt.get("exclude_star_board", True))
    exclude_bj = bool(filt.get("exclude_bj_board", True))
    exclude_gem = bool(filt.get("exclude_gem_board", True))
    exclude_st = bool(filt.get("exclude_st", True))

    out: list[StockInfo] = []
    for s in stocks:
        if exclude_st and s.is_st:
            continue
        if s.is_paused:
            continue
        if _excluded_board(s.symbol, exclude_star, exclude_bj, exclude_gem):
            continue
        out.append(s)

    limit = int(cfg.get("universe", {}).get("limit", 0))
    if limit <= 0:
        return out
    return out[:limit]
