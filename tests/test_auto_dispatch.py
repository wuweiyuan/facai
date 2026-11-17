from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from app.auto_dispatch import dispatch_pick


@pytest.mark.parametrize("kind", ["auction", "tail"])
def test_closed_day_does_not_start_pick_runner(kind):
    with patch("app.auto_dispatch.os.execv") as execv:
        assert dispatch_pick(kind, date(2026, 10, 1)) == 0

    execv.assert_not_called()


@pytest.mark.parametrize(
    ("kind", "script"),
    [("auction", "run_auction_pick_auto.sh"), ("tail", "run_tail_pick_auto.sh")],
)
def test_trading_day_starts_matching_pick_runner(kind, script):
    with patch("app.auto_dispatch.os.execv") as execv:
        assert dispatch_pick(kind, date(2026, 10, 8)) == 0

    execv.assert_called_once_with(
        "/bin/bash",
        ["/bin/bash", str(Path(__file__).resolve().parents[1] / "scripts" / script)],
    )


def test_year_without_holiday_calendar_does_not_start_pick_runner():
    with patch("app.auto_dispatch.os.execv") as execv:
        with pytest.raises(ValueError, match="2027"):
            dispatch_pick("auction", date(2027, 1, 4))

    execv.assert_not_called()
