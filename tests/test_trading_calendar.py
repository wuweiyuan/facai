from pathlib import Path
import importlib.util
from datetime import date, timedelta

from app.trading_calendar import TradingDayCheck, is_a_share_trading_day


def _load_update_script():
    spec = importlib.util.spec_from_file_location(
        "update_a_share_calendar",
        Path("scripts/update_a_share_calendar.py"),
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_a_share_trading_day_skips_weekends_and_cached_closed_weekdays(tmp_path):
    closed_weekdays = tmp_path / "closed_weekdays.csv"
    closed_weekdays.write_text("date,reason\n2026-02-16,Spring Festival\n", encoding="utf-8")

    assert is_a_share_trading_day("2026-02-14", closed_weekdays_path=closed_weekdays) == TradingDayCheck(
        is_trading_day=False,
        reason="weekend",
    )
    assert is_a_share_trading_day("2026-02-16", closed_weekdays_path=closed_weekdays) == TradingDayCheck(
        is_trading_day=False,
        reason="A-share market closed: Spring Festival",
    )
    assert is_a_share_trading_day("2026-02-24", closed_weekdays_path=closed_weekdays) == TradingDayCheck(
        is_trading_day=True,
        reason="A-share trading day",
    )


def test_a_share_closed_weekday_cache_includes_2026_holidays():
    cache_path = Path("data/a_share_closed_weekdays.csv")

    result = is_a_share_trading_day("2026-10-01", closed_weekdays_path=cache_path)

    assert result == TradingDayCheck(
        is_trading_day=False,
        reason="A-share market closed: National Day/Mid-Autumn Festival",
    )


def test_update_a_share_calendar_replaces_target_year_and_preserves_other_years(tmp_path):
    updater = _load_update_script()
    calendar_path = tmp_path / "closed_weekdays.csv"
    calendar_path.write_text(
        "date,reason\n"
        "2026-10-01,National Day\n"
        "2027-01-01,old reason\n"
        "2028-01-03,Future holiday\n",
        encoding="utf-8",
    )
    trade_dates = []
    current = date(2027, 1, 1)
    while current <= date(2027, 12, 31):
        if current.weekday() < 5 and current.isoformat() not in {"2027-01-01", "2027-01-07", "2027-01-08"}:
            trade_dates.append(current.isoformat())
        current += timedelta(days=1)

    updated = updater.update_closed_weekdays_cache(
        2027,
        trade_dates,
        calendar_path,
        reason="Exchange holiday",
    )

    assert [row["date"] for row in updated] == ["2027-01-01", "2027-01-07", "2027-01-08"]
    assert calendar_path.read_text(encoding="utf-8") == (
        "date,reason\n"
        "2026-10-01,National Day\n"
        "2027-01-01,Exchange holiday\n"
        "2027-01-07,Exchange holiday\n"
        "2027-01-08,Exchange holiday\n"
        "2028-01-03,Future holiday\n"
    )


def test_update_a_share_calendar_refuses_year_missing_from_trade_dates(tmp_path):
    updater = _load_update_script()
    calendar_path = tmp_path / "closed_weekdays.csv"
    calendar_path.write_text("date,reason\n", encoding="utf-8")

    try:
        updater.update_closed_weekdays_cache(2027, ["2026-12-31"], calendar_path)
    except ValueError as exc:
        assert "does not contain any 2027 trading dates" in str(exc)
    else:
        raise AssertionError("expected update to fail when target year is missing")
