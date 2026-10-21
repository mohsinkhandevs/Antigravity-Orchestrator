import os
import shutil
import json
import uuid
import re
import threading
from enum import Enum
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple


class TaskStatus(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    COMPLETED = "Completed"
    FAILED = "Failed"
    SKIPPED = "Skipped"
    CANCELLED = "Cancelled"


class ExecutionStep(str, Enum):
    IDLE = "Idle"
    FOCUS_WINDOW = "Focusing Antigravity Window"
    CONNECTING = "Connecting to Antigravity (CDP)"
    INSPECT_QUOTA = "Inspecting Gemini Limits"

    OPEN_CHAT = "Opening New Chat (+)"
    SELECT_PROJECT = "Selecting Target Project"
    INJECT_PROMPT = "Injecting Prompt"
    GENERATING = "Generating Response"
    STABILIZING = "Stabilization Verification"
    COOLDOWN = "Cooldown / Post-Delay"
    COMPLETED = "Prompt Completed"
    ACCOUNT_SWITCH = "Switching Google Account"
    PAUSED = "Paused"
    ERROR = "Error Encountered"


@dataclass
class PromptItem:
    text: str
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    sequence: int = 0
    name: str = ""
    status: TaskStatus = TaskStatus.PENDING
    elapsed_time: float = 0.0
    error_message: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    project: str = ""
    queue_name: str = "Default"
    conversation_id: str = ""
    is_resumed: bool = False
    resume_count: int = 0
    output_tokens: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    generation_time: float = 0.0
    tool_time: float = 0.0
    tokens_per_second: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    def duplicate(self) -> "PromptItem":
        """Creates a duplicated copy of this prompt reset to Pending status with fresh ID."""
        return PromptItem(
            text=self.text,
            name=self.name,
            project=self.project,
            queue_name=self.queue_name,
            conversation_id="",
            status=TaskStatus.PENDING,
            elapsed_time=0.0,
            error_message="",
            is_resumed=False,
            resume_count=0,
            output_tokens=0,
            input_tokens=0,
            cache_read_tokens=0,
            generation_time=0.0,
            tool_time=0.0,
            tokens_per_second=0.0,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PromptItem":
        status_val = data.get("status", TaskStatus.PENDING.value)
        try:
            status = TaskStatus(status_val)
        except ValueError:
            status = TaskStatus.PENDING

        raw_id = str(data.get("id", "")).strip()
        name = str(data.get("name", "")).strip()
        seq = int(data.get("sequence", 0))

        if raw_id and name and not raw_id.endswith(name):
            unique_id = f"{raw_id}-{name}"
        elif raw_id:
            unique_id = raw_id
        else:
            unique_id = str(uuid.uuid4())[:8]

        return cls(
            text=data.get("text", ""),
            id=unique_id,
            sequence=seq,
            name=name,
            status=status,
            elapsed_time=float(data.get("elapsed_time", 0.0) or 0.0),
            error_message=data.get("error_message", ""),
            created_at=data.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            project=str(data.get("project", "")).strip(),
            queue_name=str(data.get("queue_name", "Default")).strip() or "Default",
            conversation_id=str(data.get("conversation_id", "")).strip(),
            is_resumed=bool(data.get("is_resumed", False)),
            resume_count=int(data.get("resume_count", 0) or 0),
            output_tokens=int(data.get("output_tokens", 0) or 0),
            input_tokens=int(data.get("input_tokens", 0) or 0),
            cache_read_tokens=int(data.get("cache_read_tokens", 0) or 0),
            generation_time=float(data.get("generation_time", 0.0) or 0.0),
            tool_time=float(data.get("tool_time", 0.0) or 0.0),
            tokens_per_second=float(data.get("tokens_per_second", 0.0) or 0.0),
        )


class ThreadSafePromptQueue(list):
    """
    Thread-safe prompt queue container backed by a recursive lock (RLock).
    Inherits from list for complete backward compatibility with PyQt table
    models, list indexing, and existing test suites.
    """

    def __init__(self, iterable=None):
        super().__init__(iterable or [])
        self.lock = threading.RLock()

    def snapshot(self) -> List[PromptItem]:
        """Returns an atomic shallow copy of the prompts list under lock."""
        with self.lock:
            return super().copy()

    def copy(self) -> List[PromptItem]:
        with self.lock:
            return super().copy()

    def __enter__(self):
        self.lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.lock.release()

    def append(self, item: PromptItem):
        with self.lock:
            super().append(item)

    def extend(self, iterable):
        with self.lock:
            super().extend(iterable)

    def insert(self, index: int, item: PromptItem):
        with self.lock:
            super().insert(index, item)

    def remove(self, item: PromptItem):
        with self.lock:
            super().remove(item)

    def pop(self, index: int = -1) -> PromptItem:
        with self.lock:
            return super().pop(index)

    def clear(self):
        with self.lock:
            super().clear()

    def __getitem__(self, item):
        with self.lock:
            return super().__getitem__(item)

    def __setitem__(self, key, value):
        with self.lock:
            super().__setitem__(key, value)

    def __delitem__(self, key):
        with self.lock:
            super().__delitem__(key)

    def __len__(self) -> int:
        with self.lock:
            return super().__len__()

    def __iter__(self):
        with self.lock:
            items_copy = super().copy()
        return iter(items_copy)

    def get_by_id(self, prompt_id: str) -> Optional[PromptItem]:
        clean_id = (prompt_id or "").strip()
        with self.lock:
            for p in self:
                if p.id == clean_id:
                    return p
        return None

    def index_by_id(self, prompt_id: str) -> int:
        clean_id = (prompt_id or "").strip()
        with self.lock:
            for idx, p in enumerate(self):
                if p.id == clean_id:
                    return idx
        return -1

    def update_item_status(
        self,
        prompt_id: str,
        status: TaskStatus,
        elapsed_time: Optional[float] = None,
        error_message: Optional[str] = None,
        conversation_id: Optional[str] = None,
        output_tokens: Optional[int] = None,
        input_tokens: Optional[int] = None,
        cache_read_tokens: Optional[int] = None,
        generation_time: Optional[float] = None,
        tool_time: Optional[float] = None,
        tokens_per_second: Optional[float] = None,
    ) -> bool:
        with self.lock:
            item = self.get_by_id(prompt_id)
            if not item:
                return False
            item.status = status
            if elapsed_time is not None:
                item.elapsed_time = elapsed_time
            if error_message is not None:
                item.error_message = error_message
            if conversation_id is not None:
                item.conversation_id = conversation_id
            if output_tokens is not None:
                item.output_tokens = output_tokens
            if input_tokens is not None:
                item.input_tokens = input_tokens
            if cache_read_tokens is not None:
                item.cache_read_tokens = cache_read_tokens
            if generation_time is not None:
                item.generation_time = generation_time
            if tool_time is not None:
                item.tool_time = tool_time
            if tokens_per_second is not None:
                item.tokens_per_second = tokens_per_second
            return True


def strip_prompt_command(text: str) -> Tuple[str, str]:
    """Extracts leading slash command (/goal, /plan, \\goal, \\plan) from prompt text without eating markdown bullets or file paths."""
    clean = text.strip()
    if not clean:
        return "", ""
    m = re.match(r'^\s*([/\\](?:goal|plan))\b[ \t]*[:\-]?[ \t]*\r?\n?(.*)$', clean, re.DOTALL | re.IGNORECASE)
    if m:
        cmd = m.group(1).replace("\\", "/").lower()
        body = m.group(2).strip()
        return cmd, body
    return "", clean


def detect_prompt_mode(text: str) -> str:
    cmd, _ = strip_prompt_command(text)
    if cmd in ("/goal", "/plan"):
        return cmd
    return "none"


def ensure_prompt_prefix(text: str, mode: str = "/goal") -> str:
    clean = text.strip()
    if not clean:
        return clean

    mode_norm = (mode or "").strip().lower().replace("\\", "/")
    cmd, body = strip_prompt_command(clean)

    if mode_norm == "keep":
        if cmd in ("/goal", "/plan"):
            return f"{cmd} {body}" if body else cmd
        return clean

    if mode_norm in ("/goal", "goal"):
        return f"/goal {body}" if body else "/goal"
    elif mode_norm in ("/plan", "plan"):
        return f"/plan {body}" if body else "/plan"
    elif mode_norm in ("none", "", "plain", "raw"):
        return body
    else:
        prefix = mode_norm if mode_norm.startswith("/") else f"/{mode_norm}"
        return f"{prefix} {body}" if body else prefix


def ensure_goal_prefix(text: str) -> str:
    return ensure_prompt_prefix(text, "/goal")


def ensure_plan_prefix(text: str) -> str:
    return ensure_prompt_prefix(text, "/plan")


def _resolve_prefix_mode(prefix_mode: str, auto_add_goal_prefix: Optional[bool] = None) -> str:
    if auto_add_goal_prefix is not None:
        return "/goal" if auto_add_goal_prefix else "keep"
    return prefix_mode or "keep"


def import_prompts_from_txt(
    filepath: str,
    prefix_mode: str = "keep",
    auto_add_goal_prefix: Optional[bool] = None,
    project: str = "",
    queue_name: str = "Default",
) -> List[PromptItem]:
    mode = _resolve_prefix_mode(prefix_mode, auto_add_goal_prefix)
    items = []
    clean_proj = (project or "").strip()
    clean_queue = (queue_name or "").strip() or "Default"
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line_str = line.strip()
            if line_str and not line_str.startswith("#"):
                t = ensure_prompt_prefix(line_str, mode)
                items.append(PromptItem(text=t, project=clean_proj, queue_name=clean_queue))
    return items


def export_prompts_to_txt(items: List[PromptItem], filepath: str) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        for item in items:
            f.write(f"{item.text}\n")


def get_queue_backup_path(filepath: str) -> str:
    """Returns the redundant backup file path for queue.json."""
    dir_name = os.path.dirname(filepath) or "."
    return os.path.join(dir_name, "queue.backup.json")


def _is_production_queue_file(filepath: str) -> bool:
    """Checks whether the filepath refers to the user's active production queue.json."""
    try:
        from app.config import get_user_data_dir
        prod_path = os.path.abspath(os.path.join(get_user_data_dir(), "queue.json"))
        return os.path.abspath(filepath) == prod_path
    except Exception:
        return False


def import_prompts_from_json(
    filepath: str,
    prefix_mode: str = "keep",
    auto_add_goal_prefix: Optional[bool] = None,
    project: str = "",
    queue_name: str = "Default",
) -> List[PromptItem]:
    mode = _resolve_prefix_mode(prefix_mode, auto_add_goal_prefix)
    clean_proj = (project or "").strip()
    clean_queue = (queue_name or "").strip() or "Default"
    data = None

    # Priority 1: Main queue.json
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
        except Exception as e:
            print(f"[WARN] Failed to load {filepath}: {e}")

    # Priority 2: Auto-recover from queue.backup.json if main file is missing/corrupted
    if data is None:
        backup_path = get_queue_backup_path(filepath)
        if os.path.exists(backup_path):
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
                        print(f"[INFO] Recovered prompt queue from backup: {backup_path}")
                        try:
                            shutil.copy2(backup_path, filepath)
                        except Exception:
                            pass
            except Exception as be:
                print(f"[WARN] Failed to load backup queue file: {be}")

    if not data:
        return []

    items = []
    seen_ids = set()

    def format_text(raw_text: str) -> str:
        return ensure_prompt_prefix(raw_text, mode)

    def add_item(itm: PromptItem, idx: int):
        if not itm.sequence:
            itm.sequence = idx
        if not itm.project and clean_proj:
            itm.project = clean_proj
        if (not getattr(itm, "queue_name", "") or itm.queue_name == "Default") and clean_queue != "Default":
            itm.queue_name = clean_queue
        if itm.id in seen_ids:
            itm.id = f"{itm.id}_{idx}_{str(uuid.uuid4())[:4]}"
        seen_ids.add(itm.id)
        items.append(itm)

    if isinstance(data, list):
        for idx, entry in enumerate(data, 1):
            if isinstance(entry, str):
                if entry.strip():
                    add_item(PromptItem(text=format_text(entry), sequence=idx, project=clean_proj, queue_name=clean_queue), idx)
            elif isinstance(entry, dict):
                item = PromptItem.from_dict(entry)
                item.text = format_text(item.text)
                add_item(item, idx)
    elif isinstance(data, dict) and "prompts" in data:
        for idx, entry in enumerate(data["prompts"], 1):
            if isinstance(entry, str):
                if entry.strip():
                    add_item(PromptItem(text=format_text(entry), sequence=idx, project=clean_proj, queue_name=clean_queue), idx)
            elif isinstance(entry, dict):
                item = PromptItem.from_dict(entry)
                item.text = format_text(item.text)
                add_item(item, idx)
    return items


_QUEUE_FILE_LOCK = threading.RLock()
_LAST_BACKUP_TS: Dict[str, float] = {}


def export_prompts_to_json(
    items: List[PromptItem],
    filepath: str,
    allow_empty: bool = False,
) -> None:
    """
    Persists prompts to JSON with atomic write, disk sync, backup mirroring,
    thread-safe locking, and test-safety guard to prevent data corruption and accidental empty wipes.
    """
    import time

    # 1. Safety Guard: Never overwrite production queue.json during automated pytest runs!
    if ("PYTEST_CURRENT_TEST" in os.environ or "PYTEST_VERSION" in os.environ) and _is_production_queue_file(filepath):
        return

    with _QUEUE_FILE_LOCK:
        tmp_path = None
        try:
            # 2. Prepare thread-safe snapshot of payload
            if hasattr(items, "lock") and hasattr(items, "_items"):
                with items.lock:
                    snapshot = list(items._items)
            else:
                snapshot = list(items)

            payload = [item.to_dict() for item in snapshot]
            if not payload and not allow_empty and os.path.exists(filepath):
                # Guard against accidentally wiping an existing non-empty file with an empty list
                return

            # 3. Create/update backup mirror if target file exists and is populated (> 10 bytes),
            # throttled to once every 30s if backup already exists to avoid 19MB sync I/O stalls on every step
            now_ts = time.time()
            norm_fp = os.path.abspath(filepath)
            if os.path.exists(filepath) and os.path.getsize(filepath) > 10:
                backup_path = get_queue_backup_path(filepath)
                last_bk = _LAST_BACKUP_TS.get(norm_fp, 0.0)
                if not os.path.exists(backup_path) or (now_ts - last_bk >= 30.0):
                    try:
                        shutil.copy2(filepath, backup_path)
                        _LAST_BACKUP_TS[norm_fp] = now_ts
                    except Exception:
                        pass

            # 4. Atomic write via unique temporary file
            dir_name = os.path.dirname(filepath) or "."
            tmp_path = os.path.join(dir_name, f".queue_{os.getpid()}_{uuid.uuid4().hex[:6]}.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            os.replace(tmp_path, filepath)
            tmp_path = None

            # 5. Mirror to backup: if explicit empty export was requested, sync backup to empty as well;
            # otherwise create backup if none existed yet
            backup_path = get_queue_backup_path(filepath)
            if os.path.exists(filepath):
                if allow_empty and not payload:
                    try:
                        shutil.copy2(filepath, backup_path)
                        _LAST_BACKUP_TS[norm_fp] = now_ts
                    except Exception:
                        pass
                elif not os.path.exists(backup_path):
                    try:
                        shutil.copy2(filepath, backup_path)
                        _LAST_BACKUP_TS[norm_fp] = now_ts
                    except Exception:
                        pass

        except Exception as e:
            print(f"[ERROR] Failed to save queue.json: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass


def import_prompts_from_markdown(
    filepath: str,
    prefix_mode: str = "keep",
    auto_add_goal_prefix: Optional[bool] = None,
    project: str = "",
    queue_name: str = "Default",
) -> List[PromptItem]:
    mode = _resolve_prefix_mode(prefix_mode, auto_add_goal_prefix)
    clean_proj = (project or "").strip()
    clean_queue = (queue_name or "").strip() or "Default"
    items = []
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    lines = content.splitlines()
    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        m = re.match(r"^(?:[-*+]|\d+\.)(?:\s*\[[ xX]\])?\s+(.*)$", cleaned)
        if m:
            text = m.group(1).strip()
            if text:
                t = ensure_prompt_prefix(text, mode)
                items.append(PromptItem(text=t, project=clean_proj, queue_name=clean_queue))
        elif not cleaned.startswith("#"):
            t = ensure_prompt_prefix(cleaned, mode)
            items.append(PromptItem(text=t, project=clean_proj, queue_name=clean_queue))
    return items


def get_subqueue_key(project_name: str, queue_name: str) -> str:
    """Returns composite identifier for a project sub-queue."""
    p_clean = (project_name or "").strip() or "No Project"
    if p_clean.lower() in ("default", "none"):
        p_clean = "No Project"
    q_clean = (queue_name or "").strip()
    if not q_clean or q_clean.lower() == "default":
        return p_clean
    return f"{p_clean} :: {q_clean}"


def parse_subqueue_key(key: str) -> Tuple[str, str]:
    """Extracts (project_name, queue_name) from composite sub-queue key."""
    if " :: " in key:
        parts = key.split(" :: ", 1)
        return parts[0].strip(), parts[1].strip()
    return key.strip(), "Default"


@dataclass
class ProjectExecutionState:
    project_name: str
    queue_name: str = "Default"
    queue_key: str = ""
    current_prompt: Optional[PromptItem] = None
    conversation_id: str = ""
    status: str = "IDLE"  # IDLE, DISPATCHING, GENERATING, STABILIZING, HALTED_QUOTA, COMPLETED, PAUSED
    start_time: float = 0.0
    elapsed_time: float = 0.0
    last_poll_ts: float = 0.0
    is_paused: bool = False
    error_message: str = ""
    pending_count: int = 0
    total_count: int = 0
    completed_count: int = 0
    next_prompt_text: str = ""

    def __post_init__(self):
        if not self.queue_key:
            self.queue_key = get_subqueue_key(self.project_name, self.queue_name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "queue_name": self.queue_name,
            "queue_key": self.queue_key,
            "current_prompt_id": self.current_prompt.id if self.current_prompt else "",
            "current_prompt_text": self.current_prompt.text if self.current_prompt else "",
            "conversation_id": self.conversation_id,
            "status": self.status,
            "start_time": self.start_time,
            "elapsed_time": self.elapsed_time,
            "last_poll_ts": self.last_poll_ts,
            "is_paused": self.is_paused,
            "error_message": self.error_message,
            "pending_count": self.pending_count,
            "total_count": self.total_count,
            "completed_count": self.completed_count,
            "next_prompt_text": self.next_prompt_text,
        }


SubQueueExecutionState = ProjectExecutionState


def partition_queue_into_subqueues(
    items: List[PromptItem],
    default_project: str = "No Project",
    default_queue: str = "Default"
) -> Dict[str, List[PromptItem]]:
    """
    Partitions a unified prompt queue into per-subqueue FIFO queues.
    Each sub-queue is identified by a composite key f"{project_name} :: {queue_name}".
    """
    def_proj = (default_project or "No Project").strip()
    def_q = (default_queue or "Default").strip()
    result: Dict[str, List[PromptItem]] = {}
    items_seq = items.snapshot() if hasattr(items, "snapshot") else list(items)
    for item in items_seq:
        p_name = (item.project.strip() or def_proj).strip()
        if not p_name or p_name.lower() in ("default", "none"):
            p_name = "No Project"
        q_name = (getattr(item, "queue_name", "") or "").strip() or def_q
        key = get_subqueue_key(p_name, q_name)
        if key not in result:
            result[key] = []
        result[key].append(item)
    return result


def partition_queue_by_project(
    items: List[PromptItem],
    default_project: str = "No Project"
) -> Dict[str, List[PromptItem]]:
    """Partitions a unified prompt queue into per-project FIFO queues preserving order."""
    def_proj = (default_project or "No Project").strip()
    result: Dict[str, List[PromptItem]] = {}
    items_seq = items.snapshot() if hasattr(items, "snapshot") else list(items)
    for item in items_seq:
        p_name = (item.project.strip() or def_proj).strip()
        if not p_name or p_name.lower() in ("default", "none"):
            p_name = "No Project"
        if p_name not in result:
            result[p_name] = []
        result[p_name].append(item)
    return result
