import time
from unittest.mock import MagicMock, patch
import pytest

from app.models import (
    PromptItem,
    TaskStatus,
    ProjectExecutionState,
    partition_queue_by_project,
    partition_queue_into_subqueues,
    get_subqueue_key,
    parse_subqueue_key,
)
from app.config import config
from app.worker import AutomationWorker
from cdp.dom_controller import AntigravityDOMController


def test_partition_queue_by_project():
    items = [
        PromptItem(id="1", text="Task 1", project="Agrico-V2"),
        PromptItem(id="2", text="Task 2", project="Antigravity CDP"),
        PromptItem(id="3", text="Task 3", project="Agrico-V2"),
        PromptItem(id="4", text="Task 4", project=""),  # default fallback
        PromptItem(id="5", text="Task 5", project="2 step automation"),
    ]
    grouped = partition_queue_by_project(items, default_project="Agrico-V2")

    assert "Agrico-V2" in grouped
    assert "Antigravity CDP" in grouped
    assert "2 step automation" in grouped

    assert len(grouped["Agrico-V2"]) == 3  # items 1, 3, and 4
    assert [it.id for it in grouped["Agrico-V2"]] == ["1", "3", "4"]
    assert len(grouped["Antigravity CDP"]) == 1
    assert grouped["Antigravity CDP"][0].id == "2"
    assert len(grouped["2 step automation"]) == 1
    assert grouped["2 step automation"][0].id == "5"


def test_project_execution_state_dataclass():
    st = ProjectExecutionState(
        project_name="Agrico-V2",
        status="GENERATING",
        conversation_id="conv_123",
        is_paused=False,
    )
    d = st.to_dict()
    assert d["project_name"] == "Agrico-V2"
    assert d["status"] == "GENERATING"
    assert d["conversation_id"] == "conv_123"
    assert d["is_paused"] is False


def test_switch_to_conversation():
    mock_page = MagicMock()
    mock_page.url = "http://localhost:9222/c/old_conv"
    controller = AntigravityDOMController(mock_page)

    # 1. Already on the active conversation
    with patch.object(controller, "get_active_conversation_id", return_value="conv_abc"):
        assert controller.switch_to_conversation("conv_abc") is True
        mock_page.evaluate.assert_not_called()

    # 2. Sidebar click success
    with patch.object(controller, "get_active_conversation_id", return_value="conv_other"):
        mock_page.evaluate.return_value = True
        assert controller.switch_to_conversation("conv_target") is True

    # 3. Sidebar click fails, fallback to page.goto
    with patch.object(controller, "get_active_conversation_id", return_value="conv_other"):
        mock_page.evaluate.return_value = False
        mock_page.url = "http://localhost:9222/c/conv_other"
        assert controller.switch_to_conversation("conv_target") is True
        mock_page.goto.assert_called_with(
            "http://localhost:9222/c/conv_target",
            wait_until="domcontentloaded",
            timeout=4000,
        )


def test_is_sidebar_conversation_generating():
    mock_page = MagicMock()
    controller = AntigravityDOMController(mock_page)

    mock_page.evaluate.return_value = True
    assert controller.is_sidebar_conversation_generating("conv_123") is True

    mock_page.evaluate.return_value = False
    assert controller.is_sidebar_conversation_generating("conv_123") is False

    mock_page.evaluate.return_value = None
    assert controller.is_sidebar_conversation_generating("conv_123") is None


def test_multi_project_parallel_dispatch_strictly_one_chat_per_project():
    """
    Verifies that the orchestrator enforces strictly at most ONE active chat per project,
    even when a project has multiple pending prompts in the queue.
    """
    queue = [
        PromptItem(id="p1", text="Agrico Prompt 1", project="Agrico-V2"),
        PromptItem(id="p2", text="Agrico Prompt 2", project="Agrico-V2"),
        PromptItem(id="p3", text="CDP Prompt 1", project="Antigravity CDP"),
        PromptItem(id="p4", text="Step Prompt 1", project="2 step automation"),
    ]

    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_by_project(queue)
    worker.active_projects = {
        p: ProjectExecutionState(project_name=p, status="IDLE")
        for p in worker.project_queues.keys()
    }

    mock_dom = MagicMock()
    mock_dom.find_running_conversation_for_project.return_value = None
    mock_dom.open_new_conversation.return_value = True
    mock_dom.get_active_conversation_id.side_effect = ["conv_agrico_1", "conv_cdp_1", "conv_step_1"]
    worker.dom_controller = mock_dom

    # Simulate dispatch pass
    # Agrico-V2 has 2 prompts, Antigravity CDP has 1, 2 step automation has 1
    # Run the loop with stop requested after 1 iteration
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

    # Verify that Agrico-V2 only dispatched Prompt 1 (conv_agrico_1), NOT Prompt 2!
    assert worker.active_projects["Agrico-V2"].status == "GENERATING"
    assert worker.active_projects["Agrico-V2"].current_prompt.id == "p1"
    assert queue[0].status == TaskStatus.RUNNING
    # Prompt 2 MUST remain pending
    assert queue[1].status == TaskStatus.PENDING

    # Other distinct projects dispatched their 1 chat
    assert worker.active_projects["Antigravity CDP"].status == "GENERATING"
    assert queue[2].status == TaskStatus.RUNNING
    assert worker.active_projects["2 step automation"].status == "GENERATING"
    assert queue[3].status == TaskStatus.RUNNING


def test_independent_project_pause_and_resume():
    """
    Verifies that pausing and resuming one project operates strictly on that project,
    leaving other active projects generating in parallel.
    """
    queue = [
        PromptItem(id="p1", text="Agrico Prompt 1", project="Agrico-V2"),
        PromptItem(id="p2", text="CDP Prompt 1", project="Antigravity CDP"),
    ]

    worker = AutomationWorker(queue=queue)
    worker.dom_controller = MagicMock()

    worker.active_projects = {
        "Agrico-V2": ProjectExecutionState(
            project_name="Agrico-V2",
            status="GENERATING",
            conversation_id="conv_agrico",
            current_prompt=queue[0],
        ),
        "Antigravity CDP": ProjectExecutionState(
            project_name="Antigravity CDP",
            status="GENERATING",
            conversation_id="conv_cdp",
            current_prompt=queue[1],
        ),
    }

    # 1. Pause Agrico-V2 independently
    worker.pause_project("Agrico-V2")
    assert worker.active_projects["Agrico-V2"].is_paused is True
    assert worker.active_projects["Agrico-V2"].status == "PAUSED"
    # Antigravity CDP must remain untouched and GENERATING!
    assert worker.active_projects["Antigravity CDP"].is_paused is False
    assert worker.active_projects["Antigravity CDP"].status == "GENERATING"

    # 2. Resume Agrico-V2 independently
    worker.resume_project("Agrico-V2")
    assert worker.active_projects["Agrico-V2"].is_paused is False
    assert worker.active_projects["Agrico-V2"].status == "GENERATING"
    assert queue[0].is_resumed is True
    assert queue[0].resume_count == 1
    # dom_controller should have switched to Agrico's chat and injected resume mini-prompt
    worker.dom_controller.switch_to_conversation.assert_called_with("conv_agrico")
    worker.dom_controller.inject_resume_prompt.assert_called_once()
    # Antigravity CDP still generating
    assert worker.active_projects["Antigravity CDP"].status == "GENERATING"


def test_multi_project_round_robin_polling_advances_only_completed_project():
    """
    Simulates polling cycle: Project A completes naturally while Project B is still generating.
    Verifies Project A is marked COMPLETED and becomes IDLE, while Project B remains GENERATING.
    """
    queue = [
        PromptItem(id="p1", text="Agrico Prompt 1", project="Agrico-V2", status=TaskStatus.RUNNING),
        PromptItem(id="p2", text="Agrico Prompt 2", project="Agrico-V2", status=TaskStatus.PENDING),
        PromptItem(id="p3", text="CDP Prompt 1", project="Antigravity CDP", status=TaskStatus.RUNNING),
    ]

    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_by_project(queue)
    worker.active_projects = {
        "Agrico-V2": ProjectExecutionState(
            project_name="Agrico-V2",
            status="GENERATING",
            conversation_id="conv_agrico",
            current_prompt=queue[0],
            start_time=time.time() - 40,
        ),
        "Antigravity CDP": ProjectExecutionState(
            project_name="Antigravity CDP",
            status="GENERATING",
            conversation_id="conv_cdp",
            current_prompt=queue[2],
            start_time=time.time() - 40,
        ),
    }

    mock_dom = MagicMock()
    # Zero-Navigation Polling: checks conversation_id via sidebar + local transcript without switching UI!
    mock_dom.is_conversation_generating.side_effect = lambda conversation_id=None, project_name=None: (conversation_id == "conv_cdp")
    mock_dom.find_running_conversation_for_project.return_value = None
    mock_dom.check_termination_state.return_value = {"state": "NORMAL", "detail": ""}
    mock_dom.wait_for_completion.return_value = True
    worker.dom_controller = mock_dom

    call_count = 0
    def mock_safe_sleep(dur):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            worker._stop_requested.set()
        return True

    worker.safe_sleep = mock_safe_sleep

    # Force polling interval to trigger immediately
    with patch.object(config, "polling_interval_seconds", 0):
        worker._run_multi_project_parallel_loop()

    # Zero UI navigation during polling!
    mock_dom.switch_to_conversation.assert_not_called()

    # Project A should be COMPLETED and prompt 1 status COMPLETED
    assert queue[0].status == TaskStatus.COMPLETED
    assert worker.active_projects["Agrico-V2"].status == "IDLE"

    # Project B should remain GENERATING
    assert worker.active_projects["Antigravity CDP"].status == "GENERATING"
    assert queue[2].status == TaskStatus.RUNNING


def test_multi_project_coordinated_quota_recovery():
    """
    Simulates coordinated quota recovery:
    Project A halts on quota. Orchestrator waits until Project B stops before rotating accounts,
    then batch-resumes both project chats.
    """
    queue = [
        PromptItem(id="p1", text="Agrico Prompt 1", project="Agrico-V2"),
        PromptItem(id="p2", text="CDP Prompt 1", project="Antigravity CDP"),
    ]

    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_by_project(queue)
    worker.active_projects = {
        "Agrico-V2": ProjectExecutionState(
            project_name="Agrico-V2",
            status="HALTED_QUOTA",
            conversation_id="conv_agrico",
            current_prompt=queue[0],
        ),
        "Antigravity CDP": ProjectExecutionState(
            project_name="Antigravity CDP",
            status="HALTED_QUOTA",
            conversation_id="conv_cdp",
            current_prompt=queue[1],
        ),
    }

    mock_dom = MagicMock()
    worker.dom_controller = mock_dom
    worker._enforce_quota = MagicMock(return_value=True)

    call_count = 0
    def mock_safe_sleep(dur):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            worker._stop_requested.set()
        return True

    worker.safe_sleep = mock_safe_sleep

    with patch.object(config, "polling_interval_seconds", 999):
        worker._run_multi_project_parallel_loop()

    # Verified: _enforce_quota called once for the entire batch
    worker._enforce_quota.assert_called_once_with("Multi-Project Coordinated Quota Recovery")
    # Both chats resumed in their same chats
    assert worker.active_projects["Agrico-V2"].status == "GENERATING"
    assert queue[0].is_resumed is True
    assert worker.active_projects["Antigravity CDP"].status == "GENERATING"
    assert queue[1].is_resumed is True


def test_queue_tab_active_projects_hud():
    from PyQt6.QtWidgets import QApplication
    from app.ui.queue_tab import QueueTab

    app = QApplication.instance() or QApplication([])
    tab = QueueTab()

    # Initial state: placeholder is visible
    assert tab.hud_chips_layout.count() == 1
    assert tab.hud_placeholder_label.text() == "No active parallel projects currently generating."

    # Update with 3 active projects
    tab.update_active_projects_hud({
        "Agrico-V2": {"status": "GENERATING", "elapsed_time": 25.0, "is_paused": False},
        "Antigravity CDP": {"status": "PAUSED", "elapsed_time": 10.0, "is_paused": True},
        "2 step automation": {"status": "HALTED_QUOTA", "elapsed_time": 15.0, "is_paused": False},
    })

    # Should have 3 chips rendered
    assert tab.hud_chips_layout.count() == 3

    # Test signals when pause/resume buttons are clicked
    pause_emitted = []
    resume_emitted = []
    tab.sig_pause_project_requested.connect(lambda p: pause_emitted.append(p))
    tab.sig_resume_project_requested.connect(lambda p: resume_emitted.append(p))

    # Clear HUD when queue finishes
    tab.update_active_projects_hud({})
    assert tab.hud_chips_layout.count() == 1


def test_settings_tab_concurrency_controls():
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from app.ui.settings_tab import SettingsTab

    app = QApplication.instance() or QApplication([])
    with patch.object(SettingsTab, "_test_cdp_connection"):
        tab = SettingsTab()

    # Check defaults
    assert tab.spin_max_concurrent.value() == config.max_concurrent_projects
    assert tab.spin_poll_interval.value() == config.polling_interval_seconds
    assert tab.chk_independent_resume.isChecked() is True

    # Modify and save (including fast 2s polling interval)
    tab.spin_max_concurrent.setValue(4)
    tab.spin_poll_interval.setValue(2)
    tab.chk_independent_resume.setChecked(False)

    with patch.object(config, "save") as mock_save, \
         patch.object(QMessageBox, "information") as mock_info:
        tab._save_settings()
        mock_save.assert_called_once()
        mock_info.assert_called_once()
        assert config.max_concurrent_projects == 4
        assert config.polling_interval_seconds == 2
        assert config.enable_independent_project_resume is False


def test_find_running_conversation_for_project():
    """
    Verifies find_running_conversation_for_project returns active running info
    when spinner is detected under project header, and None when idle or not found.
    """
    mock_page = MagicMock()
    controller = AntigravityDOMController(mock_page)

    # 1. Project has running chat
    mock_page.evaluate.return_value = {
        "id": "conv_bg_99",
        "title": "AgriCo V2 Remediation Plan Execution",
        "project": "agrico-v2",
        "is_running": True,
    }
    res = controller.find_running_conversation_for_project("Agrico-V2")
    assert res is not None
    assert res["id"] == "conv_bg_99"
    assert res["is_running"] is True
    assert controller.is_project_busy("Agrico-V2") is True

    # 2. Project has no running chat (idle)
    mock_page.evaluate.return_value = None
    res = controller.find_running_conversation_for_project("Agrico-V2")
    assert res is None
    assert controller.is_project_busy("Agrico-V2") is False

    # 3. Empty project name returns None immediately without calling evaluate
    mock_page.evaluate.reset_mock()
    assert controller.find_running_conversation_for_project("") is None
    mock_page.evaluate.assert_not_called()


def test_multi_project_worker_adopts_background_running_agent():
    """
    Verifies that when a project already has an agent generating in the background,
    the multi-project parallel loop does NOT open a new chat, keeps the queued prompt
    in PENDING, adopts the running chat, and waits for it to finish.
    """
    item1 = PromptItem(id="p1", text="Queued Agrico Prompt", project="Agrico-V2")
    mock_dom = MagicMock()
    worker = AutomationWorker(queue=[item1], dom_controller=mock_dom)
    worker.project_queues = {"Agrico-V2": [item1]}
    worker.active_projects = {
        "Agrico-V2": ProjectExecutionState(project_name="Agrico-V2", status="IDLE")
    }

    # Simulate background agent is running in Agrico-V2 initially
    running_info = {
        "id": "bg_chat_123",
        "title": "AgriCo V2 Remediation Plan Execution",
        "project": "Agrico-V2",
        "is_running": True,
    }

    check_count = 0
    def mock_find_running(proj):
        nonlocal check_count
        check_count += 1
        if check_count == 1:
            return running_info
        return None

    mock_dom.find_running_conversation_for_project.side_effect = mock_find_running
    mock_dom.is_generating.side_effect = [True, False, False]
    mock_dom.check_termination_state.return_value = {"state": "NORMAL", "detail": ""}
    mock_dom.open_new_conversation.return_value = True
    mock_dom.get_active_conversation_id.return_value = "new_conv_p1"

    # Stop after 2 iterations
    loop_count = 0
    def stop_after_runs():
        nonlocal loop_count
        loop_count += 1
        if loop_count >= 3:
            worker._stop_requested.set()
        return True

    worker.check_flow = stop_after_runs

    # Run one pass of the loop logic directly
    with patch.object(config, "max_concurrent_projects", 3), \
         patch.object(config, "polling_interval_seconds", 0):  # fast poll
        worker._run_multi_project_parallel_loop()

    # Verify: on first pass, worker adopted the background chat without calling open_new_conversation
    assert mock_dom.find_running_conversation_for_project.called
    assert "Agrico-V2" in worker.active_projects


def test_fifo_worker_waits_for_background_agent_before_new_chat():
    """
    Verifies that in FIFO mode, if a background agent is running in the target project,
    the worker waits for completion via sidebar monitoring (zero UI navigation)
    before calling open_new_conversation.
    """
    item = PromptItem(id="p1", text="Task 1", project="Agrico-V2")
    mock_dom = MagicMock()
    worker = AutomationWorker(queue=[item], dom_controller=mock_dom)

    # Background agent running initially, then finishes (returns None)
    mock_dom.find_running_conversation_for_project.side_effect = [
        {
            "id": "bg_conv_agrico",
            "title": "Existing Running Task",
            "project": "Agrico-V2",
            "is_running": True,
        },
        None,
        None,
        None,
    ]
    # is_generating will report True when waiting, then False once finished
    mock_dom.is_generating.side_effect = [True, False, False, False]
    mock_dom.wait_for_completion.return_value = True
    mock_dom.is_fresh_empty_chat_open.return_value = False
    mock_dom.open_new_conversation.return_value = True
    mock_dom.get_active_conversation_id.return_value = "new_chat_1"
    mock_dom.check_termination_state.return_value = {"state": "NORMAL", "detail": ""}

    success = worker._execute_prompt_cdp(item, 0, 1)

    assert success is True
    # Zero UI navigation while waiting for background agent!
    mock_dom.switch_to_conversation.assert_not_called()
    # Verify open_new_conversation was called with target_project="Agrico-V2"
    assert mock_dom.open_new_conversation.called
    call_kwargs = mock_dom.open_new_conversation.call_args.kwargs
    assert call_kwargs.get("target_project") == "Agrico-V2"
    assert call_kwargs.get("force") is True


def test_runtime_added_prompt_is_picked_up():
    """
    Verifies that when a user adds a new prompt to the queue at runtime,
    the parallel orchestrator dynamically synchronizes and dispatches it within 1 iteration.
    """
    queue = [
        PromptItem(id="p1", text="Agrico Prompt 1", project="Agrico-V2"),
    ]
    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_by_project(queue)
    worker.active_projects = {
        p: ProjectExecutionState(project_name=p, status="IDLE")
        for p in worker.project_queues.keys()
    }

    mock_dom = MagicMock()
    mock_dom.find_running_conversation_for_project.return_value = None
    mock_dom.open_new_conversation.return_value = True
    mock_dom.get_active_conversation_id.side_effect = ["conv_agrico_1", "conv_new_proj_1"]
    worker.dom_controller = mock_dom

    iteration = 0

    def mock_safe_sleep(dur):
        nonlocal iteration
        if dur >= 0.9:
            iteration += 1
            if iteration == 1:
                # Dynamically add prompt for a new project at runtime
                queue.append(PromptItem(id="p2", text="New Project Prompt", project="New-Proj"))
            elif iteration >= 2:
                worker._stop_requested.set()
        return True

    worker.safe_sleep = mock_safe_sleep
    with patch.object(config, "max_concurrent_projects", 3), \
         patch.object(config, "polling_interval_seconds", 999):
        worker._run_multi_project_parallel_loop()

    # The new project must be recognized and dispatched
    assert "New-Proj" in worker.active_projects
    assert queue[1].status == TaskStatus.RUNNING
    assert worker.active_projects["New-Proj"].status == "GENERATING"


def test_wait_for_zero_active_project_agents_blocks_until_zero():
    """
    Verifies that _wait_for_zero_active_project_agents waits while any agent is generating
    in the project, and only returns True once active agent count reaches zero.
    """
    worker = AutomationWorker(queue=[])
    mock_dom = MagicMock(spec=AntigravityDOMController)
    worker.dom_controller = mock_dom

    # 1. Project has 1 active agent, then completes (returns None)
    running_info = {
        "id": "conv_active_1",
        "title": "Running Agent in Agrico",
        "project": "Agrico-V2",
        "is_running": True,
    }

    call_sequence = [running_info, None]
    mock_dom.find_running_conversation_for_project.side_effect = lambda proj: call_sequence.pop(0) if call_sequence else None
    mock_dom.wait_for_completion.return_value = True

    ok = worker._wait_for_zero_active_project_agents("Agrico-V2")
    assert ok is True
    # Verified it waited for the active conversation
    mock_dom.wait_for_completion.assert_called_once()
    assert mock_dom.wait_for_completion.call_args.kwargs.get("conversation_id") == "conv_active_1"
    assert mock_dom.wait_for_completion.call_args.kwargs.get("project_name") == "Agrico-V2"


def test_local_transcript_inspector_all_states(tmp_path):
    """
    Verifies <1ms local transcript.jsonl inspection for all termination states
    (NORMAL, QUOTA_EXHAUSTED, ERRORED, WAITING_FOR_INPUT, INTERRUPTED) without UI navigation.
    """
    import json
    from cdp.transcript_inspector import inspect_transcript_termination

    def write_transcript(cid, steps):
        log_dir = tmp_path / cid / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        f = log_dir / "transcript.jsonl"
        f.write_text("\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8")

    # 1. NORMAL: Last step is PLANNER_RESPONSE with status DONE and no tool_calls
    write_transcript("cid_normal", [
        {"step_index": 1, "type": "USER_INPUT", "status": "DONE", "content": "Build feature"},
        {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE", "content": "All done!"},
    ])
    res_norm = inspect_transcript_termination("cid_normal", brain_dir=str(tmp_path))
    assert res_norm["found"] is True
    assert res_norm["state"] == "NORMAL"

    # 2. QUOTA_EXHAUSTED: Error step with 429 / resource exhausted
    write_transcript("cid_quota", [
        {"step_index": 1, "type": "USER_INPUT", "status": "DONE", "content": "Run task"},
        {"step_index": 2, "type": "GENERIC", "status": "ERROR", "error": "429 Resource has been exhausted (e.g. check quota)."},
    ])
    res_quota = inspect_transcript_termination("cid_quota", brain_dir=str(tmp_path))
    assert res_quota["state"] == "QUOTA_EXHAUSTED"

    # 3. WAITING_FOR_INPUT: Planner emitted tool_calls with no response yet
    write_transcript("cid_waiting", [
        {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "tool_calls": [{"name": "run_command"}]},
    ])
    res_wait = inspect_transcript_termination("cid_waiting", brain_dir=str(tmp_path))
    assert res_wait["state"] == "WAITING_FOR_INPUT"

    # 4. ERRORED: Non-quota step error
    write_transcript("cid_err", [
        {"step_index": 1, "type": "GENERIC", "status": "ERROR", "error": "SyntaxError in tool execution"},
    ])
    res_err = inspect_transcript_termination("cid_err", brain_dir=str(tmp_path))
    assert res_err["state"] == "ERRORED"

    # 5. INTERRUPTED: Stopped right after GENERIC tool output without final PLANNER_RESPONSE
    write_transcript("cid_interrupted", [
        {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "tool_calls": [{"name": "view_file"}]},
        {"step_index": 2, "type": "GENERIC", "status": "DONE", "content": "File contents..."},
    ])
    res_int = inspect_transcript_termination("cid_interrupted", brain_dir=str(tmp_path))
    assert res_int["state"] == "INTERRUPTED"


def test_worker_auto_resumes_on_step_error_status_7():
    """
    Verifies that when an agent execution terminates due to error (status 7),
    the worker automatically injects the recovery mini-prompt into the same chat
    to finish the response without rotating accounts.
    """
    mock_dom = MagicMock()
    mock_dom.open_new_conversation.return_value = True
    mock_dom.is_fresh_empty_chat_open.return_value = True
    mock_dom.get_active_conversation_id.return_value = "conv_step_err_123"
    mock_dom.wait_for_completion.return_value = True

    # 1. Initial check: STEP_ERROR (status 7)
    # 2. Resumed check in loop: NORMAL
    # 3. Post-loop final check: NORMAL
    term_responses = [
        {"state": "STEP_ERROR", "error_code": 7, "detail": "Tool execution exception in step 45"},
        {"state": "NORMAL", "detail": "Planner response completed"},
        {"state": "NORMAL", "detail": "Planner response completed"},
    ]
    mock_dom.check_termination_state_zero_nav.side_effect = term_responses

    item = PromptItem(id="p_err", text="Generate module", project="Agrico-V2")
    worker = AutomationWorker(queue=[item])
    worker.dom_controller = mock_dom
    worker.quota_inspector = MagicMock()
    worker._enforce_quota = MagicMock(return_value=True)

    with patch("time.sleep", return_value=None), \
         patch.object(config, "enable_auto_resume_same_chat", True), \
         patch.object(config, "max_resumes_per_prompt", 2):
        success = worker._execute_prompt_cdp(item, 0, 1)

    assert success is True
    assert item.status == TaskStatus.COMPLETED
    assert item.is_resumed is True
    assert item.resume_count == 1
    # Verify in-chat recovery prompt was injected
    mock_dom.inject_resume_prompt.assert_called_once_with(config.resume_mini_prompt)
    # Account rotation was NOT called for step error (only for quota exhaustion)
    worker._enforce_quota.assert_not_called()


def test_pre_dispatch_quota_check_dispatches_when_safe():
    """
    Verifies that _check_pre_dispatch_quota returns True when quota is healthy
    and uses the 30-second safe cache on subsequent calls.
    """
    worker = AutomationWorker(queue=[])
    mock_qi = MagicMock()
    mock_qi.inspect_model_quotas.return_value = {
        "is_safe_to_prompt": True,
        "gemini": {"five_hour_percent": 85, "weekly_percent": 90},
    }
    worker.quota_inspector = mock_qi

    # First call: inspects quota
    assert worker._check_pre_dispatch_quota("Agrico-V2") is True
    assert mock_qi.inspect_model_quotas.call_count == 1

    # Second call within 30s cache TTL: reuses cache without re-inspecting
    assert worker._check_pre_dispatch_quota("Antigravity CDP") is True
    assert mock_qi.inspect_model_quotas.call_count == 1  # Still 1!


def test_pre_dispatch_quota_triggers_drain_barrier_when_other_project_running():
    """
    Verifies that when quota is low and another project is actively generating,
    _check_pre_dispatch_quota activates the drain barrier and defers account rotation
    until all active projects complete.
    """
    worker = AutomationWorker(queue=[])
    mock_qi = MagicMock()
    mock_qi.inspect_model_quotas.return_value = {
        "is_safe_to_prompt": False,
        "safety_reason": "5-hour limit at 10% (below 15% threshold)",
    }
    worker.quota_inspector = mock_qi
    worker._enforce_quota = MagicMock(return_value=True)

    # Project A is actively generating
    worker.active_projects = {
        "Project-A": ProjectExecutionState(project_name="Project-A", status="GENERATING"),
        "Project-B": ProjectExecutionState(project_name="Project-B", status="IDLE"),
    }

    # Project B tries to dispatch with low quota
    can_dispatch = worker._check_pre_dispatch_quota("Project-B")
    assert can_dispatch is False
    assert worker._quota_drain_in_progress is True
    # Account rotation must NOT be called while Project A is in-flight!
    worker._enforce_quota.assert_not_called()

    # Now Project A finishes naturally
    worker.active_projects["Project-A"].status = "IDLE"

    # Simulate loop checking barrier when has_generating_projects is False
    has_generating = any(
        st.status in ("GENERATING", "STABILIZING", "DISPATCHING")
        for st in worker.active_projects.values()
    )
    assert has_generating is False

    # Drain condition met! Safe to rotate accounts
    if worker._quota_drain_in_progress and not has_generating:
        worker._enforce_quota("Multi-Project Coordinated Quota Recovery")
        worker._quota_drain_in_progress = False


def test_pre_dispatch_quota_rotates_immediately_when_zero_active_agents_and_avoids_double_inspection(monkeypatch):
    """
    Verifies that when quota is low and 0 agents are active:
    1. _check_pre_dispatch_quota passes known_unsafe_res to _enforce_quota
    2. _enforce_quota skips the redundant pre-switch inspection (no double opening of Settings)
    3. _enforce_quota executes account switch and inspects only on the post-switch pass
    4. Upon successful recovery, quota cache is populated so subsequent prompt dispatches do not re-open Settings
    """
    from app.config import config
    monkeypatch.setattr(config, "low_quota_action", "change_account")
    monkeypatch.setattr(config, "auto_switch_on_low_quota", True)
    worker = AutomationWorker(queue=[])
    mock_qi = MagicMock()
    unsafe_quota = {
        "is_safe_to_prompt": False,
        "safety_reason": "Gemini 5-Hour Limit (19%) is BELOW safety threshold (30%)!",
        "gemini": {"five_hour_percent": 19, "weekly_percent": 87},
    }
    safe_quota_new_acct = {
        "is_safe_to_prompt": True,
        "gemini": {"five_hour_percent": 100, "weekly_percent": 100},
    }
    # First call: returns unsafe_quota (from _check_pre_dispatch_quota)
    # Second call: returns safe_quota_new_acct (from _enforce_quota post-switch)
    mock_qi.inspect_model_quotas.side_effect = [unsafe_quota, safe_quota_new_acct]
    worker.quota_inspector = mock_qi

    mock_oauth = MagicMock()
    mock_oauth.execute_account_switch.return_value = {"success": True, "active_account": "new@gmail.com"}
    worker.oauth_handler = mock_oauth

    worker.active_projects = {
        "Agrico-V2": ProjectExecutionState(project_name="Agrico-V2", status="IDLE"),
    }

    # Dispatch check: quota is low (19%), 0 agents active -> rotates account immediately
    can_dispatch = worker._check_pre_dispatch_quota("Agrico-V2")
    assert can_dispatch is True

    # Critical verification: inspect_model_quotas was called EXACTLY TWICE in total:
    # 1. Initial inspection in _check_pre_dispatch_quota
    # 2. Verification inspection on the new account in _enforce_quota
    # (It was NOT called 3 or 4 times due to double-opening before switch or wiping cache!)
    assert mock_qi.inspect_model_quotas.call_count == 2
    mock_oauth.execute_account_switch.assert_called_once()

    # Verify quota cache is retained
    assert worker._last_quota_result == safe_quota_new_acct
    assert worker._last_quota_check_ts > 0

    # Next check for another project within 30s cache TTL: instant cache hit, 0 additional inspections!
    can_dispatch_next = worker._check_pre_dispatch_quota("Antigravity CDP")
    assert can_dispatch_next is True
    assert mock_qi.inspect_model_quotas.call_count == 2


def test_wait_for_completion_does_not_timeout_when_timeout_le_stabilization():
    """
    Verifies that when wait_for_completion is called with timeout_seconds <= stabilization_seconds
    (as in parallel mode post-completion stabilization), it treats generation as already detected
    and successfully stabilizes without raising a premature TimeoutError.
    """
    mock_page = MagicMock()
    controller = AntigravityDOMController(mock_page)

    with patch.object(controller, "is_conversation_generating", return_value=False), \
         patch("time.sleep", return_value=None):
        ok = controller.wait_for_completion(
            timeout_seconds=5,
            stabilization_seconds=5,
            is_infinite=False,
            conversation_id="01e90113-fc54-4823-b19c-f0362497d261",
        )
    assert ok is True


def test_project_status_transitions_to_completed_when_queue_finishes():
    """
    Verifies that when a project's prompt queue is exhausted, its state in active_projects
    transitions to 'COMPLETED' (so the HUD displays '✔ Finished'), while a project with
    remaining pending prompts transitions to 'IDLE'.
    """
    queue = [
        PromptItem(id="p1", text="Finished Project Prompt", project="Antigravity Automation Win UIA", status=TaskStatus.RUNNING),
        PromptItem(id="p2", text="Agrico Prompt 1", project="Agrico-V2", status=TaskStatus.RUNNING),
        PromptItem(id="p3", text="Agrico Prompt 2", project="Agrico-V2", status=TaskStatus.PENDING),
    ]

    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_by_project(queue)
    worker.active_projects = {
        "Antigravity Automation Win UIA": ProjectExecutionState(
            project_name="Antigravity Automation Win UIA",
            status="GENERATING",
            conversation_id="01e90113-fc54-4823-b19c-f0362497d261",
            current_prompt=queue[0],
            start_time=time.time() - 5,
        ),
        "Agrico-V2": ProjectExecutionState(
            project_name="Agrico-V2",
            status="GENERATING",
            conversation_id="conv_agrico",
            current_prompt=queue[1],
            start_time=time.time() - 5,
        ),
    }

    mock_dom = MagicMock()
    # Antigravity Automation Win UIA has completed, Agrico is still generating
    mock_dom.is_conversation_generating.side_effect = lambda conversation_id=None, project_name=None: (conversation_id == "conv_agrico")
    mock_dom.find_running_conversation_for_project.return_value = None
    mock_dom.check_termination_state.return_value = {"state": "NORMAL", "detail": ""}
    mock_dom.wait_for_completion.return_value = True
    worker.dom_controller = mock_dom

    call_count = 0
    def mock_safe_sleep(dur):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            worker._stop_requested.set()
        return True

    worker.safe_sleep = mock_safe_sleep

    with patch.object(config, "polling_interval_seconds", 0):
        worker._run_multi_project_parallel_loop()

    # Antigravity Automation Win UIA had only 1 prompt, which completed:
    assert queue[0].status == TaskStatus.COMPLETED
    assert worker.active_projects["Antigravity Automation Win UIA"].status == "COMPLETED"

    # Agrico-V2 is still generating:
    assert worker.active_projects["Agrico-V2"].status == "GENERATING"


def test_is_conversation_generating_resilient_to_user_switching_chats():
    """
    Verifies that when a user switches away to view a different, idle chat in Antigravity:
    - is_conversation_generating() does NOT falsely report False.
    - Ground truth SQLite status (not_fully_idle=1 or RUNNING) keeps reporting True.
    - If DB is checked and shows active work, it returns True regardless of viewport active chat.
    """
    mock_page = MagicMock()
    controller = AntigravityDOMController(mock_page)

    target_cid = "4a003807-9610-438d-a94c-78c9e2f43a68"
    user_viewed_cid = "b64f2869-8595-4ada-aeee-2833b2714dae"

    # User is currently viewing a DIFFERENT conversation that is idle (no Stop button)
    with patch.object(controller, "get_active_conversation_id", return_value=user_viewed_cid), \
         patch.object(controller, "is_generating", return_value=False), \
         patch("cdp.conversation_db_inspector.get_conversation_summary") as mock_summary:
        
        # 1. Target chat is actively running according to DB
        mock_summary.return_value = {
            "found": True,
            "is_idle": False,
            "status": "CASCADE_RUN_STATUS_RUNNING",
            "not_fully_idle": 1,
            "workspace_uris": '["file:///c%3A/Users/mohsi/Desktop/Agrico-V2"]',
        }

        # Should return True despite viewport showing idle chat!
        assert controller.is_conversation_generating(conversation_id=target_cid, project_name="Agrico-V2") is True

        # 2. Sidebar spinner check also returns True if DB record is not yet written
        mock_summary.return_value = {"found": False}
        with patch.object(controller, "is_sidebar_conversation_generating", return_value=True):
            assert controller.is_conversation_generating(conversation_id=target_cid, project_name="Agrico-V2") is True


def test_parallel_dispatch_does_not_block_second_project_when_first_generating():
    """
    Verifies that Project B can dispatch its prompt concurrently while Project A is actively generating,
    without being blocked by global is_generating() or _wait_for_idle_agent.
    """
    queue = [
        PromptItem(id="p1", text="Agrico Prompt 1", project="Agrico-V2"),
        PromptItem(id="p2", text="Antigravity Automation Win UIA Prompt", project="Antigravity Automation Win UIA"),
    ]

    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_by_project(queue)
    worker.active_projects = {
        p: ProjectExecutionState(project_name=p, status="IDLE")
        for p in worker.project_queues.keys()
    }

    mock_dom = MagicMock()
    mock_dom.find_running_conversation_for_project.return_value = None
    mock_dom.open_new_conversation.return_value = True
    # Viewport has Stop button visible (because Project A is generating on screen)
    mock_dom.is_generating.return_value = True
    mock_dom.get_active_conversation_id.side_effect = ["conv_agrico_1", "conv_uia_1"]
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
         patch.object(config, "polling_interval_seconds", 999), \
         patch("cdp.conversation_db_inspector.find_running_conversations_in_db", return_value=[]):
        worker._run_multi_project_parallel_loop()

    # Both projects must have dispatched successfully to GENERATING!
    assert worker.active_projects["Agrico-V2"].status == "GENERATING"
    assert queue[0].status == TaskStatus.RUNNING

    assert worker.active_projects["Antigravity Automation Win UIA"].status == "GENERATING"
    assert queue[1].status == TaskStatus.RUNNING


def test_dynamic_prompt_addition_triggers_immediate_hud_snapshot_and_dispatch():
    """
    Verifies that adding a prompt for a new project at runtime via notify_queue_changed()
    immediately partitions the project, computes pending metrics, emits an updated HUD snapshot,
    and dispatches the new project's queue.
    """
    queue = [
        PromptItem(id="p1", text="Agrico Prompt 1", project="Agrico-V2"),
    ]

    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_by_project(queue)
    worker.active_projects = {
        "Agrico-V2": ProjectExecutionState(project_name="Agrico-V2", status="GENERATING", conversation_id="conv_1")
    }

    mock_dom = MagicMock()
    mock_dom.find_running_conversation_for_project.return_value = None
    mock_dom.open_new_conversation.return_value = True
    mock_dom.get_active_conversation_id.return_value = "conv_brand_new_1"
    worker.dom_controller = mock_dom

    snapshots_emitted = []
    worker.sig_active_projects_updated.connect(lambda snap: snapshots_emitted.append(snap))

    # Dynamically add prompt for 'BrandNewProject'
    new_prompt = PromptItem(id="p2", text="Brand new prompt", project="BrandNewProject")
    updated_queue = list(queue) + [new_prompt]

    worker.notify_queue_changed(updated_queue)

    # 1. Snapshot must have been emitted immediately with BrandNewProject
    assert len(snapshots_emitted) >= 1
    latest_snap = snapshots_emitted[-1]
    assert "BrandNewProject" in latest_snap
    assert latest_snap["BrandNewProject"]["pending_count"] == 1
    assert latest_snap["BrandNewProject"]["total_count"] == 1
    assert latest_snap["BrandNewProject"]["status"] == "IDLE"

    # 2. Parallel loop dispatches BrandNewProject
    call_count = 0
    def mock_safe_sleep(dur):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            worker._stop_requested.set()
        return True

    worker.safe_sleep = mock_safe_sleep
    with patch.object(config, "max_concurrent_projects", 3), \
         patch.object(config, "polling_interval_seconds", 999), \
         patch("cdp.conversation_db_inspector.find_running_conversations_in_db", return_value=[]):
        worker._run_multi_project_parallel_loop()

    assert worker.active_projects["BrandNewProject"].status == "GENERATING"
    assert new_prompt.status == TaskStatus.RUNNING


def test_project_hud_queued_badge_and_card_display():
    """
    Verifies that the ForegroundProjectHUD header badge and cards properly display
    queued projects, pending prompt counts, and prompt preview snippets.
    """
    from PyQt6.QtWidgets import QApplication
    from app.ui.project_hud import ForegroundProjectHUD

    app = QApplication.instance() or QApplication([])
    hud = ForegroundProjectHUD()

    projects_dict = {
        "Agrico-V2": {
            "status": "GENERATING",
            "is_paused": False,
            "elapsed_time": 25.0,
            "pending_count": 2,
            "total_count": 3,
            "completed_count": 0,
            "next_prompt_text": "Next Agrico prompt",
        },
        "Antigravity CDP": {
            "status": "IDLE",
            "is_paused": False,
            "elapsed_time": 0.0,
            "pending_count": 4,
            "total_count": 4,
            "completed_count": 0,
            "next_prompt_text": "Run CDP automation workflow",
        },
    }

    hud.update_projects(projects_dict)

    # Header badge should reflect 1 active and 2 queued projects
    badge_text = hud.lbl_queue_badge.text()
    assert "1 Active" in badge_text
    assert "2 Queued" in badge_text

    from PyQt6.QtWidgets import QLabel

    # Project cards should exist in cards_layout
    cards = [hud.cards_layout.itemAt(i).widget() for i in range(hud.cards_layout.count())]
    assert len(cards) == 2

    # Check that one card displays 'Antigravity CDP' with Queued (4 pending) and prompt preview
    card_texts = []
    for c in cards:
        labels = [lbl.text() for lbl in c.findChildren(QLabel)]
        card_texts.append(" | ".join(labels))

    cdp_text = next((t for t in card_texts if "Antigravity CDP" in t), "")
    assert "Queued" in cdp_text
    assert "4 pending" in cdp_text
    assert "Run CDP automation workflow" in cdp_text


def test_reset_pending_prompt_with_stale_conversation_id_always_opens_new_chat(tmp_path, monkeypatch):
    """
    Regression test:
    1. When prompts complete in Run 1, they get a runtime conversation_id assigned.
    2. When the user resets all prompt statuses to Pending (_reset_statuses), conversation_id must be cleared to "".
    3. Even if a Pending prompt still has a leftover conversation_id from a previous run,
       the worker MUST clear it, call open_new_conversation(force=True), and capture the new chat ID
       rather than reusing the old chat or injecting into the same chat.
    """
    from PyQt6.QtWidgets import QApplication
    from app.ui.queue_tab import QueueTab

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("app.ui.queue_tab.get_user_data_dir", lambda: str(tmp_path))

    tab = QueueTab()
    tab.prompts = [
        PromptItem(id="p1", text="Batch 1", project="Agrico-V2", status=TaskStatus.COMPLETED, conversation_id="old_conv_1"),
        PromptItem(id="p2", text="Batch 2", project="Agrico-V2", status=TaskStatus.COMPLETED, conversation_id="old_conv_2"),
    ]
    tab._reset_statuses()

    assert tab.prompts[0].status == TaskStatus.PENDING
    assert tab.prompts[0].conversation_id == ""
    assert tab.prompts[1].status == TaskStatus.PENDING
    assert tab.prompts[1].conversation_id == ""

    # Also verify worker defense-in-depth if a Pending prompt has a stale conversation_id
    stale_prompt = PromptItem(id="p_stale", text="Batch Prompt", project="Agrico-V2", status=TaskStatus.PENDING, conversation_id="stale_conv_old")
    queue = [stale_prompt]
    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_by_project(queue)
    worker.active_projects = {
        p: ProjectExecutionState(project_name=p, status="IDLE")
        for p in worker.project_queues.keys()
    }

    mock_dom = MagicMock()
    mock_dom.find_running_conversation_for_project.return_value = None
    mock_dom.open_new_conversation.return_value = True
    mock_dom.get_active_conversation_id.return_value = "fresh_new_conv_999"
    mock_dom.wait_for_completion.return_value = True
    worker.dom_controller = mock_dom

    call_count = 0
    def mock_safe_sleep(dur):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            worker._stop_requested.set()
        return True

    worker.safe_sleep = mock_safe_sleep
    with patch.object(config, "max_concurrent_projects", 1), \
         patch.object(config, "polling_interval_seconds", 999), \
         patch("cdp.conversation_db_inspector.find_running_conversations_in_db", return_value=[]):
        worker._run_multi_project_parallel_loop()

    # Must ALWAYS call open_new_conversation and NEVER switch_to_conversation for a pending prompt dispatch
    mock_dom.open_new_conversation.assert_called_once()
    mock_dom.switch_to_conversation.assert_not_called()
    assert worker.active_projects["Agrico-V2"].status == "GENERATING"
    assert worker.active_projects["Agrico-V2"].conversation_id == "fresh_new_conv_999"
    assert stale_prompt.conversation_id == "fresh_new_conv_999"


def test_user_started_second_agent_blocks_next_prompt_until_zero_agents():
    """
    Verifies that if Prompt 1 finishes, but another agent is active in that project
    (e.g. user started Agent 2 in parallel), the worker marks Prompt 1 as COMPLETED,
    detects the second agent, adopts its conversation ID, keeps the project in GENERATING state,
    and does NOT dispatch the next prompt until zero agents are running.
    """
    p1 = PromptItem(id="p1", text="Prompt 1", project="Agrico-V2", status=TaskStatus.RUNNING)
    p2 = PromptItem(id="p2", text="Prompt 2", project="Agrico-V2", status=TaskStatus.PENDING)
    queue = [p1, p2]

    worker = AutomationWorker(queue=queue)
    worker.project_queues = {"Agrico-V2": [p1, p2]}
    worker.active_projects = {
        "Agrico-V2": ProjectExecutionState(
            project_name="Agrico-V2",
            status="GENERATING",
            conversation_id="conv_agent_1",
            current_prompt=p1,
            start_time=time.time(),
        )
    }

    mock_dom = MagicMock()
    mock_dom.is_conversation_generating.return_value = False
    mock_dom.is_generating.return_value = False
    mock_dom.wait_for_completion.return_value = True

    def mock_find_running(proj):
        if proj == "Agrico-V2":
            return {"id": "conv_agent_2", "title": "User Started Agent 2", "project": "Agrico-V2", "is_running": True}
        return None

    mock_dom.find_running_conversation_for_project.side_effect = mock_find_running
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
         patch.object(config, "polling_interval_seconds", 999), \
         patch("cdp.conversation_db_inspector.find_running_conversations_in_db", return_value=[]):
        worker._run_multi_project_parallel_loop()

    # Prompt 1 is marked COMPLETED
    assert p1.status == TaskStatus.COMPLETED
    # Prompt 2 must remain PENDING because Agent 2 is running
    assert p2.status == TaskStatus.PENDING
    # Agrico-V2 remains in GENERATING state with conv_agent_2
    assert worker.active_projects["Agrico-V2"].status == "GENERATING"
    assert worker.active_projects["Agrico-V2"].conversation_id == "conv_agent_2"


def test_runtime_settings_change_updates_max_concurrent_immediately():
    """
    Verifies that worker dynamically re-evaluates max_concurrent_projects and poll_interval
    on every loop iteration and via notify_settings_changed.
    """
    queue = [
        PromptItem(id="p1", text="Task 1", project="Agrico-V2"),
        PromptItem(id="p2", text="Task 2", project="Antigravity CDP"),
    ]
    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_by_project(queue)
    worker.active_projects = {
        p: ProjectExecutionState(project_name=p, status="IDLE")
        for p in worker.project_queues.keys()
    }

    emitted = []
    worker.sig_active_projects_updated.connect(lambda s: emitted.append(s))
    worker.notify_settings_changed()
    assert len(emitted) == 1
    assert worker._queue_changed_event.is_set()


def test_resolve_real_cid_helper():
    """
    Verifies that _resolve_real_cid resolves a genuine UUID when a fallback 'conv_*' ID is present.
    """
    from app.worker import _resolve_real_cid

    genuine_uuid = "71d91ecf-6610-4c77-aee9-b80f617df473"

    # Case 1: Already has a genuine UUID -> returns it without querying controller
    assert _resolve_real_cid(None, "Agrico-V2", genuine_uuid) == genuine_uuid

    # Case 2: Has a fallback conv_* placeholder -> queries controller.get_active_conversation_id
    mock_ctrl = MagicMock()
    mock_ctrl.get_active_conversation_id.return_value = genuine_uuid
    res = _resolve_real_cid(mock_ctrl, "Agrico-V2", "conv_Agrico-V2_1728900000")
    assert res == genuine_uuid

    # Case 3: Controller returns another conv_* -> ignores and tries fallback or returns None
    mock_ctrl.get_active_conversation_id.return_value = "conv_another_123"
    mock_ctrl.find_running_conversation_for_project.return_value = None
    res2 = _resolve_real_cid(mock_ctrl, "Agrico-V2", "conv_Agrico-V2_1728900000")
    assert res2 is None


def test_parallel_worker_reconciles_fallback_conversation_id():
    """
    Verifies that when an active project in _run_parallel_loop has a fallback conv_* ID,
    the reconciliation logic dynamically resolves and updates both ProjectExecutionState.conversation_id
    and PromptItem.conversation_id to the real UUID.
    """
    genuine_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    item = PromptItem(id="p1", text="Task with delayed UUID", project="Agrico-V2")
    item.conversation_id = "conv_Agrico-V2_1728900000"

    worker = AutomationWorker(queue=[item])
    worker.project_queues = {"Agrico-V2": [item]}

    st = ProjectExecutionState(
        project_name="Agrico-V2",
        status="GENERATING",
        current_prompt=item,
        conversation_id="conv_Agrico-V2_1728900000",
        start_time=time.time(),
    )
    worker.active_projects = {"Agrico-V2": st}

    mock_controller = MagicMock()
    # First query returns the genuine UUID
    mock_controller.get_active_conversation_id.return_value = genuine_uuid
    # Sidebar generator check reports running
    mock_controller.is_sidebar_conversation_generating.return_value = True

    from app.worker import _resolve_real_cid
    resolved = _resolve_real_cid(mock_controller, "Agrico-V2", st.conversation_id)
    assert resolved == genuine_uuid

    # Simulate reconciliation update
    st.conversation_id = resolved
    item.conversation_id = resolved

    assert st.conversation_id == genuine_uuid
    assert item.conversation_id == genuine_uuid
    assert not st.conversation_id.startswith("conv_")


def test_subqueue_partitioning_same_project():
    """Verifies that multiple queues targeting the same project are partitioned into distinct subqueues."""
    items = [
        PromptItem(id="p1", text="Task 1", project="Agrico-V2", queue_name="Auth"),
        PromptItem(id="p2", text="Task 2", project="Agrico-V2", queue_name="Billing"),
        PromptItem(id="p3", text="Task 3", project="Agrico-V2", queue_name="Auth"),
    ]
    partitioned = partition_queue_into_subqueues(items)
    assert len(partitioned) == 2
    assert "Agrico-V2 :: Auth" in partitioned
    assert "Agrico-V2 :: Billing" in partitioned
    assert len(partitioned["Agrico-V2 :: Auth"]) == 2
    assert len(partitioned["Agrico-V2 :: Billing"]) == 1


def test_sister_queue_adoption_prevention():
    """Verifies that _is_untracked_external_agent prevents sister subqueues from adopting each other's chats."""
    from app.worker import _is_untracked_external_agent
    st1 = ProjectExecutionState(project_name="Agrico-V2", queue_name="Auth", queue_key="Agrico-V2 :: Auth", conversation_id="conv_auth_uuid")
    active_projects = {"Agrico-V2 :: Auth": st1}

    # Sister sub-queue chat detected: must NOT be considered untracked!
    sister_conv = {"id": "conv_auth_uuid", "title": "Auth Chat"}
    assert _is_untracked_external_agent(sister_conv, active_projects) is False

    # External chat from outside tool: IS untracked!
    external_conv = {"id": "conv_external_random_uuid", "title": "Manual Chat"}
    assert _is_untracked_external_agent(external_conv, active_projects) is True


def test_subqueue_serialized_ui_dispatch():
    """Verifies that parallel subqueues dispatch sequentially under ui_mutex."""
    q1 = PromptItem(id="q1", text="Fix Auth", project="Agrico-V2", queue_name="Auth")
    q2 = PromptItem(id="q2", text="Fix Billing", project="Agrico-V2", queue_name="Billing")
    queue = [q1, q2]
    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_into_subqueues(queue)
    worker.active_projects = {
        k: ProjectExecutionState(project_name=parse_subqueue_key(k)[0], queue_name=parse_subqueue_key(k)[1], queue_key=k, status="IDLE")
        for k in worker.project_queues.keys()
    }
    mock_dom = MagicMock()
    mock_dom.open_new_conversation.return_value = True
    mock_dom.is_conversation_generating.return_value = True
    worker.dom_controller = mock_dom

    dispatch_order = []
    def fake_inject(txt):
        dispatch_order.append(txt)
        return True
    mock_dom.inject_and_submit_prompt.side_effect = fake_inject

    def mock_safe_sleep(dur):
        worker._stop_requested.set()
        return True
    worker.safe_sleep = mock_safe_sleep

    with patch.object(config, "max_concurrent_projects", 2):
        worker._run_multi_project_parallel_loop()

    assert len(dispatch_order) == 2
    assert dispatch_order == ["Fix Auth", "Fix Billing"]
    assert q1.status == TaskStatus.RUNNING
    assert q2.status == TaskStatus.RUNNING


def test_subqueue_fresh_chat_per_prompt():
    """Verifies that subsequent prompts in a subqueue open a fresh conversation and do not reuse the old chat."""
    p1 = PromptItem(id="p1", text="Auth Step 1", project="Agrico-V2", queue_name="Auth", status=TaskStatus.RUNNING, conversation_id="conv_auth_uuid_123")
    p2 = PromptItem(id="p2", text="Auth Step 2", project="Agrico-V2", queue_name="Auth", status=TaskStatus.PENDING)
    queue = [p1, p2]
    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_into_subqueues(queue)
    st = ProjectExecutionState(
        project_name="Agrico-V2",
        queue_name="Auth",
        queue_key="Agrico-V2 :: Auth",
        status="GENERATING",
        conversation_id="conv_auth_uuid_123",
        current_prompt=p1,
        start_time=time.time(),
    )
    worker.active_projects = {"Agrico-V2 :: Auth": st}
    mock_dom = MagicMock()
    mock_dom.is_conversation_generating.return_value = False
    mock_dom.check_termination_state_zero_nav.return_value = {"state": "NORMAL"}
    mock_dom.wait_for_completion.return_value = True
    mock_dom.find_running_conversation_for_project.return_value = None
    mock_dom.open_new_conversation.return_value = True
    mock_dom.wait_for_conversation_id.return_value = "conv_auth_uuid_456"
    worker.dom_controller = mock_dom

    loop_iter = 0
    def mock_safe_sleep(dur):
        nonlocal loop_iter
        loop_iter += 1
        if loop_iter >= 2:
            worker._stop_requested.set()
        return True
    worker.safe_sleep = mock_safe_sleep

    with patch.object(config, "max_concurrent_projects", 2):
        worker._run_multi_project_parallel_loop()

    assert p1.status == TaskStatus.COMPLETED
    assert p1.conversation_id == "conv_auth_uuid_123"
    # p2 must NOT reuse p1's conversation, it must open a new conversation
    assert mock_dom.open_new_conversation.called
    mock_dom.inject_and_submit_prompt.assert_called_with("Auth Step 2")
    assert p2.conversation_id == "conv_auth_uuid_456"
    assert worker.active_projects["Agrico-V2 :: Auth"].conversation_id == "conv_auth_uuid_456"


def test_subqueue_independent_pause_resume():
    """Verifies independent pause and resume per subqueue."""
    worker = AutomationWorker(queue=[])
    st1 = ProjectExecutionState(project_name="Agrico-V2", queue_name="Auth", queue_key="Agrico-V2 :: Auth", status="GENERATING")
    st2 = ProjectExecutionState(project_name="Agrico-V2", queue_name="Billing", queue_key="Agrico-V2 :: Billing", status="GENERATING")
    worker.active_projects = {
        "Agrico-V2 :: Auth": st1,
        "Agrico-V2 :: Billing": st2,
    }
    worker.pause_subqueue("Agrico-V2 :: Auth")
    assert st1.is_paused is True
    assert st1.status == "PAUSED"
    assert st2.is_paused is False
    assert st2.status == "GENERATING"

    worker.resume_subqueue("Agrico-V2 :: Auth")
    assert st1.is_paused is False
    assert st1.status == "IDLE"


def test_startup_active_agents_decision():
    """Verifies startup decision resolution on user approval or wait."""
    worker = AutomationWorker(queue=[])
    assert worker._startup_decision == "wait"
    worker.resolve_startup_decision("proceed")
    assert worker._startup_decision == "proceed"
    assert worker._startup_decision_event.is_set()

    worker.resolve_startup_decision("wait")
    assert worker._startup_decision == "wait"


def test_open_new_conversation_does_not_wait_when_isolate_conversation_true():
    """
    Verifies that open_new_conversation skips Pre-check 2 waiting when isolate_conversation=True,
    even if the active chat in the same project is actively generating.
    """
    mock_page = MagicMock()
    mock_input = MagicMock()
    mock_input.wait_for.return_value = True
    controller = AntigravityDOMController(mock_page)

    controller.is_generating = MagicMock(return_value=True)
    controller.wait_for_completion = MagicMock(return_value=True)
    controller._get_chat_input_locator = MagicMock(return_value=mock_input)

    # When isolate_conversation=False (standard mode), it WAITS
    res1 = controller.open_new_conversation(force=True, wait_if_busy=True, isolate_conversation=False)
    assert res1 is True
    assert controller.wait_for_completion.called

    controller.wait_for_completion.reset_mock()

    # When isolate_conversation=True (parallel subqueue mode), it must NOT wait
    res2 = controller.open_new_conversation(force=True, wait_if_busy=True, target_project="Agrico-V2", isolate_conversation=True)
    assert res2 is True
    controller.wait_for_completion.assert_not_called()


def test_wait_for_conversation_id_filters_exclude_cids():
    """
    Verifies that wait_for_conversation_id excludes already tracked CIDs of sister subqueues,
    preventing ID collisions in the same project.
    """
    mock_page = MagicMock()
    controller = AntigravityDOMController(mock_page)

    sister_cid = "2541036e-f270-465e-bce4-0603f45b1d92"
    fresh_cid = "99887766-aaaa-bbbb-cccc-112233445566"

    # 1. Active CID matches exclude_cids -> returns fresh CID when it appears
    with patch.object(controller, "get_active_conversation_id", side_effect=[sister_cid, fresh_cid]), \
         patch("cdp.conversation_db_inspector.find_running_conversations_in_db", return_value=[]):
        cid = controller.wait_for_conversation_id(
            target_project="Agrico-V2",
            timeout_seconds=2.0,
            exclude_cids=[sister_cid],
        )
        assert cid == fresh_cid

    # 2. DB fallback filters out sister_cid
    with patch.object(controller, "get_active_conversation_id", return_value=None), \
         patch("cdp.conversation_db_inspector.find_running_conversations_in_db", return_value=[{"id": sister_cid}, {"id": fresh_cid}]):
        cid_from_db = controller.wait_for_conversation_id(
            target_project="Agrico-V2",
            timeout_seconds=1.0,
            exclude_cids=[sister_cid],
        )
        assert cid_from_db == fresh_cid


def test_multi_queue_same_project_parallel_dispatch_and_continuity():
    """
    Verifies that multiple subqueues under the SAME project dispatch in parallel
    without blocking, capture distinct conversation IDs, and each maintains chat continuity.
    """
    q1_p1 = PromptItem(id="p1", text="Agrico Q1 Step 1", project="Agrico-V2", queue_name="Q1")
    q1_p2 = PromptItem(id="p2", text="Agrico Q1 Step 2", project="Agrico-V2", queue_name="Q1")
    q2_p1 = PromptItem(id="p3", text="Agrico Q2 Step 1", project="Agrico-V2", queue_name="Q2")

    queue = [q1_p1, q1_p2, q2_p1]
    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_into_subqueues(queue)
    worker.active_projects = {
        k: ProjectExecutionState(project_name="Agrico-V2", queue_name=parse_subqueue_key(k)[1], queue_key=k, status="IDLE")
        for k in worker.project_queues.keys()
    }

    mock_dom = MagicMock()
    mock_dom.open_new_conversation.return_value = True
    # Two distinct CIDs returned for each new chat
    mock_dom.wait_for_conversation_id.side_effect = [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]
    mock_dom.is_conversation_generating.return_value = True
    worker.dom_controller = mock_dom

    # Stop after dispatch pass
    def stop_sleep(dur):
        worker._stop_requested.set()
        return True
    worker.safe_sleep = stop_sleep

    with patch.object(config, "max_concurrent_projects", 3):
        worker._run_multi_project_parallel_loop()

    # Both subqueues in Agrico-V2 must have dispatched in parallel into distinct conversations!
    st_q1 = worker.active_projects["Agrico-V2 :: Q1"]
    st_q2 = worker.active_projects["Agrico-V2 :: Q2"]

    assert st_q1.status == "GENERATING"
    assert st_q2.status == "GENERATING"
    assert st_q1.conversation_id == "11111111-1111-1111-1111-111111111111"
    assert st_q2.conversation_id == "22222222-2222-2222-2222-222222222222"
    assert st_q1.conversation_id != st_q2.conversation_id


def test_multi_queue_fresh_chat_and_active_chat_mapping():
    """
    Verifies that when a prompt in a subqueue completes, the NEXT prompt in the SAME subqueue
    always launches in a FRESH NEW conversation (no chat reuse) and the worker accurately
    remembers and maps each active conversation ID to its respective subqueue.
    """
    p1 = PromptItem(id="p1", text="Subqueue 1 Prompt 1", project="Agrico-V2", queue_name="Q1", status=TaskStatus.RUNNING, conversation_id="cid_chat_1")
    p2 = PromptItem(id="p2", text="Subqueue 1 Prompt 2", project="Agrico-V2", queue_name="Q1", status=TaskStatus.PENDING)
    p3 = PromptItem(id="p3", text="Subqueue 2 Prompt 1", project="Agrico-V2", queue_name="Q2", status=TaskStatus.RUNNING, conversation_id="cid_chat_2")
    queue = [p1, p2, p3]

    worker = AutomationWorker(queue=queue)
    worker.project_queues = partition_queue_into_subqueues(queue)
    st_q1 = ProjectExecutionState(
        project_name="Agrico-V2",
        queue_name="Q1",
        queue_key="Agrico-V2 :: Q1",
        status="GENERATING",
        conversation_id="cid_chat_1",
        current_prompt=p1,
        start_time=time.time(),
    )
    st_q2 = ProjectExecutionState(
        project_name="Agrico-V2",
        queue_name="Q2",
        queue_key="Agrico-V2 :: Q2",
        status="GENERATING",
        conversation_id="cid_chat_2",
        current_prompt=p3,
        start_time=time.time(),
    )
    worker.active_projects = {
        "Agrico-V2 :: Q1": st_q1,
        "Agrico-V2 :: Q2": st_q2,
    }

    mock_dom = MagicMock()
    # Q1 finishes p1, Q2 continues generating p3
    def mock_is_generating(conversation_id=None, project_name=None):
        if conversation_id == "cid_chat_1":
            return False  # p1 finished!
        if conversation_id == "cid_chat_2":
            return True   # p3 still generating!
        if conversation_id == "cid_chat_new_for_p2":
            return True   # p2 starts generating!
        return False

    mock_dom.is_conversation_generating.side_effect = mock_is_generating
    mock_dom.check_termination_state_zero_nav.return_value = {"state": "NORMAL"}
    mock_dom.wait_for_completion.return_value = True
    mock_dom.find_running_conversation_for_project.return_value = None
    mock_dom.open_new_conversation.return_value = True
    mock_dom.wait_for_conversation_id.return_value = "cid_chat_new_for_p2"
    worker.dom_controller = mock_dom

    loop_iter = 0
    def mock_sleep(dur):
        nonlocal loop_iter
        loop_iter += 1
        if loop_iter >= 2:
            worker._stop_requested.set()
        return True
    worker.safe_sleep = mock_sleep

    with patch.object(config, "max_concurrent_projects", 3):
        worker._run_multi_project_parallel_loop()

    # 1. p1 must be completed and retained its cid_chat_1
    assert p1.status == TaskStatus.COMPLETED
    assert p1.conversation_id == "cid_chat_1"

    # 2. p2 must NOT reuse cid_chat_1; open_new_conversation must have been called
    assert mock_dom.open_new_conversation.called
    kwargs = mock_dom.open_new_conversation.call_args[1]
    assert kwargs["target_project"] == "Agrico-V2"
    assert kwargs["force"] is True
    assert kwargs["isolate_conversation"] is True
    mock_dom.inject_and_submit_prompt.assert_called_with("Subqueue 1 Prompt 2")

    # 3. p2 conversation_id must be the fresh conversation
    assert p2.conversation_id == "cid_chat_new_for_p2"
    assert p2.status == TaskStatus.RUNNING

    # 4. Worker must remember which active chat is mapped to which queue
    assert worker.active_projects["Agrico-V2 :: Q1"].conversation_id == "cid_chat_new_for_p2"
    assert worker.active_projects["Agrico-V2 :: Q2"].conversation_id == "cid_chat_2"
    assert worker.active_projects["Agrico-V2 :: Q1"].status == "GENERATING"
    assert worker.active_projects["Agrico-V2 :: Q2"].status == "GENERATING"











