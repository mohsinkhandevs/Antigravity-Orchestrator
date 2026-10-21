import pytest
from PyQt6.QtWidgets import QApplication

from app.ui.accounts_tab import AccountsTab


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_accounts_tab_initialization_and_controls(qapp):
    tab = AccountsTab()

    # Core controls are present
    assert tab.btn_change_account is not None
    assert tab.btn_check_quota is not None
    assert tab.btn_cancel_switch is not None
    assert tab.lbl_step_status is not None
    assert tab.switch_prog_bar is not None

    # Snippet structures should no longer exist
    assert not hasattr(tab, "_step_widgets")
    assert not hasattr(tab, "btn_reset_snippets")
    assert not hasattr(tab, "_on_run_single_step")

    # Status updates
    tab.set_step("Step 1/10", "Pre-flight initialization")
    assert "Step 1/10" in tab.lbl_step_status.text()
    assert "Pre-flight initialization" in tab.lbl_step_status.text()

    tab.set_progress(3, 10)
    assert tab.switch_prog_bar.value() == 3
    assert tab.switch_prog_bar.maximum() == 10

    # Running state toggling
    tab.set_switch_running_state(True)
    assert not tab.btn_change_account.isEnabled()
    assert not tab.btn_check_quota.isEnabled()
    assert tab.btn_cancel_switch.isEnabled()

    tab.set_switch_running_state(False)
    assert tab.btn_change_account.isEnabled()
    assert tab.btn_check_quota.isEnabled()
    assert not tab.btn_cancel_switch.isEnabled()

    # 11 Table columns verification (Dual Quota tracking)
    assert tab.table.columnCount() == 11
    assert tab.table.horizontalHeaderItem(0).text() == "#"
    assert tab.table.horizontalHeaderItem(1).text() == "Enabled"
    assert tab.table.horizontalHeaderItem(2).text() == "Email"
    assert tab.table.horizontalHeaderItem(3).text() == "Name"
    assert tab.table.horizontalHeaderItem(4).text() == "5-Hour Quota"
    assert tab.table.horizontalHeaderItem(5).text() == "5-Hour Reset"
    assert tab.table.horizontalHeaderItem(6).text() == "Weekly Quota"
    assert tab.table.horizontalHeaderItem(7).text() == "Weekly Reset"
    assert tab.table.horizontalHeaderItem(8).text() == "Status"
    assert tab.table.horizontalHeaderItem(9).text() == "Last Used"
    assert tab.table.horizontalHeaderItem(10).text() == "Actions"
    assert hasattr(tab, "lbl_active_quota")


def test_dual_quota_pill_rendering_and_sync(qapp, monkeypatch):
    from app.account_manager import account_manager, GoogleAccount
    from PyQt6.QtWidgets import QLabel

    tab = AccountsTab()

    test_acc = GoogleAccount(
        email="test_sync_user@gmail.com",
        display_name="Sync User",
        id="sync_001",
        enabled=True,
        five_hour_percent=58,
        five_hour_refresh_raw="3 hours, 15 minutes",
        five_hour_reset_at="2026-10-11 22:36:34",
        weekly_percent=92,
        weekly_refresh_raw="5 days, 4 hours",
        weekly_reset_at="2026-10-18 12:00:00",
        last_quota_checked="2026-10-11 19:20:34",
        last_quota_reason="Pre-dispatch check (Project Alpha)",
    )
    monkeypatch.setattr(account_manager, "load_accounts", lambda: None)
    monkeypatch.setattr(account_manager, "save_accounts", lambda: None)
    monkeypatch.setattr(account_manager, "accounts", [test_acc])
    monkeypatch.setattr(account_manager, "current_email", "test_sync_user@gmail.com")

    tab.reload_accounts()

    assert tab.table.rowCount() == 1

    # Col 4 is 5-Hour Quota pill
    cell_4 = tab.table.cellWidget(0, 4)
    assert isinstance(cell_4, QLabel)
    assert "58%" in cell_4.text()
    assert "5-Hour Quota: 58%" in cell_4.toolTip()
    assert "Last Updated: 2026-10-11 19:20:34" in cell_4.toolTip()
    assert "Trigger: Pre-dispatch check (Project Alpha)" in cell_4.toolTip()

    # Col 5 is 5-Hour Reset duration item
    cell_5 = tab.table.item(0, 5)
    assert cell_5 is not None
    assert "5-Hour Limit Reset:" in cell_5.toolTip()

    # Col 6 is Weekly Quota pill
    cell_6 = tab.table.cellWidget(0, 6)
    assert isinstance(cell_6, QLabel)
    assert "92%" in cell_6.text()
    assert "Weekly Quota: 92%" in cell_6.toolTip()
    assert "Last Updated: 2026-10-11 19:20:34" in cell_6.toolTip()
    assert "Trigger: Pre-dispatch check (Project Alpha)" in cell_6.toolTip()

    # Col 7 is Weekly Reset duration item
    cell_7 = tab.table.item(0, 7)
    assert cell_7 is not None
    assert "Weekly Limit Reset:" in cell_7.toolTip()

    # Card A shows both metrics
    assert "5-Hour: 58%" in tab.lbl_active_quota.text()
    assert "Weekly: 92%" in tab.lbl_active_quota.text()

    # Now simulate quota check updating percentages and reason
    test_acc.five_hour_percent = 45
    test_acc.weekly_percent = 75
    test_acc.last_quota_checked = "2026-10-11 19:40:00"
    test_acc.last_quota_reason = "Manual on-demand check"
    tab._refresh_countdown_display()

    # Verify pills and tooltips updated dynamically without reload
    assert "45%" in cell_4.text()
    assert "5-Hour Quota: 45%" in cell_4.toolTip()
    assert "Trigger: Manual on-demand check" in cell_4.toolTip()

    assert "75%" in cell_6.text()
    assert "Weekly Quota: 75%" in cell_6.toolTip()
    assert "Trigger: Manual on-demand check" in cell_6.toolTip()

    assert "5-Hour: 45%" in tab.lbl_active_quota.text()
    assert "Weekly: 75%" in tab.lbl_active_quota.text()


