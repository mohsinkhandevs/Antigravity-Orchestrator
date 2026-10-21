"""
Subagent Inspector: Extraction, Telemetry, and Tree Hierarchy Engine
=====================================================================
Inspects Google Antigravity subagents launched by parent orchestrators.
Parses lifecycle state, execution durations, context window tokens,
prompt cache hits, tool calls, compaction checkpoints, and inter-agent
IPC messages directly from disk (~/.gemini/antigravity/brain).
"""

import glob
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

DEFAULT_BRAIN_DIR = os.path.expanduser("~/.gemini/antigravity/brain")

# Cache to avoid re-parsing large transcript files when mtime & size have not changed:
# Key: file_path -> (mtime, file_size, cached_result_dict)
_TRANSCRIPT_CACHE: Dict[str, Tuple[float, int, Dict[str, Any]]] = {}


def format_duration_human(seconds: float) -> str:
    """Formats seconds into a compact human-readable string (e.g. '4m 12s', '1h 05m 10s', '3.4s')."""
    if seconds is None or seconds < 0:
        return "0s"
    if seconds == 0:
        return "0s"
    if seconds < 60:
        return f"{seconds:.1f}s" if seconds < 10 else f"{int(round(seconds))}s"
    s = int(round(seconds))
    if s >= 3600:
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        return f"{h}h {m:02d}m {sec:02d}s"
    m = s // 60
    sec = s % 60
    return f"{m}m {sec:02d}s"


def _parse_iso_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """Safely parses ISO timestamps like '2026-10-21T09:07:51Z' or with fractional seconds."""
    if not ts_str:
        return None
    try:
        clean = ts_str.strip().replace("Z", "+00:00")
        # Handle nanoseconds if present (truncate to 6 digits for microseconds)
        if "." in clean:
            prefix, rest = clean.split(".", 1)
            # rest has e.g. '213536900+00:00' or '213536900Z'
            match = re.match(r"^(\d+)([+-]\d\d:\d\d|\+00:00)?$", rest)
            if match:
                nanos, tz = match.groups()
                micros = nanos[:6].ljust(6, "0")
                clean = f"{prefix}.{micros}{tz or '+00:00'}"
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def get_subagent_descriptors(
    conversation_id: str,
    brain_dir: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Scans <brain_dir>/<conversation_id>/.system_generated/subagents/*.json
    and returns a list of raw subagent descriptor dicts.
    """
    clean_id = (conversation_id or "").strip()
    if not clean_id:
        return []

    base = brain_dir or DEFAULT_BRAIN_DIR
    subagents_dir = os.path.join(base, clean_id, ".system_generated", "subagents")
    if not os.path.isdir(subagents_dir):
        return []

    descriptors: List[Dict[str, Any]] = []
    pattern = os.path.join(subagents_dir, "*.json")
    for fpath in glob.glob(pattern):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data.get("conversationId"):
                    data["_source_file"] = fpath
                    descriptors.append(data)
        except Exception:
            continue

    # Sort descriptors by spawnStepIndex if available
    descriptors.sort(key=lambda d: d.get("spawnStepIndex", 0))
    return descriptors


def parse_subagent_transcript(
    subagent_id: str,
    brain_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Parses a subagent's transcript.jsonl with mtime/size caching.
    Extracts step count, timing, token economics, context window, and tool executions.
    """
    base = brain_dir or DEFAULT_BRAIN_DIR
    clean_id = (subagent_id or "").strip()
    if not clean_id:
        return _empty_transcript_metrics()

    t_file = os.path.join(base, clean_id, ".system_generated", "logs", "transcript.jsonl")
    if not os.path.isfile(t_file):
        # Fallback to transcript_full.jsonl
        t_file_full = os.path.join(base, clean_id, ".system_generated", "logs", "transcript_full.jsonl")
        if os.path.isfile(t_file_full):
            t_file = t_file_full
        else:
            return _empty_transcript_metrics()

    stat = os.stat(t_file)
    mtime, size = stat.st_mtime, stat.st_size

    first_ts_str: Optional[str] = None
    last_ts_str: Optional[str] = None
    step_count = 0
    compaction_count = 0
    tool_calls_count = 0

    input_tokens_sum = 0
    cache_read_tokens_sum = 0
    output_tokens_sum = 0

    latest_context_window = 0
    peak_context_window = 0

    last_step_type = ""
    last_step_status = ""
    has_pending_tools = False

    seek_offset = 0
    cached_entry = _TRANSCRIPT_CACHE.get(t_file)
    if cached_entry:
        cached_mtime, cached_size, cached_metrics = cached_entry
        if cached_mtime == mtime and cached_size == size:
            return cached_metrics
        if size > cached_size and cached_size > 0:
            first_ts_str = cached_metrics.get("first_time")
            last_ts_str = cached_metrics.get("last_time")
            step_count = cached_metrics.get("step_count", 0)
            compaction_count = cached_metrics.get("compaction_count", 0)
            tool_calls_count = cached_metrics.get("tool_calls_count", 0)
            input_tokens_sum = cached_metrics.get("input_tokens", 0)
            cache_read_tokens_sum = cached_metrics.get("cache_read_tokens", 0)
            output_tokens_sum = cached_metrics.get("output_tokens", 0)
            latest_context_window = cached_metrics.get("current_context_tokens", 0)
            peak_context_window = cached_metrics.get("peak_context_tokens", 0)
            last_step_type = cached_metrics.get("last_step_type", "")
            last_step_status = cached_metrics.get("last_step_status", "")
            has_pending_tools = cached_metrics.get("has_pending_tools", False)
            seek_offset = cached_size

    try:
        with open(t_file, "r", encoding="utf-8", errors="replace") as f:
            if seek_offset > 0:
                f.seek(seek_offset)
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                step_count += 1
                try:
                    step = json.loads(raw)
                except Exception:
                    continue

                ts = step.get("created_at")
                if ts:
                    if not first_ts_str:
                        first_ts_str = ts
                    last_ts_str = ts

                st_type = step.get("type", "")
                st_status = step.get("status", "")
                last_step_type = st_type
                last_step_status = st_status

                if st_type == "CHECKPOINT":
                    compaction_count += 1

                t_calls = step.get("tool_calls")
                if isinstance(t_calls, list) and len(t_calls) > 0:
                    tool_calls_count += len(t_calls)
                    has_pending_tools = (st_status != "DONE")

                inp = step.get("input_tokens") or 0
                cache = step.get("cache_read_tokens") or 0
                outp = step.get("output_tokens") or 0

                if st_type == "PLANNER_RESPONSE":
                    input_tokens_sum += inp
                    cache_read_tokens_sum += cache
                    output_tokens_sum += outp

                    ctx = inp + cache + outp
                    latest_context_window = ctx
                    if ctx > peak_context_window:
                        peak_context_window = ctx

        total_input = input_tokens_sum + cache_read_tokens_sum
        hit_ratio = (cache_read_tokens_sum / total_input) if total_input > 0 else 0.0

        t_start = _parse_iso_timestamp(first_ts_str)
        t_last = _parse_iso_timestamp(last_ts_str)
        dur_secs = 0.0
        if t_start and t_last:
            dur_secs = max(0.0, (t_last - t_start).total_seconds())

        metrics = {
            "first_time": first_ts_str,
            "last_time": last_ts_str,
            "duration_seconds": dur_secs,
            "duration_human": format_duration_human(dur_secs),
            "step_count": step_count,
            "compaction_count": compaction_count,
            "tool_calls_count": tool_calls_count,
            "input_tokens": input_tokens_sum,
            "cache_read_tokens": cache_read_tokens_sum,
            "output_tokens": output_tokens_sum,
            "current_context_tokens": latest_context_window,
            "peak_context_tokens": peak_context_window,
            "cache_hit_ratio": round(hit_ratio, 4),
            "last_step_type": last_step_type,
            "last_step_status": last_step_status,
            "has_pending_tools": has_pending_tools,
        }

        # Cache result
        _TRANSCRIPT_CACHE[t_file] = (mtime, size, metrics)
        return metrics

    except Exception:
        return _empty_transcript_metrics()


def _empty_transcript_metrics() -> Dict[str, Any]:
    return {
        "first_time": None,
        "last_time": None,
        "duration_seconds": 0.0,
        "duration_human": "0s",
        "step_count": 0,
        "compaction_count": 0,
        "tool_calls_count": 0,
        "input_tokens": 0,
        "cache_read_tokens": 0,
        "output_tokens": 0,
        "current_context_tokens": 0,
        "peak_context_tokens": 0,
        "cache_hit_ratio": 0.0,
        "last_step_type": "",
        "last_step_status": "",
        "has_pending_tools": False,
    }


def get_subagent_messages(
    conversation_id: str,
    brain_dir: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Parses all inter-agent messages in <brain_dir>/<conversation_id>/.system_generated/messages/*.json.
    Returns sorted list by timestamp ascending.
    """
    clean_id = (conversation_id or "").strip()
    if not clean_id:
        return []

    base = brain_dir or DEFAULT_BRAIN_DIR
    msg_dir = os.path.join(base, clean_id, ".system_generated", "messages")
    if not os.path.isdir(msg_dir):
        return []

    messages: List[Dict[str, Any]] = []
    pattern = os.path.join(msg_dir, "*.json")
    for fpath in glob.glob(pattern):
        fname = os.path.basename(fpath).lower()
        if fname in ("read.json", "undelivered"):
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
                if isinstance(data, dict) and (data.get("id") or data.get("sender")):
                    title = ""
                    rd = data.get("renderDetails")
                    if isinstance(rd, dict):
                        title = rd.get("messageTitle", "")

                    content = data.get("content", "")
                    preview = (content[:200] + "...") if len(content) > 200 else content

                    messages.append({
                        "id": data.get("id", ""),
                        "sender": data.get("sender", ""),
                        "recipient": data.get("recipient", ""),
                        "priority": data.get("priority", "NORMAL"),
                        "timestamp": data.get("timestamp", ""),
                        "title": title or "Inter-agent Message",
                        "content": content,
                        "preview": preview,
                    })
        except Exception:
            continue

    messages.sort(key=lambda m: m.get("timestamp") or "")
    return messages


def inspect_subagent(
    subagent_id: str,
    parent_id: str = "",
    descriptor: Optional[Dict[str, Any]] = None,
    brain_dir: Optional[str] = None,
    visited: Optional[Set[str]] = None,
    depth: int = 0,
    max_depth: int = 5,
) -> Dict[str, Any]:
    """
    Fully inspects a single subagent, its transcript metrics, IPC messages,
    and recursively inspects any subagents spawned by this subagent.
    """
    clean_id = (subagent_id or "").strip()
    base = brain_dir or DEFAULT_BRAIN_DIR

    # Avoid circular references
    if visited is None:
        visited = set()
    if clean_id in visited or depth > max_depth:
        return {"conversation_id": clean_id, "error": "Max depth or circular reference"}
    visited.add(clean_id)

    desc = descriptor or {}
    sub_desc = desc.get("subagentDescriptor") or {}
    role = sub_desc.get("role", "Subagent")
    type_name = sub_desc.get("typeName", "self")
    raw_state = desc.get("state", "UNKNOWN")
    spawn_step_index = desc.get("spawnStepIndex", 0)
    workspace_uris = desc.get("workspaceUris", [])

    metrics = parse_subagent_transcript(clean_id, brain_dir=base)

    # State determination
    is_alive = (raw_state == "SUBAGENT_STATE_ALIVE")
    is_killed = (raw_state == "SUBAGENT_STATE_KILLED")
    
    # Refine alive state:
    # If raw_state is ALIVE, check if it's currently executing or idle
    status_label = "ACTIVE"
    if is_killed:
        status_label = "KILLED"
    elif is_alive:
        if metrics["has_pending_tools"]:
            status_label = "EXECUTING"
        elif metrics["last_step_type"] == "PLANNER_RESPONSE" and metrics["last_step_status"] == "DONE":
            status_label = "IDLE / WAITING"
        else:
            status_label = "ACTIVE"
    else:
        status_label = "COMPLETED"

    # Calculate real-time duration if currently alive
    dur_secs = metrics["duration_seconds"]
    if is_alive and metrics["first_time"]:
        t_start = _parse_iso_timestamp(metrics["first_time"])
        if t_start:
            now_utc = datetime.now(timezone.utc)
            live_secs = max(0.0, (now_utc - t_start).total_seconds())
            dur_secs = live_secs
            metrics["duration_seconds"] = dur_secs
            metrics["duration_human"] = format_duration_human(dur_secs)

    messages = get_subagent_messages(clean_id, brain_dir=base)

    # Check if this subagent has spawned its own subagents (recursive child tree)
    child_descriptors = get_subagent_descriptors(clean_id, brain_dir=base)
    children: List[Dict[str, Any]] = []
    for cdesc in child_descriptors:
        cid = cdesc.get("conversationId", "")
        if cid:
            child_info = inspect_subagent(
                subagent_id=cid,
                parent_id=clean_id,
                descriptor=cdesc,
                brain_dir=base,
                visited=visited,
                depth=depth + 1,
                max_depth=max_depth,
            )
            children.append(child_info)

    return {
        "conversation_id": clean_id,
        "parent_id": parent_id,
        "role": role,
        "type_name": type_name,
        "raw_state": raw_state,
        "status_label": status_label,
        "is_alive": is_alive,
        "is_killed": is_killed,
        "spawn_step_index": spawn_step_index,
        "workspace_uris": workspace_uris,
        "metrics": metrics,
        "messages": messages,
        "children": children,
        "children_count": len(children),
    }


def build_subagent_tree(
    root_conversation_id: str,
    brain_dir: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Discovers all subagents launched directly by `root_conversation_id`
    and builds their complete recursive telemetry tree.
    """
    clean_id = (root_conversation_id or "").strip()
    if not clean_id:
        return []

    base = brain_dir or DEFAULT_BRAIN_DIR
    descriptors = get_subagent_descriptors(clean_id, brain_dir=base)
    visited: Set[str] = {clean_id}

    tree: List[Dict[str, Any]] = []
    for desc in descriptors:
        sub_id = desc.get("conversationId", "")
        if sub_id:
            info = inspect_subagent(
                subagent_id=sub_id,
                parent_id=clean_id,
                descriptor=desc,
                brain_dir=base,
                visited=visited,
                depth=1,
            )
            tree.append(info)

    return tree


def get_subagents_fleet_summary(
    root_conversation_id: str,
    brain_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Returns high-level fleet statistics across all subagents of a conversation:
    - total_count: Total subagents spawned
    - active_count: Total currently ALIVE
    - total_steps: Sum of all steps across subagents
    - total_input_tokens: Uncached input tokens sum
    - total_cache_read_tokens: Context cache hit tokens sum
    - total_output_tokens: Generated output tokens sum
    - peak_context_tokens: Highest context window touched by any subagent
    - subagents_tree: The full tree list
    """
    tree = build_subagent_tree(root_conversation_id, brain_dir=brain_dir)

    total_count = 0
    active_count = 0
    total_steps = 0
    total_input = 0
    total_cache = 0
    total_output = 0
    peak_context = 0

    def _traverse(node: Dict[str, Any]):
        nonlocal total_count, active_count, total_steps, total_input, total_cache, total_output, peak_context
        total_count += 1
        if node.get("is_alive"):
            active_count += 1

        m = node.get("metrics") or {}
        total_steps += m.get("step_count", 0)
        total_input += m.get("input_tokens", 0)
        total_cache += m.get("cache_read_tokens", 0)
        total_output += m.get("output_tokens", 0)

        peak = m.get("peak_context_tokens", 0)
        if peak > peak_context:
            peak_context = peak

        for child in node.get("children", []):
            _traverse(child)

    for root_node in tree:
        _traverse(root_node)

    combined_input = total_input + total_cache
    fleet_hit_ratio = (total_cache / combined_input) if combined_input > 0 else 0.0

    return {
        "root_conversation_id": root_conversation_id,
        "total_count": total_count,
        "active_count": active_count,
        "total_steps": total_steps,
        "total_input_tokens": total_input,
        "total_cache_read_tokens": total_cache,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_cache + total_output,
        "fleet_cache_hit_ratio": round(fleet_hit_ratio, 4),
        "peak_context_tokens": peak_context,
        "subagents_tree": tree,
    }
