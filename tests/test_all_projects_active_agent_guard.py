from unittest.mock import MagicMock, patch
import pytest
from app.models import PromptItem, ProjectExecutionState, TaskStatus
from app.worker import AutomationWorker
from cdp.dom_controller import AntigravityDOMController


def test_find_all_running_conversations_in_sidebar_combines_dom_and_db():
    mock_page = MagicMock()
    # Mock DOM returning a human-started running chat in Project Alpha
    dom_found = [
        {
            "id": "conv_human_1",
            "title": "Human Refinement",
            "project": "Project Alpha",
            "is_running": True,
            "source": "sidebar_dom",
        }
    ]
    mock_page.evaluate.return_value = dom_found

    dom = AntigravityDOMController(mock_page)
    # Mock is_generating for active viewport
    dom.is_generating = MagicMock(return_value=False)

    # Mock DB inspector returning another chat in Project Beta
    with patch("cdp.conversation_db_inspector.find_running_conversations_in_db") as mock_db:
        mock_db.return_value = [
            {
                "id": "conv_db_2",
                "title": "Data Processing",
                "workspace": '["file:///c:/Users/mohsi/Desktop/Project%20Beta"]',
            }
        ]
        all_running = dom.find_all_running_conversations_in_sidebar(check_db=True)

    assert len(all_running) == 2
    ids = [r["id"] for r in all_running]
    assert "conv_human_1" in ids
    assert "conv_db_2" in ids
    projects = [r["project"] for r in all_running]
    assert "Project Alpha" in projects
    assert "Project Beta" in projects


def test_wait_for_all_sidebar_agents_idle_waits_until_all_quiet():
    worker = AutomationWorker(queue=[])
    mock_dom = MagicMock(spec=AntigravityDOMController)
    worker.dom_controller = mock_dom

    # Call sequence: 1st check finds human chat; 2nd check returns 0 chats (quiet)
    human_chat = {
        "id": "conv_human_123",
        "title": "Human Chat",
        "project": "Other-Project",
        "is_running": True,
    }
    mock_dom.find_all_running_conversations_in_sidebar.side_effect = [
        [human_chat],
        [],
    ]
    mock_dom.wait_for_completion.return_value = True

    ok = worker._wait_for_all_sidebar_agents_idle("testing startup barrier")
    assert ok is True
    # Verified it waited for the human conversation
    mock_dom.wait_for_completion.assert_called_once()
    assert mock_dom.wait_for_completion.call_args.kwargs.get("conversation_id") == "conv_human_123"
    assert mock_dom.wait_for_completion.call_args.kwargs.get("project_name") == "Other-Project"


def test_pre_dispatch_quota_blocks_switch_when_human_agent_running():
    worker = AutomationWorker(queue=[])
    mock_qi = MagicMock()
    mock_qi.inspect_model_quotas.return_value = {
        "is_safe_to_prompt": False,
        "safety_reason": "Gemini 5-Hour Limit (15%) is BELOW threshold (30%)!",
    }
    worker.quota_inspector = mock_qi
    worker._enforce_quota = MagicMock(return_value=True)

    mock_dom = MagicMock()
    # Sidebar detects an active human-started chat in an unqueued project
    mock_dom.find_all_running_conversations_in_sidebar.return_value = [
        {
            "id": "human_conv_99",
            "title": "Human Task",
            "project": "Unqueued-Project",
            "is_running": True,
        }
    ]
    worker.dom_controller = mock_dom

    # Active projects in queue are currently IDLE
    worker.active_projects = {
        "Queued-Project": ProjectExecutionState(project_name="Queued-Project", status="IDLE"),
    }

    can_dispatch = worker._check_pre_dispatch_quota("Queued-Project")

    # Must NOT dispatch yet and must NOT rotate accounts!
    assert can_dispatch is False
    assert worker._quota_drain_in_progress is True
    worker._enforce_quota.assert_not_called()


def test_enforce_quota_verifies_all_projects_idle_before_signout(monkeypatch):
    from app.config import config
    monkeypatch.setattr(config, "low_quota_action", "change_account")
    monkeypatch.setattr(config, "auto_switch_on_low_quota", True)

    worker = AutomationWorker(queue=[])
    mock_qi = MagicMock()
    unsafe_quota = {
        "is_safe_to_prompt": False,
        "safety_reason": "Low quota",
    }
    mock_qi.inspect_model_quotas.return_value = unsafe_quota
    worker.quota_inspector = mock_qi

    mock_oauth = MagicMock()
    worker.oauth_handler = mock_oauth

    # Mock _wait_for_all_sidebar_agents_idle returning False (e.g. stopped while waiting)
    worker._wait_for_all_sidebar_agents_idle = MagicMock(return_value=False)

    res = worker._enforce_quota("Pre-Dispatch Quota Recovery")
    assert res is False
    # Account switch must NOT be executed if workspace was not idle!
    mock_oauth.execute_account_switch.assert_not_called()


def test_wait_for_all_sidebar_agents_idle_respects_infinite_timeout():
    worker = AutomationWorker(queue=[])
    mock_dom = MagicMock(spec=AntigravityDOMController)
    worker.dom_controller = mock_dom

    human_chat = {
        "id": "conv_human_456",
        "title": "Fix CI Pipeline",
        "project": "Agrico-V2",
        "is_running": True,
    }
    mock_dom.find_all_running_conversations_in_sidebar.side_effect = [
        [human_chat],
        [],
    ]
    mock_dom.wait_for_completion.return_value = True

    ok = worker._wait_for_all_sidebar_agents_idle("testing infinite wait")
    assert ok is True
    mock_dom.wait_for_completion.assert_called_once()
    # Verified: is_infinite is True (not hardcoded to False or 30s)
    kwargs = mock_dom.wait_for_completion.call_args.kwargs
    assert kwargs.get("is_infinite") is True
    assert kwargs.get("timeout_seconds") >= 3600


def test_wait_for_all_sidebar_agents_idle_emits_hud_snapshot_with_active_agent():
    worker = AutomationWorker(queue=[])
    mock_dom = MagicMock(spec=AntigravityDOMController)
    worker.dom_controller = mock_dom

    snapshots_emitted = []
    worker.sig_active_projects_updated.connect(lambda s: snapshots_emitted.append(s))

    human_chat = {
        "id": "conv_human_789",
        "title": "Fix GitHub Actions CI Pipeline",
        "project": "Agrico-V2",
        "is_running": True,
    }
    mock_dom.find_all_running_conversations_in_sidebar.side_effect = [
        [human_chat],
        [],
    ]
    mock_dom.wait_for_completion.return_value = True

    ok = worker._wait_for_all_sidebar_agents_idle("testing HUD sync")
    assert ok is True
    # Verify snapshot was emitted with the detected project and chat title
    assert len(snapshots_emitted) >= 2
    first_snapshot = snapshots_emitted[0]
    assert "Agrico-V2" in first_snapshot
    assert first_snapshot["Agrico-V2"]["status"] == "GENERATING"
    assert first_snapshot["Agrico-V2"]["current_prompt_text"] == "Fix GitHub Actions CI Pipeline"

