import pytest
import os
import json
from PyQt6.QtWidgets import QApplication

from app.models import PromptItem, TaskStatus
from app.ui.queue_tab import QueueTab


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_clear_completed_prompts_all_projects(qapp, tmp_path, monkeypatch):
    queue_json = tmp_path / "queue.json"
    monkeypatch.setattr("app.ui.queue_tab.get_user_data_dir", lambda: str(tmp_path))

    tab = QueueTab()
    p1 = PromptItem(text="Prompt 1 (Completed)", project="Agrico-V2", status=TaskStatus.COMPLETED)
    p2 = PromptItem(text="Prompt 2 (Pending)", project="Agrico-V2", status=TaskStatus.PENDING)
    p3 = PromptItem(text="Prompt 3 (Completed)", project="Antigravity CDP", status=TaskStatus.COMPLETED)
    p4 = PromptItem(text="Prompt 4 (Failed)", project="Antigravity CDP", status=TaskStatus.FAILED, error_message="Err")
    p5 = PromptItem(text="Prompt 5 (Skipped)", project="Agrico-V2", status=TaskStatus.SKIPPED)

    tab.prompts = [p1, p2, p3, p4, p5]
    tab.refresh_table()

    # Clear completed with confirm=False
    cleared_count = tab._clear_completed_prompts(confirm=False)

    assert cleared_count == 2
    assert len(tab.prompts) == 3
    remaining_texts = [p.text for p in tab.prompts]
    assert "Prompt 1 (Completed)" not in remaining_texts
    assert "Prompt 3 (Completed)" not in remaining_texts
    assert "Prompt 2 (Pending)" in remaining_texts
    assert "Prompt 4 (Failed)" in remaining_texts
    assert "Prompt 5 (Skipped)" in remaining_texts

    # Verify queue.json persisted the updated queue
    with open(str(queue_json), "r", encoding="utf-8") as f:
        saved_data = json.load(f)
    assert len(saved_data) == 3
    saved_texts = [item["text"] for item in saved_data]
    assert "Prompt 1 (Completed)" not in saved_texts
    assert "Prompt 3 (Completed)" not in saved_texts


def test_clear_completed_prompts_with_project_filter(qapp, tmp_path, monkeypatch):
    queue_json = tmp_path / "queue.json"
    monkeypatch.setattr("app.ui.queue_tab.get_user_data_dir", lambda: str(tmp_path))

    tab = QueueTab()
    p1 = PromptItem(text="ProjA Completed", project="ProjA", status=TaskStatus.COMPLETED)
    p2 = PromptItem(text="ProjA Pending", project="ProjA", status=TaskStatus.PENDING)
    p3 = PromptItem(text="ProjB Completed", project="ProjB", status=TaskStatus.COMPLETED)

    tab.prompts = [p1, p2, p3]
    tab.refresh_table()

    # Set filter to ProjA
    tab.filter_project_combo.addItem("ProjA")
    tab.filter_project_combo.setCurrentText("ProjA")

    cleared_count = tab._clear_completed_prompts(confirm=False)

    assert cleared_count == 1
    assert len(tab.prompts) == 2
    remaining_texts = [p.text for p in tab.prompts]
    assert "ProjA Completed" not in remaining_texts
    assert "ProjA Pending" in remaining_texts
    assert "ProjB Completed" in remaining_texts


def test_clear_completed_zero_completed(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("app.ui.queue_tab.get_user_data_dir", lambda: str(tmp_path))

    tab = QueueTab()
    tab.prompts = [
        PromptItem(text="Prompt 1", status=TaskStatus.PENDING),
        PromptItem(text="Prompt 2", status=TaskStatus.RUNNING),
    ]
    tab.refresh_table()

    cleared_count = tab._clear_completed_prompts(confirm=False)
    assert cleared_count == 0
    assert len(tab.prompts) == 2


def test_toolbar_clear_buttons_existence(qapp):
    tab = QueueTab()
    assert hasattr(tab, "clear_completed_btn")
    assert tab.clear_completed_btn.text() == "Clear Completed"
    assert hasattr(tab, "clear_btn")
    assert tab.clear_btn.text() == "Clear All"


def test_mark_running_as_completed_on_close(qapp, tmp_path, monkeypatch):
    queue_json = tmp_path / "queue.json"
    monkeypatch.setattr("app.ui.queue_tab.get_user_data_dir", lambda: str(tmp_path))

    tab = QueueTab()
    p1 = PromptItem(text="Running prompt", status=TaskStatus.RUNNING)
    p2 = PromptItem(text="Pending prompt", status=TaskStatus.PENDING)
    p3 = PromptItem(text="Already completed", status=TaskStatus.COMPLETED)
    tab.prompts = [p1, p2, p3]
    tab.refresh_table()

    updated = tab.mark_running_as_completed()
    assert updated == 1
    # Interrupted prompt must be marked FAILED with reason, NOT falsely completed
    assert p1.status == TaskStatus.FAILED
    assert "Interrupted" in p1.error_message
    assert p2.status == TaskStatus.PENDING
    assert p3.status == TaskStatus.COMPLETED

    # Verify queue.json persisted the update
    with open(str(queue_json), "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved[0]["status"] == TaskStatus.FAILED.value
    assert saved[1]["status"] == TaskStatus.PENDING.value
    assert saved[2]["status"] == TaskStatus.COMPLETED.value

    # Verify clear completed prompts does NOT delete the interrupted failed prompt
    cleared = tab._clear_completed_prompts(confirm=False)
    assert cleared == 1  # only p3 cleared
    assert p1 in tab.prompts


def test_load_initial_prompts_normalizes_running_to_completed(qapp, tmp_path, monkeypatch):
    queue_json = tmp_path / "queue.json"
    monkeypatch.setattr("app.ui.queue_tab.get_user_data_dir", lambda: str(tmp_path))

    # Pre-populate queue.json with a prompt having status: "Running"
    raw_data = [
        {"id": "r1", "text": "Stranded Running Prompt", "status": "Running", "project": "Agrico-V2"},
        {"id": "p1", "text": "Pending Prompt", "status": "Pending", "project": "Agrico-V2"},
    ]
    with open(str(queue_json), "w", encoding="utf-8") as f:
        json.dump(raw_data, f)

    tab = QueueTab()
    tab.load_initial_prompts([])

    assert len(tab.prompts) == 2
    # Orphan running prompt normalized to FAILED with explanation
    assert tab.prompts[0].status == TaskStatus.FAILED
    assert "Interrupted" in tab.prompts[0].error_message
    assert tab.prompts[1].status == TaskStatus.PENDING

    # Verify saved queue normalized
    with open(str(queue_json), "r", encoding="utf-8") as f:
        re_saved = json.load(f)
    assert re_saved[0]["status"] == "Failed"


