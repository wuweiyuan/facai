from unittest.mock import patch

from app.config import load_config
from app.main import _build_data_source, _build_intraday_pick_data_source


def test_intraday_pick_data_source_uses_project_local_cache_by_default():
    cfg = {
        "data_source": {
            "request_timeout_sec": 7,
            "hist_retries": 4,
            "use_spot_name_merge": True,
            "cache_enabled": True,
            "cache_dir": ".cache/akshare",
        }
    }

    with patch("app.main.AkshareDataSource") as data_source_cls:
        _build_intraday_pick_data_source(cfg)

    data_source_cls.assert_called_once_with(
        request_timeout_sec=7.0,
        hist_retries=4,
        use_spot_name_merge=True,
        cache_enabled=True,
        cache_dir=".cache/intraday-akshare",
    )


def test_intraday_pick_data_source_allows_cache_dir_override():
    cfg = {
        "data_source": {"cache_dir": ".cache/akshare"},
        "intraday_pick": {"cache_dir": ".cache/local-intraday"},
    }

    with patch("app.main.AkshareDataSource") as data_source_cls:
        _build_intraday_pick_data_source(cfg)

    assert data_source_cls.call_args.kwargs["cache_dir"] == ".cache/local-intraday"


def test_regular_data_source_still_uses_primary_cache_dir():
    cfg = {
        "data_source": {
            "cache_dir": ".cache/akshare",
        }
    }

    with patch("app.main.AkshareDataSource") as data_source_cls:
        _build_data_source(cfg)

    assert data_source_cls.call_args.kwargs["cache_dir"] == ".cache/akshare"


def test_default_config_sets_intraday_pick_local_cache_dir():
    cfg = load_config("config/default.yaml")

    assert cfg["intraday_pick"]["cache_dir"] == ".cache/intraday-akshare"
