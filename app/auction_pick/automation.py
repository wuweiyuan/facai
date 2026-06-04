from __future__ import annotations

import argparse
import plistlib
from pathlib import Path
from typing import Any


LAUNCHD_LABEL = "com.wayne.auction-pick"


def build_launchd_plist(
    project_root: str | Path,
    *,
    python_bin: str = "python3",
    hour: int = 9,
    minute: int = 26,
    count: int = 5,
) -> dict[str, Any]:
    root = Path(project_root).expanduser()
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": ["/bin/bash", str(root / "scripts" / "run_auction_pick_auto.sh")],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": [{"Weekday": day, "Hour": hour, "Minute": minute} for day in range(1, 6)],
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
    count: int = 5,
) -> Path:
    plist = build_launchd_plist(project_root, python_bin=python_bin, hour=hour, minute=minute, count=count)
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
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    path = write_launchd_plist(
        args.output,
        args.project_root,
        python_bin=args.python_bin,
        hour=args.hour,
        minute=args.minute,
        count=args.count,
    )
    print(path)


if __name__ == "__main__":
    main()
