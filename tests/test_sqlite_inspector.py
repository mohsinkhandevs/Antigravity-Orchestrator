import os
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from cdp.conversation_db_inspector import (
    get_conversation_summary,
    inspect_conversation_db_termination,
)
from cdp.dom_controller import AntigravityDOMController
from app.models import PromptItem, TaskStatus


@pytest.fixture
def temp_antigravity_db_env(tmp_path):
    """
    Sets up a temporary SQLite environment mimicking ~/.gemini/antigravity.
    """
    app_dir = tmp_path / "antigravity"
    conv_dir = app_dir / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)

    summaries_db = str(app_dir / "conversation_summaries.db")
    s_conn = sqlite3.connect(summaries_db)
    s_conn.execute("""
        CREATE TABLE conversation_summaries (
            conversation_id TEXT PRIMARY KEY,
            title TEXT,
            status TEXT,
            not_fully_idle INTEGER,
            killed INTEGER,
            step_count INTEGER
        )
    """)
    s_conn.commit()
    s_conn.close()

    return str(app_dir)


def test_get_conversation_summary(temp_antigravity_db_env):
    summaries_db = os.path.join(temp_antigravity_db_env, "conversation_summaries.db")
    conn = sqlite3.connect(summaries_db)
    conn.execute(
        "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?)",
        ("conv-running", "Test Running", "CASCADE_RUN_STATUS_RUNNING", 1, 0, 10),
    )
    conn.execute(
        "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?)",
        ("conv-idle", "Test Idle", "CASCADE_RUN_STATUS_IDLE", 0, 0, 20),
    )
    conn.execute(
        "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?)",
        ("conv-killed", "Test Killed", "CASCADE_RUN_STATUS_IDLE", 0, 1, 5),
    )
    conn.commit()
    conn.close()

    # Running chat
    res_run = get_conversation_summary("conv-running", app_data_dir=temp_antigravity_db_env)
    assert res_run["found"] is True
    assert res_run["is_idle"] is False
    assert res_run["not_fully_idle"] == 1
    assert res_run["status"] == "CASCADE_RUN_STATUS_RUNNING"

    # Idle chat
    res_idle = get_conversation_summary("conv-idle", app_data_dir=temp_antigravity_db_env)
    assert res_idle["found"] is True
    assert res_idle["is_idle"] is True
    assert res_idle["not_fully_idle"] == 0

    # Killed chat
    res_killed = get_conversation_summary("conv-killed", app_data_dir=temp_antigravity_db_env)
    assert res_killed["found"] is True
    assert res_killed["killed"] is True

    # Missing chat
    res_miss = get_conversation_summary("conv-nonexistent", app_data_dir=temp_antigravity_db_env)
    assert res_miss["found"] is False


def test_inspect_conversation_db_normal_completion(temp_antigravity_db_env):
    cid = "conv-normal"
    db_path = os.path.join(temp_antigravity_db_env, "conversations", f"{cid}.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, error_details BLOB)")
    conn.execute("CREATE TABLE executor_metadata (idx INTEGER PRIMARY KEY, data BLOB)")

    # Insert normal steps ending in PLANNER_RESPONSE (step_type=15, status=3)
    conn.execute("INSERT INTO steps VALUES (0, 14, 3, NULL)")
    conn.execute("INSERT INTO steps VALUES (1, 15, 3, NULL)")
    conn.execute("INSERT INTO executor_metadata VALUES (0, ?)", (b"\x08\x01\x10\x00clean_turn",))
    conn.commit()
    conn.close()

    res = inspect_conversation_db_termination(cid, app_data_dir=temp_antigravity_db_env)
    assert res["found"] is True
    assert res["state"] == "NORMAL"
    assert res["error_code"] is None


def test_inspect_conversation_db_step_error_status_7(temp_antigravity_db_env):
    cid = "conv-error-step"
    db_path = os.path.join(temp_antigravity_db_env, "conversations", f"{cid}.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, error_details BLOB)")
    conn.execute("CREATE TABLE executor_metadata (idx INTEGER PRIMARY KEY, data BLOB)")

    # Insert steps ending in status=7
    err_blob = b"unhandled runtime exception in tool execution"
    conn.execute("INSERT INTO steps VALUES (0, 14, 3, NULL)")
    conn.execute("INSERT INTO steps VALUES (1, 132, 7, ?)", (err_blob,))
    conn.commit()
    conn.close()

    res = inspect_conversation_db_termination(cid, app_data_dir=temp_antigravity_db_env)
    assert res["found"] is True
    assert res["state"] == "STEP_ERROR"
    assert res["error_code"] == 7
    assert "unhandled runtime exception" in res["detail"]


def test_inspect_conversation_db_executor_metadata_quota_429(temp_antigravity_db_env):
    cid = "conv-quota"
    db_path = os.path.join(temp_antigravity_db_env, "conversations", f"{cid}.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, error_details BLOB)")
    conn.execute("CREATE TABLE executor_metadata (idx INTEGER PRIMARY KEY, data BLOB)")

    quota_blob = b"execution error: generating and executing: RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 3h."
    conn.execute("INSERT INTO steps VALUES (0, 14, 3, NULL)")
    conn.execute("INSERT INTO steps VALUES (1, 132, 3, NULL)")
    conn.execute("INSERT INTO executor_metadata VALUES (0, ?)", (quota_blob,))
    conn.commit()
    conn.close()

    res = inspect_conversation_db_termination(cid, app_data_dir=temp_antigravity_db_env)
    assert res["found"] is True
    assert res["state"] == "QUOTA_EXHAUSTED"
    assert res["error_code"] == 429
    assert "RESOURCE_EXHAUSTED" in res["detail"]


def test_inspect_conversation_db_executor_metadata_network_error(temp_antigravity_db_env):
    cid = "conv-exec-err"
    db_path = os.path.join(temp_antigravity_db_env, "conversations", f"{cid}.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, error_details BLOB)")
    conn.execute("CREATE TABLE executor_metadata (idx INTEGER PRIMARY KEY, data BLOB)")

    err_blob = b"execution error: generating and executing: request failed: connection reset by peer"
    conn.execute("INSERT INTO steps VALUES (0, 14, 3, NULL)")
    conn.execute("INSERT INTO steps VALUES (1, 132, 3, NULL)")
    conn.execute("INSERT INTO executor_metadata VALUES (0, ?)", (err_blob,))
    conn.commit()
    conn.close()

    res = inspect_conversation_db_termination(cid, app_data_dir=temp_antigravity_db_env)
    assert res["found"] is True
    assert res["state"] == "EXECUTION_ERROR"
    assert res["error_code"] == 500
    assert "connection reset" in res["detail"]


def test_check_termination_state_zero_nav_prioritizes_sqlite(temp_antigravity_db_env):
    cid = "conv-sqlite-priority"
    db_path = os.path.join(temp_antigravity_db_env, "conversations", f"{cid}.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, error_details BLOB)")
    conn.execute("CREATE TABLE executor_metadata (idx INTEGER PRIMARY KEY, data BLOB)")
    conn.execute("INSERT INTO steps VALUES (0, 132, 7, ?)", (b"tool error in step 7",))
    conn.commit()
    conn.close()

    mock_page = MagicMock()
    mock_page.url = f"https://127.0.0.1:56593/c/{cid}"
    controller = AntigravityDOMController(mock_page)

    res = controller.check_termination_state_zero_nav(conversation_id=cid, app_data_dir=temp_antigravity_db_env)
    assert res["state"] == "STEP_ERROR"
    assert res["error_code"] == 7
    assert res["source"] == "sqlite_db"


def test_inspect_conversation_db_stale_executor_metadata_ignored_on_normal_completion(temp_antigravity_db_env):
    """
    Scenario: Conversation previously hit quota 429 at Step 0 and was recorded in executor_metadata.
    After in-chat recovery, it resumed and finished with PLANNER_RESPONSE (step_type=15, status=3) at Step 2.
    The inspector must return NORMAL and NOT be poisoned by the stale executor error.
    """
    cid = "conv-recovered-normal"
    db_path = os.path.join(temp_antigravity_db_env, "conversations", f"{cid}.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, error_details BLOB)")
    conn.execute("CREATE TABLE executor_metadata (idx INTEGER PRIMARY KEY, data BLOB)")

    # Historical quota 429 error at Step 0
    quota_blob = b"execution error: generating and executing: RESOURCE_EXHAUSTED (code 429): Individual quota reached."
    conn.execute("INSERT INTO executor_metadata VALUES (0, ?)", (quota_blob,))

    # Conversation resumed: Step 0 input, Step 1 tool, Step 2 clean PLANNER_RESPONSE
    conn.execute("INSERT INTO steps VALUES (0, 14, 3, NULL)")
    conn.execute("INSERT INTO steps VALUES (1, 132, 3, NULL)")
    conn.execute("INSERT INTO steps VALUES (2, 15, 3, NULL)")
    conn.commit()
    conn.close()

    res = inspect_conversation_db_termination(cid, app_data_dir=temp_antigravity_db_env)
    assert res["found"] is True
    assert res["state"] == "NORMAL"
    assert res["error_code"] is None
    assert "normally" in res["detail"]


def test_inspect_conversation_db_executor_metadata_active_quota_without_planner_response(temp_antigravity_db_env):
    """
    Scenario: Conversation hit quota 429 at Step 0 while executing tool call (step 1 is type 132, not 15).
    Since no subsequent clean PLANNER_RESPONSE completed, the executor error is active.
    The inspector must report QUOTA_EXHAUSTED.
    """
    cid = "conv-active-quota"
    db_path = os.path.join(temp_antigravity_db_env, "conversations", f"{cid}.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, error_details BLOB)")
    conn.execute("CREATE TABLE executor_metadata (idx INTEGER PRIMARY KEY, data BLOB)")

    quota_blob = b"execution error: generating and executing: RESOURCE_EXHAUSTED (code 429): Individual quota reached."
    conn.execute("INSERT INTO executor_metadata VALUES (0, ?)", (quota_blob,))
    conn.execute("INSERT INTO steps VALUES (0, 14, 3, NULL)")
    conn.execute("INSERT INTO steps VALUES (1, 132, 3, NULL)")
    conn.commit()
    conn.close()

    res = inspect_conversation_db_termination(cid, app_data_dir=temp_antigravity_db_env)
    assert res["found"] is True
    assert res["state"] == "QUOTA_EXHAUSTED"
    assert res["error_code"] == 429
    assert "RESOURCE_EXHAUSTED" in res["detail"]

