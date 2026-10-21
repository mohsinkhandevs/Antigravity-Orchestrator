import json
import sqlite3
import pytest
from PyQt6.QtWidgets import QApplication

from cdp.chat_lifecycle_inspector import (
    extract_conversation_id,
    format_duration_human,
    inspect_conversation_lifecycle,
    get_active_and_unfinished_chats,
    get_recent_conversations,
    list_conversation_artifacts,
)
from app.ui.chats_tab import ChatsTab
from app.ui.tokens_tab import TokensTab


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _create_mock_db_and_brain(tmp_path):
    """Creates an isolated Antigravity app_data_dir (conversation_summaries.db + conversations/*.db) and brain_dir."""
    app_data_dir = tmp_path / "app_data"
    convs_dir = app_data_dir / "conversations"
    convs_dir.mkdir(parents=True, exist_ok=True)
    brain_root = tmp_path / "brain"
    brain_root.mkdir(parents=True, exist_ok=True)

    summaries_db_path = app_data_dir / "conversation_summaries.db"
    conn = sqlite3.connect(str(summaries_db_path))
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE conversation_summaries (
            conversation_id TEXT PRIMARY KEY,
            title TEXT,
            status TEXT,
            not_fully_idle INTEGER,
            killed INTEGER,
            step_count INTEGER,
            workspace_uris TEXT
        )
        """
    )

    # Conversation 1: Multi-turn completed conversation with brain transcript.jsonl
    cid_completed = "11111111-2222-3333-4444-555555555555"
    cur.execute(
        "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            cid_completed,
            "Build Feature X",
            "CASCADE_RUN_STATUS_IDLE",
            0,
            0,
            6,
            '["file:///c:/Users/test/ProjectAlpha"]',
        ),
    )

    c1_brain = brain_root / cid_completed
    c1_logs = c1_brain / ".system_generated" / "logs"
    c1_logs.mkdir(parents=True, exist_ok=True)
    (c1_brain / "implementation_plan.md").write_text("# Plan", encoding="utf-8")

    transcript_lines_1 = [
        {
            "step_index": 1,
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-10-13T10:00:00Z",
            "content": "<USER_REQUEST>\n/goal Implement login module\n</USER_REQUEST>",
        },
        {
            "step_index": 2,
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-13T10:01:30Z",
            "input_tokens": 4000,
            "cache_read_tokens": 16000,
            "output_tokens": 150,
            "thinking": "I need to implement authentication by writing auth.py",
            "content": "Working on login module...",
            "tool_calls": [
                {"name": "write_to_file", "args": {"TargetFile": "auth.py", "toolSummary": "Create auth module"}},
                {"name": "invoke_subagent"},
            ],
        },
        {
            "step_index": 3,
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-13T10:02:00Z",
            "input_tokens": 1000,
            "cache_read_tokens": 19000,
            "output_tokens": 200,
            "content": "Login module finished.",
            "tool_calls": [],
        },
        # Turn 2: Auto-resume prompt ("continue") after a pause
        {
            "step_index": 4,
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-10-13T10:10:00Z",
            "content": "continue",
        },
        {
            "step_index": 5,
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-13T10:10:20Z",
            "input_tokens": 500,
            "cache_read_tokens": 20000,
            "output_tokens": 80,
            "content": "Running tests...",
            "tool_calls": [{"name": "run_command"}],
        },
        {
            "step_index": 6,
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-13T10:10:45Z",
            "input_tokens": 200,
            "cache_read_tokens": 20500,
            "output_tokens": 120,
            "content": "Completed remaining verification.",
            "tool_calls": [],
        },
    ]
    with open(c1_logs / "transcript.jsonl", "w", encoding="utf-8") as f:
        for obj in transcript_lines_1:
            f.write(json.dumps(obj) + "\n")

    # Conversation 2: Currently RUNNING conversation in ProjectBeta
    cid_running = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    cur.execute(
        "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            cid_running,
            "Refactor Database Layer",
            "CASCADE_RUN_STATUS_RUNNING",
            1,
            0,
            12,
            '["file:///c:/Users/test/ProjectBeta"]',
        ),
    )

    # Conversation 3: Halted by QUOTA_EXHAUSTED in ProjectGamma (SQLite per-conversation db)
    cid_quota = "99999999-8888-7777-6666-555555555555"
    cur.execute(
        "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            cid_quota,
            "Optimize Query Pipeline",
            "CASCADE_RUN_STATUS_IDLE",
            0,
            0,
            4,
            '["file:///c:/Users/test/ProjectGamma"]',
        ),
    )

    conn.commit()
    conn.close()

    # Create conversations/<cid_quota>.db with executor_metadata and steps
    c3_db_path = convs_dir / f"{cid_quota}.db"
    c3_conn = sqlite3.connect(str(c3_db_path))
    c3_cur = c3_conn.cursor()
    c3_cur.execute("CREATE TABLE executor_metadata (idx INTEGER PRIMARY KEY, data BLOB)")
    c3_cur.execute(
        "CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, error_details BLOB)"
    )
    c3_cur.execute(
        "INSERT INTO executor_metadata VALUES (?, ?)",
        (1, b"execution error: RESOURCE_EXHAUSTED: code 429 individual quota reached for Gemini"),
    )
    c3_cur.execute(
        "INSERT INTO steps VALUES (?, ?, ?, ?)",
        (1, 14, 3, b""),
    )
    c3_cur.execute(
        "INSERT INTO steps VALUES (?, ?, ?, ?)",
        (2, 132, 3, b""),
    )
    c3_cur.execute(
        "INSERT INTO steps VALUES (?, ?, ?, ?)",
        (3, 15, 7, b"ResourceExhausted: Model quota limit reached for Gemini."),
    )
    c3_conn.commit()
    c3_conn.close()

    return str(app_data_dir), str(brain_root), cid_completed, cid_running, cid_quota


def test_extract_conversation_id_formats():
    uid = "71d91ecf-6610-4c77-aee9-b80f617df473"
    assert extract_conversation_id(uid) == uid
    assert extract_conversation_id(f"  conversation://{uid}  ") == uid
    assert extract_conversation_id(f"C:\\Users\\mohsi\\.gemini\\antigravity\\brain\\{uid}\\plan.md") == uid
    assert extract_conversation_id("not-a-uuid") == ""
    assert extract_conversation_id("") == ""


def test_inspect_conversation_lifecycle_multi_turn_and_metrics(tmp_path):
    app_data_dir, brain_root, cid_completed, _, _ = _create_mock_db_and_brain(tmp_path)

    report = inspect_conversation_lifecycle(
        cid_completed,
        app_data_dir=app_data_dir,
        brain_dir=brain_root,
    )

    assert report["found"] is True
    assert report["conversation_id"] == cid_completed
    assert report["title"] == "Build Feature X"
    assert report["project"] == "ProjectAlpha"
    assert report["overall_state"] == "COMPLETED"
    assert report["is_running"] is False

    # 2 total prompts: 1 user prompt + 1 auto-resume ("continue")
    assert report["total_prompts"] == 2
    assert report["user_prompts_count"] == 1
    assert report["auto_resume_prompts_count"] == 1

    turns = report["prompt_turns"]
    assert len(turns) == 2
    assert turns[0]["prompt_text"] == "/goal Implement login module"
    assert turns[0]["is_auto_resume"] is False
    assert turns[0]["duration_seconds"] == pytest.approx(120.0)
    assert turns[0]["tool_calls_count"] == 2

    # Check token metrics in turns
    assert turns[0]["input_tokens"] == 5000  # 4000 + 1000
    assert turns[0]["cache_read_tokens"] == 35000  # 16000 + 19000
    assert turns[0]["output_tokens"] == 350  # 150 + 200

    assert turns[1]["prompt_text"] == "continue"
    assert turns[1]["is_auto_resume"] is True
    assert turns[1]["duration_seconds"] == pytest.approx(45.0)
    assert turns[1]["tool_calls_count"] == 1
    assert turns[1]["input_tokens"] == 700  # 500 + 200
    assert turns[1]["cache_read_tokens"] == 40500  # 20000 + 20500
    assert turns[1]["output_tokens"] == 200  # 80 + 120

    # Overall conversation token totals
    assert report["conversation_uncached_input_tokens"] == 5700
    assert report["conversation_cache_tokens"] == 75500
    assert report["conversation_output_tokens"] == 550
    assert report["conversation_total_tokens"] == 81750
    assert report["conversation_cache_hit_rate"] > 90.0

    assert report["total_active_duration_seconds"] == pytest.approx(165.0)
    assert report["wall_clock_duration_seconds"] == pytest.approx(645.0)
    assert report["total_steps"] == 6
    assert report["total_tool_calls"] == 3
    assert report["total_subagents"] == 1
    assert len(report["artifacts"]) == 1
    assert report["artifacts"][0]["name"] == "implementation_plan.md"


def test_inspect_conversation_sqlite_fallback_and_quota_exhausted(tmp_path):
    app_data_dir, brain_root, _, _, cid_quota = _create_mock_db_and_brain(tmp_path)

    report = inspect_conversation_lifecycle(
        cid_quota,
        app_data_dir=app_data_dir,
        brain_dir=brain_root,
    )

    assert report["found"] is True
    assert report["conversation_id"] == cid_quota
    assert report["project"] == "ProjectGamma"
    assert report["overall_state"] == "QUOTA_EXHAUSTED"
    assert report["is_running"] is False
    assert report["total_prompts"] == 1
    assert report["total_tool_calls"] == 1
    assert "RESOURCE_EXHAUSTED" in report["termination_detail"]


def test_get_active_and_unfinished_chats_discovery(tmp_path):
    app_data_dir, brain_root, _, cid_running, cid_quota = _create_mock_db_and_brain(tmp_path)

    active_list = get_active_and_unfinished_chats(
        app_data_dir=app_data_dir,
        brain_dir=brain_root,
        limit=10,
    )

    ids = [c["id"] for c in active_list]
    assert cid_running in ids
    assert cid_quota in ids
    assert active_list[0]["id"] == cid_running
    assert active_list[0]["category"] == "GENERATING"
    assert active_list[0]["project"] == "ProjectBeta"


def test_chats_tab_ui_and_signals(qapp, tmp_path, monkeypatch):
    app_data_dir, brain_root, cid_completed, cid_running, cid_quota = _create_mock_db_and_brain(tmp_path)

    monkeypatch.setattr(
        "app.ui.chats_tab.get_recent_conversations",
        lambda limit=35: get_recent_conversations(app_data_dir=app_data_dir, limit=limit),
    )
    monkeypatch.setattr(
        "app.ui.chats_tab.inspect_conversation_lifecycle",
        lambda cid: inspect_conversation_lifecycle(
            cid, app_data_dir=app_data_dir, brain_dir=brain_root
        ),
    )

    tab = ChatsTab()

    # Verify recent chats dropdown populated
    assert tab.combo_recent_chats.count() > 1

    # Inspect the completed multi-turn conversation
    report = tab.inspect_conversation(cid_completed)
    assert report is not None
    assert "COMPLETED" in tab.lbl_status_badge.text()
    assert "ProjectAlpha" in tab.lbl_project_chip.text()
    assert tab.table_turns.rowCount() == 2
    assert not tab.frame_diag_banner.isVisible()

    # Selecting row 0 shows Turn 1 prompt text, files impacted, thinking toggle & waterfall
    tab.table_turns.selectRow(0)
    assert "/goal Implement login module" in tab.txt_turn_prompt.toPlainText()
    assert tab.files_chips_layout.count() == 1
    assert "auth.py" in tab.files_chips_layout.itemAt(0).widget().text()

    # Verify Tool Execution Waterfall timeline
    assert "Model:" in tab.lbl_waterfall_summary.text()
    assert tab.waterfall_chips_layout.count() >= 1

    # Toggle to Model Thinking (Chain-of-Thought) view
    tab.btn_view_thinking.click()
    assert "I need to implement authentication" in tab.txt_turn_response.toPlainText()
    assert "Thinking" in tab.lbl_selected_response_hdr.text()

    # Toggle back to Output view
    tab.btn_view_output.click()
    assert "Login module finished." in tab.txt_turn_response.toPlainText()
    assert "Output" in tab.lbl_selected_response_hdr.text()

    # Inspect the quota-exhausted conversation -> Diagnostic banner must be visible
    tab.inspect_conversation(cid_quota)
    assert not tab.frame_diag_banner.isHidden()
    assert "QUOTA EXHAUSTED" in tab.lbl_status_badge.text()

    # Verify Open in Antigravity signal
    opened_cids = []
    tab.sig_open_in_antigravity_requested.connect(lambda c: opened_cids.append(c))

    tab.btn_open_chat.click()
    assert opened_cids == [cid_quota]


def test_tokens_tab_ui_and_kpi_cards(qapp, tmp_path, monkeypatch):
    app_data_dir, brain_root, cid_completed, _, _ = _create_mock_db_and_brain(tmp_path)

    monkeypatch.setattr(
        "app.ui.tokens_tab.get_recent_conversations",
        lambda limit=35: get_recent_conversations(app_data_dir=app_data_dir, limit=limit),
    )
    monkeypatch.setattr(
        "app.ui.tokens_tab.inspect_conversation_lifecycle",
        lambda cid: inspect_conversation_lifecycle(
            cid, app_data_dir=app_data_dir, brain_dir=brain_root
        ),
    )

    tokens_tab = TokensTab()

    # Inspect completed conversation
    rep = tokens_tab.inspect_conversation(cid_completed)
    assert rep is not None

    # Verify KPI Card metrics
    assert tokens_tab.lbl_kpi_input_tokens.text() == "5,700"
    assert tokens_tab.lbl_kpi_cache_tokens.text() == "75,500"
    assert tokens_tab.lbl_kpi_output_tokens.text() == "550"
    assert "Cache Hit Rate" in tokens_tab.lbl_kpi_cache_rate.text()

    # Verify Turn-by-Turn table has 2 rows
    assert tokens_tab.table_turns.rowCount() == 2

    # Verify Step-by-Step API Calls table populated for selected turn
    tokens_tab.table_turns.selectRow(0)
    assert tokens_tab.table_steps.rowCount() >= 2
    # Check that model step shows input and output tokens
    step0_in = tokens_tab.table_steps.item(0, 3).text()
    assert step0_in != "—" and step0_in != "0"


def test_chats_and_tokens_tab_cross_sync(qapp, tmp_path, monkeypatch):
    app_data_dir, brain_root, cid_completed, _, cid_quota = _create_mock_db_and_brain(tmp_path)

    monkeypatch.setattr(
        "app.ui.chats_tab.get_recent_conversations",
        lambda limit=35: get_recent_conversations(app_data_dir=app_data_dir, limit=limit),
    )
    monkeypatch.setattr(
        "app.ui.chats_tab.inspect_conversation_lifecycle",
        lambda cid: inspect_conversation_lifecycle(
            cid, app_data_dir=app_data_dir, brain_dir=brain_root
        ),
    )
    monkeypatch.setattr(
        "app.ui.tokens_tab.get_recent_conversations",
        lambda limit=35: get_recent_conversations(app_data_dir=app_data_dir, limit=limit),
    )
    monkeypatch.setattr(
        "app.ui.tokens_tab.inspect_conversation_lifecycle",
        lambda cid: inspect_conversation_lifecycle(
            cid, app_data_dir=app_data_dir, brain_dir=brain_root
        ),
    )

    chats_tab = ChatsTab()
    tokens_tab = TokensTab()

    # Connect cross-tab sync signals
    chats_tab.sig_conversation_selected.connect(tokens_tab.sync_conversation)
    tokens_tab.sig_conversation_selected.connect(chats_tab.sync_conversation)

    # Selecting in chats_tab updates tokens_tab
    chats_tab.inspect_conversation(cid_completed)
    assert tokens_tab.current_conversation_id == cid_completed
    assert tokens_tab.lbl_kpi_input_tokens.text() == "5,700"

    # Selecting in tokens_tab updates chats_tab
    tokens_tab.inspect_conversation(cid_quota)
    assert chats_tab.current_conversation_id == cid_quota
    assert "QUOTA EXHAUSTED" in chats_tab.lbl_status_badge.text()


def test_chats_tab_autoscroll_toggle(qapp, tmp_path, monkeypatch):
    app_data_dir, brain_root, cid_completed, _, _ = _create_mock_db_and_brain(tmp_path)
    monkeypatch.setattr(
        "app.ui.chats_tab.get_recent_conversations",
        lambda limit=35: get_recent_conversations(app_data_dir=app_data_dir, limit=limit),
    )
    monkeypatch.setattr(
        "app.ui.chats_tab.inspect_conversation_lifecycle",
        lambda cid: inspect_conversation_lifecycle(
            cid, app_data_dir=app_data_dir, brain_dir=brain_root
        ),
    )

    tab = ChatsTab()
    assert hasattr(tab, "chk_resp_autoscroll")
    assert tab.chk_resp_autoscroll.isChecked() is True

    tab.inspect_conversation(cid_completed)
    tab.table_turns.selectRow(0)

    cursor = tab.txt_turn_response.textCursor()
    assert cursor.position() == len(tab.txt_turn_response.toPlainText())

    tab.chk_resp_autoscroll.setChecked(False)
    assert tab.chk_resp_autoscroll.isChecked() is False

    from PyQt6.QtGui import QTextCursor
    c0 = tab.txt_turn_response.textCursor()
    c0.movePosition(QTextCursor.MoveOperation.Start)
    tab.txt_turn_response.setTextCursor(c0)

    tab.btn_view_thinking.click()
    tab.chk_resp_autoscroll.setChecked(True)
    c_end = tab.txt_turn_response.textCursor()
    assert c_end.position() == len(tab.txt_turn_response.toPlainText())


def test_format_token_count_helper():
    from cdp.chat_lifecycle_inspector import format_token_count

    assert format_token_count(0) == "0"
    assert format_token_count(None) == "0"
    assert format_token_count(450) == "450"
    assert format_token_count(1500) == "1.5k"
    assert format_token_count(25000) == "25.0k" or format_token_count(25000) == "25k"
    assert format_token_count(1200000) == "1.2M"


def test_main_window_open_in_antigravity_minimizes_window():
    from unittest.mock import MagicMock, patch
    from app.ui.main_window import MainWindow

    win = MagicMock(spec=MainWindow)
    win.showMinimized = MagicMock()
    win.status_label = MagicMock()
    win.dashboard_tab = MagicMock()

    with patch("PyQt6.QtCore.QThread.start"):
        MainWindow._on_open_chat_in_antigravity(win, "test-conv-uuid-1234")

    win.showMinimized.assert_called_once()
