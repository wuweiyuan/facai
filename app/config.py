from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(path: str | Path) -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError("Config root must be an object")
    return cfg


def merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key, value in base.items():
        if isinstance(value, dict):
            merged[key] = merge_config(value, {})
        elif isinstance(value, list):
            merged[key] = list(value)
        else:
            merged[key] = value
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_config(merged[key], value)
        elif isinstance(value, dict):
            merged[key] = merge_config({}, value)
        elif isinstance(value, list):
            merged[key] = list(value)
        else:
            merged[key] = value
    return merged


def apply_strategy_profile(cfg: dict[str, Any], profile_name: str | None) -> dict[str, Any]:
    if not profile_name:
        return merge_config(cfg, {})
    profiles = cfg.get("strategy_profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("Config key strategy_profiles must be an object")
    profile = profiles.get(profile_name)
    if profile is None:
        raise KeyError(f"Strategy profile not found: {profile_name}")
    if not isinstance(profile, dict):
        raise ValueError(f"Strategy profile must be an object: {profile_name}")
    return merge_config(cfg, profile)


def apply_adaptive_parameter_overrides(
    cfg: dict[str, Any],
    market_label: str,
    command_name: str,
) -> dict[str, Any]:
    adaptive_cfg = cfg.get("adaptive_strategy", {})
    if not isinstance(adaptive_cfg, dict):
        return merge_config(cfg, {})

    all_overrides = adaptive_cfg.get("parameter_overrides", {})
    if not isinstance(all_overrides, dict):
        return merge_config(cfg, {})

    market_overrides = all_overrides.get(market_label, {})
    if not isinstance(market_overrides, dict):
        return merge_config(cfg, {})

    command_overrides = market_overrides.get(command_name, {})
    if not isinstance(command_overrides, dict):
        return merge_config(cfg, {})

    merged = merge_config(cfg, command_overrides)
    strategy_override = command_overrides.get("strategy", {})
    if isinstance(strategy_override, dict) and "pick_count" in strategy_override:
        merged.setdefault("adaptive_strategy", {}).setdefault("strategy_pick_counts", {})[command_name] = (
            strategy_override["pick_count"]
        )
    return merged
