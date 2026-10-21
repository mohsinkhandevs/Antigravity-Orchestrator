import json
import os
from typing import Any, Dict, List, Optional


DEFAULT_BRAIN_DIR = os.path.expanduser("~/.gemini/antigravity/brain")

QUOTA_ERROR_KEYWORDS = (
    "quota",
    "429",
    "resource has been exhausted",
    "rate limit",
    "limit reached",
    "capacity",
    "too many requests",
)


def get_transcript_path(conversation_id: str, brain_dir: Optional[str] = None) -> Optional[str]:
    """
    Returns the absolute path to the local transcript.jsonl for a given conversation UUID,
    or None if conversation_id is empty.
    """
    clean_id = (conversation_id or "").strip()
    if not clean_id:
        return None

    base = brain_dir or DEFAULT_BRAIN_DIR
    primary = os.path.join(base, clean_id, ".system_generated", "logs", "transcript.jsonl")
    if os.path.isfile(primary):
        return primary

    fallback = os.path.join(base, clean_id, ".system_generated", "logs", "transcript_full.jsonl")
    if os.path.isfile(fallback):
        return fallback

    return primary


def read_last_transcript_steps(
    conversation_id: str,
    max_steps: int = 3,
    brain_dir: Optional[str] = None,
    tail_bytes: int = 32768,
) -> List[Dict[str, Any]]:
    """
    Reads the last `max_steps` JSON objects from the conversation's local transcript.jsonl
    in <1ms by seeking to the tail of the file.
    """
    path = get_transcript_path(conversation_id, brain_dir=brain_dir)
    if not path or not os.path.isfile(path):
        return []

    try:
        file_size = os.path.getsize(path)
        if file_size == 0:
            return []

        non_empty: List[str] = []
        candidate_sizes = [tail_bytes, 131072, 524288, file_size]
        with open(path, "rb") as f:
            for chunk_size in candidate_sizes:
                if file_size > chunk_size:
                    f.seek(file_size - chunk_size, os.SEEK_SET)
                    raw = f.read().decode("utf-8", errors="replace")
                    # Drop potentially partial first line after seek
                    lines = raw.splitlines()[1:]
                else:
                    f.seek(0, os.SEEK_SET)
                    raw = f.read().decode("utf-8", errors="replace")
                    lines = raw.splitlines()

                non_empty = [line.strip() for line in lines if line.strip()]
                if non_empty or chunk_size >= file_size:
                    break

        tail_lines = non_empty[-max_steps:] if max_steps > 0 else non_empty

        steps: List[Dict[str, Any]] = []
        for line in tail_lines:
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    steps.append(obj)
            except Exception:
                continue
        return steps
    except Exception:
        return []


def _is_quota_error_text(text: str, strict: bool = False) -> bool:
    lower = (text or "").lower()
    if strict:
        strict_keywords = (
            "429",
            "resource has been exhausted",
            "quota exceeded",
            "rate limit exceeded",
            "too many requests",
            "model quota",
        )
        return any(k in lower for k in strict_keywords)
    return any(k in lower for k in QUOTA_ERROR_KEYWORDS)


def inspect_transcript_termination(
    conversation_id: str,
    brain_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Determines the exact termination reason for a stopped conversation by inspecting
    the last 2-3 lines of its local transcript.jsonl on disk (<1ms, zero UI navigation).

    Returns a dictionary:
    - found: bool (True if transcript existed and had steps)
    - state: "NORMAL" | "QUOTA_EXHAUSTED" | "ERRORED" | "WAITING_FOR_INPUT" | "INTERRUPTED" | "UNKNOWN"
    - detail: Human-readable explanation
    - last_step_type: e.g. "PLANNER_RESPONSE", "GENERIC", "USER_INPUT"
    - last_step_status: e.g. "DONE", "ERROR", "RUNNING"
    - has_tool_calls: bool
    """
    steps = read_last_transcript_steps(conversation_id, max_steps=3, brain_dir=brain_dir)
    if not steps:
        return {
            "found": False,
            "state": "UNKNOWN",
            "detail": "Transcript file not found or empty",
            "last_step_type": "",
            "last_step_status": "",
            "has_tool_calls": False,
        }

    last = steps[-1]
    last_type = str(last.get("type") or "")
    last_status = str(last.get("status") or "")
    tool_calls = last.get("tool_calls")
    has_tool_calls = bool(isinstance(tool_calls, list) and len(tool_calls) > 0)

    # 1. Check recent steps for explicit ERROR / INVALID status or error field
    for step in reversed(steps[-2:]):
        st_type = str(step.get("type") or "")
        st_status = str(step.get("status") or "")
        st_err = str(step.get("error") or "")
        if st_status in ("ERROR", "INVALID") or st_err:
            if st_type == "GENERIC":
                # Tool output content may contain source code with words like 'capacity' or 'quota';
                # only inspect explicit error field or strict 429 API patterns.
                combined_err = st_err or str(step.get("content") or "")[:300]
                is_quota = _is_quota_error_text(st_err) if st_err else _is_quota_error_text(combined_err, strict=True)
            else:
                combined_err = f"{st_err} {step.get('content') or ''}".strip()
                is_quota = _is_quota_error_text(combined_err)
            if is_quota:
                return {
                    "found": True,
                    "state": "QUOTA_EXHAUSTED",
                    "detail": combined_err[:300] or "Quota exhausted in transcript step",
                    "last_step_type": last_type,
                    "last_step_status": last_status,
                    "has_tool_calls": has_tool_calls,
                }

    # 2. If the very last step itself has status ERROR or INVALID
    if last_status in ("ERROR", "INVALID"):
        err_detail = str(last.get("error") or last.get("content") or "Step ended with ERROR").strip()
        return {
            "found": True,
            "state": "ERRORED",
            "detail": err_detail[:300],
            "last_step_type": last_type,
            "last_step_status": last_status,
            "has_tool_calls": has_tool_calls,
        }

    # 3. If the last step is still marked RUNNING in transcript
    if last_status == "RUNNING":
        return {
            "found": True,
            "state": "WAITING_FOR_INPUT",
            "detail": f"Step {last.get('step_index', '')} ({last_type}) has status RUNNING",
            "last_step_type": last_type,
            "last_step_status": last_status,
            "has_tool_calls": has_tool_calls,
        }

    # 4. If the last step is PLANNER_RESPONSE with status DONE
    if last_type == "PLANNER_RESPONSE" and last_status == "DONE":
        if not has_tool_calls:
            # Final planner response with no further tool calls -> 100% Normal Completion!
            return {
                "found": True,
                "state": "NORMAL",
                "detail": "Planner response completed (DONE) with no pending tool calls",
                "last_step_type": last_type,
                "last_step_status": last_status,
                "has_tool_calls": False,
            }
        else:
            # Planner emitted a tool call, but no GENERIC tool response step has been written yet
            return {
                "found": True,
                "state": "WAITING_FOR_INPUT",
                "detail": "Planner emitted tool call with no response yet",
                "last_step_type": last_type,
                "last_step_status": last_status,
                "has_tool_calls": True,
            }

    # 5. If the last step is GENERIC (tool output finished) or USER_INPUT, and spinner has stopped:
    # The agent halted mid-trajectory before producing a final PLANNER_RESPONSE (e.g. quota banner or user stop)
    return {
        "found": True,
        "state": "INTERRUPTED",
        "detail": f"Conversation stopped after {last_type} ({last_status}) without a final PLANNER_RESPONSE",
        "last_step_type": last_type,
        "last_step_status": last_status,
        "has_tool_calls": has_tool_calls,
    }
