import pytest
from datetime import datetime, timedelta
from app.account_manager import AccountManager, GoogleAccount


def test_account_freezing_and_unfreezing(tmp_path):
    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))

    # Add account
    acc = mgr.add_account("test@gmail.com", "Test User")
    assert acc is not None

    # Update with low weekly quota (< 4%)
    now = datetime(2026, 10, 1, 12, 0, 0)
    updated = mgr.update_account_quota("test@gmail.com", weekly_percent=2, weekly_refresh_raw="2 hours", now=now)
    assert updated.is_frozen is True
    assert updated.quota_status == "Frozen"
    assert mgr.is_account_frozen(updated, now=now) is True

    # 1 hour later: still frozen
    later_1h = now + timedelta(hours=1)
    assert mgr.is_account_frozen(updated, now=later_1h) is True

    # 4 hours later: (2 hours + 1 hour buffer = 3 hours reset time), at +4h automatically unfrozen!
    later_4h = now + timedelta(hours=4)
    assert mgr.is_account_frozen(updated, now=later_4h) is False
    assert updated.is_frozen is False
    assert updated.quota_status == "Ready"


def test_weekly_reset_hours_plus_one_buffer():
    from app.quota_checker import calculate_weekly_reset_timestamp
    base = datetime(2026, 10, 10, 12, 0, 0)

    # "6 days, 23 hours" -> (6*24 + 23) = 167h + 1h buffer = 168h
    res_wk = calculate_weekly_reset_timestamp("6 days, 23 hours", base_time=base)
    assert res_wk == base + timedelta(hours=168)

    # "23 hours" -> 23 + 1 = 24h
    res_23h = calculate_weekly_reset_timestamp("23 hours", base_time=base)
    assert res_23h == base + timedelta(hours=24)

    # "1 hour" -> 1 + 1 = 2h
    res_1h = calculate_weekly_reset_timestamp("1 hour", base_time=base)
    assert res_1h == base + timedelta(hours=2)

    # "3 hours, 10 minutes" -> smallest unit is minutes -> 3h + (10+1)m = 3h 11m
    res_3h10m = calculate_weekly_reset_timestamp("3 hours, 10 minutes", base_time=base)
    assert res_3h10m == base + timedelta(hours=3, minutes=11)


def test_five_hour_reset_minutes_plus_one_buffer():
    from app.quota_checker import calculate_five_hour_reset_timestamp
    base = datetime(2026, 10, 10, 12, 0, 0)

    # "4 hours, 4 minutes" -> (4*60 + 4) = 244m + 1m buffer = 245m
    res_4h = calculate_five_hour_reset_timestamp("4 hours, 4 minutes", base_time=base)
    assert res_4h == base + timedelta(minutes=245)

    # "1 hour, 14 minutes" -> (1*60 + 14) = 74m + 1m buffer = 75m
    res_1h = calculate_five_hour_reset_timestamp("1 hour, 14 minutes", base_time=base)
    assert res_1h == base + timedelta(minutes=75)

    # "45 minutes" -> 45 + 1 = 46m
    res_45m = calculate_five_hour_reset_timestamp("45 minutes", base_time=base)
    assert res_45m == base + timedelta(minutes=46)


def test_dual_threshold_freezing_policy(tmp_path, monkeypatch):
    from app.config import config
    monkeypatch.setattr(config, "min_gemini_five_hour_quota", 30)
    monkeypatch.setattr(config, "min_gemini_weekly_quota", 4)
    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))
    acc = mgr.add_account("dev.test@example.com", "Mohsin Khan")
    now = datetime(2026, 10, 10, 12, 0, 0)

    # Case 1: Healthy Account (86% 5-hour >= 30%, 97% weekly >= 4%) -> MUST BE USABLE / READY
    healthy = mgr.update_account_quota(
        "dev.test@example.com",
        five_hour_percent=86,
        five_hour_refresh_raw="4 hours, 4 minutes",
        weekly_percent=97,
        weekly_refresh_raw="6 days, 23 hours",
        now=now,
    )
    assert healthy.is_frozen is False
    assert healthy.quota_status == "Ready"
    assert mgr.is_account_frozen(healthy, now=now) is False
    assert healthy in mgr.get_available_accounts(now=now)

    # Case 2: 5-Hour below threshold (19% < 30%) even if Weekly is high (87% >= 4%) -> FROZEN until 5h reset
    low_5h = mgr.update_account_quota(
        "dev.test@example.com",
        five_hour_percent=19,
        five_hour_refresh_raw="1 hour, 14 minutes",
        weekly_percent=87,
        weekly_refresh_raw="6 days, 20 hours",
        now=now,
    )
    assert low_5h.is_frozen is True
    assert low_5h.quota_status == "Frozen"
    assert mgr.is_account_frozen(low_5h, now=now) is True
    assert low_5h not in mgr.get_available_accounts(now=now)

    # 76 minutes later (1h 14m + 1m buffer = 75 minutes reset): automatically unfrozen!
    later_76m = now + timedelta(minutes=76)
    assert mgr.is_account_frozen(low_5h, now=later_76m) is False
    assert low_5h.is_frozen is False
    assert low_5h.quota_status == "Ready"
    assert low_5h in mgr.get_available_accounts(now=later_76m)

    # Case 3: Weekly below threshold (2% < 4%) even if 5-hour is high (100% >= 30%) -> FROZEN
    low_wk = mgr.update_account_quota(
        "dev.test@example.com",
        five_hour_percent=100,
        five_hour_refresh_raw="Unknown",
        weekly_percent=2,
        weekly_refresh_raw="1 hour",
        now=now,
    )
    assert low_wk.is_frozen is True
    assert low_wk.quota_status == "Frozen"
    assert mgr.is_account_frozen(low_wk, now=now) is True
    assert low_wk not in mgr.get_available_accounts(now=now)

    # 3 hours later (1h + 1h buffer = 2h reset): automatically unfrozen!
    later_3h = now + timedelta(hours=3)
    assert mgr.is_account_frozen(low_wk, now=later_3h) is False
    assert low_wk.is_frozen is False
    assert low_wk.quota_status == "Ready"


def test_circular_queue_next_account(tmp_path):
    f = tmp_path / "accounts.json"
    mgr = AccountManager(filepath=str(f))
    mgr.accounts = [
        GoogleAccount(email="acc1@gmail.com", enabled=True),
        GoogleAccount(email="acc2@gmail.com", enabled=True),
        GoogleAccount(email="acc3@gmail.com", enabled=True),
    ]
    mgr.save_accounts()

    assert mgr.get_next_account("acc1@gmail.com").email == "acc2@gmail.com"
    assert mgr.get_next_account("acc2@gmail.com").email == "acc3@gmail.com"
    assert mgr.get_next_account("acc3@gmail.com").email == "acc1@gmail.com"

