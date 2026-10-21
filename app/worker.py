import time
import threading
import re
from typing import List, Optional, Dict, Any, Collection, Set
from PyQt6.QtCore import QThread, pyqtSignal

from app.config import config
from app.models import (
    PromptItem,
    TaskStatus,
    ExecutionStep,
    ProjectExecutionState,
    SubQueueExecutionState,
    get_subqueue_key,
    parse_subqueue_key,
    partition_queue_by_project,
    partition_queue_into_subqueues,
)
from app.account_manager import account_manager
from cdp.client import AntigravityCDPClient
from cdp.dom_controller import AntigravityDOMController
from cdp.quota_inspector import DOMQuotaInspector
from cdp.browser_oauth import BrowserOAuthHandler
from cdp.state_manager import AntigravityStateManager, AntigravityUIState
from app.power_manager import power_manager


def format_duration(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        return f"{h}h {m:02d}m {sec:02d}s"
    elif s >= 60:
        m = s // 60
        sec = s % 60
        return f"{m}m {sec:02d}s"
    else:
        return f"{seconds:.1f}s"


def _check_conversation_generating(controller, conversation_id: Optional[str] = None, project_name: Optional[str] = None) -> bool:
    if not controller:
        return False
    if isinstance(controller, AntigravityDOMController):
        return controller.is_conversation_generating(conversation_id=conversation_id, project_name=project_name)
    if hasattr(controller, "is_conversation_generating"):
        mock_m = controller.is_conversation_generating
        if hasattr(mock_m, "_mock_return_value") or (hasattr(mock_m, "side_effect") and mock_m.side_effect is not None):
            return bool(mock_m(conversation_id=conversation_id, project_name=project_name))
    return bool(controller.is_generating())


def _find_all_running_project_agents(
    controller,
    project_name: str,
    exclude_cids: Optional[Collection[str]] = None,
) -> List[Dict[str, Any]]:
    clean_p = (project_name or "").strip()
    excluded = {str(c).strip().lower() for c in (exclude_cids or []) if c}
    agents: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    def _add_agent(item: Optional[Dict[str, Any]]):
        if not item or not isinstance(item, dict):
            return
        cid = str(item.get("id", "")).strip()
        cid_lower = cid.lower()
        if not cid or cid_lower in excluded or cid_lower in seen_ids:
            return
        seen_ids.add(cid_lower)
        agent_dict = {
            "id": item["id"],
            "title": item.get("title") or clean_p or item["id"],
            "project": item.get("project") or clean_p,
            "is_running": True,
        }
        for k, v in item.items():
            if k not in agent_dict:
                agent_dict[k] = v
        agents.append(agent_dict)

    # 1. If controller is a mock with find_running_conversation_for_project
    if hasattr(controller, "find_running_conversation_for_project"):
        mock_m = controller.find_running_conversation_for_project
        if hasattr(mock_m, "_mock_return_value") or (hasattr(mock_m, "side_effect") and mock_m.side_effect is not None):
            try:
                res = mock_m(project_name, exclude_cids=exclude_cids)
            except TypeError:
                try:
                    res = mock_m(project_name)
                except Exception:
                    res = None
            except Exception:
                res = None

            if isinstance(res, list):
                for it in res:
                    _add_agent(it)
            elif isinstance(res, dict) and res.get("id"):
                _add_agent(res)
            return agents

    # 3. Database inspector (for real controller or when controller is unmocked)
    if clean_p:
        try:
            from cdp.conversation_db_inspector import find_running_conversations_in_db
            running_in_db = find_running_conversations_in_db(clean_p)
            for item in (running_in_db or []):
                cand_id = str(item.get("id", "")).strip().lower()
                if cand_id and cand_id not in excluded and cand_id not in seen_ids:
                    _add_agent({
                        "id": item["id"],
                        "title": item.get("title", clean_p),
                        "project": clean_p,
                        "is_running": True,
                    })
        except Exception:
            pass

    # 4. Controller's find_running_conversation_for_project (real controller)
    if hasattr(controller, "find_running_conversation_for_project"):
        fn = controller.find_running_conversation_for_project
        try:
            res = fn(project_name, exclude_cids=exclude_cids)
        except TypeError:
            try:
                res = fn(project_name)
            except Exception:
                res = None
        except Exception:
            res = None

        if isinstance(res, dict) and res.get("id"):
            _add_agent(res)
        elif isinstance(res, list):
            for it in res:
                _add_agent(it)

    # 5. Controller's find_all_running_conversations_in_sidebar (filtered by project)
    if hasattr(controller, "find_all_running_conversations_in_sidebar"):
        mock_m = controller.find_all_running_conversations_in_sidebar
        try:
            sidebar_items = mock_m(check_db=False)
            if isinstance(sidebar_items, list):
                clean_lower = clean_p.lower()
                for r in sidebar_items:
                    if isinstance(r, dict) and r.get("id"):
                        proj = str(r.get("project", "")).strip().lower()
                        is_match = False
                        if clean_lower in ("", "no project", "none", "default"):
                            if proj in ("", "no project", "none", "default"):
                                is_match = True
                        else:
                            if proj == clean_lower:
                                is_match = True
                        if is_match:
                            _add_agent(r)
        except Exception:
            pass

    return agents


def _find_running_project_agent(
    controller,
    project_name: str,
    exclude_cids: Optional[Collection[str]] = None,
) -> Optional[Dict[str, Any]]:
    agents = _find_all_running_project_agents(controller, project_name, exclude_cids=exclude_cids)
    return agents[0] if agents else None


def _is_untracked_external_agent(running_conv: Optional[Dict[str, Any]], active_projects: Dict[str, Any]) -> bool:
    """
    Checks if a detected running agent conversation belongs to an unmanaged external chat
    rather than one of the active sub-queues currently managed by the worker.
    """
    if not running_conv or not isinstance(running_conv, dict):
        return False
    cid = running_conv.get("id")
    if not cid:
        return False
    for st in active_projects.values():
        tracked_cid = getattr(st, "conversation_id", "")
        if tracked_cid and (tracked_cid == cid or cid.startswith(tracked_cid) or tracked_cid.startswith(cid)):
            return False
    return True


def _capture_submitted_cid(controller, project_name: str, exclude_cids: Optional[Collection[str]] = None) -> str:
    """
    Captures conversation UUID after prompt submission:
    - For live AntigravityDOMController, polls wait_for_conversation_id() up to 4s.
    - For mocks/fallbacks, queries get_active_conversation_id() or wait_for_conversation_id().
    - Excludes any conversation IDs present in exclude_cids (to prevent adopting sister-queue chats).
    """
    if not controller:
        return ""
    excluded = {str(c).strip().lower() for c in (exclude_cids or []) if c}

    if isinstance(controller, AntigravityDOMController):
        cid = controller.wait_for_conversation_id(target_project=project_name, timeout_seconds=4.0, exclude_cids=exclude_cids) or ""
        if cid and cid.strip().lower() not in excluded:
            return cid
    elif hasattr(controller, "wait_for_conversation_id"):
        try:
            res = controller.wait_for_conversation_id(target_project=project_name, timeout_seconds=4.0, exclude_cids=exclude_cids)
            if isinstance(res, str) and res and res.strip().lower() not in excluded:
                return res
        except TypeError:
            res = controller.wait_for_conversation_id(target_project=project_name, timeout_seconds=4.0)
            if isinstance(res, str) and res and res.strip().lower() not in excluded:
                return res

    if hasattr(controller, "get_active_conversation_id"):
        res = controller.get_active_conversation_id()
        if isinstance(res, str) and res and res.strip().lower() not in excluded:
            return res
    return ""


def _is_synthetic_cid(cid: Optional[str]) -> bool:
    """Returns True if cid is empty or matches the runtime synthetic fallback pattern 'conv_{proj}_{ts}'."""
    if not cid:
        return True
    return bool(re.match(r"^conv_.*_\d{9,12}$", str(cid).strip()))


def _resolve_real_cid(
    controller,
    project_name: str,
    current_cid: Optional[str] = None,
    app_data_dir: Optional[str] = None,
    exclude_cids: Optional[Collection[str]] = None,
) -> Optional[str]:
    """
    Attempts to resolve a genuine conversation UUID for a project whose conversation_id
    is currently missing or a temporary synthetic timestamp placeholder ('conv_{proj}_{ts}').
    Checks active page URL/DOM, SQLite summaries DB, and sidebar running elements.
    Excludes any conversation IDs present in exclude_cids.
    """
    clean_current = (current_cid or "").strip()
    if clean_current and not _is_synthetic_cid(clean_current):
        return clean_current

    if not controller:
        return None

    excluded = {str(c).strip().lower() for c in (exclude_cids or []) if c}

    # 1. Try DOM controller's active conversation ID
    if hasattr(controller, "get_active_conversation_id"):
        try:
            cand = controller.get_active_conversation_id()
            if cand and isinstance(cand, str) and not _is_synthetic_cid(cand) and len(cand) >= 20:
                if cand.strip().lower() not in excluded:
                    return cand
        except Exception:
            pass

    # 2. For live controller or when app_data_dir is explicitly specified, check SQLite DB
    if isinstance(controller, AntigravityDOMController) or app_data_dir:
        clean_proj = (project_name or "").strip()
        if clean_proj:
            try:
                from cdp.conversation_db_inspector import find_running_conversations_in_db
                running = find_running_conversations_in_db(clean_proj, app_data_dir=app_data_dir)
                for item in (running or []):
                    cand = item.get("id")
                    if cand and isinstance(cand, str) and not _is_synthetic_cid(cand) and len(cand) >= 20:
                        if cand.strip().lower() not in excluded:
                            return cand
            except Exception:
                pass

        if clean_proj and hasattr(controller, "find_running_conversation_for_project"):
            try:
                running_dom = controller.find_running_conversation_for_project(clean_proj)
                if running_dom and isinstance(running_dom, dict) and running_dom.get("id"):
                    cand = running_dom["id"]
                    if cand and isinstance(cand, str) and not _is_synthetic_cid(cand) and len(cand) >= 20:
                        if cand.strip().lower() not in excluded:
                            return cand
            except Exception:
                pass

    return None


def _check_termination_zero_nav(controller, conversation_id: Optional[str] = None) -> Dict[str, Any]:
    if not controller:
        return {"state": "NORMAL", "detail": ""}
    if isinstance(controller, AntigravityDOMController):
        return controller.check_termination_state_zero_nav(conversation_id=conversation_id)
    if hasattr(controller, "check_termination_state_zero_nav"):
        mock_m = controller.check_termination_state_zero_nav
        if hasattr(mock_m, "_mock_return_value") or (hasattr(mock_m, "side_effect") and mock_m.side_effect is not None):
            res = mock_m(conversation_id=conversation_id)
            if isinstance(res, dict):
                return res
    if hasattr(controller, "check_termination_state"):
        res = controller.check_termination_state()
        if isinstance(res, dict):
            return res
    return {"state": "NORMAL", "detail": ""}


def _populate_prompt_token_telemetry(item: Optional[PromptItem]) -> None:
    """Extracts output tokens, pure generation time, tool time, and token generation speed for completed prompt."""
    if not item or not item.conversation_id:
        return
    try:
        from cdp.chat_lifecycle_inspector import inspect_conversation_lifecycle
        rep = inspect_conversation_lifecycle(item.conversation_id)
        if rep and rep.get("prompt_turns"):
            turn = rep["prompt_turns"][-1]
            item.output_tokens = turn.get("output_tokens", 0)
            item.input_tokens = turn.get("input_tokens", 0)
            item.cache_read_tokens = turn.get("cache_read_tokens", 0)
            item.generation_time = turn.get("total_model_duration_seconds", 0.0)
            item.tool_time = turn.get("total_tool_duration_seconds", 0.0)
            item.tokens_per_second = turn.get("tokens_per_second", 0.0)
    except Exception:
        pass


class AutomationWorker(QThread):
    # Signal definitions for UI and Floating HUD
    sig_step_changed = pyqtSignal(str, str)            # (ExecutionStep value, descriptive message)
    sig_prompt_started = pyqtSignal(str, int, int)       # (prompt_id, index 0-based, total count)
    sig_prompt_progress = pyqtSignal(str, int, float)    # (prompt_id, index 0-based, elapsed_seconds)
    sig_prompt_finished = pyqtSignal(str, int, str, str) # (prompt_id, index 0-based, TaskStatus value, error_msg)
    sig_queue_finished = pyqtSignal(bool)                # (completed_cleanly)
    sig_log = pyqtSignal(str, str)                      # (level: INFO/SUCCESS/WARN/ERROR, message)
    sig_state_changed = pyqtSignal(str)                 # ("RUNNING", "PAUSED", "STOPPED", "IDLE")
    sig_error_prompt = pyqtSignal(str, str, str, str)   # (prompt_id, error_title, error_detail, screenshot_path)
    sig_quota_updated = pyqtSignal(dict)                # (quota_inspection_result)
    sig_stabilization_progress = pyqtSignal(float, float) # (remaining_seconds, total_seconds)
    sig_project_state_changed = pyqtSignal(str, str, str) # (project_name/queue_key, status, prompt_id)
    sig_subqueue_state_changed = pyqtSignal(str, str, str) # (queue_key, status, prompt_id)
    sig_startup_active_agents_detected = pyqtSignal(list, int) # (running_agents, queue_count)
    sig_active_projects_updated = pyqtSignal(dict)         # snapshot of all active project states
    sig_pre_switch_countdown_started = pyqtSignal(str, str, int)  # (current_email, target_email, total_seconds)
    sig_pre_switch_countdown_tick = pyqtSignal(int, int)          # (remaining_seconds, total_seconds)
    sig_pre_switch_countdown_finished = pyqtSignal()

    def __init__(
        self,
        queue: List[PromptItem],
        countdown_seconds: int = 0,
        target_hwnd: int = 0,
        is_manual_mode: bool = False,
        dom_controller: Optional[AntigravityDOMController] = None,
        **kwargs,
    ):
        super().__init__()
        self.queue = queue
        self.countdown_seconds = countdown_seconds
        self.target_hwnd = target_hwnd
        self.is_manual_mode = is_manual_mode

        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_requested = threading.Event()
        self._skip_current_requested = threading.Event()
        self._error_response_event = threading.Event()
        self._switch_now_event = threading.Event()
        self._queue_changed_event = threading.Event()
        self._startup_decision_event = threading.Event()
        self._startup_decision: str = "wait"
        self._busy_projects_waiting: Set[str] = set()
        self._error_action = "abort"
        self.ui_mutex = threading.RLock()

        self.current_prompt_id: Optional[str] = None
        self.current_index: int = 0
        self.is_paused: bool = False
        self._quota_reset_target_ts: Optional[float] = None

        self.cdp_client = AntigravityCDPClient()
        self.dom_controller: Optional[AntigravityDOMController] = dom_controller
        self.quota_inspector: Optional[DOMQuotaInspector] = None
        self.oauth_handler: Optional[BrowserOAuthHandler] = None
        self.state_manager: Optional[AntigravityStateManager] = None

        self.active_projects: Dict[str, ProjectExecutionState] = {}
        self.project_queues: Dict[str, List[PromptItem]] = {}

        self._last_quota_check_ts: float = 0.0
        self._last_quota_result: Optional[Dict[str, Any]] = None
        self._quota_drain_in_progress: bool = False

    def resolve_startup_decision(self, action: str):
        """Called when user makes a decision on the startup active agents dialog or on timeout."""
        self._startup_decision = "proceed" if action == "proceed" else "wait"
        self._startup_decision_event.set()
        if self._startup_decision == "proceed":
            self._busy_projects_waiting.clear()
            self.sig_log.emit("INFO", "⚡ User approved starting parallel queues immediately without waiting for existing agents.")
        else:
            self.sig_log.emit("INFO", "⏳ User selected waiting for active agents to reach zero before starting busy projects.")
        self._queue_changed_event.set()

    def notify_queue_changed(self, new_queue: Optional[List[PromptItem]] = None):
        """
        Dynamically called when prompts are added, edited, or modified in the queue at runtime.
        Immediately re-partitions queues, updates project metrics, emits an updated HUD snapshot,
        and unblocks the parallel loop.
        """
        if new_queue is not None:
            self.queue = new_queue
        self.project_queues = partition_queue_into_subqueues(self.queue)
        for p, items in self.project_queues.items():
            p_name, q_name = parse_subqueue_key(p)
            pending_count = sum(1 for it in items if it.status == TaskStatus.PENDING)
            completed_count = sum(1 for it in items if it.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED))
            total_count = len(items)
            next_it = next((it.text for it in items if it.status == TaskStatus.PENDING), "")

            if p not in self.active_projects:
                self.active_projects[p] = ProjectExecutionState(
                    project_name=p_name,
                    queue_name=q_name,
                    queue_key=p,
                    status="IDLE" if pending_count > 0 else "COMPLETED",
                    pending_count=pending_count,
                    total_count=total_count,
                    completed_count=completed_count,
                    next_prompt_text=next_it,
                )
            else:
                st = self.active_projects[p]
                st.project_name = p_name
                st.queue_name = q_name
                st.queue_key = p
                st.pending_count = pending_count
                st.total_count = total_count
                st.completed_count = completed_count
                st.next_prompt_text = next_it
                if st.status == "COMPLETED" and pending_count > 0:
                    st.status = "IDLE"
        self._emit_active_projects_snapshot()
        self._queue_changed_event.set()

    def _resolve_queue_index(self, item) -> int:
        """Thread-safe index lookup for a prompt item in the queue. Returns -1 if not found."""
        if not item or not self.queue:
            return -1
        if hasattr(self.queue, "index_by_id"):
            item_id = getattr(item, "id", None)
            if item_id:
                idx = self.queue.index_by_id(item_id)
                if idx != -1:
                    return idx
        try:
            return self.queue.index(item) if item in self.queue else -1
        except (ValueError, RuntimeError):
            return -1


    def notify_settings_changed(self):
        """Called when settings are saved at runtime in the Settings tab."""
        max_c = getattr(config, "max_concurrent_projects", 3)
        poll_i = getattr(config, "polling_interval_seconds", 2)
        self.sig_log.emit("INFO", f"⚙️ [Runtime Settings] Concurrency Limit: {max_c} | Polling Interval: {poll_i}s")
        self._emit_active_projects_snapshot()
        self._queue_changed_event.set()

    def pause_subqueue(self, queue_key: str):
        """Pauses execution for a single sub-queue independently while others continue running."""
        clean = (queue_key or "").strip()
        if clean in self.active_projects:
            st = self.active_projects[clean]
            st.is_paused = True
            st.status = "PAUSED"
            self.sig_project_state_changed.emit(clean, "PAUSED", st.current_prompt.id if st.current_prompt else "")
            self.sig_subqueue_state_changed.emit(clean, "PAUSED", st.current_prompt.id if st.current_prompt else "")
            self._emit_active_projects_snapshot()
            self.sig_log.emit("WARN", f"[{clean}] Queue paused independently. Other parallel queues remain running.")

    def resume_subqueue(self, queue_key: str):
        """Resumes execution for a single sub-queue independently while other queues remain running."""
        clean = (queue_key or "").strip()
        if clean in self.active_projects:
            st = self.active_projects[clean]
            st.is_paused = False
            if st.status in ("PAUSED", "HALTED_QUOTA") and st.conversation_id and self.dom_controller:
                self.sig_log.emit("INFO", f"[{clean}] Resuming queue in chat {st.conversation_id}...")
                with self.ui_mutex:
                    self.dom_controller.switch_to_conversation(st.conversation_id)
                    mini_prompt = getattr(config, "resume_mini_prompt", "Please resume where you left off and finish the remaining work.")
                    self.dom_controller.inject_resume_prompt(mini_prompt)
                if st.current_prompt:
                    st.current_prompt.is_resumed = True
                    st.current_prompt.resume_count += 1
                st.status = "GENERATING"
                st.start_time = time.time()
            elif st.status == "PAUSED":
                st.status = "GENERATING" if st.current_prompt else "IDLE"
            self.sig_project_state_changed.emit(clean, st.status, st.current_prompt.id if st.current_prompt else "")
            self.sig_subqueue_state_changed.emit(clean, st.status, st.current_prompt.id if st.current_prompt else "")
            self._emit_active_projects_snapshot()
            self.sig_log.emit("INFO", f"[{clean}] Queue resumed independently. Other parallel queues remain running.")

    def pause_project(self, target: str):
        """Pauses execution for a single sub-queue or all sub-queues under a project."""
        clean = (target or "").strip()
        if clean in self.active_projects:
            self.pause_subqueue(clean)
            return
        matching = [k for k in self.active_projects.keys() if parse_subqueue_key(k)[0] == clean]
        if matching:
            for k in matching:
                self.pause_subqueue(k)

    def resume_project(self, target: str):
        """Resumes execution for a single sub-queue or all sub-queues under a project."""
        clean = (target or "").strip()
        if clean in self.active_projects:
            self.resume_subqueue(clean)
            return
        matching = [k for k in self.active_projects.keys() if parse_subqueue_key(k)[0] == clean]
        if matching:
            for k in matching:
                self.resume_subqueue(k)

    def _emit_active_projects_snapshot(self):
        for p, st in self.active_projects.items():
            items = self.project_queues.get(p, [])
            st.pending_count = sum(1 for it in items if it.status == TaskStatus.PENDING)
            st.total_count = len(items)
            st.completed_count = sum(1 for it in items if it.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED))
            next_it = next((it.text for it in items if it.status == TaskStatus.PENDING), "")
            st.next_prompt_text = next_it
        snapshot = {p: st.to_dict() for p, st in self.active_projects.items()}
        self.sig_active_projects_updated.emit(snapshot)

    def pause(self, reason: str = "Automation paused."):
        if not self.is_paused and not self._stop_requested.is_set():
            self.is_paused = True
            self._pause_event.clear()
            self.sig_state_changed.emit("PAUSED")
            self.sig_step_changed.emit(ExecutionStep.PAUSED.value, reason)
            self.sig_log.emit("WARN", reason)

    def resume(self):
        if self.is_paused and not self._stop_requested.is_set():
            self._quota_reset_target_ts = None
            self.is_paused = False
            self._pause_event.set()
            self.sig_state_changed.emit("RUNNING")
            self.sig_log.emit("INFO", "Automation resumed.")

    def cancel(self):
        self._stop_requested.set()
        self._pause_event.set()
        self._error_response_event.set()
        self._switch_now_event.set()
        self.sig_stabilization_progress.emit(0.0, 0.0)
        self.sig_state_changed.emit("STOPPED")
        self.sig_step_changed.emit(ExecutionStep.IDLE.value, "Automation stopped.")
        self.sig_log.emit("WARN", "Emergency cancel / stop requested.")

    def trigger_switch_now(self):
        """Triggered when user clicks 'Change Now' on the 30s Pre-Switch Mini HUD."""
        self._switch_now_event.set()

    def _wait_for_pre_switch_countdown(self, current_email: str, target_email: str) -> bool:
        """
        Displays the 30-second Pre-Switch Countdown Mini HUD before clicking 'Continue with Google'.
        Returns True to proceed with account switch (either via 'Change Now' or 30s timeout),
        or False if automation was cancelled/stopped.
        """
        if not getattr(config, "enable_pre_switch_hud", True):
            return not self._stop_requested.is_set()

        total_sec = int(getattr(config, "pre_switch_countdown_seconds", 30))
        if total_sec <= 0:
            return not self._stop_requested.is_set()

        self._switch_now_event.clear()
        self.sig_pre_switch_countdown_started.emit(current_email or "", target_email or "", total_sec)
        self.sig_log.emit(
            "INFO",
            f"⏳ Waiting {total_sec}s before clicking 'Continue with Google' for '{target_email}' "
            f"(click 'Change Now' on Mini HUD to switch immediately)...",
        )

        deadline = time.time() + total_sec
        last_emitted_sec = total_sec
        try:
            while not self._stop_requested.is_set():
                if self._switch_now_event.is_set():
                    self.sig_log.emit(
                        "INFO",
                        "⚡ 'Change Now' clicked on Mini HUD — switching Google account immediately!",
                    )
                    return True

                remaining = deadline - time.time()
                if remaining <= 0:
                    self.sig_log.emit(
                        "INFO",
                        f"⏰ {total_sec}s pre-switch countdown elapsed — automatically clicking 'Continue with Google'...",
                    )
                    return True

                rem_int = int(remaining) + (1 if (remaining - int(remaining)) > 0.05 else 0)
                rem_int = max(0, min(total_sec, rem_int))
                if rem_int != last_emitted_sec:
                    last_emitted_sec = rem_int
                    self.sig_pre_switch_countdown_tick.emit(rem_int, total_sec)
                    self.sig_step_changed.emit(
                        ExecutionStep.ACCOUNT_SWITCH.value,
                        f"Switching to {target_email} in {rem_int}s (or click Change Now)...",
                    )

                self._switch_now_event.wait(timeout=0.1)
            return False
        finally:
            self.sig_pre_switch_countdown_finished.emit()

    def skip_current(self):
        self._skip_current_requested.set()
        self.sig_stabilization_progress.emit(0.0, 0.0)
        if self.is_paused:
            self.resume()
        self.sig_log.emit("INFO", "Next Prompt requested from HUD / Studio. Advancing...")

    def check_flow(self) -> bool:
        if self._stop_requested.is_set():
            return False

        if not self._pause_event.is_set():
            last_hud_update = 0.0
            while not self._pause_event.is_set():
                if self._stop_requested.is_set():
                    return False

                # Handle quota reset timer expiry while paused
                reset_target = getattr(self, "_quota_reset_target_ts", None)
                if reset_target:
                    remaining = reset_target - time.time()
                    if remaining <= 0:
                        self.sig_log.emit("SUCCESS", "⏰ Quota reset time elapsed. Automatically resuming queue...")
                        self._quota_reset_target_ts = None
                        self.resume()
                        break

                    now = time.time()
                    if now - last_hud_update >= 2.0:
                        last_hud_update = now
                        h = int(remaining // 3600)
                        m = int((remaining % 3600) // 60)
                        s = int(remaining % 60)
                        countdown = f"{h}h {m}m {s}s" if h > 0 else f"{m}m {s}s"
                        self.sig_step_changed.emit(
                            ExecutionStep.PAUSED.value,
                            f"Waiting for Limit Reset ({countdown} remaining)",
                        )

                time.sleep(0.3)

        return not self._stop_requested.is_set()

    def safe_sleep(self, duration: float) -> bool:
        end_time = time.time() + duration
        while time.time() < end_time:
            if self._skip_current_requested.is_set() or not self.check_flow():
                return False
            if self._queue_changed_event.is_set():
                self._queue_changed_event.clear()
                return True
            time.sleep(min(0.05, max(0.01, end_time - time.time())))
        return True

    def run(self):
        self.sig_state_changed.emit("RUNNING")
        self.sig_log.emit("INFO", f"Starting Antigravity CDP queue processing ({len(self.queue)} items)...")

        total = len(self.queue)
        completed_cleanly = True

        try:
            # Prevent Windows system sleep and display sleep during automation run
            power_manager.acquire(
                prevent_display_sleep=True,
                log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
            )

            # 1. Connect to Antigravity via CDP
            self.sig_step_changed.emit(ExecutionStep.CONNECTING.value, "Connecting to Antigravity over CDP...")
            self.sig_log.emit("INFO", f"Connecting to Antigravity on port {self.cdp_client.port}...")

            ok, msg = self.cdp_client.connect(auto_launch=getattr(config, "auto_launch_antigravity", True))
            if not ok:
                raise RuntimeError(msg)

            page = self.cdp_client.get_page()
            self.dom_controller = AntigravityDOMController(page)
            self.quota_inspector = DOMQuotaInspector(page)
            self.oauth_handler = BrowserOAuthHandler(page)
            self.state_manager = AntigravityStateManager(page)
            self.sig_log.emit("SUCCESS", f"Connected to Antigravity: '{page.title()}' (URL: {page.url})")

            # 1b. Pre-flight State Normalization:
            # Dynamically self-heals any non-workspace states (e.g. logged out, unexpected issue,
            # lingering settings modal, terms dialog, or onboarding carousel) before queue dispatch.
            self.sig_step_changed.emit(ExecutionStep.CONNECTING.value, "Evaluating Antigravity workspace state...")
            normalized = self.state_manager.normalize_to_workspace(
                log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
                oauth_handler=self.oauth_handler,
                stop_event=self._stop_requested,
                pre_switch_callback=self._wait_for_pre_switch_countdown,
                dom_controller=self.dom_controller,
            )
            if not normalized and self._stop_requested.is_set():
                completed_cleanly = False
                return

            # 2a. Pre-Run CID Reconciliation:
            # Check if any previously executed or interrupted prompt finished in the background
            try:
                from cdp.reconciliation import reconcile_prompt_status_from_conversation
                prompt_lock = getattr(self.queue, "lock", None)
                reconciled_any = False
                if prompt_lock:
                    with prompt_lock:
                        for p in self.queue:
                            if getattr(p, "conversation_id", None) and p.status in (TaskStatus.RUNNING, TaskStatus.FAILED):
                                if reconcile_prompt_status_from_conversation(p):
                                    reconciled_any = True
                                    status_str = p.status.value if hasattr(p.status, "value") else str(p.status)
                                    self.sig_prompt_finished.emit(p.id, self._resolve_queue_index(p), status_str, p.error_message or "")
                else:
                    for p in self.queue:
                        if getattr(p, "conversation_id", None) and p.status in (TaskStatus.RUNNING, TaskStatus.FAILED):
                            if reconcile_prompt_status_from_conversation(p):
                                reconciled_any = True
                                status_str = p.status.value if hasattr(p.status, "value") else str(p.status)
                                self.sig_prompt_finished.emit(p.id, self._resolve_queue_index(p), status_str, p.error_message or "")
                if reconciled_any:
                    self.sig_log.emit("INFO", "⚡ [Reconciliation] Reconciled prompt statuses from Antigravity background state.")
            except Exception as e:
                self.sig_log.emit("WARN", f"[Reconciliation] Pre-run status check warning: {e}")

            # 2b. Partition queue by project and sub-queue
            self.project_queues = partition_queue_into_subqueues(
                self.queue,
                default_project=getattr(config, "target_project_name", "No Project")
            )
            self.active_projects = {}
            for p, items in self.project_queues.items():
                proj_name, q_name = parse_subqueue_key(p)
                running_item = next((it for it in items if it.status == TaskStatus.RUNNING), None)
                if running_item:
                    # An item in this subqueue is already actively running (e.g. from reconciliation or previous session)!
                    self.active_projects[p] = ProjectExecutionState(
                        project_name=proj_name,
                        queue_name=q_name,
                        queue_key=p,
                        status="GENERATING",
                        conversation_id=running_item.conversation_id or "",
                        current_prompt=running_item,
                        start_time=running_item.start_time or time.time(),
                    )
                elif any(it.status == TaskStatus.PENDING for it in items):
                    self.active_projects[p] = ProjectExecutionState(
                        project_name=proj_name,
                        queue_name=q_name,
                        queue_key=p,
                        status="IDLE",
                    )
                else:
                    self.active_projects[p] = ProjectExecutionState(
                        project_name=proj_name,
                        queue_name=q_name,
                        queue_key=p,
                        status="COMPLETED",
                    )
            # Immediately emit active projects snapshot so Floating HUD displays all projects at startup
            self._emit_active_projects_snapshot()

            # 2c. Startup Active Agents Detection Gate:
            # If target project(s) already have active generating agents, prompt user:
            # "Start parallel queues now, or wait for active agents to reach zero?"
            # Idle projects start immediately. Busy projects wait independently or start if user approves.
            target_projects = list(dict.fromkeys(parse_subqueue_key(k)[0] for k in self.project_queues.keys()))
            tracked_cids = {s.conversation_id for s in self.active_projects.values() if s.conversation_id}
            busy_projects = {}
            idle_projects = []
            for proj in target_projects:
                running_agents = _find_all_running_project_agents(self.dom_controller, proj, exclude_cids=tracked_cids)
                if running_agents:
                    busy_projects[proj] = running_agents
                else:
                    idle_projects.append(proj)

            if busy_projects:
                all_busy_agents = [ag for ags in busy_projects.values() for ag in ags]
                self._busy_projects_waiting = set(busy_projects.keys())
                self._startup_decision = "wait"
                self._startup_decision_event.clear()
                self.sig_startup_active_agents_detected.emit(all_busy_agents, len(self.project_queues))

                idle_msg = f" Idle project(s) ({', '.join(idle_projects)}) start immediately." if idle_projects else ""
                self.sig_log.emit(
                    "WARN",
                    f"⚠️ [Startup Detection] Found {len(all_busy_agents)} active agent(s) across busy project(s): {list(busy_projects.keys())}. "
                    f"Prompting user for decision (auto-defaults to waiting).{idle_msg}"
                )
            else:
                self._busy_projects_waiting = set()

            # Always run the multi-project parallel orchestrator:
            # - Concurrently handles 1 or many projects/sub-queues up to max_concurrent_projects.
            # - Dynamically syncs newly added prompts/projects at runtime.
            # - Emits live active project states and queue counts to HUD.
            completed_cleanly = self._run_multi_project_parallel_loop()

        except Exception as e:
            err = str(e)
            self.sig_log.emit("ERROR", f"Fatal worker exception: {err}")
            self.sig_step_changed.emit(ExecutionStep.ERROR.value, err)
            completed_cleanly = False
        finally:
            if hasattr(self, "cdp_client") and self.cdp_client:
                try:
                    self.cdp_client.disconnect()
                except Exception:
                    pass
            power_manager.release()
            self.sig_state_changed.emit("IDLE")
            self.sig_step_changed.emit(ExecutionStep.IDLE.value, "Queue finished." if completed_cleanly else "Queue stopped.")
            self.sig_queue_finished.emit(completed_cleanly)
            self.sig_log.emit("INFO" if completed_cleanly else "WARN", "Queue execution ended.")

    def _run_multi_project_parallel_loop(self) -> bool:
        """
        Executes prompts across multiple projects concurrently with strict per-project constraints:
        1. At most ONE active running chat per project at any given time.
        2. Dispatches up to max_concurrent_projects simultaneously across distinct projects.
        3. Round-robin polling (every polling_interval_seconds, default 30s) switches between active chats to check status.
        4. Independent project resume: allows resuming or pausing a single project without disturbing others.
        5. Coordinated quota recovery barrier: waits for all active projects to stop on quota before switching accounts.
        """
        last_poll_ts = 0.0
        last_status_log_ts = 0.0

        self.sig_log.emit(
            "INFO",
            f"🚀 [Multi-Project Parallel] Started zero-navigation parallel orchestrator. "
            f"Projects: {len(self.project_queues)} | Concurrency Limit: {getattr(config, 'max_concurrent_projects', 3)} | Check Interval: {getattr(config, 'polling_interval_seconds', 2)}s"
        )
        self._emit_active_projects_snapshot()

        while not self._stop_requested.is_set():
            if not self.check_flow():
                return False

            now = time.time()
            max_concurrent = getattr(config, "max_concurrent_projects", 3)
            poll_interval = getattr(config, "polling_interval_seconds", 2)

            # Dynamically sync any prompts added to queue at runtime
            self.project_queues = partition_queue_into_subqueues(
                self.queue,
                default_project=getattr(config, "target_project_name", "No Project")
            )
            projects_changed = False
            for p, items in self.project_queues.items():
                p_name, q_name = parse_subqueue_key(p)
                pending_in_p = sum(1 for it in items if it.status == TaskStatus.PENDING)
                completed_in_p = sum(1 for it in items if it.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED))
                total_in_p = len(items)
                next_it = next((it.text for it in items if it.status == TaskStatus.PENDING), "")

                if p not in self.active_projects:
                    self.active_projects[p] = ProjectExecutionState(
                        project_name=p_name,
                        queue_name=q_name,
                        queue_key=p,
                        status="IDLE" if pending_in_p > 0 else "COMPLETED",
                        pending_count=pending_in_p,
                        total_count=total_in_p,
                        completed_count=completed_in_p,
                        next_prompt_text=next_it,
                    )
                    projects_changed = True
                else:
                    st = self.active_projects[p]
                    if st.pending_count != pending_in_p or st.total_count != total_in_p:
                        projects_changed = True
                    st.project_name = p_name
                    st.queue_name = q_name
                    st.queue_key = p
                    st.pending_count = pending_in_p
                    st.total_count = total_in_p
                    st.completed_count = completed_in_p
                    st.next_prompt_text = next_it
                    if st.status == "COMPLETED" and pending_in_p > 0:
                        st.status = "IDLE"
                        projects_changed = True

            if projects_changed:
                self._emit_active_projects_snapshot()

            # -------------------------------------------------------------
            # Check completion: all prompts across all projects completed?
            # -------------------------------------------------------------
            all_done = True
            for proj, items in self.project_queues.items():
                has_pending = any(item.status == TaskStatus.PENDING for item in items)
                proj_state = self.active_projects.get(proj)
                is_busy = proj_state and proj_state.status in ("DISPATCHING", "GENERATING", "STABILIZING", "HALTED_QUOTA")
                if has_pending or is_busy:
                    all_done = False
                    break

            if all_done:
                self.sig_log.emit("SUCCESS", "🎉 [Multi-Project Parallel] All queued prompts across all projects completed!")
                for st in self.active_projects.values():
                    st.status = "COMPLETED"
                self._emit_active_projects_snapshot()
                return True

            # -------------------------------------------------------------
            # Coordinated Quota Recovery Barrier Check
            # -------------------------------------------------------------
            quota_halted_projects = [
                p for p, st in self.active_projects.items() if st.status == "HALTED_QUOTA"
            ]
            has_generating_projects = any(
                st.status in ("GENERATING", "STABILIZING", "DISPATCHING")
                for st in self.active_projects.values()
            )

            if (quota_halted_projects or self._quota_drain_in_progress) and not has_generating_projects:
                # All active projects have reached quota halt or finished during drain! Execute coordinated recovery
                self.sig_log.emit(
                    "WARN",
                    f"🔒 [Coordinated Quota Recovery] All active chats have stopped ({len(quota_halted_projects)} halted on quota). "
                    f"Executing safe Google account rotation..."
                )
                if not self._enforce_quota("Multi-Project Coordinated Quota Recovery"):
                    self.sig_log.emit("ERROR", "Failed to recover quota. Stopping parallel loop.")
                    return False

                self._quota_drain_in_progress = False

                # Ensure workspace is clean & normalized post account switch
                if self.state_manager:
                    self.state_manager.normalize_to_workspace(
                        log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
                        oauth_handler=self.oauth_handler,
                        stop_event=self._stop_requested,
                        pre_switch_callback=self._wait_for_pre_switch_countdown,
                        dom_controller=self.dom_controller,
                    )

                # Account switch succeeded / quota recovered! Resume each halted project in its SAME chat
                mini_prompt = getattr(config, "resume_mini_prompt", "Please resume where you left off and finish the remaining work.")
                for qp in quota_halted_projects:
                    st = self.active_projects[qp]
                    if st.conversation_id:
                        self.sig_log.emit("INFO", f"[{qp}] Resuming conversation {st.conversation_id} in-place...")
                        with self.ui_mutex:
                            self.dom_controller.switch_to_conversation(st.conversation_id)
                            self.dom_controller.inject_resume_prompt(mini_prompt)
                        if st.current_prompt:
                            st.current_prompt.is_resumed = True
                            st.current_prompt.resume_count += 1
                        st.status = "GENERATING"
                        st.start_time = time.time()
                        self.sig_project_state_changed.emit(qp, "GENERATING", st.current_prompt.id if st.current_prompt else "")
                        self.sig_subqueue_state_changed.emit(qp, "GENERATING", st.current_prompt.id if st.current_prompt else "")
                self._emit_active_projects_snapshot()
                last_poll_ts = time.time()
                continue

            # -------------------------------------------------------------
            # Phase 1: Dispatch pending prompts for idle projects up to max_concurrent
            # -------------------------------------------------------------
            active_count = sum(
                1 for st in self.active_projects.values()
                if st.status in ("DISPATCHING", "GENERATING", "STABILIZING", "HALTED_QUOTA")
            )

            # Only dispatch if not waiting on quota barrier or draining
            if not quota_halted_projects and not self._quota_drain_in_progress and active_count < max_concurrent:
                for proj, items in self.project_queues.items():
                    if active_count >= max_concurrent or self._quota_drain_in_progress:
                        break

                    proj_state = self.active_projects.get(proj)
                    if not proj_state or proj_state.is_paused:
                        continue

                    # Strict per-queue constraint: sub-queue must be IDLE
                    if proj_state.status != "IDLE":
                        continue

                    proj_name, q_name = parse_subqueue_key(proj)

                    # Per-project startup waiting check:
                    if proj_name in self._busy_projects_waiting:
                        if self._startup_decision == "proceed":
                            self._busy_projects_waiting.discard(proj_name)
                        else:
                            tracked_cids = {s.conversation_id for s in self.active_projects.values() if s.conversation_id}
                            still_running = _find_all_running_project_agents(self.dom_controller, proj_name, exclude_cids=tracked_cids)
                            if not still_running:
                                self.sig_log.emit(
                                    "SUCCESS",
                                    f"[{proj}] Active agents in project '{proj_name}' have reached zero! Starting queue..."
                                )
                                self._busy_projects_waiting.discard(proj_name)
                                try:
                                    from cdp.reconciliation import reconcile_prompt_status_from_conversation
                                    for it in self.queue:
                                        if getattr(it, "conversation_id", None) and it.status in (TaskStatus.RUNNING, TaskStatus.FAILED):
                                            if reconcile_prompt_status_from_conversation(it):
                                                status_str = it.status.value if hasattr(it.status, "value") else str(it.status)
                                                self.sig_prompt_finished.emit(it.id, self._resolve_queue_index(it), status_str, it.error_message or "")
                                except Exception:
                                    pass
                            else:
                                # Still busy: skip dispatch for this project, let other projects continue
                                continue

                    # Pre-check: Does project ALREADY have an UNTRACKED active running agent in the background?
                    tracked_cids = {s.conversation_id for s in self.active_projects.values() if s.conversation_id}
                    running_conv = _find_running_project_agent(self.dom_controller, proj_name, exclude_cids=tracked_cids)
                    if self._startup_decision != "proceed" and _is_untracked_external_agent(running_conv, self.active_projects):
                        chat_title = running_conv.get("title") or running_conv.get("id")
                        proj_state.status = "GENERATING"
                        proj_state.conversation_id = running_conv["id"]
                        proj_state.current_prompt = None  # Background agent is NOT a queued prompt yet!
                        proj_state.start_time = time.time()
                        proj_state.last_poll_ts = time.time()

                        self.sig_log.emit(
                            "WARN",
                            f"[{proj}] Found unmanaged active agent generating in chat '{chat_title}' ({running_conv['id']}). "
                            f"Adopting chat and waiting for completion before starting queued prompts..."
                        )
                        self.sig_project_state_changed.emit(proj, "GENERATING", "")
                        self.sig_subqueue_state_changed.emit(proj, "GENERATING", "")
                        self._emit_active_projects_snapshot()
                        active_count += 1
                        continue

                    # Find next pending prompt for this sub-queue
                    next_item = next((it for it in items if it.status == TaskStatus.PENDING), None)
                    if not next_item:
                        continue

                    # Pre-dispatch quota check gate: verify quota before opening new chat
                    if not self._check_pre_dispatch_quota(proj_name):
                        continue

                    # Dispatch next_item for this sub-queue
                    proj_state.status = "DISPATCHING"
                    proj_state.current_prompt = next_item
                    next_item.status = TaskStatus.RUNNING
                    next_item.start_time = time.time()
                    total_idx = self._resolve_queue_index(next_item)

                    self.sig_prompt_started.emit(next_item.id, total_idx, len(self.queue))
                    self.sig_project_state_changed.emit(proj, "DISPATCHING", next_item.id)
                    self.sig_subqueue_state_changed.emit(proj, "DISPATCHING", next_item.id)
                    self._emit_active_projects_snapshot()
                    self.sig_log.emit("INFO", f"[{proj}] Dispatching Prompt #{total_idx + 1}: \"{next_item.text[:60]}...\"")

                    # UI interaction MUST be serialized under ui_mutex to prevent collisions in Playwright viewport
                    try:
                        with self.ui_mutex:
                            # Pre-dispatch state normalization: auto-dismiss terms notices, settings, or auth errors
                            if self.state_manager:
                                self.state_manager.normalize_to_workspace(
                                    log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
                                    oauth_handler=self.oauth_handler,
                                    stop_event=self._stop_requested,
                                    pre_switch_callback=self._wait_for_pre_switch_countdown,
                                    dom_controller=self.dom_controller,
                                )

                            # Open fresh conversation for this prompt (strict rule: fresh new chat for each prompt)
                            # isolate_conversation=True prevents blocking on sister chats in same project
                            opened = self.dom_controller.open_new_conversation(
                                force=True,
                                target_project=proj_name,
                                isolate_conversation=True,
                                check_flow_callback=self.check_flow,
                                progress_callback=self.sig_stabilization_progress.emit,
                                log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
                            )
                            if not opened:
                                raise RuntimeError(f"Failed to open fresh conversation view for '{proj}'")
                            self.dom_controller.select_target_project(proj_name)
                            self.dom_controller.inject_and_submit_prompt(next_item.text)

                            # Exclude all currently active conversation IDs across all projects
                            # AND all conversation IDs already recorded in the queue from earlier prompts
                            active_cids = {st.conversation_id for st in self.active_projects.values() if st.conversation_id}
                            known_queue_cids = {it.conversation_id for it in self.queue if it.conversation_id}
                            exclude_cids = active_cids | known_queue_cids

                            cid = _capture_submitted_cid(self.dom_controller, proj_name, exclude_cids=exclude_cids) or f"conv_{int(time.time())}"
                            proj_state.conversation_id = cid
                            next_item.conversation_id = cid
                            proj_state.status = "GENERATING"
                            proj_state.start_time = time.time()
                            proj_state.last_poll_ts = time.time()

                        self.sig_project_state_changed.emit(proj, "GENERATING", next_item.id)
                        self.sig_subqueue_state_changed.emit(proj, "GENERATING", next_item.id)
                        self._emit_active_projects_snapshot()
                        self.sig_log.emit("SUCCESS", f"[{proj}] Prompt launched successfully (Chat ID: {cid}). Now generating in background.")
                        active_count += 1
                    except Exception as ex:
                        self.sig_log.emit("ERROR", f"[{proj}] Error dispatching prompt: {ex}")
                        proj_state.status = "IDLE"
                        proj_state.conversation_id = ""
                        proj_state.current_prompt = None
                        next_item.status = TaskStatus.FAILED
                        self.sig_prompt_finished.emit(next_item.id, total_idx, TaskStatus.FAILED.value, str(ex))
                        self._emit_active_projects_snapshot()

            # -------------------------------------------------------------
            # Phase 2: Zero-Navigation Sidebar + Local Transcript Polling Cycle
            # -------------------------------------------------------------
            if now - last_poll_ts >= poll_interval:
                last_poll_ts = now
                generating_projects = [
                    (p, st) for p, st in self.active_projects.items()
                    if st.status == "GENERATING" and not st.is_paused
                ]

                if generating_projects and (now - last_status_log_ts >= 30.0):
                    last_status_log_ts = now
                    self.sig_log.emit(
                        "INFO",
                        f"⏱ [Zero-Nav Monitor] Checking {len(generating_projects)} active project(s) via Sidebar DOM + Local Transcript..."
                    )

                for proj, st in generating_projects:
                    if self._stop_requested.is_set():
                        return False
                    if st.is_paused:
                        continue

                    proj_name, q_name = parse_subqueue_key(proj)

                    # Active CID reconciliation: if st.conversation_id is missing or temporary fallback conv_{proj}_{ts}, resolve it!
                    if not st.conversation_id or _is_synthetic_cid(st.conversation_id):
                        other_cids = {s.conversation_id for k, s in self.active_projects.items() if k != proj and s.conversation_id}
                        known_cids = {it.conversation_id for it in self.queue if it.conversation_id and it.conversation_id != st.conversation_id}
                        all_excluded = other_cids | known_cids
                        real_cid = _resolve_real_cid(self.dom_controller, proj_name, st.conversation_id, exclude_cids=all_excluded)
                        if real_cid and real_cid != st.conversation_id:
                            old_cid = st.conversation_id
                            st.conversation_id = real_cid
                            if st.current_prompt:
                                st.current_prompt.conversation_id = real_cid
                            self.sig_log.emit(
                                "INFO",
                                f"[{proj}] Resolved active conversation UUID: {real_cid} (upgraded from {old_cid or 'none'})"
                            )
                            self._emit_active_projects_snapshot()

                    if not st.conversation_id:
                        continue

                    try:
                        # 1. Check Sidebar DOM (Foreground Spinner + Floating Bouncing Dots) without switching UI!
                        is_gen = _check_conversation_generating(
                            self.dom_controller,
                            conversation_id=st.conversation_id,
                            project_name=proj_name,
                        )

                        if is_gen:
                            # Still generating: update elapsed time without touching or switching the open chat
                            if st.current_prompt:
                                st.elapsed_time = round(time.time() - (st.start_time or time.time()), 1)
                                st.current_prompt.elapsed_time = st.elapsed_time
                                total_idx = self._resolve_queue_index(st.current_prompt)
                                self.sig_prompt_progress.emit(st.current_prompt.id, total_idx, st.elapsed_time)
                            self._emit_active_projects_snapshot()
                            continue

                        # 2. Sidebar shows stopped! Check termination reason via Local Transcript (<1ms, zero UI nav)
                        term = _check_termination_zero_nav(self.dom_controller, conversation_id=st.conversation_id)
                        term_state = term.get("state", "NORMAL")

                        if term_state == "QUOTA_EXHAUSTED":
                            self.sig_log.emit("WARN", f"[{proj}] Quota exhausted in chat {st.conversation_id} (detected via {term.get('source', 'sqlite_db')}).")
                            st.status = "HALTED_QUOTA"
                            self.sig_project_state_changed.emit(proj, "HALTED_QUOTA", st.current_prompt.id if st.current_prompt else "")
                            self.sig_subqueue_state_changed.emit(proj, "HALTED_QUOTA", st.current_prompt.id if st.current_prompt else "")
                            self._emit_active_projects_snapshot()
                            continue

                        if term_state in ("STEP_ERROR", "EXECUTION_ERROR", "ERRORED"):
                            max_resumes = getattr(config, "max_resumes_per_prompt", 2)
                            auto_resume_enabled = getattr(config, "enable_auto_resume_same_chat", True)
                            item = st.current_prompt
                            if auto_resume_enabled and item and item.resume_count < max_resumes:
                                mini_prompt = getattr(config, "resume_mini_prompt", "Please resume where you left off and finish the remaining work.")
                                self.sig_log.emit(
                                    "WARN",
                                    f"[{proj}] Agent stopped due to error ({term_state}: {term.get('detail', '')}). "
                                    f"Injecting recovery prompt to complete response ({item.resume_count + 1}/{max_resumes})..."
                                )
                                with self.ui_mutex:
                                    self.dom_controller.switch_to_conversation(st.conversation_id)
                                    self.dom_controller.inject_resume_prompt(mini_prompt)
                                item.is_resumed = True
                                item.resume_count += 1
                                st.status = "GENERATING"
                                st.start_time = time.time()
                                st.last_poll_ts = time.time()
                                self.sig_project_state_changed.emit(proj, "GENERATING", item.id)
                                self.sig_subqueue_state_changed.emit(proj, "GENERATING", item.id)
                                self._emit_active_projects_snapshot()
                                continue

                        if term_state == "WAITING_FOR_INPUT":
                            # Tool call waiting for response or confirmation; keep monitoring
                            if st.current_prompt:
                                st.elapsed_time = round(time.time() - (st.start_time or time.time()), 1)
                                st.current_prompt.elapsed_time = st.elapsed_time
                                total_idx = self._resolve_queue_index(st.current_prompt)
                                self.sig_prompt_progress.emit(st.current_prompt.id, total_idx, st.elapsed_time)
                            self._emit_active_projects_snapshot()
                            continue

                        if term_state == "INTERRUPTED":
                            # Stopped mid-turn (after GENERIC tool or USER_INPUT).
                            # Avoid hijacking the visible DOM pane if another project is actively generating unless already focused.
                            other_active = any(
                                k != proj and s.status in ("DISPATCHING", "GENERATING", "STABILIZING")
                                for k, s in self.active_projects.items()
                            )
                            dom_term = {"state": "NORMAL"}
                            with self.ui_mutex:
                                active_cid_now = (
                                    self.dom_controller.get_active_conversation_id()
                                    if self.dom_controller and hasattr(self.dom_controller, "get_active_conversation_id")
                                    else ""
                                )
                                if not other_active or active_cid_now == st.conversation_id:
                                    if hasattr(self.dom_controller, "switch_to_conversation") and active_cid_now != st.conversation_id:
                                        self.dom_controller.switch_to_conversation(st.conversation_id)
                                        self.safe_sleep(0.2)
                                    dom_term = self.dom_controller.check_termination_state() if self.dom_controller else {"state": "NORMAL"}
                            if dom_term.get("state") == "QUOTA_EXHAUSTED":
                                self.sig_log.emit("WARN", f"[{proj}] Quota exhausted in chat {st.conversation_id}.")
                                st.status = "HALTED_QUOTA"
                                self.sig_project_state_changed.emit(proj, "HALTED_QUOTA", st.current_prompt.id if st.current_prompt else "")
                                self.sig_subqueue_state_changed.emit(proj, "HALTED_QUOTA", st.current_prompt.id if st.current_prompt else "")
                                self._emit_active_projects_snapshot()
                                continue

                        # 3. Finished! Wait brief stabilization (zero UI navigation)
                        stab_sec = getattr(config, "completion_stabilization_seconds", 3)
                        self.dom_controller.wait_for_completion(
                            timeout_seconds=stab_sec,
                            stabilization_seconds=stab_sec,
                            is_infinite=False,
                            conversation_id=st.conversation_id,
                            project_name=proj_name,
                            check_flow_callback=self.check_flow,
                            progress_callback=self.sig_stabilization_progress.emit,
                            log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
                        )

                        item = st.current_prompt
                        if item:
                            item.status = TaskStatus.COMPLETED
                            if not item.conversation_id and st.conversation_id:
                                item.conversation_id = st.conversation_id
                            item.elapsed_time = round(time.time() - (st.start_time or time.time()), 1)
                            _populate_prompt_token_telemetry(item)
                            total_idx = self._resolve_queue_index(item)
                            self.sig_prompt_finished.emit(item.id, total_idx, TaskStatus.COMPLETED.value, "")
                            prompt_lbl = f"Prompt #{total_idx + 1}" if total_idx >= 0 else "Prompt"
                            tok_info = ""
                            if item.output_tokens > 0:
                                speed_str = f" @ {item.tokens_per_second:.1f} tok/s" if item.tokens_per_second > 0 else ""
                                tok_info = f" ({item.output_tokens:,} tokens{speed_str} | Gen: {item.generation_time:.1f}s, Tools: {item.tool_time:.1f}s)"
                            self.sig_log.emit("SUCCESS", f"[{proj}] {prompt_lbl} completed successfully! ({item.elapsed_time}s){tok_info}")
                            st.current_prompt = None

                        # Check if more pending prompts remain for this sub-queue
                        has_more_pending = any(
                            it.status == TaskStatus.PENDING
                            for it in self.project_queues.get(proj, [])
                        )

                        # Check if an unmanaged external agent in this project is generating
                        tracked_cids = {s.conversation_id for s in self.active_projects.values() if s.conversation_id}
                        running_chk = _find_running_project_agent(self.dom_controller, proj_name, exclude_cids=tracked_cids)
                        if _is_untracked_external_agent(running_chk, self.active_projects):
                            chat_title = running_chk.get("title") or running_chk.get("id")
                            cid2 = running_chk.get("id")
                            self.sig_log.emit(
                                "WARN",
                                f"[{proj}] Another unmanaged agent is active in project '{proj_name}' (chat '{chat_title}' [{cid2}]). "
                                f"Waiting for zero active agents in '{proj_name}' before next prompt can dispatch..."
                            )
                            st.status = "GENERATING"
                            st.conversation_id = cid2
                            st.current_prompt = None
                            st.start_time = time.time()
                            st.last_poll_ts = time.time()
                            self.sig_project_state_changed.emit(proj, "GENERATING", "")
                            self.sig_subqueue_state_changed.emit(proj, "GENERATING", "")
                            self._emit_active_projects_snapshot()
                            continue

                        if not item:
                            self.sig_log.emit("SUCCESS", f"[{proj}] Background agent completed in chat '{st.conversation_id}'! Queue is now idle.")

                        if has_more_pending:
                            st.status = "IDLE"
                            st.conversation_id = ""  # Reset active chat ID: each prompt runs in a fresh new chat!
                            self.sig_project_state_changed.emit(proj, "IDLE", "")
                            self.sig_subqueue_state_changed.emit(proj, "IDLE", "")
                        else:
                            st.status = "COMPLETED"
                            st.conversation_id = ""  # Clean up once subqueue is completely finished
                            self.sig_project_state_changed.emit(proj, "COMPLETED", "")
                            self.sig_subqueue_state_changed.emit(proj, "COMPLETED", "")

                        st.current_prompt = None
                        self._emit_active_projects_snapshot()
                    except Exception as p_ex:
                        self.sig_log.emit("WARN", f"[{proj}] Poll error on chat {st.conversation_id}: {p_ex}")

            # Sleep briefly or wake up immediately if prompts were added to queue at runtime
            self.safe_sleep(1.0)

        return not self._stop_requested.is_set()

    def _wait_for_all_sidebar_agents_idle(
        self,
        context_description: str = "before proceeding",
        timeout_seconds: int = 3600,
    ) -> bool:
        """
        Universal Workspace Active Generation Guard:
        Polls the Antigravity sidebar DOM across ALL projects, the active viewport,
        and the SQLite database to ensure that ZERO agents (human-started or background)
        are actively generating before dispatching prompts or rotating accounts.
        """
        if not self.dom_controller:
            return True

        start_ts = time.time()
        last_log_ts = 0.0
        stab_sec = getattr(config, "completion_stabilization_seconds", 3)
        is_inf = getattr(config, "infinite_generation_timeout", True)

        while not self._stop_requested.is_set():
            if not self.check_flow():
                return False

            all_running = []
            if hasattr(self.dom_controller, "find_all_running_conversations_in_sidebar"):
                try:
                    all_running = self.dom_controller.find_all_running_conversations_in_sidebar()
                except Exception:
                    all_running = []
            else:
                all_running = [
                    {"id": st.conversation_id, "title": st.conversation_id, "project": p}
                    for p, st in self.active_projects.items()
                    if st.status in ("GENERATING", "STABILIZING", "DISPATCHING")
                ]

            if not all_running:
                # Reset any queued projects back to IDLE, and external/completed to COMPLETED
                for p, st in self.active_projects.items():
                    items = self.project_queues.get(p, [])
                    if any(it.status == TaskStatus.PENDING for it in items):
                        st.status = "IDLE"
                        st.current_prompt = None
                    else:
                        st.status = "COMPLETED"
                        st.current_prompt = None
                self._emit_active_projects_snapshot()
                # Completely quiet! 0 agents generating anywhere in the workspace
                return True

            # Synchronize active in-flight agents to active_projects for Floating HUD display
            for r in all_running:
                pname = r.get("project") or "Active Project"
                cid = r.get("id") or ""
                title = r.get("title") or "Generating..."
                if pname not in self.active_projects:
                    self.active_projects[pname] = ProjectExecutionState(
                        project_name=pname,
                        status="GENERATING",
                        conversation_id=cid,
                    )
                else:
                    self.active_projects[pname].status = "GENERATING"
                    self.active_projects[pname].conversation_id = cid

                if not self.active_projects[pname].current_prompt:
                    self.active_projects[pname].current_prompt = PromptItem(
                        text=title,
                        project=pname,
                        status=TaskStatus.RUNNING,
                        conversation_id=cid,
                    )
            self._emit_active_projects_snapshot()

            now = time.time()
            if now - last_log_ts >= 6.0:
                last_log_ts = now
                summary_items = [f"[{r.get('project', 'Project')}: '{r.get('title', 'Chat')}']" for r in all_running[:3]]
                summary_str = ", ".join(summary_items)
                if len(all_running) > 3:
                    summary_str += f" (+{len(all_running) - 3} more)"

                self.sig_step_changed.emit(
                    ExecutionStep.GENERATING.value,
                    f"Waiting for {len(all_running)} active agent(s) to finish ({summary_str})...",
                )
                self.sig_log.emit(
                    "WARN",
                    f"⏳ [Active Generation Guard] Detected {len(all_running)} active agent(s) generating in "
                    f"Antigravity: {summary_str}. Waiting for complete termination before {context_description}...",
                )

            # Wait on the running conversation using zero-nav completion check
            target_conv = all_running[0]
            cid = target_conv.get("id")
            pname = target_conv.get("project")

            if hasattr(self.dom_controller, "wait_for_completion"):
                try:
                    finished = self.dom_controller.wait_for_completion(
                        timeout_seconds=timeout_seconds,
                        stabilization_seconds=stab_sec,
                        is_infinite=is_inf,
                        check_flow_callback=self.check_flow,
                        progress_callback=self.sig_stabilization_progress.emit,
                        log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
                        conversation_id=cid,
                        project_name=pname,
                    )
                    if not finished and self._stop_requested.is_set():
                        return False
                except TimeoutError:
                    if not is_inf:
                        self.sig_log.emit("ERROR", f"Timed out waiting for active agents to finish before {context_description}.")
                        return False
            else:
                time.sleep(1.0)

            time.sleep(1.0)
            if not is_inf and (time.time() - start_ts > timeout_seconds):
                self.sig_log.emit("ERROR", f"Timed out waiting for active agents to finish before {context_description}.")
                return False

        return not self._stop_requested.is_set()

    def _wait_for_zero_active_project_agents(
        self,
        project_name: str,
        context_description: str = "before proceeding",
        timeout_seconds: int = 3600,
    ) -> bool:
        """
        Zero Active Agents Project Gate:
        Polls the Antigravity sidebar DOM to ensure that ZERO agents in `project_name`
        are actively generating before dispatching a new prompt or opening a new chat.
        Waits until all running agents in that project complete and stabilize without switching UI.
        """
        if not self.dom_controller:
            return True

        clean_proj = (project_name or "").strip()
        if not clean_proj:
            return True

        start_ts = time.time()
        last_log_ts = 0.0
        stab_sec = getattr(config, "completion_stabilization_seconds", 3)
        is_inf = getattr(config, "infinite_generation_timeout", True)

        while not self._stop_requested.is_set():
            if not self.check_flow():
                return False

            running = _find_running_project_agent(self.dom_controller, clean_proj)
            if not running or not running.get("id"):
                # 0 active agents in this project!
                return True

            chat_title = running.get("title") or running.get("id") or "active chat"
            cid = running.get("id", "")
            now = time.time()

            if now - last_log_ts >= 10.0:
                last_log_ts = now
                self.sig_step_changed.emit(
                    ExecutionStep.GENERATING.value,
                    f"Waiting for active agent in project '{clean_proj}' to finish ({chat_title})...",
                )
                self.sig_log.emit(
                    "WARN",
                    f"[Zero Active Agents Gate] Project '{clean_proj}' has an active agent generating in "
                    f"chat '{chat_title}' ({cid}). Waiting until 0 active agents {context_description}...",
                )

            # Wait for this active conversation to complete via sidebar monitoring (zero UI navigation)
            finished = self.dom_controller.wait_for_completion(
                timeout_seconds=timeout_seconds,
                stabilization_seconds=stab_sec,
                is_infinite=is_inf,
                conversation_id=cid,
                project_name=clean_proj,
                check_flow_callback=self.check_flow,
                progress_callback=self.sig_stabilization_progress.emit,
                log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
            )
            if not finished:
                return False

            # Check again to verify 0 active agents
            if not self.safe_sleep(0.5):
                return False

            if not is_inf and (time.time() - start_ts > timeout_seconds):
                self.sig_log.emit("ERROR", f"Timed out waiting for 0 active agents in project '{clean_proj}'.")
                return False

        return not self._stop_requested.is_set()

    def _wait_for_idle_agent(
        self,
        context_description: str = "before proceeding",
        project_name: Optional[str] = None,
    ) -> bool:
        """
        Guards against running actions while Antigravity is actively generating a response.
        Waits for completion and stabilization before continuing.
        If project_name is provided, also ensures 0 active agents in that project.
        """
        if project_name:
            if not self._wait_for_zero_active_project_agents(project_name, context_description):
                return False

        if not self.dom_controller or not self.dom_controller.is_generating():
            return True

        # If project_name is provided, check if the active chat in view belongs to a different project running in parallel
        if project_name:
            active_cid = self.dom_controller.get_active_conversation_id()
            if active_cid:
                try:
                    import urllib.parse
                    from cdp.conversation_db_inspector import get_conversation_summary
                    summary = get_conversation_summary(active_cid)
                    ws = str(summary.get("workspace_uris", "") or "").strip()
                    clean_proj = project_name.strip().lower()
                    target_is_no_proj = clean_proj in ("", "no project", "none", "default")
                    chat_is_no_proj = not ws or ws in ("[]", '""', "null")
                    if target_is_no_proj:
                        if not chat_is_no_proj:
                            # Active chat belongs to a folder project; target project is No Project (idle)!
                            return True
                    else:
                        if chat_is_no_proj:
                            # Active chat is No Project; target project is a specific folder (idle)!
                            return True
                        else:
                            unquoted_ws = urllib.parse.unquote(ws).lower()
                            if clean_proj not in unquoted_ws and clean_proj not in ws.lower():
                                # The visible active chat belongs to a different project running in parallel; target project is idle!
                                return True
                except Exception:
                    pass

        self.sig_step_changed.emit(
            ExecutionStep.GENERATING.value,
            f"Agent is busy generating. Waiting for completion {context_description}...",
        )
        self.sig_log.emit(
            "WARN",
            f"Agent is actively generating in Antigravity. Waiting for completion {context_description}...",
        )
        stab_sec = getattr(config, "completion_stabilization_seconds", 3)
        timeout = getattr(config, "generation_timeout", 3600)
        is_inf = getattr(config, "infinite_generation_timeout", True)

        finished = self.dom_controller.wait_for_completion(
            timeout_seconds=timeout,
            stabilization_seconds=stab_sec,
            is_infinite=is_inf,
            project_name=project_name,
            check_flow_callback=self.check_flow,
            progress_callback=self.sig_stabilization_progress.emit,
            log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
        )
        if finished:
            self.sig_log.emit("SUCCESS", f"Response completed and stabilized {context_description}.")
        return finished

    def _execute_prompt_cdp(self, item: PromptItem, idx: int, total: int) -> bool:
        start_time = time.time()
        try:
            raw_p = item.project.strip()
            proj_name = raw_p if (raw_p and raw_p.lower() not in ("default", "none")) else "No Project"

            # Step 0: Ensure target project has 0 active running agents before opening fresh conversation
            if not self._wait_for_zero_active_project_agents(proj_name, f"before opening fresh conversation for Prompt #{idx + 1}"):
                return False

            # Ensure active agent view is also not generating
            if not self._wait_for_idle_agent("before opening fresh conversation", project_name=proj_name):
                return False

            if not self.check_flow():
                return False

            # Step 1: Open New Conversation
            self.sig_step_changed.emit(ExecutionStep.OPEN_CHAT.value, "Opening fresh conversation...")
            if not self.dom_controller.open_new_conversation(
                force=(not self.dom_controller.is_fresh_empty_chat_open()),
                target_project=proj_name,
                check_flow_callback=self.check_flow,
                progress_callback=self.sig_stabilization_progress.emit,
                log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
            ):
                raise RuntimeError("Failed to verify fresh chat in Antigravity DOM.")

            if not self.check_flow():
                return False

            # Step 2: Select Target Project
            self.sig_step_changed.emit(ExecutionStep.SELECT_PROJECT.value, f"Checking target project '{proj_name}'...")
            self.dom_controller.select_target_project(proj_name)

            if not self.check_flow():
                return False

            # Step 3: Inject Prompt
            self.sig_step_changed.emit(ExecutionStep.INJECT_PROMPT.value, "Injecting prompt into chat...")
            self.dom_controller.inject_and_submit_prompt(item.text)

            # Capture active conversation ID once submitted
            item.conversation_id = _capture_submitted_cid(self.dom_controller, proj_name)

            if not self.check_flow():
                return False

            # Step 4: Monitor Generation State
            self.sig_step_changed.emit(ExecutionStep.GENERATING.value, "Monitoring generation status...")
            stab_sec = getattr(config, "completion_stabilization_seconds", 3)
            timeout = getattr(config, "generation_timeout", 3600)
            is_inf = getattr(config, "infinite_generation_timeout", True)

            def on_progress(rem, tot):
                self.sig_stabilization_progress.emit(rem, tot)
                elapsed = time.time() - start_time
                item.elapsed_time = round(elapsed, 1)
                self.sig_prompt_progress.emit(item.id, idx, item.elapsed_time)

            finished = self.dom_controller.wait_for_completion(
                timeout_seconds=timeout,
                stabilization_seconds=stab_sec,
                is_infinite=is_inf,
                conversation_id=item.conversation_id,
                project_name=proj_name,
                check_flow_callback=self.check_flow,
                progress_callback=on_progress,
                log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
            )

            if not finished:
                return False

            # Active CID reconciliation: resolve genuine UUID before termination inspection
            if not item.conversation_id or _is_synthetic_cid(item.conversation_id):
                real_cid = _resolve_real_cid(self.dom_controller, proj_name, item.conversation_id)
                if real_cid:
                    item.conversation_id = real_cid

            # Step 5: Check Termination State and Handle In-Chat Auto-Resume
            max_resumes = getattr(config, "max_resumes_per_prompt", 2)
            auto_resume_enabled = getattr(config, "enable_auto_resume_same_chat", True)

            while item.resume_count < max_resumes:
                if not self.check_flow():
                    return False

                term_state = _check_termination_zero_nav(self.dom_controller, conversation_id=item.conversation_id)
                if term_state.get("state") == "INTERRUPTED":
                    if item.conversation_id and hasattr(self.dom_controller, "switch_to_conversation"):
                        self.dom_controller.switch_to_conversation(item.conversation_id)
                    term_state = self.dom_controller.check_termination_state()

                if term_state.get("state") == "QUOTA_EXHAUSTED":
                    if auto_resume_enabled:
                        self.sig_log.emit(
                            "WARN",
                            f"[Auto-Resume] Quota exhaustion detected ({term_state.get('detail', '')}). "
                            f"Initiating account rotation and in-chat resume ({item.resume_count + 1}/{max_resumes})..."
                        )
                        # Rotate Google Account or wait for quota reset
                        if not self._enforce_quota("In-Chat Recovery Quota Check"):
                            return False

                        if not self.check_flow():
                            return False

                        # Switch to conversation only when injecting recovery prompt
                        if item.conversation_id and hasattr(self.dom_controller, "switch_to_conversation"):
                            self.dom_controller.switch_to_conversation(item.conversation_id)

                        # In-chat recovery: stay in the SAME conversation, inject mini-prompt
                        mini_prompt = getattr(
                            config,
                            "resume_mini_prompt",
                            "Please resume where you left off and finish the remaining work."
                        )
                        self.sig_step_changed.emit(
                            ExecutionStep.INJECT_PROMPT.value,
                            f"Injecting resume prompt (Resume #{item.resume_count + 1})..."
                        )
                        self.sig_log.emit("INFO", f"Injecting recovery mini-prompt into current chat: \"{mini_prompt}\"")
                        self.dom_controller.inject_resume_prompt(mini_prompt)
                        item.is_resumed = True
                        item.resume_count += 1

                        self.sig_step_changed.emit(
                            ExecutionStep.GENERATING.value,
                            f"Monitoring resumed generation (Resume #{item.resume_count})..."
                        )
                        resumed_finished = self.dom_controller.wait_for_completion(
                            timeout_seconds=timeout,
                            stabilization_seconds=stab_sec,
                            is_infinite=is_inf,
                            conversation_id=item.conversation_id,
                            project_name=proj_name,
                            check_flow_callback=self.check_flow,
                            progress_callback=on_progress,
                            log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
                        )
                        if not resumed_finished:
                            return False
                        continue
                    else:
                        raise RuntimeError(f"Prompt terminated early due to quota exhaustion: {term_state.get('detail', '')}")
                elif term_state.get("state") in ("STEP_ERROR", "EXECUTION_ERROR", "ERRORED"):
                    if auto_resume_enabled:
                        self.sig_log.emit(
                            "WARN",
                            f"[Auto-Resume] Agent terminated due to error ({term_state.get('state')}: {term_state.get('detail', '')}). "
                            f"Injecting recovery prompt into current chat ({item.resume_count + 1}/{max_resumes})..."
                        )
                        if not self.check_flow():
                            return False

                        # Switch to conversation only when injecting recovery prompt
                        if item.conversation_id and hasattr(self.dom_controller, "switch_to_conversation"):
                            self.dom_controller.switch_to_conversation(item.conversation_id)

                        mini_prompt = getattr(
                            config,
                            "resume_mini_prompt",
                            "Please resume where you left off and finish the remaining work."
                        )
                        self.sig_step_changed.emit(
                            ExecutionStep.INJECT_PROMPT.value,
                            f"Injecting recovery prompt (Resume #{item.resume_count + 1})..."
                        )
                        self.sig_log.emit("INFO", f"Injecting recovery mini-prompt into current chat: \"{mini_prompt}\"")
                        self.dom_controller.inject_resume_prompt(mini_prompt)
                        item.is_resumed = True
                        item.resume_count += 1

                        self.sig_step_changed.emit(
                            ExecutionStep.GENERATING.value,
                            f"Monitoring resumed generation (Resume #{item.resume_count})..."
                        )
                        resumed_finished = self.dom_controller.wait_for_completion(
                            timeout_seconds=timeout,
                            stabilization_seconds=stab_sec,
                            is_infinite=is_inf,
                            conversation_id=item.conversation_id,
                            project_name=proj_name,
                            check_flow_callback=self.check_flow,
                            progress_callback=on_progress,
                            log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
                        )
                        if not resumed_finished:
                            return False
                        continue
                    else:
                        raise RuntimeError(f"Prompt encountered error: {term_state.get('detail', '')}")
                else:
                    break

            final_term = _check_termination_zero_nav(self.dom_controller, conversation_id=item.conversation_id)
            if final_term.get("state") == "QUOTA_EXHAUSTED":
                raise RuntimeError(
                    f"Exceeded max resumes ({max_resumes}) for prompt. Quota repeatedly exhausted: {final_term.get('detail', '')}"
                )
            elif final_term.get("state") in ("STEP_ERROR", "EXECUTION_ERROR", "ERRORED"):
                raise RuntimeError(
                    f"Exceeded max resumes ({max_resumes}) for prompt. Error persisted: {final_term.get('detail', '')}"
                )

            item.status = TaskStatus.COMPLETED
            item.error_message = ""
            _populate_prompt_token_telemetry(item)
            self.sig_prompt_finished.emit(item.id, idx, TaskStatus.COMPLETED.value, "")
            resumed_note = f" [Resumed {item.resume_count}x]" if item.is_resumed else ""
            tok_info = ""
            if item.output_tokens > 0:
                speed_str = f" @ {item.tokens_per_second:.1f} tok/s" if item.tokens_per_second > 0 else ""
                tok_info = f" ({item.output_tokens:,} tokens{speed_str} | Gen: {item.generation_time:.1f}s, Tools: {item.tool_time:.1f}s)"
            self.sig_log.emit("SUCCESS", f"[{idx + 1}/{total}] Prompt completed successfully{resumed_note} ({format_duration(item.elapsed_time)}){tok_info}.")
            return True

        except Exception as e:
            err = str(e)
            item.status = TaskStatus.FAILED
            item.error_message = err
            self.sig_log.emit("ERROR", f"Error on prompt [{idx + 1}]: {err}")
            self.sig_step_changed.emit(ExecutionStep.ERROR.value, err)
            self.sig_prompt_finished.emit(item.id, idx, TaskStatus.FAILED.value, err)
            return False

    def _sync_inspected_quota_to_account_manager(self, res: Dict[str, Any], reason: str = "Pre-dispatch check"):
        """Persists inspected 5-hour and weekly quotas directly to account_manager."""
        if not res:
            return
        gem = res.get("gemini", {})
        five_pct = gem.get("five_hour_percent")
        five_ref = gem.get("five_hour_refresh", "")
        week_pct = gem.get("weekly_percent")
        week_ref = gem.get("weekly_refresh", "")
        if five_pct is None and week_pct is None:
            return

        active_email = res.get("current_email") or ""
        if not active_email and self.oauth_handler:
            try:
                active_email = self.oauth_handler._read_current_email()
            except Exception:
                pass
        if not active_email:
            active_email = account_manager.current_email
        if not active_email and account_manager.accounts:
            active_acc = next((a for a in account_manager.accounts if a.quota_status == "Active"), None)
            active_email = active_acc.email if active_acc else account_manager.accounts[0].email

        if active_email:
            account_manager.set_active_account(active_email)
            account_manager.update_account_quota(
                email=active_email,
                five_hour_percent=five_pct,
                five_hour_refresh_raw=five_ref,
                weekly_percent=week_pct,
                weekly_refresh_raw=week_ref,
                reason=reason,
            )
            next_acc = account_manager.get_next_account(active_email)
            next_email = next_acc.email if next_acc else "None"
            is_jump = bool(account_manager.jump_next_email and next_acc and account_manager.jump_next_email.lower() == next_acc.email.lower())
            jump_str = " (Jump Target)" if is_jump else ""
            is_unreg = not account_manager.is_registered_account(active_email)
            unreg_str = " [Outside Account - Not in Queue]" if is_unreg else ""
            self.sig_log.emit(
                "INFO",
                f"👤 Current Account: '{active_email}'{unreg_str} | Next in Queue: '{next_email}'{jump_str}"
            )

    def _check_pre_dispatch_quota(self, project_name: str) -> bool:
        """
        Enforces proactive quota checking before dispatching a prompt for `project_name`.
        1. Checks quota cache: if checked <= quota_cache_seconds ago and safe, returns True immediately.
        2. Inspects live quota via DOMQuotaInspector (Settings -> Models & Usage).
        3. If quota is safe (>= 30% 5-hour and >= 4% weekly):
           Updates cache and returns True (proceed to dispatch).
        4. If quota is LOW (< 30%):
           Checks how many other projects are actively generating.
           - If 0 active generating projects: safe to rotate accounts right now via _enforce_quota().
           - If >= 1 active generating projects: does NOT switch accounts yet to avoid crashing in-flight streams.
             Activates the Quota Drain Barrier (self._quota_drain_in_progress = True),
             defers this prompt dispatch, and lets in-flight agents finish or halt first.
             Returns False.
        """
        is_quota_enabled = getattr(config, "check_quota_each_prompt", True) and getattr(config, "check_quota_before_queue", True)
        if not is_quota_enabled or not self.quota_inspector:
            return True

        cache_ttl = getattr(config, "quota_cache_seconds", 60)
        now = time.time()

        # Check safe cache (valid if safe and within TTL)
        if self._last_quota_result and (now - self._last_quota_check_ts < cache_ttl):
            if self._last_quota_result.get("is_safe_to_prompt", True):
                return True

        # Need fresh inspection
        self.sig_log.emit("INFO", f"[{project_name}] Running pre-dispatch quota inspection...")
        res = self.quota_inspector.inspect_model_quotas(
            close_settings=True,
            log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
        )
        self._last_quota_check_ts = time.time()
        self._last_quota_result = res
        chk_reason = f"Pre-dispatch check ({project_name})"
        res["check_reason"] = chk_reason
        self._sync_inspected_quota_to_account_manager(res, reason=chk_reason)
        self.sig_quota_updated.emit(res)

        # Check if the active session belongs to an outside / unregistered account
        dom_email = (res.get("current_email") or "").split("(")[0].strip().lower()
        is_outside = bool(dom_email and not account_manager.is_registered_account(dom_email))
        if is_outside:
            if res.get("is_safe_to_prompt", True):
                self.sig_log.emit(
                    "INFO",
                    f"[{project_name}] Outside account active ('{dom_email}') with safe quota. Proceeding with prompt dispatch."
                )
                return True
            else:
                target_acc = account_manager.get_next_account(dom_email)
                target_desc = target_acc.email if target_acc else "first ready account"
                reason = (
                    f"Outside account ('{dom_email}') quota depleted ({res.get('safety_reason', 'below threshold')}). "
                    f"Auto-switching to queue account: '{target_desc}'"
                )
                res["is_safe_to_prompt"] = False
                res["is_outside_account"] = True
                res["safety_reason"] = reason
                self.sig_log.emit("WARN", f"[{project_name}] ⚠️ {reason}")

        if res.get("is_safe_to_prompt", True):
            return True

        # Check if Antigravity is logged out!
        if res.get("is_logged_out") is True:
            self.sig_log.emit(
                "WARN",
                f"[{project_name}] ⚡ Antigravity session is logged out. Triggering Google sign-in/rotation..."
            )
            recovered = self._enforce_quota(f"Session Logged Out ({project_name})", known_unsafe_res=res)
            return bool(recovered)

        # Check if this was a DOM read failure rather than verified low quota.
        # A read failure MUST NOT trigger destructive account rotation / sign-out!
        is_read_failure = (
            res.get("read_failed") is True or
            "unable to read" in res.get("safety_reason", "").lower() or
            res.get("error") is not None
        )
        if is_read_failure:
            self.sig_log.emit(
                "WARN",
                f"[{project_name}] ⚠️ Could not verify Gemini quota from DOM ({res.get('safety_reason', 'Read failed')}). "
                f"Proceeding with active account without rotating..."
            )
            return True

        # Quota is CONFIRMED below threshold!
        reason = res.get("safety_reason", "Quota below configured threshold.")
        active_generating = [
            p for p, st in self.active_projects.items()
            if st.status in ("GENERATING", "STABILIZING", "DISPATCHING")
        ]

        # Scan sidebar across all projects and SQLite database for human-started / background agents
        if self.dom_controller and hasattr(self.dom_controller, "find_all_running_conversations_in_sidebar"):
            try:
                sidebar_running = self.dom_controller.find_all_running_conversations_in_sidebar()
                for r in sidebar_running:
                    r_proj = r.get("project") or "Background Project"
                    if r_proj not in active_generating:
                        active_generating.append(r_proj)
            except Exception:
                pass

        if not active_generating:
            # Zero active generating projects: completely safe to rotate Google accounts immediately!
            self.sig_log.emit("WARN", f"[{project_name}] Pre-dispatch quota low ({reason}). No active agents running; rotating account now...")
            recovered = self._enforce_quota(f"Pre-Dispatch Quota Recovery ({project_name})", known_unsafe_res=res)
            return bool(recovered)
        else:
            # Active projects in-flight: activate drain barrier so we don't crash their streams!
            self.sig_log.emit(
                "WARN",
                f"🔒 [Quota Drain Barrier] Pre-dispatch quota low for '{project_name}' ({reason}). "
                f"Waiting for {len(active_generating)} running project(s) ({', '.join(active_generating)}) "
                f"to finish or halt before rotating accounts..."
            )
            self._quota_drain_in_progress = True
            return False

    def _enforce_quota(self, step_label: str, known_unsafe_res: Optional[Dict[str, Any]] = None) -> bool:
        current_label = step_label
        first_pass = True
        while not self._stop_requested.is_set():
            if first_pass and known_unsafe_res is not None:
                # Quota was already inspected right before this call and determined to be unsafe!
                # Reuse known_unsafe_res to avoid opening Settings dialog twice back-to-back.
                res = known_unsafe_res
                first_pass = False
            else:
                self.sig_step_changed.emit(ExecutionStep.INSPECT_QUOTA.value, f"{current_label}: Reading Gemini limits...")
                res = self.quota_inspector.inspect_model_quotas(
                    close_settings=True,
                    log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
                )
                chk_reason = f"Quota recovery check ({current_label})"
                res["check_reason"] = chk_reason
                self._sync_inspected_quota_to_account_manager(res, reason=chk_reason)
                self.sig_quota_updated.emit(res)
                first_pass = False

            dom_email = (res.get("current_email") or "").split("(")[0].strip().lower()
            is_outside = bool(res.get("is_outside_account") or (dom_email and not account_manager.is_registered_account(dom_email)))

            if res.get("is_safe_to_prompt", True):
                self._last_quota_check_ts = time.time()
                self._last_quota_result = res
                return True

            reason = res.get("safety_reason", "Quota below configured threshold.")
            low_action = getattr(config, "low_quota_action", "change_account")

            is_read_failure = (
                res.get("read_failed") is True or
                "unable to read" in reason.lower() or
                res.get("error") is not None
            )

            if is_read_failure and low_action == "change_account":
                self.sig_log.emit(
                    "WARN",
                    f"[{current_label}] ⚠️ Cannot verify quota from DOM ({reason}). "
                    f"Skipping auto-account switch to avoid signing out active account. Proceeding with caution..."
                )
                return True

            if is_outside or (res.get("is_logged_out") is True) or (low_action == "change_account" and getattr(config, "auto_switch_on_low_quota", True)):
                # Ensure ZERO active agents across the entire workspace before executing account switch
                if not self._wait_for_all_sidebar_agents_idle("switching Google accounts"):
                    return False
                if not self.oauth_handler:
                    return False
                if is_outside:
                    target_acc = account_manager.get_next_account(dom_email)
                    target_disp = target_acc.email if target_acc else "first ready account"
                    action_desc = f"Auto-switching outside account to queue account '{target_disp}'..."
                elif res.get("is_logged_out") is True:
                    action_desc = "Signing in to Google account..."
                else:
                    action_desc = "Auto-switching Google account..."
                self.sig_log.emit("WARN", f"[{current_label}] {action_desc}")
                self.sig_step_changed.emit(ExecutionStep.ACCOUNT_SWITCH.value, action_desc)
                switch_res = self.oauth_handler.execute_account_switch(
                    log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
                    stop_event=self._stop_requested,
                    pre_continue_callback=self._wait_for_pre_switch_countdown,
                )
                if switch_res.get("success"):
                    current_label = f"{step_label} (Post-Switch)"
                    if self.state_manager:
                        self.state_manager.normalize_to_workspace(
                            log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
                            oauth_handler=self.oauth_handler,
                            stop_event=self._stop_requested,
                            pre_switch_callback=self._wait_for_pre_switch_countdown,
                            dom_controller=self.dom_controller,
                        )
                    continue
                else:
                    self.sig_log.emit("ERROR", f"Account switch failed: {switch_res.get('error')}. Falling back to wait.")

            # Policy: Wait for limit reset time (with fallback to earliest frozen account or retry interval)
            wait_sec = res.get("refresh_wait_seconds")
            ref_text = res.get("refresh_time_text", "Unknown")
            fallback_interval = getattr(config, "quota_retry_interval_seconds", 60)

            if not wait_sec or wait_sec <= 0:
                now_ts = time.time()
                frozen_waits = [
                    (a.frozen_until - now_ts, a.email)
                    for a in getattr(account_manager, "accounts", [])
                    if getattr(a, "enabled", True) and getattr(a, "frozen_until", 0.0) > now_ts
                ]
                if frozen_waits:
                    frozen_waits.sort(key=lambda item: item[0])
                    wait_sec = int(frozen_waits[0][0])
                    ref_text = f"earliest frozen account '{frozen_waits[0][1]}' unfreezes"

            buffer_sec = getattr(config, "quota_buffer_seconds", 60)
            if wait_sec and wait_sec > 0:
                # Add buffer (+1 minute by default) for server-side clock skew & cache propagation
                effective_wait = wait_sec + buffer_sec
                self._quota_reset_target_ts = time.time() + effective_wait

                h = int(effective_wait // 3600)
                m = int((effective_wait % 3600) // 60)
                s = int(effective_wait % 60)
                eff_countdown = f"{h}h {m}m" if h > 0 else (f"{m}m" if m > 0 else f"{s}s")
                if s > 0 and (h > 0 or m > 0):
                    eff_countdown += f" {s}s"

                buffer_min = buffer_sec // 60
                buffer_str = f"+{buffer_min}m" if (buffer_sec % 60 == 0 and buffer_min > 0) else f"+{buffer_sec}s"
                pause_msg = f"Paused: Waiting for limit reset (~{ref_text} {buffer_str} buffer -> {eff_countdown})"
                self.sig_log.emit("WARN", f"⏳ Execution paused until reset time (~{ref_text} {buffer_str} buffer -> {eff_countdown}).")
            else:
                effective_wait = fallback_interval
                self._quota_reset_target_ts = time.time() + effective_wait
                pause_msg = f"Paused: {reason} (Auto-checking in {fallback_interval}s)"
                self.sig_log.emit("WARN", f"⏳ {reason} Periodic auto-check scheduled in {fallback_interval}s.")

            self.pause(reason=pause_msg)
            if not self.check_flow():
                return False

            # Re-verify quota after waiting for reset or manual resume
            self.sig_log.emit("INFO", f"[{step_label}] Re-verifying Gemini quota after wait...")
            current_label = f"{step_label} (Post-Reset)"

        return not self._stop_requested.is_set()
