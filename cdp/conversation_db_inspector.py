import os
import sqlite3
import re
from typing import Any, Dict, Optional


DEFAULT_APP_DATA_DIR = os.path.expanduser("~/.gemini/antigravity")


def get_conversations_db_path(conversation_id: str, app_data_dir: Optional[str] = None) -> Optional[str]:
    """
    Returns the absolute path to <conversation_id>.db inside conversations/.
    """
    clean_id = (conversation_id or "").strip()
    if not clean_id:
        return None
    base = app_data_dir or DEFAULT_APP_DATA_DIR
    path = os.path.join(base, "conversations", f"{clean_id}.db")
    return path


def get_summaries_db_path(app_data_dir: Optional[str] = None) -> str:
    """
    Returns the absolute path to conversation_summaries.db.
    """
    base = app_data_dir or DEFAULT_APP_DATA_DIR
    return os.path.join(base, "conversation_summaries.db")


def _connect_ro(db_path: str) -> Optional[sqlite3.Connection]:
    """
    Opens an SQLite connection in read-only mode using a URI to prevent WAL locking
    against Antigravity's active database engine.
    """
    if not os.path.isfile(db_path):
        return None
    try:
        norm_path = os.path.abspath(db_path).replace("\\", "/")
        uri = f"file:///{norm_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        return conn
    except Exception:
        return None


def get_conversation_summary(conversation_id: str, app_data_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Queries conversation_summaries.db to check high-level engine execution status:
    - is_idle: True when not_fully_idle == 0 and status == 'CASCADE_RUN_STATUS_IDLE'
    - status: 'CASCADE_RUN_STATUS_RUNNING' or 'CASCADE_RUN_STATUS_IDLE'
    - not_fully_idle: 0 (idle) or 1 (running)
    - killed: True if user explicitly killed the agent
    - step_count: total step count
    """
    clean_id = (conversation_id or "").strip()
    result = {
        "found": False,
        "is_idle": True,
        "status": "UNKNOWN",
        "not_fully_idle": 0,
        "killed": False,
        "step_count": 0,
    }
    if not clean_id:
        return result

    db_path = get_summaries_db_path(app_data_dir=app_data_dir)
    conn = _connect_ro(db_path)
    if not conn:
        return result

    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT status, not_fully_idle, killed, step_count, workspace_uris FROM conversation_summaries WHERE conversation_id = ? LIMIT 1",
                (clean_id,),
            )
            row = cur.fetchone()
            ws = str(row[4] or "") if row and len(row) > 4 else ""
        except sqlite3.OperationalError:
            cur.execute(
                "SELECT status, not_fully_idle, killed, step_count FROM conversation_summaries WHERE conversation_id = ? LIMIT 1",
                (clean_id,),
            )
            row = cur.fetchone()
            ws = ""
        if row:
            status_text = row[0] or ""
            not_idle = int(row[1] or 0)
            is_killed = bool(row[2])
            steps = int(row[3] or 0)
            is_idle = (not_idle == 0) and (status_text == "CASCADE_RUN_STATUS_IDLE")

            result.update({
                "found": True,
                "is_idle": is_idle,
                "status": status_text,
                "not_fully_idle": not_idle,
                "killed": is_killed,
                "step_count": steps,
                "workspace_uris": ws,
            })
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return result


def find_running_conversations_in_db(
    project_name: Optional[str] = None,
    app_data_dir: Optional[str] = None,
) -> list:
    """
    Returns list of conversation dicts that are actively generating (not_fully_idle = 1 or status CASCADE_RUN_STATUS_RUNNING).
    If project_name is provided, filters to conversations whose workspace_uris contains project_name.
    """
    db_path = get_summaries_db_path(app_data_dir=app_data_dir)
    conn = _connect_ro(db_path)
    if not conn:
        return []
    try:
        import urllib.parse
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT conversation_id, title, workspace_uris FROM conversation_summaries "
                "WHERE (not_fully_idle = 1 OR status = 'CASCADE_RUN_STATUS_RUNNING')",
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            cur.execute(
                "SELECT conversation_id, title FROM conversation_summaries "
                "WHERE (not_fully_idle = 1 OR status = 'CASCADE_RUN_STATUS_RUNNING')",
            )
            rows = [(r[0], r[1], "") for r in cur.fetchall()]
        results = []
        clean_target = (project_name or "").strip().lower()
        for r in rows:
            cid, title, ws = r[0], r[1] or "", str(r[2] or "")
            if clean_target and clean_target != "no project":
                unquoted_ws = urllib.parse.unquote(ws).lower()
                clean_ws = ws.lower()
                if (clean_target in unquoted_ws) or (clean_target in clean_ws) or (clean_target in title.lower()):
                    results.append({"id": cid, "title": title, "workspace": ws})
            elif clean_target == "no project":
                if not ws or ws in ("[]", '""', "null"):
                    results.append({"id": cid, "title": title, "workspace": ws})
            else:
                results.append({"id": cid, "title": title, "workspace": ws})
        return results
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def inspect_conversation_db_termination(
    conversation_id: str,
    app_data_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Inspects <conversation_id>.db to classify the terminal execution state of the conversation:
    - NORMAL: Finished cleanly (last step status=3, PLANNER_RESPONSE, no execution error).
    - QUOTA_EXHAUSTED: Latest turn in executor_metadata contains RESOURCE_EXHAUSTED / 429.
    - STEP_ERROR: Last step status is 7 (unhandled tool/execution error).
    - EXECUTION_ERROR: Latest turn in executor_metadata contains fatal execution error.
    - USER_STOPPED: Killed flag set in conversation_summaries.db.
    - WAITING_FOR_INPUT: Last step is awaiting user or model response.
    - INTERRUPTED: Stopped mid-turn before planner completed.
    """
    clean_id = (conversation_id or "").strip()
    result = {
        "found": False,
        "state": "UNKNOWN",
        "error_code": None,
        "detail": "",
        "last_step_status": None,
        "last_step_type": None,
        "is_idle": True,
    }
    if not clean_id:
        return result

    # 1. Check conversation_summaries.db for overall engine idle/killed state
    summary = get_conversation_summary(clean_id, app_data_dir=app_data_dir)
    if summary["found"]:
        result["is_idle"] = summary["is_idle"]

    # 2. Check <conversation_id>.db
    conv_path = get_conversations_db_path(clean_id, app_data_dir=app_data_dir)
    if not conv_path or not os.path.isfile(conv_path):
        if summary["found"] and summary["killed"]:
            result.update({
                "found": True,
                "state": "USER_STOPPED",
                "detail": "Killed flag set in conversation_summaries.db",
            })
        return result

    conn = _connect_ro(conv_path)
    if not conn:
        if summary["found"] and summary["killed"]:
            result.update({
                "found": True,
                "state": "USER_STOPPED",
                "detail": "Killed flag set in conversation_summaries.db",
            })
        return result

    try:
        cur = conn.cursor()

        # Step 2a: Query latest step and latest executor metadata
        cur.execute("SELECT idx, step_type, status, error_details FROM steps ORDER BY idx DESC LIMIT 1")
        step_row = cur.fetchone()

        cur.execute("SELECT idx, data FROM executor_metadata ORDER BY idx DESC LIMIT 1")
        meta_row = cur.fetchone()

        # Step 2b: Evaluate latest step status for active running or normal completion
        if step_row:
            s_idx, s_type, s_status, s_err = step_row
            result["found"] = True
            result["last_step_status"] = s_status
            result["last_step_type"] = s_type

            # Status 6 is still actively running (supersedes any historical killed flag)
            if s_status == 6:
                result.update({
                    "state": "RUNNING",
                    "detail": f"Step {s_idx} is actively running",
                })
                return result

        if summary["found"] and summary["killed"]:
            result.update({
                "found": True,
                "state": "USER_STOPPED",
                "detail": "Killed flag set in conversation_summaries.db",
            })
            return result

        if step_row:
            # Status 3 with PLANNER_RESPONSE (step_type=15) is a completed, successful turn.
            # Stale executor errors from prior turns are superseded by this completed step.
            if s_status == 3 and s_type == 15:
                result.update({
                    "state": "NORMAL",
                    "detail": f"Planner response completed normally at step {s_idx}",
                })
                return result

        # Step 2c: If the turn did NOT complete normally with a planner response, check executor_metadata for fatal errors
        if meta_row and meta_row[1]:
            blob = meta_row[1]
            if isinstance(blob, (bytes, bytearray)):
                blob_lower = blob.lower()
                if b"resource_exhausted" in blob_lower or b"code 429" in blob_lower or b"individual quota reached" in blob_lower:
                    err_msg = _extract_error_snippet(blob)
                    result.update({
                        "found": True,
                        "state": "QUOTA_EXHAUSTED",
                        "error_code": 429,
                        "detail": err_msg,
                    })
                    return result
                elif b"execution error:" in blob_lower:
                    err_msg = _extract_error_snippet(blob)
                    result.update({
                        "found": True,
                        "state": "EXECUTION_ERROR",
                        "error_code": 500,
                        "detail": err_msg,
                    })
                    return result

        # Step 2d: Check steps table for terminal error status 7
        if step_row and s_status == 7:
            err_text = _extract_step_error_text(s_err)
            if any(q in err_text.lower() for q in ("quota", "429", "exhausted", "capacity")):
                result.update({
                    "state": "QUOTA_EXHAUSTED",
                    "error_code": 429,
                    "detail": err_text or f"Step {s_idx} failed with status 7 (quota)",
                })
            else:
                result.update({
                    "state": "STEP_ERROR",
                    "error_code": 7,
                    "detail": err_text or f"Step {s_idx} terminated with error status 7",
                })
            return result

        # Step 2d: Intermediate step states without fatal executor errors
        if step_row and s_status == 3:
            if s_type in (14, 17):  # USER_INPUT
                result.update({
                    "state": "WAITING_FOR_INPUT",
                    "detail": f"Awaiting model response to user input at step {s_idx}",
                })
                return result
            elif s_type == 132:  # GENERIC tool output
                result.update({
                    "state": "INTERRUPTED",
                    "detail": f"Turn ended after tool output at step {s_idx} without final planner response",
                })
                return result

    except Exception as e:
        result["detail"] = f"DB inspection exception: {str(e)}"
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return result


def _extract_error_snippet(blob: bytes) -> str:
    """Extracts a printable error string from executor_metadata binary protobuf blob."""
    blob_lower = blob.lower()
    for needle in (b"execution error:", b"resource_exhausted", b"individual quota reached", b"code 429"):
        pos = blob_lower.find(needle)
        if pos != -1:
            start = max(0, pos - 20) if needle != b"execution error:" else pos
            sub = blob[start:pos + 300]
            try:
                clean = sub.decode("utf-8", errors="replace")
                clean = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", clean).strip()
                if clean:
                    return clean
            except Exception:
                pass
    return "execution error recorded in executor_metadata"


def _extract_step_error_text(err_blob: Optional[bytes]) -> str:
    """Extracts error message from steps.error_details protobuf blob."""
    if not err_blob or not isinstance(err_blob, (bytes, bytearray)):
        return ""
    try:
        decoded = err_blob.decode("utf-8", errors="replace")
        clean = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", decoded).strip()
        return clean[:300]
    except Exception:
        return ""
