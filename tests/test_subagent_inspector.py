import json
import pytest
from cdp.subagent_inspector import (
    build_subagent_tree,
    format_duration_human,
    get_subagent_descriptors,
    get_subagent_messages,
    get_subagents_fleet_summary,
    inspect_subagent,
    parse_subagent_transcript,
)
from cdp.chat_lifecycle_inspector import inspect_conversation_lifecycle


def test_format_duration_human():
    assert format_duration_human(-5) == "0s"
    assert format_duration_human(0) == "0s"
    assert format_duration_human(5.2) == "5.2s"
    assert format_duration_human(45) == "45s"
    assert format_duration_human(125) == "2m 05s"
    assert format_duration_human(3665) == "1h 01m 05s"


def test_empty_and_invalid_subagent_directory(tmp_path):
    brain_dir = str(tmp_path / "brain")
    # Non-existent ID
    desc = get_subagent_descriptors("non-existent-id", brain_dir=brain_dir)
    assert desc == []

    metrics = parse_subagent_transcript("non-existent-id", brain_dir=brain_dir)
    assert metrics["step_count"] == 0
    assert metrics["current_context_tokens"] == 0

    tree = build_subagent_tree("non-existent-id", brain_dir=brain_dir)
    assert tree == []

    summary = get_subagents_fleet_summary("non-existent-id", brain_dir=brain_dir)
    assert summary["total_count"] == 0
    assert summary["active_count"] == 0


def test_subagent_full_telemetry_and_tree(tmp_path):
    brain_dir = tmp_path / "brain"
    root_id = "root-lead-1234"
    child1_id = "child-builder-5678"
    child2_id = "child-reviewer-9999"

    # 1. Create root brain directory and subagent descriptor files
    root_subagents_dir = brain_dir / root_id / ".system_generated" / "subagents"
    root_subagents_dir.mkdir(parents=True, exist_ok=True)

    desc1 = {
        "conversationId": child1_id,
        "subagentDescriptor": {
            "typeName": "self",
            "role": "Refocus P1-021 Builder",
        },
        "state": "SUBAGENT_STATE_ALIVE",
        "spawnStepIndex": 10,
        "workspaceUris": ["file:///workspace/refocus"],
    }
    with open(root_subagents_dir / f"{child1_id}.json", "w", encoding="utf-8") as f:
        json.dump(desc1, f)

    desc2 = {
        "conversationId": child2_id,
        "subagentDescriptor": {
            "typeName": "code-reviewer",
            "role": "Adversarial Code Reviewer",
        },
        "state": "SUBAGENT_STATE_KILLED",
        "spawnStepIndex": 25,
        "workspaceUris": ["file:///workspace/refocus"],
    }
    with open(root_subagents_dir / f"{child2_id}.json", "w", encoding="utf-8") as f:
        json.dump(desc2, f)

    # 2. Create transcript for Child 1 (Builder)
    child1_logs = brain_dir / child1_id / ".system_generated" / "logs"
    child1_logs.mkdir(parents=True, exist_ok=True)
    child1_transcript = child1_logs / "transcript.jsonl"

    c1_steps = [
        {
            "step_index": 0,
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-10-21T09:00:00Z",
            "content": "Start task",
        },
        {
            "step_index": 1,
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-21T09:05:00Z",
            "input_tokens": 1000,
            "cache_read_tokens": 9000,
            "output_tokens": 200,
            "tool_calls": [{"name": "run_command", "args": {"cmd": "ls"}}],
        },
        {
            "step_index": 2,
            "type": "CHECKPOINT",
            "status": "DONE",
            "created_at": "2026-10-21T09:10:00Z",
            "content": "Resuming from compaction",
        },
        {
            "step_index": 3,
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-21T09:15:00Z",
            "input_tokens": 2000,
            "cache_read_tokens": 18000,
            "output_tokens": 300,
            "tool_calls": [],
        },
    ]
    with open(child1_transcript, "w", encoding="utf-8") as f:
        for s in c1_steps:
            f.write(json.dumps(s) + "\n")

    # 3. Create IPC messages in Child 1
    child1_msgs = brain_dir / child1_id / ".system_generated" / "messages"
    child1_msgs.mkdir(parents=True, exist_ok=True)

    msg1 = {
        "id": "msg-001",
        "sender": root_id,
        "recipient": child1_id,
        "timestamp": "2026-10-21T09:00:05Z",
        "renderDetails": {"messageTitle": "Task Briefing"},
        "content": "Implement P1-021 entity",
    }
    with open(child1_msgs / "msg-001.json", "w", encoding="utf-8") as f:
        json.dump(msg1, f)

    # Marker file read.json should be ignored
    with open(child1_msgs / "read.json", "w", encoding="utf-8") as f:
        json.dump({"msg-001": True}, f)

    # 4. Verify get_subagent_descriptors
    descs = get_subagent_descriptors(root_id, brain_dir=str(brain_dir))
    assert len(descs) == 2
    assert descs[0]["conversationId"] == child1_id
    assert descs[1]["conversationId"] == child2_id

    # 5. Verify parse_subagent_transcript
    m1 = parse_subagent_transcript(child1_id, brain_dir=str(brain_dir))
    assert m1["step_count"] == 4
    assert m1["compaction_count"] == 1
    assert m1["tool_calls_count"] == 1
    assert m1["input_tokens"] == 3000
    assert m1["cache_read_tokens"] == 27000
    assert m1["output_tokens"] == 500
    assert m1["current_context_tokens"] == 20300  # 2000 + 18000 + 300
    assert m1["peak_context_tokens"] == 20300
    assert m1["cache_hit_ratio"] == 0.9  # 27000 / 30000

    # 6. Verify IPC messages
    msgs = get_subagent_messages(child1_id, brain_dir=str(brain_dir))
    assert len(msgs) == 1
    assert msgs[0]["id"] == "msg-001"
    assert msgs[0]["title"] == "Task Briefing"

    # 7. Verify full tree & fleet summary
    summary = get_subagents_fleet_summary(root_id, brain_dir=str(brain_dir))
    assert summary["total_count"] == 2
    assert summary["active_count"] == 1  # desc1 is ALIVE, desc2 is KILLED
    assert summary["total_steps"] == 4
    assert summary["total_input_tokens"] == 3000
    assert summary["total_cache_read_tokens"] == 27000
    assert summary["peak_context_tokens"] == 20300

    tree = summary["subagents_tree"]
    assert len(tree) == 2
    assert tree[0]["role"] == "Refocus P1-021 Builder"
    assert tree[0]["is_alive"] is True
    assert tree[1]["is_killed"] is True
    assert tree[1]["status_label"] == "KILLED"


def test_lifecycle_inspector_integration(tmp_path):
    brain_dir = tmp_path / "brain"
    root_id = "11111111-2222-3333-4444-555555555555"
    sub_id = "66666666-7777-8888-9999-000000000000"

    root_subagents_dir = brain_dir / root_id / ".system_generated" / "subagents"
    root_subagents_dir.mkdir(parents=True, exist_ok=True)

    with open(root_subagents_dir / f"{sub_id}.json", "w", encoding="utf-8") as f:
        json.dump({
            "conversationId": sub_id,
            "subagentDescriptor": {"role": "Subagent Worker"},
            "state": "SUBAGENT_STATE_ALIVE",
        }, f)

    root_logs = brain_dir / root_id / ".system_generated" / "logs"
    root_logs.mkdir(parents=True, exist_ok=True)
    with open(root_logs / "transcript.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "step_index": 0,
            "type": "USER_INPUT",
            "content": "Run orchestrator",
            "created_at": "2026-10-21T09:00:00Z"
        }) + "\n")

    res = inspect_conversation_lifecycle(root_id, brain_dir=str(brain_dir))
    assert res["found"] is True
    assert res["total_subagents"] == 1
    assert "subagents_summary" in res
    assert len(res["subagents_tree"]) == 1
    assert res["subagents_tree"][0]["conversation_id"] == sub_id


from PyQt6.QtWidgets import QApplication
from app.ui.subagents_widget import SubagentsWidget
from app.ui.chats_tab import ChatsTab
from app.ui.tokens_tab import TokensTab


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_subagents_widget_ui(qapp, tmp_path):
    brain_dir = tmp_path / "brain"
    root_id = "22222222-3333-4444-5555-666666666666"
    sub_id = "77777777-8888-9999-0000-111111111111"

    # Setup subagent files
    sub_dir = brain_dir / root_id / ".system_generated" / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    with open(sub_dir / f"{sub_id}.json", "w", encoding="utf-8") as f:
        json.dump({
            "conversationId": sub_id,
            "subagentDescriptor": {"role": "Widget Test Builder", "typeName": "self"},
            "state": "SUBAGENT_STATE_ALIVE",
            "spawnStepIndex": 5,
        }, f)

    sub_logs = brain_dir / sub_id / ".system_generated" / "logs"
    sub_logs.mkdir(parents=True, exist_ok=True)
    with open(sub_logs / "transcript.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "step_index": 0,
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-10-21T09:00:00Z"
        }) + "\n")
        f.write(json.dumps({
            "step_index": 1,
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-21T09:05:00Z",
            "input_tokens": 500,
            "cache_read_tokens": 2000,
            "output_tokens": 100,
        }) + "\n")

    sub_msgs = brain_dir / sub_id / ".system_generated" / "messages"
    sub_msgs.mkdir(parents=True, exist_ok=True)
    with open(sub_msgs / "msg1.json", "w", encoding="utf-8") as f:
        json.dump({
            "id": "m1",
            "sender": root_id,
            "recipient": sub_id,
            "timestamp": "2026-10-21T09:00:05Z",
            "renderDetails": {"messageTitle": "Builder Brief"},
            "content": "Implement feature X",
        }, f)

    # 1. Test standalone SubagentsWidget
    widget = SubagentsWidget(brain_dir=str(brain_dir))
    widget.load_conversation(root_id)
    assert widget.badge_count.text() == "1 Subagent"
    assert widget.badge_active.text() == "1 Active"
    assert widget.tree.topLevelItemCount() == 1

    item = widget.tree.topLevelItem(0)
    assert "Widget Test Builder" in item.text(0)
    assert "IDLE / WAITING" in item.text(1)

    # Select item to trigger drawer
    widget.tree.setCurrentItem(item)
    assert "Widget Test Builder" in widget.drawer_title.text()
    assert "Implement feature X" in widget.messages_view.toPlainText()

    # 2. Test ChatsTab integration
    chats_tab = ChatsTab()
    chats_tab.subagents_widget.brain_dir = str(brain_dir)
    mock_report = {
        "conversation_id": root_id,
        "title": "Test Chat",
        "found": True,
        "overall_state": "NORMAL",
        "total_subagents": 1,
        "prompt_turns": [],
        "artifacts": [],
    }
    chats_tab._render_report(mock_report)
    assert not chats_tab.subagents_widget.isHidden()
    assert chats_tab.subagents_widget.badge_count.text() == "1 Subagent"

    # When report has 0 subagents, widget hides
    mock_report_no_subs = {
        "conversation_id": "33333333-4444-5555-6666-777777777777",
        "title": "Empty Chat",
        "found": True,
        "overall_state": "NORMAL",
        "total_subagents": 0,
        "prompt_turns": [],
        "artifacts": [],
    }
    chats_tab._render_report(mock_report_no_subs)
    assert chats_tab.subagents_widget.isHidden()

    # 3. Test TokensTab integration
    tokens_tab = TokensTab()
    tokens_tab.subagents_widget.brain_dir = str(brain_dir)
    tokens_tab._render_token_dashboard(mock_report)
    assert not tokens_tab.subagents_widget.isHidden()

    tokens_tab._render_token_dashboard(mock_report_no_subs)
    assert tokens_tab.subagents_widget.isHidden()
