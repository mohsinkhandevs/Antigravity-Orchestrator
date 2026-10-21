import pytest
from unittest.mock import MagicMock, patch
from app.models import PromptItem, TaskStatus, ThreadSafePromptQueue
from cdp.reconciliation import reconcile_prompt_status_from_conversation


def test_reconcile_no_conversation_id():
    p = PromptItem(text="Prompt without CID", conversation_id="", status=TaskStatus.FAILED)
    assert reconcile_prompt_status_from_conversation(p) is False
    assert p.status == TaskStatus.FAILED


def test_reconcile_normal_completion():
    p = PromptItem(
        text="Prompt with CID",
        conversation_id="conv-123",
        status=TaskStatus.FAILED,
        error_message="Interrupted by application exit",
    )
    with patch("cdp.reconciliation.inspect_conversation_db_termination", return_value={"state": "NORMAL"}), \
         patch("cdp.reconciliation.inspect_transcript_termination", return_value={"state": "NORMAL"}), \
         patch("cdp.reconciliation.get_conversation_summary", return_value={"found": True, "is_idle": True, "killed": False}):
        modified = reconcile_prompt_status_from_conversation(p)
        assert modified is True
        assert p.status == TaskStatus.COMPLETED
        assert p.error_message == ""


def test_reconcile_normal_completion_already_completed():
    p = PromptItem(
        text="Already completed",
        conversation_id="conv-123",
        status=TaskStatus.COMPLETED,
        error_message="",
    )
    with patch("cdp.reconciliation.inspect_conversation_db_termination", return_value={"state": "NORMAL"}), \
         patch("cdp.reconciliation.inspect_transcript_termination", return_value={"state": "NORMAL"}), \
         patch("cdp.reconciliation.get_conversation_summary", return_value={"found": True, "is_idle": True, "killed": False}):
        modified = reconcile_prompt_status_from_conversation(p)
        assert modified is False
        assert p.status == TaskStatus.COMPLETED


def test_reconcile_actively_running():
    p = PromptItem(
        text="Still running",
        conversation_id="conv-456",
        status=TaskStatus.FAILED,
        error_message="Interrupted by application exit",
    )
    with patch("cdp.reconciliation.inspect_conversation_db_termination", return_value={"state": "RUNNING"}), \
         patch("cdp.reconciliation.inspect_transcript_termination", return_value={"state": "UNKNOWN", "last_step_status": "RUNNING"}), \
         patch("cdp.reconciliation.get_conversation_summary", return_value={"found": True, "status": "CASCADE_RUN_STATUS_RUNNING", "not_fully_idle": 1, "killed": False}):
        modified = reconcile_prompt_status_from_conversation(p)
        assert modified is True
        assert p.status == TaskStatus.RUNNING
        assert "Actively generating in background" in p.error_message


def test_reconcile_quota_exhausted():
    p = PromptItem(
        text="Quota hit",
        conversation_id="conv-789",
        status=TaskStatus.FAILED,
        error_message="Interrupted by application exit",
    )
    with patch("cdp.reconciliation.inspect_conversation_db_termination", return_value={"state": "QUOTA_EXHAUSTED", "detail": "Rate limit exceeded"}), \
         patch("cdp.reconciliation.inspect_transcript_termination", return_value={"state": "QUOTA_EXHAUSTED", "detail": "Rate limit exceeded"}), \
         patch("cdp.reconciliation.get_conversation_summary", return_value={"found": True, "is_idle": True, "killed": False}):
        modified = reconcile_prompt_status_from_conversation(p)
        assert modified is True
        assert p.status == TaskStatus.FAILED
        assert "Quota exhausted" in p.error_message


def test_reconcile_step_error():
    p = PromptItem(
        text="Step error",
        conversation_id="conv-err",
        status=TaskStatus.FAILED,
        error_message="Interrupted by application exit",
    )
    with patch("cdp.reconciliation.inspect_conversation_db_termination", return_value={"state": "STEP_ERROR", "detail": "Fatal bash syntax error"}), \
         patch("cdp.reconciliation.inspect_transcript_termination", return_value={"state": "ERRORED", "detail": "Fatal bash syntax error"}), \
         patch("cdp.reconciliation.get_conversation_summary", return_value={"found": True, "is_idle": True, "killed": False}):
        modified = reconcile_prompt_status_from_conversation(p)
        assert modified is True
        assert p.status == TaskStatus.FAILED
        assert "Fatal bash syntax error" in p.error_message


def test_reconcile_user_stopped():
    p = PromptItem(
        text="User killed",
        conversation_id="conv-kill",
        status=TaskStatus.RUNNING,
    )
    with patch("cdp.reconciliation.inspect_conversation_db_termination", return_value={"state": "USER_STOPPED"}), \
         patch("cdp.reconciliation.inspect_transcript_termination", return_value={"state": "UNKNOWN"}), \
         patch("cdp.reconciliation.get_conversation_summary", return_value={"found": True, "is_idle": True, "killed": True}):
        modified = reconcile_prompt_status_from_conversation(p)
        assert modified is True
        assert p.status == TaskStatus.CANCELLED
        assert "Stopped by user" in p.error_message


def test_queue_tab_load_initial_prompts_auto_reconciles(tmp_path, monkeypatch):
    from app.ui.queue_tab import QueueTab
    from PyQt6.QtWidgets import QApplication
    import sys

    app = QApplication.instance() or QApplication(sys.argv)

    tab = QueueTab()
    queue_file = str(tmp_path / "queue.json")
    monkeypatch.setattr(tab, "get_queue_file_path", lambda: queue_file)

    # Prepare saved items: prompt 1 was interrupted with CID but completed in background
    saved_prompts = [
        PromptItem(
            text="Task 1",
            conversation_id="conv-bg-done",
            status=TaskStatus.FAILED,
            error_message="Interrupted by application exit",
        )
    ]
    from app.models import export_prompts_to_json
    export_prompts_to_json(saved_prompts, queue_file)

    with patch("cdp.reconciliation.inspect_conversation_db_termination", return_value={"state": "NORMAL"}), \
         patch("cdp.reconciliation.inspect_transcript_termination", return_value={"state": "NORMAL"}), \
         patch("cdp.reconciliation.get_conversation_summary", return_value={"found": True, "is_idle": True, "killed": False}):
        tab.load_initial_prompts(["default"])

    assert len(tab.prompts) == 1
    assert tab.prompts[0].status == TaskStatus.COMPLETED
    assert tab.prompts[0].error_message == ""


def test_queue_tab_reconcile_all_prompts(monkeypatch):
    from app.ui.queue_tab import QueueTab
    from PyQt6.QtWidgets import QApplication
    import sys

    app = QApplication.instance() or QApplication(sys.argv)
    tab = QueueTab()
    tab.save_queue = MagicMock()
    tab._sync_project_filter_items = MagicMock()

    tab.prompts[:] = [
        PromptItem(text="Task 1", conversation_id="conv-1", status=TaskStatus.FAILED, error_message="Interrupted"),
        PromptItem(text="Task 2", conversation_id="", status=TaskStatus.PENDING),
    ]

    with patch("cdp.reconciliation.inspect_conversation_db_termination", return_value={"state": "NORMAL"}), \
         patch("cdp.reconciliation.inspect_transcript_termination", return_value={"state": "NORMAL"}), \
         patch("cdp.reconciliation.get_conversation_summary", return_value={"found": True, "is_idle": True, "killed": False}):
        updated = tab.reconcile_all_prompts(notify=False)

    assert updated == 1
    assert tab.prompts[0].status == TaskStatus.COMPLETED
    assert tab.prompts[0].error_message == ""
    assert tab.prompts[1].status == TaskStatus.PENDING
    tab.save_queue.assert_called_once()
