import pytest
from unittest.mock import MagicMock, patch

from app.config import config
from app.models import PromptItem, TaskStatus
from app.worker import AutomationWorker
from cdp.dom_controller import AntigravityDOMController


def test_dom_controller_get_active_conversation_id():
    mock_page = MagicMock()
    mock_page.url = "https://127.0.0.1:57845/c/a6ae1b60-8c97-4515-8230-416ee9e182f8?mode=agent"

    dom = AntigravityDOMController(mock_page)
    cid = dom.get_active_conversation_id()
    assert cid == "a6ae1b60-8c97-4515-8230-416ee9e182f8"

    # Fallback to DOM evaluation if URL does not contain /c/
    mock_page.url = "https://127.0.0.1:57845/"
    mock_page.evaluate.return_value = "dom-cascade-uuid-999"
    cid2 = dom.get_active_conversation_id()
    assert cid2 == "dom-cascade-uuid-999"


def test_dom_controller_check_termination_state():
    mock_page = MagicMock()
    mock_input = MagicMock()
    mock_input.is_visible.return_value = True
    mock_input.is_enabled.return_value = True
    mock_page.locator.return_value.first = mock_input

    dom = AntigravityDOMController(mock_page)

    # 1. Quota Exhaustion
    mock_page.evaluate.return_value = {
        "state": "QUOTA_EXHAUSTED",
        "detail": "Resource has been exhausted (HTTP 429)",
    }
    term = dom.check_termination_state()
    assert term["state"] == "QUOTA_EXHAUSTED"
    assert "Resource has been exhausted" in term["detail"]
    assert term["can_resume"] is True

    # 2. Normal completion
    mock_page.evaluate.return_value = {
        "state": "NORMAL",
        "detail": "",
    }
    term_normal = dom.check_termination_state()
    assert term_normal["state"] == "NORMAL"
    assert term_normal["can_resume"] is True


def test_dom_controller_inject_resume_prompt(monkeypatch):
    mock_page = MagicMock()
    mock_input = MagicMock()
    mock_page.locator.return_value.first = mock_input

    copied = []
    monkeypatch.setattr("pyperclip.copy", lambda text: copied.append(text))
    monkeypatch.setattr("pyperclip.paste", lambda: "original_clipboard_data")

    dom = AntigravityDOMController(mock_page)
    success = dom.inject_resume_prompt("Please resume where you left off.", post_submit_delay=0.0)

    assert success is True
    assert copied[0] == "Please resume where you left off."
    assert copied[-1] == "original_clipboard_data"
    mock_input.click.assert_called_once()
    mock_page.keyboard.press.assert_any_call("Control+V")
    mock_page.keyboard.press.assert_any_call("Enter")


def test_worker_auto_resume_on_quota_interruption(monkeypatch):
    item = PromptItem(text="/goal Build website", project="Agrico-V2")
    worker = AutomationWorker(queue=[item])

    mock_dom = MagicMock()
    mock_dom.is_fresh_empty_chat_open.return_value = True
    mock_dom.open_new_conversation.return_value = True
    mock_dom.get_active_conversation_id.return_value = "c-123"
    mock_dom.wait_for_completion.return_value = True

    # First completion: quota exhausted. Second completion (after resume): clean normal finish!
    mock_dom.check_termination_state.side_effect = [
        {"state": "QUOTA_EXHAUSTED", "detail": "Out of credits"},
        {"state": "NORMAL", "detail": ""},
        {"state": "NORMAL", "detail": ""},
    ]

    worker.dom_controller = mock_dom
    monkeypatch.setattr(worker, "_wait_for_idle_agent", lambda *args, **kwargs: True)
    monkeypatch.setattr(worker, "_enforce_quota", lambda *args, **kwargs: True)

    monkeypatch.setattr(config, "enable_auto_resume_same_chat", True)
    monkeypatch.setattr(config, "max_resumes_per_prompt", 2)
    monkeypatch.setattr(config, "resume_mini_prompt", "Please continue and complete task.")

    success = worker._execute_prompt_cdp(item, 0, 1)

    assert success is True
    assert item.status == TaskStatus.COMPLETED
    assert item.is_resumed is True
    assert item.resume_count == 1
    assert item.conversation_id == "c-123"

    # CRITICAL: Verify open_new_conversation was called ONLY ONCE for the prompt start,
    # and was NOT called for the recovery (retained same chat!).
    assert mock_dom.open_new_conversation.call_count == 1
    mock_dom.inject_resume_prompt.assert_called_once_with("Please continue and complete task.")


def test_worker_auto_resume_exceeds_max_resumes(monkeypatch):
    item = PromptItem(text="/goal Hard task", project="Agrico-V2")
    worker = AutomationWorker(queue=[item])

    mock_dom = MagicMock()
    mock_dom.is_fresh_empty_chat_open.return_value = True
    mock_dom.open_new_conversation.return_value = True
    mock_dom.wait_for_completion.return_value = True

    # Quota is permanently exhausted across all resume attempts
    mock_dom.check_termination_state.return_value = {
        "state": "QUOTA_EXHAUSTED",
        "detail": "Account exhausted",
    }

    worker.dom_controller = mock_dom
    monkeypatch.setattr(worker, "_wait_for_idle_agent", lambda *args, **kwargs: True)
    monkeypatch.setattr(worker, "_enforce_quota", lambda *args, **kwargs: True)

    monkeypatch.setattr(config, "enable_auto_resume_same_chat", True)
    monkeypatch.setattr(config, "max_resumes_per_prompt", 2)

    success = worker._execute_prompt_cdp(item, 0, 1)

    assert success is False
    assert item.status == TaskStatus.FAILED
    assert "Exceeded max resumes" in item.error_message
