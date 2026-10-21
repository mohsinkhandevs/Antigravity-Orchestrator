"""
Reconciliation Module: Inspects Antigravity's local SQLite database and transcript files
to reconcile the true completion status of prompts using their conversation_id.
"""

from typing import Optional, Dict, Any
from app.models import PromptItem, TaskStatus
from cdp.conversation_db_inspector import (
    inspect_conversation_db_termination,
    get_conversation_summary,
)
from cdp.transcript_inspector import inspect_transcript_termination


def reconcile_prompt_status_from_conversation(
    prompt: PromptItem,
    app_data_dir: Optional[str] = None,
    brain_dir: Optional[str] = None,
) -> bool:
    """
    Inspects Antigravity's local SQLite summaries/conversation databases and transcript logs
    for the prompt's conversation_id to determine if execution finished or is still running.

    Updates prompt.status and prompt.error_message in-place.
    Returns True if prompt's status or error message was modified, False otherwise.
    """
    cid = (getattr(prompt, "conversation_id", "") or "").strip()
    if not cid:
        return False

    # 1. Inspect conversation databases and transcript
    db_term = inspect_conversation_db_termination(cid, app_data_dir=app_data_dir)
    trans_term = inspect_transcript_termination(cid, brain_dir=brain_dir)
    summary = get_conversation_summary(cid, app_data_dir=app_data_dir)

    # 2. Check if still actively running in Antigravity engine
    is_running = (
        summary.get("not_fully_idle") == 1
        or summary.get("status") == "CASCADE_RUN_STATUS_RUNNING"
        or db_term.get("state") in ("RUNNING", "WAITING_FOR_INPUT")
        or trans_term.get("last_step_status") == "RUNNING"
        or trans_term.get("state") == "WAITING_FOR_INPUT"
    )
    if is_running and db_term.get("state") != "NORMAL" and trans_term.get("state") != "NORMAL":
        modified = False
        if prompt.status != TaskStatus.RUNNING:
            prompt.status = TaskStatus.RUNNING
            modified = True
        running_msg = "Actively generating in background"
        if prompt.error_message != running_msg:
            prompt.error_message = running_msg
            modified = True
        return modified

    # 3. Check if completed normally in either database or transcript
    non_normal_db_states = (
        "QUOTA_EXHAUSTED",
        "STEP_ERROR",
        "EXECUTION_ERROR",
        "INTERRUPTED",
        "WAITING_FOR_INPUT",
        "USER_STOPPED",
        "RUNNING",
    )
    non_normal_trans_states = (
        "QUOTA_EXHAUSTED",
        "ERRORED",
        "INTERRUPTED",
        "WAITING_FOR_INPUT",
    )
    is_normal = (
        (
            db_term.get("state") == "NORMAL"
            and trans_term.get("state") not in non_normal_trans_states
        )
        or (
            trans_term.get("state") == "NORMAL"
            and db_term.get("state") not in non_normal_db_states
        )
        or (
            summary.get("found")
            and summary.get("is_idle")
            and not summary.get("killed")
            and db_term.get("state") not in non_normal_db_states
            and trans_term.get("state") not in non_normal_trans_states
        )
    )

    if is_normal:
        modified = False
        if prompt.status != TaskStatus.COMPLETED:
            prompt.status = TaskStatus.COMPLETED
            modified = True
        if prompt.error_message:
            prompt.error_message = ""
            modified = True
        return modified

    # 4. Check for quota exhaustion (429)
    if db_term.get("state") == "QUOTA_EXHAUSTED" or trans_term.get("state") == "QUOTA_EXHAUSTED":
        quota_msg = db_term.get("detail") or trans_term.get("detail") or "Quota exhausted (429)"
        if "quota" not in quota_msg.lower() and "429" not in quota_msg:
            quota_msg = f"Quota exhausted (429): {quota_msg}"
        modified = False
        if prompt.status != TaskStatus.FAILED:
            prompt.status = TaskStatus.FAILED
            modified = True
        if prompt.error_message != quota_msg:
            prompt.error_message = quota_msg
            modified = True
        return modified

    # 5. Check for step or execution errors
    if db_term.get("state") in ("STEP_ERROR", "EXECUTION_ERROR") or trans_term.get("state") == "ERRORED":
        err_msg = db_term.get("detail") or trans_term.get("detail") or "Execution error in Antigravity"
        modified = False
        if prompt.status != TaskStatus.FAILED:
            prompt.status = TaskStatus.FAILED
            modified = True
        if prompt.error_message != err_msg:
            prompt.error_message = err_msg
            modified = True
        return modified

    # 6. Check if stopped by user
    if summary.get("killed") or db_term.get("state") == "USER_STOPPED":
        stop_msg = "Stopped by user in Antigravity"
        modified = False
        if prompt.status != TaskStatus.CANCELLED:
            prompt.status = TaskStatus.CANCELLED
            modified = True
        if prompt.error_message != stop_msg:
            prompt.error_message = stop_msg
            modified = True
        return modified

    return False
