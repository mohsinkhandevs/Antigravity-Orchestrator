import os
import json
import pytest
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication

from app.config import AppConfig
from app.models import (
    PromptItem,
    TaskStatus,
    export_prompts_to_json,
    import_prompts_from_json,
    get_queue_backup_path,
)
from app.account_manager import AccountManager, GoogleAccount
from app.worker import AutomationWorker, ProjectExecutionState
from app.ui.dialogs import ActiveAgentsStartupDialog
from app.ui.queue_tab import QueueTab


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_active_agents_startup_dialog_contract(qapp):
    """Verifies ActiveAgentsStartupDialog accepts running_agents or active_conversations and exposes get_selected_action."""
    agents = [
        {"id": "cid-1", "title": "Worker Task", "project": "Alpha", "isSelected": True}
    ]
    dlg1 = ActiveAgentsStartupDialog(running_agents=agents, project_name="Alpha")
    assert dlg1.get_selected_action() == "wait"
    # Single project should not prefix row with [Alpha]
    assert dlg1.conv_list.count() == 1
    assert "[Alpha]" not in dlg1.conv_list.item(0).text()
    assert "Worker Task" in dlg1.conv_list.item(0).text()

    dlg2 = ActiveAgentsStartupDialog(active_conversations=agents)
    assert dlg2.get_selected_action() == "wait"


def test_active_agents_startup_dialog_multi_project(qapp):
    """Verifies ActiveAgentsStartupDialog with multiple busy projects formats prefixes and messaging."""
    multi_agents = [
        {"id": "cid-1", "title": "Task 1", "project": "ProjectA"},
        {"id": "cid-2", "title": "Task 2", "project": "ProjectB"},
    ]
    dlg = ActiveAgentsStartupDialog(
        project_name="ProjectA, ProjectB",
        running_agents=multi_agents,
        queue_count=2,
    )
    assert "Active Agents Detected in Target Projects" in dlg.h_title.text()
    assert "ProjectA, ProjectB" in dlg.h_sub.text()
    assert "Idle target projects start immediately" in dlg.h_sub.text()
    assert "busy projects will wait" in dlg.h_sub.text()

    # Verify conversation items are prefixed with [Project]
    assert dlg.conv_list.count() == 2
    assert "[ProjectA]" in dlg.conv_list.item(0).text()
    assert "[ProjectB]" in dlg.conv_list.item(1).text()

    # Test proceed button
    dlg.btn_proceed.click()
    assert dlg.get_selected_action() == "proceed"

    # Test wait button
    dlg.btn_wait.click()
    assert dlg.get_selected_action() == "wait"


def test_active_agents_startup_dialog_timeout_default(qapp):
    """Verifies dialog auto-defaults to 'wait' when timer expires."""
    agents = [{"id": "cid-1", "title": "Task 1", "project": "ProjectA"}]
    dlg = ActiveAgentsStartupDialog(running_agents=agents, timeout_seconds=1)
    dlg._on_timer_tick()
    assert dlg.get_selected_action() == "wait"


def test_main_window_startup_active_agents_handling(qapp, monkeypatch):
    """Verifies MainWindow._on_startup_active_agents_detected creates dialog with comma-separated projects and resolves decision."""
    from unittest.mock import MagicMock
    from app.ui.main_window import MainWindow

    created_dialog_kwargs = {}
    mock_action = "proceed"

    class MockDialog:
        def __init__(self, **kwargs):
            created_dialog_kwargs.update(kwargs)
        def exec(self):
            return 1
        def get_selected_action(self):
            return mock_action

    monkeypatch.setattr("app.ui.dialogs.ActiveAgentsStartupDialog", MockDialog)

    win = MagicMock(spec=MainWindow)
    win.worker = MagicMock()
    agents = [
        {"id": "c1", "project": "ProjectA"},
        {"id": "c2", "project": "ProjectB"},
    ]
    MainWindow._on_startup_active_agents_detected(win, agents, 2)

    assert created_dialog_kwargs["project_name"] == "ProjectA, ProjectB"
    assert created_dialog_kwargs["running_agents"] == agents
    assert created_dialog_kwargs["queue_count"] == 2
    win.worker.resolve_startup_decision.assert_called_once_with("proceed")


def test_worker_resolve_queue_index_safety():
    """Verifies _resolve_queue_index returns -1 for untracked items to prevent corrupting Prompt #1."""
    p1 = PromptItem(text="Prompt 1", id="id-1")
    p2 = PromptItem(text="Prompt 2", id="id-2")
    worker = AutomationWorker(queue=[p1, p2], target_hwnd=0, countdown_seconds=0)

    untracked = PromptItem(text="External Untracked", id="id-untracked")
    assert worker._resolve_queue_index(untracked) == -1
    assert worker._resolve_queue_index(p1) == 0
    assert worker._resolve_queue_index(p2) == 1


def test_queue_tab_update_status_and_progress_safety(qapp, tmp_path, monkeypatch):
    """Verifies update_prompt_status and update_prompt_progress never mutate row 0 when given an untracked prompt_id."""
    monkeypatch.setattr("app.ui.queue_tab.get_user_data_dir", lambda: str(tmp_path))
    tab = QueueTab()
    p1 = PromptItem(text="Prompt 1", id="prompt-1", status=TaskStatus.PENDING)
    p2 = PromptItem(text="Prompt 2", id="prompt-2", status=TaskStatus.PENDING)
    tab.prompts = [p1, p2]
    tab.refresh_table()

    # Pass untracked ID with row_idx=0 (which previously mutated self.prompts[0])
    tab.update_prompt_status(prompt_id="untracked-uuid", status=TaskStatus.COMPLETED, elapsed=45.0, row_idx=0)
    assert p1.status == TaskStatus.PENDING
    assert p1.elapsed_time == 0.0

    tab.update_prompt_progress(prompt_id="untracked-uuid", elapsed=99.0, row_idx=0)
    assert p1.elapsed_time == 0.0

    # Pass valid ID with conflicting row_idx: prompt_id must take priority!
    tab.update_prompt_status(prompt_id="prompt-2", status=TaskStatus.COMPLETED, elapsed=30.0, row_idx=0)
    assert p2.status == TaskStatus.COMPLETED
    assert p2.elapsed_time == 30.0
    assert p1.status == TaskStatus.PENDING


def test_empty_queue_persistence(tmp_path):
    """Verifies saving an empty queue persists [] and syncs backup, preventing ghost revival on import."""
    queue_file = str(tmp_path / "queue.json")
    items = [PromptItem(text="P1"), PromptItem(text="P2")]
    export_prompts_to_json(items, queue_file)

    backup_file = get_queue_backup_path(queue_file)
    assert os.path.exists(queue_file)
    assert os.path.exists(backup_file)

    # Explicit clear
    export_prompts_to_json([], queue_file, allow_empty=True)

    with open(queue_file, "r", encoding="utf-8") as f:
        assert json.load(f) == []
    with open(backup_file, "r", encoding="utf-8") as f:
        assert json.load(f) == []

    # Import from empty file
    imported = import_prompts_from_json(queue_file)
    assert imported == []


def test_account_manager_empty_persistence(tmp_path):
    """Verifies removing the last account persists [] and does not resurrect accounts from backup."""
    acc_file = str(tmp_path / "accounts.json")
    mgr = AccountManager(filepath=acc_file)
    mgr.add_account("user1@gmail.com")
    assert len(mgr.accounts) == 1

    # Remove the account
    assert mgr.remove_account("user1@gmail.com") is True
    assert len(mgr.accounts) == 0

    # Fresh manager on same file
    mgr2 = AccountManager(filepath=acc_file)
    assert len(mgr2.accounts) == 0
    assert mgr2.current_email == ""


def test_app_config_atomic_save(tmp_path):
    """Verifies AppConfig.save writes configuration cleanly and atomically."""
    cfg_file = str(tmp_path / "config.json")
    cfg = AppConfig(hud_hotkey="Ctrl+Alt+H", cdp_port=9333)
    cfg.save(cfg_file)

    loaded = AppConfig.load(cfg_file)
    assert loaded.hud_hotkey == "Ctrl+Alt+H"
    assert loaded.cdp_port == 9333


def test_worker_idle_agents_clears_external_generating_status():
    """Verifies _wait_for_all_sidebar_agents_idle marks non-pending projects as COMPLETED to prevent quota deadlock."""
    worker = AutomationWorker(queue=[], target_hwnd=0, countdown_seconds=0)
    worker.active_projects["ExternalProject"] = ProjectExecutionState(
        project_name="ExternalProject",
        status="GENERATING",
        conversation_id="ext-cid",
    )
    mock_dom = MagicMock()
    mock_dom.find_all_running_conversations_in_sidebar.return_value = []
    worker.dom_controller = mock_dom

    assert worker._wait_for_all_sidebar_agents_idle() is True
    assert worker.active_projects["ExternalProject"].status == "COMPLETED"


def test_move_selected_respects_visible_rows_under_filter(qapp, tmp_path, monkeypatch):
    """Verifies _move_selected swaps only with the next visible row under table filtering."""
    monkeypatch.setattr("app.ui.queue_tab.get_user_data_dir", lambda: str(tmp_path))
    tab = QueueTab()
    p1 = PromptItem(text="Alpha 1", project="Alpha")
    p2 = PromptItem(text="Beta 1", project="Beta")
    p3 = PromptItem(text="Alpha 2", project="Alpha")
    tab.prompts = [p1, p2, p3]
    tab.refresh_table()

    # Apply filter for Alpha: row 1 (Beta 1) will be hidden
    tab.filter_project_combo.setCurrentText("Alpha")
    tab._apply_project_filter()
    assert not tab.table.isRowHidden(0)
    assert tab.table.isRowHidden(1)
    assert not tab.table.isRowHidden(2)

    # Select row 0 and move DOWN: should skip hidden row 1 and swap with row 2!
    tab.table.selectRow(0)
    tab._move_selected(1)

    assert tab.prompts[0].text == "Alpha 2"
    assert tab.prompts[1].text == "Beta 1"  # Unchanged in middle
    assert tab.prompts[2].text == "Alpha 1"


def test_queue_tab_reconciliation_actively_generating_preserved(qapp, tmp_path, monkeypatch):
    """Verifies load_initial_prompts preserves RUNNING status if actively generating in background."""
    monkeypatch.setattr("app.ui.queue_tab.get_user_data_dir", lambda: str(tmp_path))
    queue_file = str(tmp_path / "queue.json")
    prompt = PromptItem(
        text="Generating Prompt",
        status=TaskStatus.RUNNING,
        conversation_id="cid-active",
        error_message="",
    )
    export_prompts_to_json([prompt], queue_file)

    def mock_reconcile(p):
        p.status = TaskStatus.RUNNING
        p.error_message = "Actively generating in background"
        return True

    monkeypatch.setattr("cdp.reconciliation.reconcile_prompt_status_from_conversation", mock_reconcile)

    tab = QueueTab()
    tab.get_queue_file_path = lambda: queue_file
    tab.load_initial_prompts([])

    assert len(tab.prompts) == 1
    assert tab.prompts[0].status == TaskStatus.RUNNING
    assert tab.prompts[0].error_message == "Actively generating in background"
