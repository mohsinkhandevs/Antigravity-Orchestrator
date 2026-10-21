import json
import os
import re
import time
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from cdp.conversation_db_inspector import (
    DEFAULT_APP_DATA_DIR,
    _connect_ro,
    get_conversation_summary,
    get_conversations_db_path,
    get_summaries_db_path,
    inspect_conversation_db_termination,
)
from cdp.transcript_inspector import (
    DEFAULT_BRAIN_DIR,
    _is_quota_error_text,
    get_transcript_path,
    inspect_transcript_termination,
)


UUID_PATTERN = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def extract_conversation_id(raw_input: str) -> str:
    """
    Extracts a clean conversation ID from either a raw ID string, brain path, or a full URL
    (e.g., 'https://.../c/c542bbae-687b-4a74-bf78-1501c58fd0b6').
    Returns empty string if no valid conversation ID is present.
    """
    clean = (raw_input or "").strip()
    if not clean:
        return ""

    # 1. Check for standard UUID anywhere in string
    m_uuid = UUID_PATTERN.search(clean)
    if m_uuid:
        return m_uuid.group(1).lower()

    # 2. Check for /c/<id> pattern in URL
    m_url = re.search(r"/c/([^/?#&\s]+)", clean, re.IGNORECASE)
    if m_url:
        return m_url.group(1).strip()

    return ""


def format_duration_human(seconds: float) -> str:
    """Formats seconds into a compact human-readable string (e.g. '4m 12s', '1h 05m 10s', '3.4s')."""
    if seconds is None or seconds < 0:
        return "-"
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


def format_token_count(tokens: int) -> str:
    """Formats token count into human-readable compact string (e.g. '1.5k', '24.5k', '1.2M')."""
    if tokens is None or tokens <= 0:
        return "0"
    if tokens < 1000:
        return str(tokens)
    if tokens < 1_000_000:
        val = tokens / 1000.0
        return f"{val:.1f}k" if val < 100 else f"{int(round(val))}k"
    val = tokens / 1_000_000.0
    return f"{val:.1f}M"


def _parse_iso_timestamp(ts_str: str) -> Optional[float]:
    """Parses an ISO 8601 timestamp string into epoch seconds (float)."""
    clean = (ts_str or "").strip()
    if not clean:
        return None
    try:
        if clean.endswith("Z"):
            clean = clean[:-1] + "+00:00"
        dt = datetime.fromisoformat(clean)
        return dt.timestamp()
    except Exception:
        return None


def _format_timestamp_display(ts_str: str) -> str:
    """Formats an ISO 8601 timestamp into local 'YYYY-MM-DD HH:MM:SS' for clean UI display."""
    ts = _parse_iso_timestamp(ts_str)
    if ts is None:
        return (ts_str or "-").strip() or "-"
    try:
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts_str


def _extract_project_from_workspace_uri(ws_uri: str) -> str:
    """Extracts the human-readable project folder name from Antigravity's workspace_uris field."""
    raw = str(ws_uri or "").strip()
    if not raw:
        return ""
    try:
        unquoted = urllib.parse.unquote(raw)
        # Strip JSON brackets/quotes if serialized as JSON array
        cleaned = unquoted.strip("[]\"' ")
        if "," in cleaned:
            cleaned = cleaned.split(",")[0].strip("\"' ")
        norm = cleaned.replace("\\", "/").rstrip("/")
        parts = [p for p in norm.split("/") if p]
        if parts:
            return parts[-1]
    except Exception:
        pass
    return ""


def _clean_user_prompt_text(raw_content: str) -> str:
    """
    Extracts the user's actual request from <USER_REQUEST>...</USER_REQUEST> wrapper tags
    if present in the transcript step content.
    """
    text = str(raw_content or "").strip()
    if not text:
        return ""
    m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def list_conversation_artifacts(
    conversation_id: str,
    brain_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Lists markdown and media artifacts generated inside ~/.gemini/antigravity/brain/<conversation_id>/.
    """
    clean_id = extract_conversation_id(conversation_id)
    if not clean_id:
        return []
    base = brain_dir or DEFAULT_BRAIN_DIR
    conv_brain_dir = os.path.join(base, clean_id)
    if not os.path.isdir(conv_brain_dir):
        return []

    artifacts: List[Dict[str, Any]] = []
    try:
        for entry in sorted(os.listdir(conv_brain_dir)):
            if entry.startswith(".") or entry.endswith(".metadata.json") or entry.endswith(".tmp"):
                continue
            full_path = os.path.join(conv_brain_dir, entry)
            if os.path.isfile(full_path):
                try:
                    stat = os.stat(full_path)
                    artifacts.append({
                        "name": entry,
                        "path": os.path.abspath(full_path),
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    })
                except Exception:
                    continue
    except Exception:
        pass
    return artifacts


def read_all_transcript_steps(
    conversation_id: str,
    brain_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Reads all steps from a conversation's local transcript.jsonl file.
    """
    clean_id = extract_conversation_id(conversation_id)
    if not clean_id:
        return []
    path = get_transcript_path(clean_id, brain_dir=brain_dir)
    if not path or not os.path.isfile(path):
        return []

    steps: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                    if isinstance(obj, dict):
                        steps.append(obj)
                except Exception:
                    continue
    except Exception:
        return []
    return steps


def get_recent_conversations(
    app_data_dir: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Returns recent conversations from conversation_summaries.db ordered from newest to oldest.
    """
    db_path = get_summaries_db_path(app_data_dir=app_data_dir)
    conn = _connect_ro(db_path)
    if not conn:
        return []

    results: List[Dict[str, Any]] = []
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT conversation_id, title, status, not_fully_idle, killed, step_count, workspace_uris "
                "FROM conversation_summaries ORDER BY ROWID DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
            has_ws = True
        except Exception:
            cur.execute(
                "SELECT conversation_id, title, status, not_fully_idle, killed, step_count "
                "FROM conversation_summaries ORDER BY ROWID DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
            has_ws = False

        for r in rows:
            cid = str(r[0] or "").strip()
            if not cid:
                continue
            title = str(r[1] or "").strip() or cid[:12]
            status_text = str(r[2] or "")
            not_idle = int(r[3] or 0)
            killed = bool(r[4])
            step_count = int(r[5] or 0)
            ws = str(r[6] or "") if has_ws and len(r) > 6 else ""
            proj = _extract_project_from_workspace_uri(ws) or "Default"
            is_running = (not_idle == 1) or ("RUNNING" in status_text.upper())
            results.append({
                "id": cid,
                "title": title,
                "project": proj,
                "workspace_uris": ws,
                "status": status_text,
                "is_running": is_running,
                "killed": killed,
                "step_count": step_count,
            })
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return results


def get_active_and_unfinished_chats(
    app_data_dir: Optional[str] = None,
    brain_dir: Optional[str] = None,
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """
    Finds all conversations across projects where:
    1. An agent is actively generating right now (RUNNING / GENERATING).
    2. An agent is active/unfinished (halted on QUOTA_EXHAUSTED, INTERRUPTED mid-turn,
       WAITING_FOR_INPUT, or stopped on STEP_ERROR / EXECUTION_ERROR before finishing).
    """
    recent = get_recent_conversations(app_data_dir=app_data_dir, limit=limit)
    active_or_unfinished: List[Dict[str, Any]] = []
    seen_ids = set()

    for item in recent:
        cid = item["id"]
        seen_ids.add(cid)
        is_running = item["is_running"]

        if is_running:
            active_or_unfinished.append({
                "id": cid,
                "title": item["title"],
                "project": item["project"],
                "category": "GENERATING",
                "state": "GENERATING",
                "step_count": item["step_count"],
                "detail": f"Agent is actively generating (Step #{item['step_count']})",
            })
            continue

        # Check if unfinished in SQLite DB or local transcript
        db_term = inspect_conversation_db_termination(cid, app_data_dir=app_data_dir)
        tr_term = inspect_transcript_termination(cid, brain_dir=brain_dir)

        term_state = "NORMAL"
        detail = ""
        if db_term.get("found") and db_term.get("state") not in ("UNKNOWN", "NORMAL"):
            term_state = db_term["state"]
            detail = db_term.get("detail", "")
        elif tr_term.get("found") and tr_term.get("state") not in ("UNKNOWN", "NORMAL"):
            term_state = tr_term["state"]
            detail = tr_term.get("detail", "")

        if term_state in (
            "RUNNING",
            "QUOTA_EXHAUSTED",
            "INTERRUPTED",
            "WAITING_FOR_INPUT",
            "STEP_ERROR",
            "EXECUTION_ERROR",
            "ERRORED",
        ):
            cat = "GENERATING" if term_state == "RUNNING" else "UNFINISHED"
            active_or_unfinished.append({
                "id": cid,
                "title": item["title"],
                "project": item["project"],
                "category": cat,
                "state": "GENERATING" if term_state == "RUNNING" else term_state,
                "step_count": item["step_count"],
                "detail": detail or f"Unfinished state: {term_state}",
            })

    # Sort so GENERATING chats appear first, followed by UNFINISHED chats
    active_or_unfinished.sort(key=lambda x: (0 if x["category"] == "GENERATING" else 1))
    return active_or_unfinished


def parse_protobuf_step_timestamps(meta: bytes) -> Dict[int, float]:
    """
    Parses start and end timestamps from steps.metadata protobuf blob without external dependencies.
    Field 1: Step start timestamp (submessage: 1=seconds, 2=nanos).
    Field 7/8/6: Step finish timestamp (submessage: 1=seconds, 2=nanos).
    Returns dict mapping protobuf field_num -> epoch seconds (float).
    """
    if not meta:
        return {}
    pos = 0
    ts_dict: Dict[int, float] = {}
    l_meta = len(meta)
    while pos < l_meta:
        tag = 0
        shift = 0
        while True:
            if pos >= l_meta:
                return ts_dict
            b = meta[pos]
            pos += 1
            tag |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        wire = tag & 7
        f_num = tag >> 3
        if wire == 0:
            while True:
                if pos >= l_meta:
                    return ts_dict
                b = meta[pos]
                pos += 1
                if not (b & 0x80):
                    break
        elif wire == 1:
            pos += 8
        elif wire == 2:
            length = 0
            shift = 0
            while True:
                if pos >= l_meta:
                    return ts_dict
                b = meta[pos]
                pos += 1
                length |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            sub = meta[pos : pos + length]
            pos += length
            if length >= 2:
                s_pos = 0
                s_len = len(sub)
                sec = 0
                nanos = 0
                has_sec = False
                while s_pos < s_len:
                    s_tag = 0
                    s_shift = 0
                    while True:
                        if s_pos >= s_len:
                            break
                        sb = sub[s_pos]
                        s_pos += 1
                        s_tag |= (sb & 0x7F) << s_shift
                        if not (sb & 0x80):
                            break
                        s_shift += 7
                    s_wire = s_tag & 7
                    s_fnum = s_tag >> 3
                    if s_wire == 0:
                        s_val = 0
                        s_shift = 0
                        while True:
                            if s_pos >= s_len:
                                break
                            sb = sub[s_pos]
                            s_pos += 1
                            s_val |= (sb & 0x7F) << s_shift
                            if not (sb & 0x80):
                                break
                            s_shift += 7
                        if s_fnum == 1:
                            sec = s_val
                            has_sec = True
                        elif s_fnum == 2:
                            nanos = s_val
                    elif s_wire == 1:
                        s_pos += 8
                    elif s_wire == 2:
                        l_sub = 0
                        shift = 0
                        while True:
                            if s_pos >= s_len:
                                break
                            sb = sub[s_pos]
                            s_pos += 1
                            l_sub |= (sb & 0x7F) << shift
                            if not (sb & 0x80):
                                break
                            shift += 7
                        s_pos += l_sub
                    elif s_wire == 5:
                        s_pos += 4
                    else:
                        break
                if has_sec and sec > 1_000_000_000:
                    ts_dict[f_num] = sec + nanos / 1e9
        elif wire == 5:
            pos += 4
        else:
            break
    return ts_dict


def get_db_step_durations(conversation_id: str, app_data_dir: Optional[str] = None) -> Dict[int, float]:
    """
    Extracts microsecond-precise execution durations per step index from SQLite <conversation_id>.db steps.metadata.
    Returns dict mapping step_idx -> duration_seconds (float).
    """
    clean_id = extract_conversation_id(conversation_id)
    if not clean_id:
        return {}
    conv_path = get_conversations_db_path(clean_id, app_data_dir=app_data_dir)
    if not conv_path or not os.path.isfile(conv_path):
        return {}
    conn = _connect_ro(conv_path)
    if not conn:
        return {}
    durations: Dict[int, float] = {}
    try:
        cur = conn.cursor()
        cur.execute("SELECT idx, step_type, status, metadata FROM steps ORDER BY idx ASC")
        rows = cur.fetchall()
        for idx, st_type, status, meta in rows:
            if not meta or not isinstance(meta, (bytes, bytearray)):
                continue
            ts_dict = parse_protobuf_step_timestamps(meta)
            t_start = ts_dict.get(1)
            t_end = ts_dict.get(7) or ts_dict.get(8) or ts_dict.get(6)
            if t_start and t_end and t_end >= t_start:
                durations[int(idx)] = round(t_end - t_start, 2)
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return durations


def _segment_sqlite_steps_fallback(
    conversation_id: str,
    is_running: bool,
    overall_state: str,
    app_data_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fallback turn segmentation using conversations/<id>.db steps table when transcript.jsonl is absent.
    step_type 14 or 17 = USER_INPUT, 15 = PLANNER_RESPONSE, 132 = GENERIC (tool call/output).
    """
    conv_path = get_conversations_db_path(conversation_id, app_data_dir=app_data_dir)
    if not conv_path or not os.path.isfile(conv_path):
        return []

    conn = _connect_ro(conv_path)
    if not conn:
        return []

    turns: List[Dict[str, Any]] = []
    try:
        cur = conn.cursor()
        cur.execute("SELECT idx, step_type, status, error_details FROM steps ORDER BY idx ASC")
        rows = cur.fetchall()
        if not rows:
            return []

        current_turn_rows: List[ Any ] = []
        grouped_rows: List[List[Any]] = []
        for r in rows:
            s_type = int(r[1] or 0)
            if s_type in (14, 17):
                if current_turn_rows:
                    grouped_rows.append(current_turn_rows)
                current_turn_rows = [r]
            else:
                if not current_turn_rows:
                    current_turn_rows = [r]
                else:
                    current_turn_rows.append(r)
        if current_turn_rows:
            grouped_rows.append(current_turn_rows)

        total_turns = len(grouped_rows)
        for idx, t_rows in enumerate(grouped_rows, start=1):
            is_last_turn = idx == total_turns
            tool_count = sum(1 for r in t_rows if int(r[1] or 0) == 132)
            last_r = t_rows[-1]
            last_type = int(last_r[1] or 0)
            last_status = int(last_r[2] or 0)

            if is_last_turn and is_running:
                t_status = "GENERATING"
            elif last_status == 7:
                t_status = overall_state if (is_last_turn and overall_state != "NORMAL") else "ERRORED"
            elif is_last_turn and overall_state not in ("NORMAL", "UNKNOWN"):
                t_status = overall_state
            elif last_type == 15 and last_status == 3:
                t_status = "COMPLETED"
            elif not is_last_turn:
                t_status = "COMPLETED"
            else:
                t_status = "INTERRUPTED"

            turns.append({
                "prompt_index": idx,
                "prompt_text": f"[Prompt #{idx} recorded in SQLite steps (Step #{t_rows[0][0]})]",
                "response_text": f"[Turn #{idx} finished at Step #{last_r[0]} with status={last_status}]",
                "started_at": "-",
                "ended_at": "-",
                "duration_seconds": 0.0,
                "duration_formatted": "-",
                "steps_count": len(t_rows),
                "tool_calls_count": tool_count,
                "tool_names": [],
                "subagents_count": 0,
                "status": t_status,
                "total_model_duration_seconds": 0.0,
                "total_tool_duration_seconds": 0.0,
                "tokens_per_second": 0.0,
                "wall_clock_speed": 0.0,
                "start_step_index": int(t_rows[0][0]),
                "end_step_index": int(last_r[0]),
            })
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return turns


def inspect_conversation_lifecycle(
    conversation_id: str,
    app_data_dir: Optional[str] = None,
    brain_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Performs a comprehensive, zero-navigation inspection of a conversation's lifecycle:
    - Metadata (Title, Project, Workspace URI, Engine Status, Termination Reason)
    - Overall Lifecycle (First started, Last active, Total active generation time, Wall-clock span)
    - Per-Prompt Breakdown (Every prompt in the chat, time taken per prompt, steps, tool calls, status, response)
    - Artifacts list in brain/<conversation_id>/
    """
    clean_id = extract_conversation_id(conversation_id)
    result: Dict[str, Any] = {
        "found": False,
        "conversation_id": clean_id,
        "title": "",
        "project": "Unknown Project",
        "workspace_uris": "",
        "is_running": False,
        "killed": False,
        "overall_state": "UNKNOWN",
        "termination_detail": "",
        "termination_source": "",
        "total_prompts": 0,
        "user_prompts_count": 0,
        "auto_resume_prompts_count": 0,
        "total_steps": 0,
        "total_tool_calls": 0,
        "total_subagents": 0,
        "subagents_summary": {},
        "subagents_tree": [],
        "total_active_duration_seconds": 0.0,
        "total_active_duration_formatted": "0s",
        "wall_clock_duration_seconds": 0.0,
        "wall_clock_duration_formatted": "0s",
        "conversation_model_duration_seconds": 0.0,
        "conversation_tool_duration_seconds": 0.0,
        "conversation_generation_speed": 0.0,
        "first_started_at": "-",
        "last_active_at": "-",
        "prompt_turns": [],
        "artifacts": [],
        "brain_folder_path": "",
    }
    if not clean_id:
        return result

    base_brain = brain_dir or DEFAULT_BRAIN_DIR
    candidate_brain_folder = os.path.join(base_brain, clean_id)
    if os.path.isdir(candidate_brain_folder):
        result["brain_folder_path"] = os.path.abspath(candidate_brain_folder)

    # 1. Query conversation_summaries.db
    summary = get_conversation_summary(clean_id, app_data_dir=app_data_dir)
    if summary.get("found"):
        result["found"] = True
        result["is_running"] = not summary.get("is_idle", True)
        result["killed"] = bool(summary.get("killed", False))
        result["total_steps"] = int(summary.get("step_count", 0))
        ws = str(summary.get("workspace_uris", "") or "")
        result["workspace_uris"] = ws
        proj = _extract_project_from_workspace_uri(ws)
        if proj:
            result["project"] = proj

    # Fetch title from conversation_summaries.db if available
    db_path = get_summaries_db_path(app_data_dir=app_data_dir)
    conn = _connect_ro(db_path)
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT title FROM conversation_summaries WHERE conversation_id = ? LIMIT 1", (clean_id,))
            row = cur.fetchone()
            if row and row[0]:
                result["title"] = str(row[0]).strip()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # 2. Check Termination State (SQLite + Transcript)
    db_term = inspect_conversation_db_termination(clean_id, app_data_dir=app_data_dir)
    tr_term = inspect_transcript_termination(clean_id, brain_dir=brain_dir)

    if result["is_running"]:
        result["overall_state"] = "GENERATING"
        result["termination_detail"] = "Agent is actively generating in this conversation."
        result["termination_source"] = "sqlite_summary"
    elif db_term.get("found") and db_term.get("state") not in ("UNKNOWN",):
        result["found"] = True
        st = db_term["state"]
        # If SQLite says INTERRUPTED or NORMAL, cross-check transcript for more specific quota/error details
        if st in ("NORMAL", "INTERRUPTED", "USER_STOPPED") and tr_term.get("found") and tr_term.get("state") in ("QUOTA_EXHAUSTED", "ERRORED"):
            result["overall_state"] = tr_term["state"]
            result["termination_detail"] = tr_term.get("detail", "")
            result["termination_source"] = "transcript_jsonl"
        elif st in ("USER_STOPPED", "INTERRUPTED"):
            result["overall_state"] = "STOPPED_BY_USER"
            result["termination_detail"] = "Agent execution was stopped by user."
            result["termination_source"] = "sqlite_summary"
        else:
            result["overall_state"] = "COMPLETED" if st == "NORMAL" else st
            result["termination_detail"] = db_term.get("detail", "")
            result["termination_source"] = "sqlite_db"
    elif tr_term.get("found"):
        result["found"] = True
        st = tr_term["state"]
        if st == "INTERRUPTED":
            result["overall_state"] = "STOPPED_BY_USER"
            result["termination_detail"] = "Agent execution was stopped by user."
        else:
            result["overall_state"] = "COMPLETED" if st == "NORMAL" else st
            result["termination_detail"] = tr_term.get("detail", "")
        result["termination_source"] = "transcript_jsonl"

    # 3. Parse full transcript.jsonl for per-prompt turns, durations, and lifecycle
    steps = read_all_transcript_steps(clean_id, brain_dir=brain_dir)
    if steps:
        result["found"] = True
        result["total_steps"] = max(result["total_steps"], len(steps))
        db_durations = get_db_step_durations(clean_id, app_data_dir=app_data_dir)

        # Group steps into turns starting at each USER_INPUT
        grouped_steps: List[List[Dict[str, Any]]] = []
        current_group: List[Dict[str, Any]] = []

        for step in steps:
            s_type = str(step.get("type") or "").upper()
            if s_type == "USER_INPUT":
                if current_group:
                    grouped_steps.append(current_group)
                current_group = [step]
            else:
                if not current_group:
                    current_group = [step]
                else:
                    current_group.append(step)
        if current_group:
            grouped_steps.append(current_group)

        turns: List[Dict[str, Any]] = []
        all_timestamps: List[Tuple[float, str]] = []
        total_active_sec = 0.0
        total_tools = 0
        total_subagents = 0

        num_groups = len(grouped_steps)
        for idx, g_steps in enumerate(grouped_steps, start=1):
            is_last_turn = idx == num_groups
            first_step = g_steps[0]
            last_step = g_steps[-1]

            raw_prompt = first_step.get("content", "") if str(first_step.get("type") or "").upper() == "USER_INPUT" else ""
            prompt_text = _clean_user_prompt_text(raw_prompt) or f"(System / Continuation Step #{first_step.get('step_index', idx)})"
            resume_candidates = {
                "continue",
                "please continue",
                "resume",
                "please resume where you left off and finish the remaining work.",
            }
            try:
                from app.config import config as _app_cfg
                cfg_resume = (getattr(_app_cfg, "resume_mini_prompt", "") or "").strip().lower()
                if cfg_resume:
                    resume_candidates.add(cfg_resume)
            except Exception:
                pass
            is_auto_resume = prompt_text.strip().lower() in resume_candidates

            # Collect timestamps within this turn
            turn_ts: List[Tuple[float, str]] = []
            for s in g_steps:
                c_at = str(s.get("created_at") or "").strip()
                parsed_t = _parse_iso_timestamp(c_at)
                if parsed_t is not None:
                    turn_ts.append((parsed_t, c_at))
                    all_timestamps.append((parsed_t, c_at))

            start_iso = turn_ts[0][1] if turn_ts else str(first_step.get("created_at") or "")
            end_iso = turn_ts[-1][1] if turn_ts else str(last_step.get("created_at") or "")

            if turn_ts:
                t0 = turn_ts[0][0]
                t1 = turn_ts[-1][0]
                if is_last_turn and result["is_running"]:
                    t1 = max(t1, time.time())
                dur_sec = max(0.0, round(t1 - t0, 1))
            else:
                dur_sec = 0.0

            total_active_sec += dur_sec

            # Count tool calls and extract latest planner response
            turn_tools = 0
            turn_tool_names: List[str] = []
            turn_subagents = 0
            latest_response = ""
            turn_error_text = ""
            turn_thinking: List[str] = []
            files_dict: Dict[str, Dict[str, str]] = {}

            # Token extraction & Waterfall execution sequence
            turn_input_tokens = 0
            turn_cache_read_tokens = 0
            turn_output_tokens = 0
            turn_context_peak = 0

            waterfall_steps: List[Dict[str, Any]] = []
            total_model_sec = 0.0
            total_tool_sec = 0.0
            last_step_time = _parse_iso_timestamp(str(first_step.get("created_at") or ""))
            pending_tools: List[Dict[str, Any]] = []

            for s_i, s in enumerate(g_steps):
                s_type = str(s.get("type") or "").upper()
                s_status = str(s.get("status") or "DONE").upper()
                s_err = str(s.get("error") or "").strip()
                s_idx = s.get("step_index", 0)
                cur_time = _parse_iso_timestamp(str(s.get("created_at") or ""))
                in_tok = int(s.get("input_tokens") or 0)
                cache_tok = int(s.get("cache_read_tokens") or 0)
                out_tok = int(s.get("output_tokens") or 0)

                # Calculate duration of this specific step
                step_dur_sec = 0.0
                if s_idx in db_durations:
                    step_dur_sec = db_durations[s_idx]
                elif cur_time is not None:
                    next_time = None
                    if s_i + 1 < len(g_steps):
                        next_time = _parse_iso_timestamp(str(g_steps[s_i + 1].get("created_at") or ""))
                    if next_time is not None:
                        step_dur_sec = max(0.0, round(next_time - cur_time, 1))
                    elif is_last_turn and result["is_running"]:
                        step_dur_sec = max(0.0, round(time.time() - cur_time, 1))
                    elif turn_ts and turn_ts[-1][0] > cur_time:
                        step_dur_sec = max(0.0, round(turn_ts[-1][0] - cur_time, 1))
                    else:
                        step_dur_sec = 0.5 if (s_type == "PLANNER_RESPONSE" and out_tok > 0) else 0.0

                # Extract model reasoning / thinking
                th = str(s.get("thinking") or "").strip()
                if th and th not in turn_thinking:
                    turn_thinking.append(th)

                # Token extraction on PLANNER_RESPONSE
                if s_type == "PLANNER_RESPONSE":
                    turn_input_tokens += in_tok
                    turn_cache_read_tokens += cache_tok
                    turn_output_tokens += out_tok
                    ctx_here = in_tok + cache_tok
                    if ctx_here > turn_context_peak:
                        turn_context_peak = ctx_here

                    c_text = str(s.get("content") or "").strip()
                    if c_text:
                        latest_response = c_text

                    t_calls = s.get("tool_calls")
                    has_tools = isinstance(t_calls, list) and len(t_calls) > 0
                    if has_tools:
                        turn_tools += len(t_calls)
                        for tc in t_calls:
                            if isinstance(tc, dict):
                                t_name = str(tc.get("name") or tc.get("tool_name") or "").strip()
                                if t_name:
                                    if t_name not in turn_tool_names:
                                        turn_tool_names.append(t_name)
                                    if "invoke_subagent" in t_name:
                                        turn_subagents += 1

                                    # Extract files impacted
                                    args = tc.get("args") or {}
                                    if isinstance(args, str):
                                        try:
                                            args = json.loads(args)
                                        except Exception:
                                            args = {}
                                    elif not isinstance(args, dict):
                                        args = {}

                                    raw_file = None
                                    action = None
                                    if t_name in ("replace_file_content", "write_to_file"):
                                        raw_file = (
                                            args.get("TargetFile")
                                            or args.get("target_file")
                                            or args.get("file_path")
                                            or args.get("path")
                                        )
                                        action = "Created" if t_name == "write_to_file" else "Modified"
                                    elif t_name in ("view_file", "read_file"):
                                        raw_file = (
                                            args.get("AbsolutePath")
                                            or args.get("absolute_path")
                                            or args.get("file_path")
                                            or args.get("path")
                                        )
                                        action = "Read"

                                    if raw_file and isinstance(raw_file, str):
                                        clean_file = raw_file.strip("\"' ").replace("\\", "/")
                                        if clean_file:
                                            norm_path = os.path.normpath(clean_file)
                                            basename = os.path.basename(norm_path) or clean_file
                                            # Modified/Created overrides Read
                                            if norm_path not in files_dict or action in ("Modified", "Created"):
                                                files_dict[norm_path] = {
                                                    "path": norm_path,
                                                    "name": basename,
                                                    "action": action,
                                                    "tool": t_name,
                                                }

                    # Waterfall entry for Model Generation
                    total_model_sec += step_dur_sec
                    step_speed = round(out_tok / step_dur_sec, 1) if step_dur_sec > 0.05 else 0.0
                    phase_name = "Model Gen" if has_tools else "Final Response"
                    summary = th[:60] + "..." if len(th) > 60 else (th or "Model deliberation & planning")
                    if not has_tools and c_text:
                        summary = c_text[:60] + "..." if len(c_text) > 60 else c_text

                    waterfall_steps.append({
                        "step_index": s_idx,
                        "phase": "model",
                        "name": phase_name,
                        "summary": summary,
                        "duration_seconds": step_dur_sec,
                        "duration_formatted": format_duration_human(step_dur_sec),
                        "status": s_status,
                        "is_bottleneck": bool(step_dur_sec >= 10.0),
                        "error": s_err,
                        "input_tokens": in_tok,
                        "cache_read_tokens": cache_tok,
                        "output_tokens": out_tok,
                        "total_tokens": in_tok + cache_tok + out_tok,
                        "tokens_per_second": step_speed,
                    })

                    if has_tools:
                        pending_tools = [
                            {
                                "name": str(tc.get("name") or tc.get("tool_name") or "tool").strip(),
                                "args": tc.get("args") or {},
                            }
                            for tc in t_calls
                            if isinstance(tc, dict)
                        ]
                    else:
                        pending_tools = []

                elif s_type in ("GENERIC", "TOOL_RESULT") or pending_tools:
                    # Tool Execution Step
                    total_tool_sec += step_dur_sec
                    current_tool = pending_tools.pop(0) if pending_tools else None
                    tool_name = current_tool.get("name", "tool") if current_tool else "tool_execution"
                    args = current_tool.get("args", {}) if current_tool else {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    elif not isinstance(args, dict):
                        args = {}

                    cmd = args.get("CommandLine") or args.get("command") or args.get("query")
                    target_f = args.get("TargetFile") or args.get("AbsolutePath") or args.get("path")
                    summary_label = args.get("toolSummary") or args.get("toolAction")

                    if summary_label:
                        clean_summary = str(summary_label).strip("\"' ")
                    elif cmd:
                        clean_summary = str(cmd).strip("\"' ")[:60]
                    elif target_f:
                        clean_summary = os.path.basename(str(target_f).strip("\"' "))
                    else:
                        clean_summary = f"{tool_name} execution"

                    waterfall_steps.append({
                        "step_index": s_idx,
                        "phase": "tool",
                        "name": tool_name,
                        "summary": clean_summary,
                        "duration_seconds": step_dur_sec,
                        "duration_formatted": format_duration_human(step_dur_sec),
                        "status": s_status,
                        "is_bottleneck": bool(step_dur_sec >= 10.0),
                        "error": s_err,
                        "input_tokens": 0,
                        "cache_read_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "tokens_per_second": 0.0,
                    })

                if s_status in ("ERROR", "INVALID") or s_err:
                    turn_error_text = f"{s_err} {s.get('content') or ''}".strip()

                if cur_time is not None:
                    last_step_time = cur_time

            total_tools += turn_tools
            total_subagents += turn_subagents

            # Sort files: Created, Modified, then Read
            action_prio = {"Created": 0, "Modified": 1, "Read": 2}
            files_impacted = sorted(
                files_dict.values(),
                key=lambda x: (action_prio.get(x["action"], 99), x["name"].lower()),
            )
            thinking_text = "\n\n────────────────────────────────────────\n\n".join(turn_thinking)

            # Token percentages
            total_prompt_tok = turn_input_tokens + turn_cache_read_tokens
            cache_hit_rate = (turn_cache_read_tokens / total_prompt_tok * 100.0) if total_prompt_tok > 0 else 0.0

            # Determine turn status
            last_s_type = str(last_step.get("type") or "").upper()
            last_s_status = str(last_step.get("status") or "").upper()
            last_s_tools = last_step.get("tool_calls")
            last_has_tools = bool(isinstance(last_s_tools, list) and len(last_s_tools) > 0)

            if is_last_turn and result["is_running"]:
                turn_status = "GENERATING"
            elif turn_error_text and _is_quota_error_text(turn_error_text):
                turn_status = "QUOTA_EXHAUSTED"
            elif is_last_turn and result["overall_state"] not in ("COMPLETED", "NORMAL", "UNKNOWN"):
                turn_status = result["overall_state"]
            elif last_s_status in ("ERROR", "INVALID"):
                turn_status = "ERRORED"
            elif last_s_type == "PLANNER_RESPONSE" and last_s_status == "DONE" and not last_has_tools:
                turn_status = "COMPLETED"
            elif not is_last_turn:
                # Followed by a subsequent user prompt or resume
                turn_status = "COMPLETED" if latest_response else "STOPPED_BY_USER"
            else:
                turn_status = "STOPPED_BY_USER" if not turn_error_text else "ERRORED"

            if not latest_response and turn_error_text:
                latest_response = f"[Error / Halted]: {turn_error_text[:500]}"

            turn_speed = round(turn_output_tokens / total_model_sec, 1) if total_model_sec > 0.05 else 0.0
            wall_speed = round(turn_output_tokens / dur_sec, 1) if dur_sec > 0.05 else 0.0

            turns.append({
                "prompt_index": idx,
                "prompt_text": prompt_text,
                "is_auto_resume": is_auto_resume,
                "response_text": latest_response or "(No final planner response recorded for this turn yet)",
                "started_at": _format_timestamp_display(start_iso),
                "ended_at": _format_timestamp_display(end_iso),
                "duration_seconds": dur_sec,
                "duration_formatted": format_duration_human(dur_sec),
                "steps_count": len(g_steps),
                "tool_calls_count": turn_tools,
                "tool_names": turn_tool_names,
                "subagents_count": turn_subagents,
                "status": turn_status,
                "thinking_text": thinking_text,
                "files_impacted": files_impacted,
                "input_tokens": turn_input_tokens,
                "cache_read_tokens": turn_cache_read_tokens,
                "output_tokens": turn_output_tokens,
                "total_tokens": turn_input_tokens + turn_cache_read_tokens + turn_output_tokens,
                "cache_hit_rate": round(cache_hit_rate, 1),
                "context_window_peak": turn_context_peak,
                "total_model_duration_seconds": round(total_model_sec, 1),
                "total_tool_duration_seconds": round(total_tool_sec, 1),
                "tokens_per_second": turn_speed,
                "wall_clock_speed": wall_speed,
                "waterfall_steps": waterfall_steps,
                "start_step_index": first_step.get("step_index", 0),
                "end_step_index": last_step.get("step_index", len(g_steps) - 1),
            })

        result["prompt_turns"] = turns
        result["total_prompts"] = len(turns)
        result["auto_resume_prompts_count"] = sum(1 for t in turns if t.get("is_auto_resume"))
        result["user_prompts_count"] = len(turns) - result["auto_resume_prompts_count"]
        result["total_tool_calls"] = total_tools
        result["total_subagents"] = total_subagents
        result["total_active_duration_seconds"] = round(total_active_sec, 1)
        result["total_active_duration_formatted"] = format_duration_human(total_active_sec)
        uncached_in = sum(t.get("input_tokens", 0) for t in turns)
        cached_in = sum(t.get("cache_read_tokens", 0) for t in turns)
        out_tokens = sum(t.get("output_tokens", 0) for t in turns)
        tot_prompt = uncached_in + cached_in
        hit_rate = round((cached_in / tot_prompt * 100.0), 1) if tot_prompt > 0 else 0.0

        result["conversation_peak_context"] = max([t.get("context_window_peak", 0) for t in turns] + [0])
        result["conversation_uncached_input_tokens"] = uncached_in
        result["conversation_cache_tokens"] = cached_in
        result["conversation_output_tokens"] = out_tokens
        result["conversation_total_prompt_tokens"] = tot_prompt
        result["conversation_total_tokens"] = uncached_in + cached_in + out_tokens
        result["conversation_cache_hit_rate"] = hit_rate

        conv_model_sec = round(sum(t.get("total_model_duration_seconds", 0.0) for t in turns), 1)
        conv_tool_sec = round(sum(t.get("total_tool_duration_seconds", 0.0) for t in turns), 1)
        conv_speed = round(out_tokens / conv_model_sec, 1) if conv_model_sec > 0.05 else 0.0
        result["conversation_model_duration_seconds"] = conv_model_sec
        result["conversation_tool_duration_seconds"] = conv_tool_sec
        result["conversation_generation_speed"] = conv_speed

        if all_timestamps:
            all_timestamps.sort(key=lambda x: x[0])
            first_ts, first_iso = all_timestamps[0]
            last_ts, last_iso = all_timestamps[-1]
            if result["is_running"]:
                last_ts = max(last_ts, time.time())
            wall_sec = max(0.0, round(last_ts - first_ts, 1))
            result["first_started_at"] = _format_timestamp_display(first_iso)
            result["last_active_at"] = _format_timestamp_display(last_iso)
            result["wall_clock_duration_seconds"] = wall_sec
            result["wall_clock_duration_formatted"] = format_duration_human(wall_sec)

        if not result["title"] and turns:
            first_line = turns[0]["prompt_text"].splitlines()[0] if turns[0]["prompt_text"] else clean_id[:12]
            result["title"] = first_line[:70]
    else:
        # 4. Fallback to SQLite steps table if transcript.jsonl does not exist
        fallback_turns = _segment_sqlite_steps_fallback(
            clean_id,
            is_running=result["is_running"],
            overall_state=result["overall_state"],
            app_data_dir=app_data_dir,
        )
        if fallback_turns:
            result["found"] = True
            result["prompt_turns"] = fallback_turns
            result["total_prompts"] = len(fallback_turns)
            result["total_steps"] = max(
                result["total_steps"],
                sum(t["steps_count"] for t in fallback_turns),
            )
            result["total_tool_calls"] = sum(t["tool_calls_count"] for t in fallback_turns)

    if not result["title"]:
        result["title"] = f"Conversation {clean_id[:8]}"

    # 5. Collect generated artifacts in brain/<conversation_id>/
    result["artifacts"] = list_conversation_artifacts(clean_id, brain_dir=brain_dir)

    # 6. Collect subagent fleet & tree telemetry
    try:
        from cdp.subagent_inspector import get_subagents_fleet_summary
        fleet_summary = get_subagents_fleet_summary(clean_id, brain_dir=brain_dir)
        result["subagents_summary"] = fleet_summary
        result["subagents_tree"] = fleet_summary.get("subagents_tree", [])
        if fleet_summary.get("total_count", 0) > 0:
            result["total_subagents"] = fleet_summary.get("total_count", 0)
    except Exception:
        result["subagents_summary"] = {}
        result["subagents_tree"] = []

    return result
