import pytest
from PyQt6.QtWidgets import QApplication

from app.models import PromptItem, TaskStatus
from app.ui.queue_tab import QueueTab
from app.ui.dialogs import PromptEditDialog, ImportPromptsDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_queue_tab_9_columns_and_project_filter(qapp):
    tab = QueueTab()

    # 1. Verify 9 Columns (including Speed t/s)
    assert tab.table.columnCount() == 9
    headers = [tab.table.horizontalHeaderItem(i).text() for i in range(9)]
    assert headers == ["#", "Project", "Queue", "Prompt", "Status", "Duration", "Speed", "Details", "Mode"]

    # 2. Add prompts with different project and queue targets
    tab.prompts = [
        PromptItem(text="/goal Build module A", project="Agrico-V2", queue_name="Module A"),
        PromptItem(text="/plan Design schema", project="Antigravity CDP", queue_name="Design"),
        PromptItem(text="Run sanity tests", project="2 step automation", queue_name="Testing"),
    ]
    tab.refresh_table()

    assert tab.table.rowCount() == 3
    assert tab.table.item(0, 1).text() == "Agrico-V2"
    assert tab.table.item(1, 1).text() == "Antigravity CDP"
    assert tab.table.item(2, 1).text() == "2 step automation"

    # 3. Verify Project Filter combo has the projects populated
    items = [tab.filter_project_combo.itemText(i) for i in range(tab.filter_project_combo.count())]
    assert "All Projects" in items
    assert "Agrico-V2" in items
    assert "Antigravity CDP" in items
    assert "2 step automation" in items

    # 4. Filter by 'Antigravity CDP'
    idx = tab.filter_project_combo.findText("Antigravity CDP")
    assert idx != -1
    tab.filter_project_combo.setCurrentIndex(idx)

    assert tab.table.isRowHidden(0) is True
    assert tab.table.isRowHidden(1) is False
    assert tab.table.isRowHidden(2) is True
    assert "Filtered: 1/3" in tab.lbl_stats.text()

    # 5. Reset filter to 'All Projects'
    tab.filter_project_combo.setCurrentIndex(0)
    assert tab.table.isRowHidden(0) is False
    assert tab.table.isRowHidden(1) is False
    assert tab.table.isRowHidden(2) is False
    assert "Total: 3" in tab.lbl_stats.text()


def test_prompt_edit_dialog_project_selection(qapp):
    dlg = PromptEditDialog(initial_text="/goal Test task", initial_project="Antigravity CDP")
    assert dlg.get_text() == "/goal Test task"
    assert dlg.get_project() == "Antigravity CDP"

    dlg.project_combo.setCurrentText("Custom Workspace")
    assert dlg.get_project() == "Custom Workspace"


def test_import_prompts_dialog_project_selection(qapp):
    dlg = ImportPromptsDialog(prompt_count=5, has_existing_prompts=True)
    assert dlg.combo_import_project is not None
    dlg.combo_import_project.setCurrentText("Antigravity CDP")
    assert dlg.get_project() == "Antigravity CDP"
