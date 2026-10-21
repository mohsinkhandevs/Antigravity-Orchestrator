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


def test_prompt_item_duplicate():
    original = PromptItem(
        text="/goal Refactor login controller",
        name="Login Refactor",
        project="Agrico-V2",
        status=TaskStatus.COMPLETED,
        elapsed_time=45.2,
        error_message="Previous error",
        conversation_id="conv-123",
        is_resumed=True,
        resume_count=2,
    )

    duplicate = original.duplicate()

    # Content and targeting are identical
    assert duplicate.text == original.text
    assert duplicate.name == original.name
    assert duplicate.project == original.project

    # ID is unique and freshly generated
    assert duplicate.id != original.id
    assert len(duplicate.id) > 0

    # Execution state is clean / reset
    assert duplicate.status == TaskStatus.PENDING
    assert duplicate.elapsed_time == 0.0
    assert duplicate.error_message == ""
    assert duplicate.conversation_id == ""
    assert duplicate.is_resumed is False
    assert duplicate.resume_count == 0


def test_queue_tab_duplicate_prompt(qapp, tmp_path, monkeypatch):
    queue_json = tmp_path / "queue.json"
    monkeypatch.setattr("app.ui.queue_tab.get_user_data_dir", lambda: str(tmp_path))

    tab = QueueTab()
    tab.prompts = [
        PromptItem(text="/goal Step 1: Initial audit", project="ProjA", status=TaskStatus.COMPLETED, elapsed_time=12.0),
        PromptItem(text="/plan Step 2: Implement core", project="ProjB", status=TaskStatus.FAILED, error_message="Failed"),
    ]
    tab.refresh_table()
    assert len(tab.prompts) == 2

    # Select first prompt (row 0) and duplicate it
    tab.table.selectRow(0)
    assert tab.table.currentRow() == 0

    tab._duplicate_selected_prompt()

    # Should now have 3 prompts
    assert len(tab.prompts) == 3

    # The duplicate should be inserted at index 1 (immediately below row 0)
    dup_item = tab.prompts[1]
    assert dup_item.text == "/goal Step 1: Initial audit"
    assert dup_item.project == "ProjA"
    assert dup_item.status == TaskStatus.PENDING
    assert dup_item.elapsed_time == 0.0
    assert dup_item.id != tab.prompts[0].id

    # The 3rd item should remain unchanged
    assert tab.prompts[2].text == "/plan Step 2: Implement core"

    # UI selection should focus the newly duplicated row (row 1)
    assert tab.table.currentRow() == 1

    # Verify auto-save to queue.json
    assert os.path.exists(queue_json)
    with open(queue_json, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert len(saved) == 3
    assert saved[1]["text"] == "/goal Step 1: Initial audit"
    assert saved[1]["status"] == "Pending"


def test_duplicate_multiple_dialog(qapp):
    from app.ui.dialogs import DuplicateMultipleDialog

    original = PromptItem(
        text="/goal Build Feature X\nLine 2",
        name="Feature X",
        project="AlphaProject",
        queue_name="Urgent",
    )

    dialog = DuplicateMultipleDialog(prompt_item=original, default_count=3)
    assert dialog.get_count() == 3

    # Test spinbox change
    dialog.spin_count.setValue(7)
    assert dialog.get_count() == 7
    assert "7" in dialog.duplicate_btn.text()


def test_queue_tab_duplicate_multiple_prompts(qapp, tmp_path, monkeypatch):
    queue_json = tmp_path / "queue.json"
    monkeypatch.setattr("app.ui.queue_tab.get_user_data_dir", lambda: str(tmp_path))

    tab = QueueTab()
    tab.prompts = [
        PromptItem(text="/goal Step 1: Alpha", project="ProjA", name="Task Alpha", status=TaskStatus.COMPLETED, elapsed_time=15.0),
        PromptItem(text="/plan Step 2: Beta", project="ProjB", name="Task Beta", status=TaskStatus.PENDING),
    ]
    tab.refresh_table()
    assert len(tab.prompts) == 2

    # Select first prompt and duplicate 3 times
    tab.table.selectRow(0)
    tab._duplicate_multiple_selected_prompt(count=3)

    # 2 original + 3 duplicates = 5 prompts
    assert len(tab.prompts) == 5

    # Check that duplicates are inserted sequentially directly below row 0 (indices 1, 2, 3)
    ids = set()
    for idx in range(1, 4):
        item = tab.prompts[idx]
        assert item.text == "/goal Step 1: Alpha"
        assert item.project == "ProjA"
        assert item.status == TaskStatus.PENDING
        assert item.elapsed_time == 0.0
        assert item.error_message == ""
        assert item.conversation_id == ""
        assert item.id != tab.prompts[0].id
        assert item.id not in ids
        ids.add(item.id)

    # Original second prompt is now pushed to index 4
    assert tab.prompts[4].text == "/plan Step 2: Beta"

    # Selection moved to the first duplicated item
    assert tab.table.currentRow() == 1

    # Verify saved queue.json
    assert os.path.exists(queue_json)
    with open(queue_json, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert len(saved) == 5
    assert saved[1]["text"] == "/goal Step 1: Alpha"
    assert saved[2]["text"] == "/goal Step 1: Alpha"
    assert saved[3]["text"] == "/goal Step 1: Alpha"
    assert saved[4]["text"] == "/plan Step 2: Beta"


def test_queue_tab_duplicate_multiple_dialog_cancelled(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QDialog
    monkeypatch.setattr("app.ui.queue_tab.get_user_data_dir", lambda: str(tmp_path))

    tab = QueueTab()
    tab.prompts = [
        PromptItem(text="/goal Scrape Data", name="Scraper", project="ProjA"),
    ]
    tab.refresh_table()
    tab.table.selectRow(0)

    # Mock dialog execution to simulate user clicking Cancel
    monkeypatch.setattr("app.ui.queue_tab.DuplicateMultipleDialog.exec", lambda self: QDialog.DialogCode.Rejected)

    tab._duplicate_multiple_selected_prompt()

    # Nothing should be added
    assert len(tab.prompts) == 1


def test_context_menu_has_duplicate_multiple(qapp, monkeypatch):
    from PyQt6.QtWidgets import QMenu
    from PyQt6.QtCore import QPoint

    tab = QueueTab()
    tab.prompts = [
        PromptItem(text="/goal Test Task", project="ProjA"),
    ]
    tab.refresh_table()
    tab.table.selectRow(0)

    captured_action_texts = []
    def mock_exec(menu_self, *args, **kwargs):
        for act in menu_self.actions():
            captured_action_texts.append(act.text())
        return None

    monkeypatch.setattr(QMenu, "exec", mock_exec)

    tab._show_context_menu(QPoint(10, 10))

    assert any("Duplicate Multiple Copies" in txt for txt in captured_action_texts)
    assert any("Duplicate Prompt" in txt for txt in captured_action_texts)


