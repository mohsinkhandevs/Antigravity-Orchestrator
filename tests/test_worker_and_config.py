import pytest
from app.config import AppConfig
from app.models import PromptItem, TaskStatus
from app.worker import AutomationWorker, format_duration
from cdp.client import is_port_listening


def test_format_duration():
    assert format_duration(12.4) == "12.4s"
    assert format_duration(75.0) == "1m 15s"
    assert format_duration(3665.0) == "1h 01m 05s"


def test_config_defaults_and_serialization(tmp_path):
    cfg_file = str(tmp_path / "config.json")
    cfg = AppConfig(cdp_port=9222, target_project_name="TestProject")
    cfg.save(cfg_file)

    loaded = AppConfig.load(cfg_file)
    assert loaded.cdp_port == 9222
    assert loaded.target_project_name == "TestProject"
    assert loaded.low_quota_action == "change_account"
    assert loaded.auto_switch_on_low_quota is True


def test_worker_initialization():
    items = [PromptItem(text="Test 1"), PromptItem(text="Test 2")]
    worker = AutomationWorker(queue=items, target_hwnd=0, countdown_seconds=0)

    assert len(worker.queue) == 2
    assert worker.current_index == 0
    assert worker.is_paused is False
    assert worker.cdp_client.port > 0


def test_is_port_listening_closed_port():
    # An arbitrarily high unassigned port should return False
    assert is_port_listening(59999) is False


def test_worker_wait_for_idle_agent():
    from unittest.mock import MagicMock
    items = [PromptItem(text="Test 1")]
    worker = AutomationWorker(queue=items, target_hwnd=0, countdown_seconds=0)

    # When controller is None or not generating, should return True immediately
    worker.dom_controller = None
    assert worker._wait_for_idle_agent() is True

    # When controller is generating, should call wait_for_completion
    mock_dom = MagicMock()
    mock_dom.is_generating.return_value = True
    mock_dom.wait_for_completion.return_value = True
    worker.dom_controller = mock_dom

    assert worker._wait_for_idle_agent("testing") is True
    mock_dom.wait_for_completion.assert_called_once()


def test_worker_enforce_quota_waits_for_reset(monkeypatch):
    from unittest.mock import MagicMock
    from app.config import config

    monkeypatch.setattr(config, "low_quota_action", "wait_for_reset")
    monkeypatch.setattr(config, "auto_switch_on_low_quota", False)
    monkeypatch.setattr(config, "quota_buffer_seconds", 0)

    items = [PromptItem(text="Test 1")]
    worker = AutomationWorker(queue=items, target_hwnd=0, countdown_seconds=0)

    mock_inspector = MagicMock()
    # First check: low quota (1%) with 1 second reset wait; Second check (post-reset): healthy (100%)
    mock_inspector.inspect_model_quotas.side_effect = [
        {
            "success": True,
            "is_safe_to_prompt": False,
            "safety_reason": "Gemini 5-Hour Limit (1%) is BELOW safety threshold (22%)!",
            "refresh_time_text": "1 second",
            "refresh_wait_seconds": 1,
        },
        {
            "success": True,
            "is_safe_to_prompt": True,
            "safety_reason": "",
        },
    ]
    worker.quota_inspector = mock_inspector

    assert worker._enforce_quota("Prompt #1") is True
    assert mock_inspector.inspect_model_quotas.call_count == 2
    assert worker.is_paused is False


def test_worker_enforce_quota_fallback_when_wait_seconds_is_none(monkeypatch):
    from unittest.mock import MagicMock
    from app.config import config

    monkeypatch.setattr(config, "low_quota_action", "wait_for_reset")
    monkeypatch.setattr(config, "auto_switch_on_low_quota", False)
    # Set fallback interval to 1s for fast unit test
    monkeypatch.setattr(config, "quota_retry_interval_seconds", 1)

    items = [PromptItem(text="Test 1")]
    worker = AutomationWorker(queue=items, target_hwnd=0, countdown_seconds=0)

    mock_inspector = MagicMock()
    # First check: low quota with unknown reset time (None); Second check: recovered (True)
    mock_inspector.inspect_model_quotas.side_effect = [
        {
            "success": True,
            "is_safe_to_prompt": False,
            "safety_reason": "Unable to read Gemini quota limits from Settings DOM.",
            "refresh_time_text": "Unknown",
            "refresh_wait_seconds": None,
        },
        {
            "success": True,
            "is_safe_to_prompt": True,
            "safety_reason": "",
        },
    ]
    worker.quota_inspector = mock_inspector

    # Must automatically resume via the 1s fallback retry interval and succeed on 2nd inspection
    assert worker._enforce_quota("Prompt #1") is True
    assert mock_inspector.inspect_model_quotas.call_count == 2
    assert worker.is_paused is False


def test_worker_enforce_quota_adds_one_minute_buffer(monkeypatch):
    import time
    from unittest.mock import MagicMock
    from app.config import config

    monkeypatch.setattr(config, "low_quota_action", "wait_for_reset")
    monkeypatch.setattr(config, "auto_switch_on_low_quota", False)
    monkeypatch.setattr(config, "quota_buffer_seconds", 60)

    items = [PromptItem(text="Test 1")]
    worker = AutomationWorker(queue=items, target_hwnd=0, countdown_seconds=0)

    mock_inspector = MagicMock()
    # 2 hours 40 minutes = 9600 seconds
    mock_inspector.inspect_model_quotas.return_value = {
        "success": True,
        "is_safe_to_prompt": False,
        "safety_reason": "Low quota",
        "refresh_time_text": "2 hours, 40 minutes",
        "refresh_wait_seconds": 9600,
    }
    worker.quota_inspector = mock_inspector

    # Cancel worker during pause to inspect target ts without waiting 2.6 hours
    orig_pause = worker.pause
    def mock_pause(reason=""):
        orig_pause(reason)
        worker.cancel()
    worker.pause = mock_pause

    t_before = time.time()
    worker._enforce_quota("Prompt #1")

    # Target timestamp should be 9600s + 60s (+1m) = 9660s from start
    assert worker._quota_reset_target_ts is not None
    assert (worker._quota_reset_target_ts - t_before) >= 9659


def test_pre_dispatch_quota_does_not_switch_on_read_failure(monkeypatch):
    from unittest.mock import MagicMock
    from app.config import config
    from app.models import ProjectExecutionState

    monkeypatch.setattr(config, "low_quota_action", "change_account")
    monkeypatch.setattr(config, "auto_switch_on_low_quota", True)

    worker = AutomationWorker(queue=[])
    mock_qi = MagicMock()
    mock_qi.inspect_model_quotas.return_value = {
        "success": False,
        "read_failed": True,
        "is_low_quota": False,
        "is_safe_to_prompt": False,
        "safety_reason": "Unable to read Gemini quota limits from Settings DOM.",
        "gemini": {"five_hour_percent": None, "weekly_percent": None},
    }
    worker.quota_inspector = mock_qi

    mock_oauth = MagicMock()
    worker.oauth_handler = mock_oauth
    worker.active_projects = {
        "Agrico-V2": ProjectExecutionState(project_name="Agrico-V2", status="IDLE"),
    }

    # Pre-dispatch quota check with read failure must proceed (return True)
    # and MUST NOT trigger account rotation or sign out!
    can_dispatch = worker._check_pre_dispatch_quota("Agrico-V2")
    assert can_dispatch is True
    mock_oauth.execute_account_switch.assert_not_called()


def test_enforce_quota_does_not_switch_on_read_failure(monkeypatch):
    from unittest.mock import MagicMock
    from app.config import config

    monkeypatch.setattr(config, "low_quota_action", "change_account")
    monkeypatch.setattr(config, "auto_switch_on_low_quota", True)

    worker = AutomationWorker(queue=[])
    mock_qi = MagicMock()
    mock_qi.inspect_model_quotas.return_value = {
        "success": False,
        "read_failed": True,
        "is_low_quota": False,
        "is_safe_to_prompt": False,
        "safety_reason": "Unable to read Gemini quota limits from Settings DOM.",
        "gemini": {"five_hour_percent": None, "weekly_percent": None},
    }
    worker.quota_inspector = mock_qi

    mock_oauth = MagicMock()
    worker.oauth_handler = mock_oauth

    # When low_action is change_account, an unreadable DOM must NOT trigger switch
    result = worker._enforce_quota("Test Step")
    assert result is True
    mock_oauth.execute_account_switch.assert_not_called()


def test_quota_cache_seconds_bounds(tmp_path):
    cfg_low = AppConfig(quota_cache_seconds=10)
    assert cfg_low.quota_cache_seconds == 30

    cfg_high = AppConfig(quota_cache_seconds=200)
    assert cfg_high.quota_cache_seconds == 120

    cfg_valid = AppConfig(quota_cache_seconds=75)
    assert cfg_valid.quota_cache_seconds == 75

    # Test serialization
    cfg_file = str(tmp_path / "config_cache.json")
    cfg_valid.save(cfg_file)
    loaded = AppConfig.load(cfg_file)
    assert loaded.quota_cache_seconds == 75


def test_settings_tab_quota_cache_controls():
    from unittest.mock import patch
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from app.ui.settings_tab import SettingsTab
    from app.config import config

    app = QApplication.instance() or QApplication([])
    with patch.object(SettingsTab, "_test_cdp_connection"):
        tab = SettingsTab()

    # Minimum should be 30, Maximum should be 120
    assert tab.spin_quota_cache_seconds.minimum() == 30
    assert tab.spin_quota_cache_seconds.maximum() == 120

    # Test setting value and saving
    tab.spin_quota_cache_seconds.setValue(90)
    with patch.object(config, "save") as mock_save, \
         patch.object(QMessageBox, "information"):
        tab._save_settings()
        mock_save.assert_called_once()
        assert config.quota_cache_seconds == 90





