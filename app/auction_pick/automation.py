from __future__ import annotations

import argparse
import plistlib
from datetime import date
from pathlib import Path
from typing import Any

from app.trading_calendar import trading_day_launchd_intervals


LAUNCHD_LABEL = "com.wayne.auction-pick"


def build_launchd_plist(
    project_root: str | Path,
    *,
    python_bin: str = "python3",
    hour: int = 9,
    minute: int = 26,
    count: int = 2,
    schedule_year: int | None = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser()
    year = schedule_year if schedule_year is not None else date.today().year
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": ["/usr/bin/env", python_bin, "-m", "app.auto_dispatch", "auction"],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": trading_day_launchd_intervals(year, hour=hour, minute=minute),
        "EnvironmentVariables": {
            "PYTHON_BIN": python_bin,
            "AUCTION_COUNT": str(count),
        },
        "StandardOutPath": str(root / "reports" / "auction_pick" / "launchd.out.log"),
        "StandardErrorPath": str(root / "reports" / "auction_pick" / "launchd.err.log"),
        "RunAtLoad": False,
    }


def write_launchd_plist(
    output_path: str | Path,
    project_root: str | Path,
    *,
    python_bin: str = "python3",
    hour: int = 9,
    minute: int = 26,
    count: int = 2,
    schedule_year: int | None = None,
) -> Path:
    plist = build_launchd_plist(project_root, python_bin=python_bin, hour=hour, minute=minute, count=count, schedule_year=schedule_year)
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(plist, f)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Write auction-pick launchd plist")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--python-bin", default="python3")
    parser.add_argument("--hour", type=int, default=9)
    parser.add_argument("--minute", type=int, default=26)
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--schedule-year", type=int)
    args = parser.parse_args()
    path = write_launchd_plist(
        args.output,
        args.project_root,
        python_bin=args.python_bin,
        hour=args.hour,
        minute=args.minute,
        count=args.count,
        schedule_year=args.schedule_year,
    )
    print(path)


if __name__ == "__main__":
    main()
