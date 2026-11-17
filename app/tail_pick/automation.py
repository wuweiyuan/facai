from __future__ import annotations

import argparse
import plistlib
from datetime import date
from pathlib import Path
from typing import Any

from app.trading_calendar import trading_day_launchd_intervals


LAUNCHD_LABEL = "com.wayne.tail-pick"


def build_launchd_plist(
    project_root: str | Path,
    *,
    python_bin: str = "python3",
    hour: int = 14,
    minute: int = 44,
    schedule_year: int | None = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser()
    year = schedule_year if schedule_year is not None else date.today().year
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": ["/usr/bin/env", python_bin, "-m", "app.auto_dispatch", "tail"],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": trading_day_launchd_intervals(year, hour=hour, minute=minute),
        "EnvironmentVariables": {
            "PYTHON_BIN": python_bin,
        },
        "StandardOutPath": str(root / "reports" / "tail_pick" / "launchd.out.log"),
        "StandardErrorPath": str(root / "reports" / "tail_pick" / "launchd.err.log"),
        "RunAtLoad": False,
    }


def write_launchd_plist(
    output_path: str | Path,
    project_root: str | Path,
    *,
    python_bin: str = "python3",
    hour: int = 14,
    minute: int = 44,
    schedule_year: int | None = None,
) -> Path:
    plist = build_launchd_plist(project_root, python_bin=python_bin, hour=hour, minute=minute, schedule_year=schedule_year)
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(plist, f)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Write tail-pick launchd plist")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--python-bin", default="python3")
    parser.add_argument("--hour", type=int, default=14)
    parser.add_argument("--minute", type=int, default=44)
    parser.add_argument("--schedule-year", type=int)
    args = parser.parse_args()
    path = write_launchd_plist(
        args.output,
        args.project_root,
        python_bin=args.python_bin,
        hour=args.hour,
        minute=args.minute,
        schedule_year=args.schedule_year,
    )
    print(path)


if __name__ == "__main__":
    main()
