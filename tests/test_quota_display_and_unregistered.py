import pytest
import os
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from app.quota_checker import (
    format_remaining_duration,
    format_verbose_remaining_duration,
    build_quota_tooltip,
    get_smallest_unit_buffer_note,
    calculate_reset_timestamp_with_smallest_unit_buffer,
    parse_hours_from_string,
    parse_minutes_from_string,
    calculate_five_hour_reset_timestamp,
    calculate_weekly_reset_timestamp,
)
from app.account_manager import AccountManager, GoogleAccount
from cdp.quota_inspector import DOMQuotaInspector


def test_format_remaining_duration_multi_unit_precision():
    now = datetime(2026, 10, 11, 12, 0, 0)

    # 4 hours, 58 minutes
    t_4h58m = (now + timedelta(hours=4, minutes=58)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_remaining_duration(t_4h58m, now=now) == "in 4h 58m"

    # 2 hours, 52 minutes
    t_2h52m = (now + timedelta(hours=2, minutes=52)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_remaining_duration(t_2h52m, now=now) == "in 2h 52m"

    # 6 days, 23 hours
    t_6d23h = (now + timedelta(days=6, hours=23)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_remaining_duration(t_6d23h, now=now) == "in 6d 23h"

    # Exactly 1 day (0 hours)
    t_1d = (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_remaining_duration(t_1d, now=now) == "in 1d"

    # 45 minutes
    t_45m = (now + timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_remaining_duration(t_45m, now=now) == "in 45m"

    # 30 seconds (<1m)
    t_30s = (now + timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_remaining_duration(t_30s, now=now) == "in <1m"

    # Past reset (0 or negative)
    t_past = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_remaining_duration(t_past, now=now) == "Reset now"

    # Invalid / empty
    assert format_remaining_duration("", now=now) == "—"
    assert format_remaining_duration("invalid", now=now) == "—"


def test_parse_minutes_from_string():
    assert parse_minutes_from_string("4 hours, 58 minutes") == 298
    assert parse_minutes_from_string("2 hours, 52 minutes") == 172
    assert parse_minutes_from_string("45 minutes") == 45
    assert parse_minutes_from_string("1 day, 2 hours, 10 minutes") == 1440 + 120 + 10  # 1570
    assert parse_minutes_from_string("less than a minute") == 0
    assert parse_minutes_from_string("unknown") is None


def test_dom_quota_segmented_card_parsing():
    page = MagicMock()
    # Simulated Antigravity Models & Usage innerText where Weekly Limit comes first
    dom_text = """
    Settings
    Gemini Models
    Weekly Limit Remaining
    100%
    Resets in 6 days, 23 hours
    Five Hour Limit Remaining
    99%
    Resets in 4 hours, 58 minutes
    Claude & GPT Models
    Claude Limit Remaining 80%
    """
    page.evaluate.return_value = dom_text
    inspector = DOMQuotaInspector(page)
    data = inspector._extract_gemini_limits_from_dom()

    assert data["success"] is True
    assert data["weekly_percent"] == 100
    assert data["weekly_refresh"] == "6 days, 23 hours"
    assert data["five_hour_percent"] == 99
    assert data["five_hour_refresh"] == "4 hours, 58 minutes"


def test_unregistered_account_in_memory_quota(tmp_path):
    json_path = str(tmp_path / "accounts.json")
    mgr = AccountManager(filepath=json_path)
    mgr.accounts = [
        GoogleAccount(email="reg1@example.com", display_name="Registered 1", enabled=True, quota_status="Ready"),
        GoogleAccount(email="reg2@example.com", display_name="Registered 2", enabled=True, quota_status="Ready"),
    ]
    mgr.save_accounts()

    now = datetime(2026, 10, 11, 10, 0, 0)
    # Check quota for an unregistered account
    res = mgr.update_account_quota(
        email="unregistered_user@gmail.com",
        five_hour_percent=95,
        five_hour_refresh_raw="4 hours, 58 minutes",
        weekly_percent=100,
        weekly_refresh_raw="6 days, 23 hours",
        now=now,
    )

    # 1. Result should be None (not in registered accounts list)
    assert res is None

    # 2. In-memory dictionary on AccountManager must exist
    unreg_q = mgr.get_unregistered_quota("unregistered_user@gmail.com")
    assert unreg_q is not None
    assert unreg_q["five_hour_percent"] == 95
    assert unreg_q["weekly_percent"] == 100

    # 3. accounts.json MUST NOT contain unregistered_user@gmail.com
    with open(json_path, "r", encoding="utf-8") as f:
        saved_data = json.load(f)
    saved_emails = [item["email"] for item in saved_data]
    assert "unregistered_user@gmail.com" not in saved_emails

    # 4. Circular queue safely selects first registered account
    next_acc = mgr.get_next_account(current_email="unregistered_user@gmail.com")
    assert next_acc is not None
    assert next_acc.email == "reg1@example.com"

    # 5. Sanitize email with UI tag
    mgr.set_active_account("unregistered_user@gmail.com (Unregistered)")
    assert mgr.current_email == "unregistered_user@gmail.com"


def test_format_verbose_remaining_duration():
    now = datetime(2026, 10, 11, 12, 0, 0)

    # 4 hours, 16 minutes
    t_4h16m = (now + timedelta(hours=4, minutes=16)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_verbose_remaining_duration(t_4h16m, now=now) == "4 hours, 16 minutes"

    # 1 hour, 1 minute
    t_1h1m = (now + timedelta(hours=1, minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_verbose_remaining_duration(t_1h1m, now=now) == "1 hour, 1 minute"

    # 2 hours, 0 minutes
    t_2h = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_verbose_remaining_duration(t_2h, now=now) == "2 hours"

    # 6 days, 23 hours
    t_6d23h = (now + timedelta(days=6, hours=23)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_verbose_remaining_duration(t_6d23h, now=now) == "6 days, 23 hours"

    # 1 day
    t_1d = (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_verbose_remaining_duration(t_1d, now=now) == "1 day"

    # 45 minutes
    t_45m = (now + timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_verbose_remaining_duration(t_45m, now=now) == "45 minutes"

    # 1 minute
    t_1m = (now + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_verbose_remaining_duration(t_1m, now=now) == "1 minute"

    # 30 seconds (<1m)
    t_30s = (now + timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_verbose_remaining_duration(t_30s, now=now) == "less than a minute"

    # Past reset
    t_past = (now - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_verbose_remaining_duration(t_past, now=now) == "Reset now"

    # Empty / invalid
    assert format_verbose_remaining_duration("", now=now) == "Unknown"
    assert format_verbose_remaining_duration("invalid", now=now) == "Unknown"


def test_build_quota_tooltip():
    now = datetime(2026, 10, 11, 12, 0, 0)
    reset_at = (now + timedelta(hours=4, minutes=16)).strftime("%Y-%m-%d %H:%M:%S")

    # Dynamic buffer note resolution from raw_refresh
    tip_dyn = build_quota_tooltip(
        limit_name="5-Hour",
        reset_at_str=reset_at,
        raw_refresh="4 hours, 15 minutes",
        now=now,
    )
    assert "5-Hour Limit Reset:" in tip_dyn
    assert "• Remaining: 4 hours, 16 minutes" in tip_dyn
    assert f"• Reset Time: {reset_at} (+1 minute buffer)" in tip_dyn

    # Explicit buffer note override
    tip = build_quota_tooltip(
        limit_name="5-Hour",
        reset_at_str=reset_at,
        raw_refresh="4 hours, 15 minutes",
        buffer_note="(Custom buffer)",
        now=now,
    )
    assert f"• Reset Time: {reset_at} (Custom buffer)" in tip

    # Fallback to raw when reset_at_str is None
    tip_raw = build_quota_tooltip(
        limit_name="Weekly",
        reset_at_str=None,
        raw_refresh="6 days, 23 hours",
        now=now,
    )
    assert "Weekly Limit Reset:" in tip_raw
    assert "• Remaining: ~6 days, 23 hours" in tip_raw
    assert "• Reset Time: Pending quota check" in tip_raw

    # Neither reset_at nor raw
    tip_none = build_quota_tooltip(
        limit_name="5-Hour",
        reset_at_str="",
        raw_refresh="",
    )
    assert "5-Hour Limit Reset:" in tip_none
    assert "• No reset data recorded (inspect quota to update)" in tip_none


def test_get_smallest_unit_buffer_note():
    assert get_smallest_unit_buffer_note("6 days, 23 hours") == "(+1 hour buffer)"
    assert get_smallest_unit_buffer_note("3 hours, 10 minutes") == "(+1 minute buffer)"
    assert get_smallest_unit_buffer_note("4 hours, 58 minutes") == "(+1 minute buffer)"
    assert get_smallest_unit_buffer_note("45 minutes") == "(+1 minute buffer)"
    assert get_smallest_unit_buffer_note("23 hours") == "(+1 hour buffer)"
    assert get_smallest_unit_buffer_note("1 day") == "(+1 day buffer)"
    assert get_smallest_unit_buffer_note("less than a minute") == "(+1 minute buffer)"
    assert get_smallest_unit_buffer_note("30 seconds") == "(+1 second buffer)"
    assert get_smallest_unit_buffer_note("") == ""
    assert get_smallest_unit_buffer_note("unknown") == ""


def test_calculate_reset_timestamp_with_smallest_unit_buffer():
    base = datetime(2026, 10, 11, 10, 0, 0)

    # 1. Days & Hours -> increments hour (+1 hour)
    # "6 days, 23 hours" -> (6*24 + 23 + 1) = 168h
    t_6d23h = calculate_reset_timestamp_with_smallest_unit_buffer("6 days, 23 hours", base_time=base)
    assert t_6d23h == base + timedelta(days=6, hours=24)

    # 2. Hours & Minutes -> increments minute (+1 minute)
    # "3 hours, 10 minutes" -> 3h 11m
    t_3h10m = calculate_reset_timestamp_with_smallest_unit_buffer("3 hours, 10 minutes", base_time=base)
    assert t_3h10m == base + timedelta(hours=3, minutes=11)

    # "4 hours, 58 minutes" -> 4h 59m
    t_4h58m = calculate_reset_timestamp_with_smallest_unit_buffer("4 hours, 58 minutes", base_time=base)
    assert t_4h58m == base + timedelta(hours=4, minutes=59)

    # 3. Minutes only -> increments minute (+1 minute)
    # "45 minutes" -> 46m
    t_45m = calculate_reset_timestamp_with_smallest_unit_buffer("45 minutes", base_time=base)
    assert t_45m == base + timedelta(minutes=46)

    # 4. Hours only -> increments hour (+1 hour)
    # "23 hours" -> 24h
    t_23h = calculate_reset_timestamp_with_smallest_unit_buffer("23 hours", base_time=base)
    assert t_23h == base + timedelta(hours=24)

    # 5. Days only -> increments day (+1 day)
    # "1 day" -> 2 days
    t_1d = calculate_reset_timestamp_with_smallest_unit_buffer("1 day", base_time=base)
    assert t_1d == base + timedelta(days=2)

    # 6. "less than a minute" -> +1 minute
    t_sub_min = calculate_reset_timestamp_with_smallest_unit_buffer("less than a minute", base_time=base)
    assert t_sub_min == base + timedelta(minutes=1)

    # 7. Invalid / empty -> None
    assert calculate_reset_timestamp_with_smallest_unit_buffer("", base_time=base) is None
    assert calculate_reset_timestamp_with_smallest_unit_buffer("invalid", base_time=base) is None


def test_format_time_ago():
    from app.quota_checker import format_time_ago
    base = datetime(2026, 10, 11, 15, 0, 0)

    # 30 seconds ago -> "just now"
    t_30s = (base - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_time_ago(t_30s, now=base) == "just now"

    # 5 minutes ago -> "5m ago"
    t_5m = (base - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_time_ago(t_5m, now=base) == "5m ago"

    # 2 hours 15 minutes ago -> "2h 15m ago"
    t_2h15m = (base - timedelta(hours=2, minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_time_ago(t_2h15m, now=base) == "2h 15m ago"

    # 3 days ago -> "3d ago"
    t_3d = (base - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    assert format_time_ago(t_3d, now=base) == "3d ago"

    # Empty / unknown
    assert format_time_ago("", now=base) == "never"
    assert format_time_ago("unknown", now=base) == "never"


def test_build_percentage_tooltip():
    from app.quota_checker import build_percentage_tooltip
    base = datetime(2026, 10, 11, 15, 0, 0)
    chk_time = (base - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
    reset_time = (base + timedelta(hours=3, minutes=15)).strftime("%Y-%m-%d %H:%M:%S")

    tip = build_percentage_tooltip(
        limit_name="5-Hour",
        percent=58,
        last_checked_str=chk_time,
        check_reason="Pre-dispatch check (Project Alpha)",
        reset_at_str=reset_time,
        raw_refresh="3 hours, 15 minutes",
        now=base,
    )

    assert "5-Hour Quota: 58% remaining" in tip
    assert f"• Last Updated: {chk_time} (20m ago)" in tip
    assert "• Trigger: Pre-dispatch check (Project Alpha)" in tip
    assert "• Reset In: 3 hours, 15 minutes" in tip
    assert "• Reset Time: " in tip




