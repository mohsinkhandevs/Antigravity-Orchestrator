import pytest
from app.models import (
    PromptItem,
    TaskStatus,
    ExecutionStep,
    strip_prompt_command,
    detect_prompt_mode,
    ensure_prompt_prefix,
    ensure_goal_prefix,
    ensure_plan_prefix,
)


def test_prompt_item_serialization():
    item = PromptItem(text="Test prompt", sequence=1)
    d = item.to_dict()
    assert d["text"] == "Test prompt"
    assert d["status"] == TaskStatus.PENDING.value
    assert d["sequence"] == 1

    item2 = PromptItem.from_dict(d)
    assert item2.text == "Test prompt"
    assert item2.status == TaskStatus.PENDING
    assert item2.sequence == 1


def test_strip_prompt_command():
    assert strip_prompt_command("/goal Write tests") == ("/goal", "Write tests")
    assert strip_prompt_command("/plan Design architecture") == ("/plan", "Design architecture")
    assert strip_prompt_command("Plain prompt") == ("", "Plain prompt")
    assert strip_prompt_command("\\goal Backslash goal") == ("/goal", "Backslash goal")


def test_ensure_prompt_prefix():
    assert ensure_prompt_prefix("Write tests", "/goal") == "/goal Write tests"
    assert ensure_prompt_prefix("/goal Already goal", "/goal") == "/goal Already goal"
    assert ensure_prompt_prefix("/goal Switch to plan", "/plan") == "/plan Switch to plan"
    assert ensure_prompt_prefix("/goal Remove prefix", "none") == "Remove prefix"


def test_ensure_goal_and_plan_prefix():
    assert ensure_goal_prefix("Do this") == "/goal Do this"
    assert ensure_plan_prefix("Do this") == "/plan Do this"


def test_prompt_item_multi_project_and_resume_fields():
    item = PromptItem(
        text="Build API",
        project="Agrico-V2",
        conversation_id="conv-1234-uuid",
        is_resumed=True,
        resume_count=1,
    )
    d = item.to_dict()
    assert d["project"] == "Agrico-V2"
    assert d["conversation_id"] == "conv-1234-uuid"
    assert d["is_resumed"] is True
    assert d["resume_count"] == 1

    restored = PromptItem.from_dict(d)
    assert restored.project == "Agrico-V2"
    assert restored.conversation_id == "conv-1234-uuid"
    assert restored.is_resumed is True
    assert restored.resume_count == 1


def test_prompt_item_backward_compatibility_deserialization():
    legacy_data = {
        "text": "Legacy prompt without project",
        "id": "leg-001",
        "sequence": 5,
        "status": "Completed",
        "elapsed_time": 42.5,
        "error_message": "",
    }
    restored = PromptItem.from_dict(legacy_data)
    assert restored.text == "Legacy prompt without project"
    assert restored.id == "leg-001"
    assert restored.sequence == 5
    assert restored.status == TaskStatus.COMPLETED
    assert restored.elapsed_time == 42.5
    assert restored.project == ""
    assert restored.conversation_id == ""
    assert restored.is_resumed is False
    assert restored.resume_count == 0


def test_import_prompts_with_project(tmp_path):
    from app.models import import_prompts_from_txt, import_prompts_from_json, import_prompts_from_markdown

    txt_file = tmp_path / "prompts.txt"
    txt_file.write_text("Prompt one\nPrompt two\n", encoding="utf-8")
    items = import_prompts_from_txt(str(txt_file), project="Antigravity CDP")
    assert len(items) == 2
    assert items[0].project == "Antigravity CDP"
    assert items[1].project == "Antigravity CDP"

    json_file = tmp_path / "prompts.json"
    json_file.write_text('["Prompt A", "Prompt B"]', encoding="utf-8")
    items_json = import_prompts_from_json(str(json_file), project="2 step automation")
    assert len(items_json) == 2
    assert items_json[0].project == "2 step automation"

    md_file = tmp_path / "prompts.md"
    md_file.write_text("- Task alpha\n- Task beta\n", encoding="utf-8")
    items_md = import_prompts_from_markdown(str(md_file), project="Agrico-V2")
    assert len(items_md) == 2
    assert items_md[0].project == "Agrico-V2"


def test_thread_safe_prompt_queue_concurrent_mutations():
    import threading
    import time
    from app.models import ThreadSafePromptQueue, PromptItem, TaskStatus

    q = ThreadSafePromptQueue([
        PromptItem(text=f"Prompt {i}", sequence=i, id=f"id-{i}") for i in range(20)
    ])

    stop_evt = threading.Event()
    errors = []

    def modifier():
        counter = 100
        while not stop_evt.is_set():
            counter += 1
            item = PromptItem(text=f"Dynamic {counter}", id=f"dyn-{counter}")
            q.append(item)
            q.update_item_status(f"id-{counter % 20}", TaskStatus.RUNNING)
            if len(q) > 50:
                try:
                    q.pop()
                except IndexError:
                    pass
            time.sleep(0.001)

    def reader():
        while not stop_evt.is_set():
            try:
                # Iteration should never throw RuntimeError: list changed size during iteration
                count = sum(1 for _ in q)
                assert count >= 0
                snap = q.snapshot()
                assert isinstance(snap, list)
                idx = q.index_by_id("id-5")
                assert idx != -1 or "id-5" not in [x.id for x in q]
            except Exception as e:
                errors.append(e)
            time.sleep(0.001)

    t_mod = threading.Thread(target=modifier)
    t_read = threading.Thread(target=reader)

    t_mod.start()
    t_read.start()

    time.sleep(0.3)
    stop_evt.set()

    t_mod.join()
    t_read.join()

    assert len(errors) == 0, f"Thread-safety errors encountered: {errors}"


def test_atomic_queue_export_and_backup_recovery(tmp_path):
    import json
    from app.models import (
        ThreadSafePromptQueue,
        PromptItem,
        export_prompts_to_json,
        import_prompts_from_json,
        get_queue_backup_path,
    )

    queue_file = tmp_path / "queue.json"
    backup_file = tmp_path / "queue.backup.json"

    items = ThreadSafePromptQueue([
        PromptItem(text="Prompt Alpha", project="Agrico", id="a1"),
        PromptItem(text="Prompt Beta", project="CDP", id="b2"),
    ])

    # 1. Export atomically
    export_prompts_to_json(items, str(queue_file))
    assert queue_file.exists()
    assert backup_file.exists()

    # Verify contents
    with open(str(queue_file), "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 2
    assert data[0]["id"] == "a1"

    # 2. Simulate catastrophic corruption / wipe of main queue file
    with open(str(queue_file), "w", encoding="utf-8") as f:
        f.write("")  # Empty file crash artifact

    # 3. Import should auto-recover from queue.backup.json
    recovered = import_prompts_from_json(str(queue_file))
    assert len(recovered) == 2
    assert recovered[0].id == "a1"
    assert recovered[1].id == "b2"

    # Verify main queue file was auto-restored
    assert queue_file.stat().st_size > 0

