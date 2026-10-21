import time
from unittest.mock import MagicMock, patch
import pytest

from app.models import (
    PromptItem,
    TaskStatus,
    ProjectExecutionState,
    partition_queue_into_subqueues,
    parse_subqueue_key,
)
from app.config import config
from app.worker import AutomationWorker, _find_all_running_project_agents, _find_running_project_agent


def test_find_all_running_project_agents_scopes_by_project():
    mock_dom = MagicMock()
    # Mock find_running_conversation_for_project to return an agent only for ProjA
    def mock_find(proj, exclude_cids=None):
        if proj == "ProjA":
            return {"id": "conv_a_1", "title": "Task A", "project": "ProjA", "is_running": True}
        return None

    mock_dom.find_running_conversation_for_project.side_effect = mock_find
    agents_a = _find_all_running_project_agents(mock_dom, "ProjA")
    agents_b = _find_all_running_project_agents(mock_dom, "ProjB")

    assert len(agents_a) == 1
    assert agents_a[0]["id"] == "conv_a_1"
    assert len(agents_b) == 0

    single_a = _find_running_project_agent(mock_dom, "ProjA")
    single_b = _find_running_project_agent(mock_dom, "ProjB")
    assert single_a is not None and single_a["id"] == "conv_a_1"
    assert single_b is None


def test_startup_gate_ignores_non_target_projects():
    """Startup active agents detection gate checks only projects present in queue, completely ignoring non-target projects."""
    q_item = PromptItem(id="p1", text="Target Prompt", project="TargetProj")
    worker = AutomationWorker(queue=[q_item])
    mock_dom = MagicMock()

    # NonTargetProj has a running agent; TargetProj has 0 running agents
    def mock_find(proj, exclude_cids=None):
        if proj == "NonTargetProj":
            return {"id": "conv_nontarget", "title": "Other Agent", "project": "NonTargetProj", "is_running": True}
        return None

    mock_dom.find_running_conversation_for_project.side_effect = mock_find
    worker.dom_controller = mock_dom

    dialog_emitted = []
    worker.sig_startup_active_agents_detected.connect(lambda ags, cnt: dialog_emitted.append((ags, cnt)))

    worker.project_queues = partition_queue_into_subqueues(worker.queue)
    worker.active_projects = {
        k: ProjectExecutionState(project_name="TargetProj", status="IDLE")
        for k in worker.project_queues.keys()
    }

    # Execute Step 2c logic
    target_projects = list(dict.fromkeys(parse_subqueue_key(k)[0] for k in worker.project_queues.keys()))
    tracked_cids = {s.conversation_id for s in worker.active_projects.values() if s.conversation_id}
    busy_projects = {}
    idle_projects = []
    for proj in target_projects:
        running_agents = _find_all_running_project_agents(worker.dom_controller, proj, exclude_cids=tracked_cids)
        if running_agents:
            busy_projects[proj] = running_agents
        else:
            idle_projects.append(proj)

    assert len(busy_projects) == 0
    assert idle_projects == ["TargetProj"]
    assert len(dialog_emitted) == 0
    assert worker._busy_projects_waiting == set()


def test_startup_gate_splits_idle_and_busy_target_projects():
    """Startup gate partitions target projects into busy and idle, emitting dialog only for busy projects."""
    q_items = [
        PromptItem(id="p1", text="Prompt A", project="TargetBusy"),
        PromptItem(id="p2", text="Prompt B", project="TargetIdle"),
    ]
    worker = AutomationWorker(queue=q_items)
    mock_dom = MagicMock()

    def mock_find(proj, exclude_cids=None):
        if proj == "TargetBusy":
            return {"id": "conv_busy_1", "title": "Busy Task", "project": "TargetBusy", "is_running": True}
        return None

    mock_dom.find_running_conversation_for_project.side_effect = mock_find
    worker.dom_controller = mock_dom

    dialog_emitted = []
    worker.sig_startup_active_agents_detected.connect(lambda ags, cnt: dialog_emitted.append((ags, cnt)))

    worker.project_queues = partition_queue_into_subqueues(worker.queue)
    worker.active_projects = {
        "TargetBusy": ProjectExecutionState(project_name="TargetBusy", status="IDLE"),
        "TargetIdle": ProjectExecutionState(project_name="TargetIdle", status="IDLE"),
    }

    target_projects = list(dict.fromkeys(parse_subqueue_key(k)[0] for k in worker.project_queues.keys()))
    tracked_cids = {s.conversation_id for s in worker.active_projects.values() if s.conversation_id}
    busy_projects = {}
    idle_projects = []
    for proj in target_projects:
        running_agents = _find_all_running_project_agents(worker.dom_controller, proj, exclude_cids=tracked_cids)
        if running_agents:
            busy_projects[proj] = running_agents
        else:
            idle_projects.append(proj)

    if busy_projects:
        all_busy_agents = [ag for ags in busy_projects.values() for ag in ags]
        worker._busy_projects_waiting = set(busy_projects.keys())
        worker.sig_startup_active_agents_detected.emit(all_busy_agents, len(worker.project_queues))

    assert "TargetBusy" in busy_projects
    assert "TargetIdle" in idle_projects
    assert worker._busy_projects_waiting == {"TargetBusy"}
    assert len(dialog_emitted) == 1
    assert dialog_emitted[0][0][0]["id"] == "conv_busy_1"


def test_idle_projects_start_immediately_while_busy_projects_wait():
    """Idle projects start immediately in parallel loop; busy project waits and does not block idle project."""
    p_busy = PromptItem(id="p1", text="Busy Task Prompt", project="ProjBusy")
    p_idle = PromptItem(id="p2", text="Idle Task Prompt", project="ProjIdle")
    queue = [p_busy, p_idle]

    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_into_subqueues(queue)
    worker.active_projects = {
        "ProjBusy": ProjectExecutionState(project_name="ProjBusy", status="IDLE"),
        "ProjIdle": ProjectExecutionState(project_name="ProjIdle", status="IDLE"),
    }
    worker._busy_projects_waiting = {"ProjBusy"}
    worker._startup_decision = "wait"

    mock_dom = MagicMock()
    # ProjBusy has a running agent; ProjIdle has None
    mock_dom.find_running_conversation_for_project.side_effect = lambda proj, **kw: (
        {"id": "conv_busy", "title": "Active ProjBusy Chat", "project": "ProjBusy", "is_running": True}
        if proj == "ProjBusy" else None
    )
    mock_dom.open_new_conversation.return_value = True
    mock_dom.get_active_conversation_id.return_value = "new_idle_chat"
    worker.dom_controller = mock_dom

    call_count = 0
    def mock_safe_sleep(dur):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            worker._stop_requested.set()
        return True

    worker.safe_sleep = mock_safe_sleep

    with patch.object(config, "max_concurrent_projects", 3), \
         patch.object(config, "polling_interval_seconds", 999):
        worker._run_multi_project_parallel_loop()

    # ProjIdle dispatched Prompt 2 immediately!
    assert worker.active_projects["ProjIdle"].status == "GENERATING"
    assert worker.active_projects["ProjIdle"].current_prompt.id == "p2"
    assert p_idle.status == TaskStatus.RUNNING

    # ProjBusy waited: Prompt 1 remains PENDING and state remains IDLE
    assert worker.active_projects["ProjBusy"].status == "IDLE"
    assert p_busy.status == TaskStatus.PENDING
    assert "ProjBusy" in worker._busy_projects_waiting


def test_busy_projects_start_independently_when_zero_agents_reached():
    """Two busy projects: Proj1 finishes first and starts immediately without waiting for Proj2."""
    p1 = PromptItem(id="p1", text="Prompt 1", project="Proj1")
    p2 = PromptItem(id="p2", text="Prompt 2", project="Proj2")
    queue = [p1, p2]

    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_into_subqueues(queue)
    worker.active_projects = {
        "Proj1": ProjectExecutionState(project_name="Proj1", status="IDLE"),
        "Proj2": ProjectExecutionState(project_name="Proj2", status="IDLE"),
    }
    worker._busy_projects_waiting = {"Proj1", "Proj2"}
    worker._startup_decision = "wait"

    proj1_running = True
    proj2_running = True

    mock_dom = MagicMock()
    def mock_find(proj, **kw):
        if proj == "Proj1" and proj1_running:
            return {"id": "conv_proj1", "title": "Proj1 Chat", "project": "Proj1", "is_running": True}
        if proj == "Proj2" and proj2_running:
            return {"id": "conv_proj2", "title": "Proj2 Chat", "project": "Proj2", "is_running": True}
        return None

    mock_dom.find_running_conversation_for_project.side_effect = mock_find
    mock_dom.open_new_conversation.return_value = True
    mock_dom.get_active_conversation_id.return_value = "chat_proj1_fresh"
    worker.dom_controller = mock_dom

    loop_iter = 0
    def mock_safe_sleep(dur):
        nonlocal loop_iter, proj1_running
        loop_iter += 1
        if loop_iter == 1:
            # Proj1 agent finishes! Proj2 remains running!
            proj1_running = False
        elif loop_iter >= 2:
            worker._stop_requested.set()
        return True

    worker.safe_sleep = mock_safe_sleep

    with patch.object(config, "max_concurrent_projects", 3), \
         patch.object(config, "polling_interval_seconds", 999):
        worker._run_multi_project_parallel_loop()

    # Proj1 started immediately upon reaching 0 active agents!
    assert "Proj1" not in worker._busy_projects_waiting
    assert worker.active_projects["Proj1"].status == "GENERATING"
    assert p1.status == TaskStatus.RUNNING

    # Proj2 is still busy and still waiting!
    assert "Proj2" in worker._busy_projects_waiting
    assert worker.active_projects["Proj2"].status == "IDLE"
    assert p2.status == TaskStatus.PENDING


def test_startup_decision_proceed_starts_busy_project_immediately():
    """When user resolves startup decision with 'proceed' (Prompt Now), busy project starts immediately without waiting."""
    p1 = PromptItem(id="p1", text="Prompt 1", project="ProjBusy")
    queue = [p1]

    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_into_subqueues(queue)
    worker.active_projects = {
        "ProjBusy": ProjectExecutionState(project_name="ProjBusy", status="IDLE"),
    }
    worker._busy_projects_waiting = {"ProjBusy"}

    # User clicks 'Prompt Now'
    worker.resolve_startup_decision("proceed")
    assert worker._busy_projects_waiting == set()
    assert worker._startup_decision == "proceed"

    mock_dom = MagicMock()
    # Running agent still exists
    mock_dom.find_running_conversation_for_project.return_value = {
        "id": "untracked_agent_chat", "title": "Untracked", "project": "ProjBusy", "is_running": True
    }
    mock_dom.open_new_conversation.return_value = True
    mock_dom.get_active_conversation_id.return_value = "new_prompt_chat"
    worker.dom_controller = mock_dom

    call_count = 0
    def mock_safe_sleep(dur):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            worker._stop_requested.set()
        return True

    worker.safe_sleep = mock_safe_sleep

    with patch.object(config, "max_concurrent_projects", 2), \
         patch.object(config, "polling_interval_seconds", 999):
        worker._run_multi_project_parallel_loop()

    # Prompt 1 was dispatched immediately (fresh chat opened, NOT blocked on untracked agent)!
    assert worker.active_projects["ProjBusy"].status == "GENERATING"
    assert worker.active_projects["ProjBusy"].current_prompt.id == "p1"
    assert p1.status == TaskStatus.RUNNING
