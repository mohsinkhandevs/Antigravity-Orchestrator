import json
import sqlite3
import pytest
from PyQt6.QtWidgets import QApplication

from app.models import PromptItem, TaskStatus
from app.ui.queue_tab import QueueTab
from app.ui.tokens_tab import TokensTab
from cdp.chat_lifecycle_inspector import (
    parse_protobuf_step_timestamps,
    inspect_conversation_lifecycle,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _encode_varint(val: int) -> bytes:
    res = bytearray()
    while val > 0x7F:
        res.append((val & 0x7F) | 0x80)
        val >>= 7
    res.append(val & 0x7F)
    return bytes(res)


def _encode_tag_and_val(field_number: int, wire_type: int, payload: bytes) -> bytes:
    tag = (field_number << 3) | wire_type
    if wire_type == 2:
        return _encode_varint(tag) + _encode_varint(len(payload)) + payload
    return _encode_varint(tag) + payload


def _create_mock_timestamp_msg(sec: int, nsec: int) -> bytes:
    sec_bytes = _encode_tag_and_val(1, 0, _encode_varint(sec))
    nsec_bytes = _encode_tag_and_val(2, 0, _encode_varint(nsec))
    return sec_bytes + nsec_bytes


def test_protobuf_step_timestamp_parsing():
    """Verifies pure-Python Protobuf parser accurately extracts start & finish nanoseconds."""
    # Field 1 = Start Time (1700000000s, 250000000ns = 1700000000.25)
    start_submsg = _create_mock_timestamp_msg(1700000000, 250000000)
    field1 = _encode_tag_and_val(1, 2, start_submsg)

    # Field 7 = Finish Time (1700000005s, 750000000ns = 1700000005.75)
    finish_submsg = _create_mock_timestamp_msg(1700000005, 750000000)
    field7 = _encode_tag_and_val(7, 2, finish_submsg)

    raw_meta = field1 + field7
    ts_dict = parse_protobuf_step_timestamps(raw_meta)

    assert 1 in ts_dict
    assert 7 in ts_dict
    assert ts_dict[1] == pytest.approx(1700000000.25)
    assert ts_dict[7] == pytest.approx(1700000005.75)


def test_pure_token_speed_excludes_tool_execution(tmp_path):
    """
    Verifies that model token speed strictly isolates active LLM deliberation
    and excludes tool execution time.
    """
    app_data = tmp_path / "app_data"
    convs_dir = app_data / "conversations"
    convs_dir.mkdir(parents=True, exist_ok=True)
    brain_root = tmp_path / "brain"
    brain_root.mkdir(parents=True, exist_ok=True)

    cid = "33333333-4444-5555-6666-777777777777"

    # Summaries DB
    s_db = app_data / "conversation_summaries.db"
    conn = sqlite3.connect(str(s_db))
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, title TEXT, status TEXT, not_fully_idle INTEGER, killed INTEGER, step_count INTEGER, workspace_uris TEXT)"
    )
    cur.execute(
        "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cid, "Test Pure Token Speed", "CASCADE_RUN_STATUS_IDLE", 0, 0, 4, '["file:///c:/proj"]'),
    )
    conn.commit()
    conn.close()

    # Brain transcript
    c_brain = brain_root / cid / ".system_generated" / "logs"
    c_brain.mkdir(parents=True, exist_ok=True)

    # 4 steps in 1 turn:
    # Step 1: User prompt at 12:00:00
    # Step 2: Model Gen (300 output tokens, duration = 6.0s: 12:00:00 -> 12:00:06), calls tool
    # Step 3: Tool Execution (duration = 20.0s: 12:00:06 -> 12:00:26)
    # Step 4: Model Gen Final (100 output tokens, duration = 4.0s: 12:00:26 -> 12:00:30)
    steps = [
        {
            "step_index": 1,
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-10-20T12:00:00Z",
            "content": "Create test report",
        },
        {
            "step_index": 2,
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-20T12:00:00Z",
            "input_tokens": 1200,
            "cache_read_tokens": 10000,
            "output_tokens": 300,
            "content": "Generating report...",
            "tool_calls": [{"name": "write_file", "args": {"Path": "report.md"}}],
        },
        {
            "step_index": 3,
            "type": "GENERIC",
            "status": "DONE",
            "created_at": "2026-10-20T12:00:06Z",
            "content": "Tool finished",
        },
        {
            "step_index": 4,
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-20T12:00:26Z",
            "input_tokens": 400,
            "cache_read_tokens": 11000,
            "output_tokens": 100,
            "content": "Report generation finished.",
            "tool_calls": [],
        },
    ]

    with open(c_brain / "transcript.jsonl", "w", encoding="utf-8") as f:
        for s in steps:
            f.write(json.dumps(s) + "\n")

    # SQLite conversation db with protobuf step metadata
    c_db_path = convs_dir / f"{cid}.db"
    c_conn = sqlite3.connect(str(c_db_path))
    c_cur = c_conn.cursor()
    c_cur.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, metadata BLOB)")

    # Step 2: 6.0s (1700000000 to 1700000006)
    m2 = _encode_tag_and_val(1, 2, _create_mock_timestamp_msg(1700000000, 0)) + _encode_tag_and_val(7, 2, _create_mock_timestamp_msg(1700000006, 0))
    # Step 3: 20.0s (1700000006 to 1700000026)
    m3 = _encode_tag_and_val(1, 2, _create_mock_timestamp_msg(1700000006, 0)) + _encode_tag_and_val(7, 2, _create_mock_timestamp_msg(1700000026, 0))
    # Step 4: 4.0s (1700000026 to 1700000030)
    m4 = _encode_tag_and_val(1, 2, _create_mock_timestamp_msg(1700000026, 0)) + _encode_tag_and_val(7, 2, _create_mock_timestamp_msg(1700000030, 0))

    c_cur.execute("INSERT INTO steps VALUES (?, ?, ?, ?)", (2, 15, 3, m2))
    c_cur.execute("INSERT INTO steps VALUES (?, ?, ?, ?)", (3, 132, 3, m3))
    c_cur.execute("INSERT INTO steps VALUES (?, ?, ?, ?)", (4, 15, 3, m4))
    c_conn.commit()
    c_conn.close()

    report = inspect_conversation_lifecycle(
        cid,
        app_data_dir=str(app_data),
        brain_dir=str(brain_root),
    )

    assert report["found"] is True
    assert len(report["prompt_turns"]) == 1
    turn = report["prompt_turns"][0]

    # Total tokens: 400 output tokens
    assert turn["output_tokens"] == 400

    # Model duration = Step 2 (6.0s) + Step 4 (4.0s) = 10.0s
    assert turn["total_model_duration_seconds"] == pytest.approx(10.0, abs=0.5)

    # Tool duration = Step 3 (20.0s)
    assert turn["total_tool_duration_seconds"] == pytest.approx(20.0, abs=0.5)

    # Pure LLM Speed = 400 tokens / 10.0s = 40.0 tok/s
    assert turn["tokens_per_second"] == pytest.approx(40.0, abs=1.0)

    # Wall-Clock Speed = 400 tokens / 26.0s = 15.4 tok/s
    assert turn["wall_clock_speed"] == pytest.approx(15.4, abs=0.5)

    # Overall conversation metrics
    assert report["conversation_model_duration_seconds"] == pytest.approx(10.0, abs=0.5)
    assert report["conversation_tool_duration_seconds"] == pytest.approx(20.0, abs=0.5)
    assert report["conversation_generation_speed"] == pytest.approx(40.0, abs=1.0)


def test_prompt_item_model_telemetry():
    """Verifies PromptItem retains token telemetry and serializes properly."""
    item = PromptItem(
        text="Calculate pi",
        output_tokens=500,
        input_tokens=1500,
        cache_read_tokens=12000,
        generation_time=10.0,
        tool_time=15.0,
        tokens_per_second=50.0,
    )

    d = item.to_dict()
    assert d["output_tokens"] == 500
    assert d["input_tokens"] == 1500
    assert d["cache_read_tokens"] == 12000
    assert d["generation_time"] == 10.0
    assert d["tool_time"] == 15.0
    assert d["tokens_per_second"] == 50.0

    restored = PromptItem.from_dict(d)
    assert restored.output_tokens == 500
    assert restored.generation_time == 10.0
    assert restored.tokens_per_second == 50.0

    # Duplicating resets execution stats for a fresh run
    clone = item.duplicate()
    assert clone.text == item.text
    assert clone.status == TaskStatus.PENDING
    assert clone.output_tokens == 0
    assert clone.tokens_per_second == 0.0


def test_queue_tab_speed_column_rendering(qapp):
    """Verifies that QueueTab displays pure generation speed in column 6."""
    tab = QueueTab()
    assert tab.table.columnCount() == 9

    item = PromptItem(
        text="Test generation speed",
        status=TaskStatus.COMPLETED,
        elapsed_time=25.0,
        output_tokens=600,
        generation_time=10.0,
        tool_time=15.0,
        tokens_per_second=60.0,
    )
    tab.prompts = [item]
    tab.refresh_table()

    assert tab.table.rowCount() == 1

    # Column 5: Duration
    dur_item = tab.table.item(0, 5)
    assert "25" in dur_item.text()
    assert "Model Gen: 10.0s" in dur_item.toolTip()

    # Column 6: Speed
    spd_item = tab.table.item(0, 6)
    assert spd_item.text() == "60.0 t/s"
    assert "Pure Model Generation Speed: 60.0 tokens/sec" in spd_item.toolTip()
    assert "Generation Duration: 10.0s" in spd_item.toolTip()
    assert "(Excludes tool execution and non-generation time)" in spd_item.toolTip()


def test_tokens_tab_speed_integration(qapp, tmp_path, monkeypatch):
    """Verifies TokensTab KPI Card and tables display pure generation speed."""
    app_data = tmp_path / "app_data"
    convs_dir = app_data / "conversations"
    convs_dir.mkdir(parents=True, exist_ok=True)
    brain_root = tmp_path / "brain"
    brain_root.mkdir(parents=True, exist_ok=True)

    cid = "88888888-9999-aaaa-bbbb-cccccccccccc"

    s_db = app_data / "conversation_summaries.db"
    conn = sqlite3.connect(str(s_db))
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, title TEXT, status TEXT, not_fully_idle INTEGER, killed INTEGER, step_count INTEGER, workspace_uris TEXT)"
    )
    cur.execute(
        "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cid, "Speed Test Tab", "CASCADE_RUN_STATUS_IDLE", 0, 0, 3, '["file:///c:/proj"]'),
    )
    conn.commit()
    conn.close()

    c_brain = brain_root / cid / ".system_generated" / "logs"
    c_brain.mkdir(parents=True, exist_ok=True)

    steps = [
        {
            "step_index": 1,
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-10-20T14:00:00Z",
            "content": "Perform benchmark",
        },
        {
            "step_index": 2,
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-20T14:00:00Z",
            "input_tokens": 1000,
            "cache_read_tokens": 8000,
            "output_tokens": 450,
            "content": "Benchmarking complete.",
            "tool_calls": [],
        },
        {
            "step_index": 3,
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-20T14:00:05Z",
            "input_tokens": 200,
            "cache_read_tokens": 9000,
            "output_tokens": 0,
            "content": "",
            "tool_calls": [],
        },
    ]

    with open(c_brain / "transcript.jsonl", "w", encoding="utf-8") as f:
        for s in steps:
            f.write(json.dumps(s) + "\n")

    monkeypatch.setattr(
        "app.ui.tokens_tab.get_recent_conversations",
        lambda limit=35: [{"id": cid, "title": "Speed Test Tab", "project": "proj"}],
    )
    monkeypatch.setattr(
        "app.ui.tokens_tab.inspect_conversation_lifecycle",
        lambda c: inspect_conversation_lifecycle(
            c, app_data_dir=str(app_data), brain_dir=str(brain_root)
        ),
    )

    tab = TokensTab()
    rep = tab.inspect_conversation(cid)
    assert rep is not None

    # Check KPI Card 3 has speed
    assert "tok/s" in tab.lbl_kpi_speed.text()
    assert float(tab.lbl_kpi_speed.text().replace("⚡", "").replace("tok/s", "").strip()) > 0

    # Check Turn-by-Turn table has 10 columns
    assert tab.table_turns.columnCount() == 10
    assert tab.table_turns.rowCount() == 1

    # Gen Time (col 6) and Speed (col 7)
    gen_time_item = tab.table_turns.item(0, 6)
    speed_item = tab.table_turns.item(0, 7)
    assert "s" in gen_time_item.text()
    assert "t/s" in speed_item.text()

    # Step table has 8 columns
    assert tab.table_steps.columnCount() == 8
    tab.table_turns.selectRow(0)
    assert tab.table_steps.rowCount() >= 1
    step_speed = tab.table_steps.item(0, 7)
    assert "t/s" in step_speed.text()
