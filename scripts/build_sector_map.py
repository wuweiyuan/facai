from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

import akshare as ak


def normalize_symbol(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if "." in text:
        text = text.split(".", 1)[0].strip()
    return text.zfill(6)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build local symbol->sector map from AkShare industry boards.")
    parser.add_argument("--output", default="data/sector_map.csv", help="Output CSV path")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    names_df = ak.stock_board_industry_name_em()
    if names_df.empty:
        raise RuntimeError("No industry boards returned from AkShare")

    sector_map: dict[str, str] = {}
    sector_rows: list[tuple[str, str]] = []
    failed_boards: list[str] = []

    for _, row in names_df.iterrows():
        board_name = str(row.get("板块名称", "") or "").strip()
        if not board_name:
            continue
        try:
            cons_df = ak.stock_board_industry_cons_em(symbol=board_name)
        except Exception:
            failed_boards.append(board_name)
            continue
        if cons_df.empty:
            continue
        for _, cons_row in cons_df.iterrows():
            symbol = normalize_symbol(cons_row.get("代码"))
            if not symbol:
                continue
            # Keep first-hit mapping to avoid overwriting with duplicate or noisy boards.
            if symbol in sector_map:
                continue
            sector_map[symbol] = board_name
            sector_rows.append((symbol, board_name))

    out_df = pd.DataFrame(sector_rows, columns=["symbol", "sector"]).sort_values(["sector", "symbol"]).reset_index(drop=True)
    out_df.to_csv(output_path, index=False)

    print(f"saved: {output_path}")
    print(f"rows: {len(out_df)}")
    print(f"unique_sectors: {out_df['sector'].nunique() if not out_df.empty else 0}")
    if failed_boards:
        preview = ", ".join(failed_boards[:10])
        print(f"failed_boards: {len(failed_boards)}")
        print(f"failed_preview: {preview}")


if __name__ == "__main__":
    main()
