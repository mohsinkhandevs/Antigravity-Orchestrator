import os
import json
import pytest
from app.account_manager import AccountManager, GoogleAccount


def test_production_file_detection(tmp_path):
    from app.config import get_user_data_dir

    prod_path = os.path.abspath(os.path.join(get_user_data_dir(), "accounts.json"))
    prod_am = AccountManager(filepath=prod_path)
    assert prod_am._is_production_file() is True

    tmp_file = str(tmp_path / "accounts.json")
    tmp_am = AccountManager(filepath=tmp_file)
    assert tmp_am._is_production_file() is False


def test_save_accounts_blocks_write_to_production_during_pytest(tmp_path, monkeypatch):
    fake_user_dir = tmp_path / "app_data"
    fake_user_dir.mkdir(parents=True, exist_ok=True)
    prod_path = fake_user_dir / "accounts.json"
    original_content = '[{"email": "initial@test.com"}]'
    prod_path.write_text(original_content, encoding="utf-8")

    monkeypatch.setattr("app.account_manager.get_user_data_dir", lambda: str(fake_user_dir))

    am = AccountManager(filepath=str(prod_path))
    assert am._is_production_file() is True

    # Attempt to overwrite with dummy accounts
    am.accounts = [GoogleAccount(email="malicious_overwrite@test.com", display_name="Fake")]
    am.save_accounts()

    # Verify prod_path was NOT changed
    current_content = prod_path.read_text(encoding="utf-8")
    assert original_content == current_content
    assert "malicious_overwrite@test.com" not in current_content


def test_auto_recovery_from_backup_file(tmp_path):
    main_file = tmp_path / "accounts.json"
    backup_file = tmp_path / "accounts.backup.json"

    # Write valid accounts to backup file
    backup_data = [
        {"email": "backup_user@gmail.com", "display_name": "Backup User", "quota_status": "Ready"}
    ]
    with open(backup_file, "w", encoding="utf-8") as f:
        json.dump(backup_data, f)

    # Main file does not exist yet
    assert not os.path.exists(main_file)

    am = AccountManager(filepath=str(main_file))
    # Should recover from backup
    assert len(am.accounts) == 1
    assert am.accounts[0].email == "backup_user@gmail.com"
    # Main file should have been restored
    assert os.path.exists(main_file)


def test_atomic_save_and_backup_creation_on_temp_file(tmp_path):
    target_file = tmp_path / "accounts.json"
    backup_file = tmp_path / "accounts.backup.json"

    am = AccountManager(filepath=str(target_file))
    am.accounts = [
        GoogleAccount(email="test1@domain.com", display_name="Test 1", quota_status="Ready"),
        GoogleAccount(email="test2@domain.com", display_name="Test 2", quota_status="Ready"),
    ]
    # In temp path, save_accounts proceeds and creates backup
    am.save_accounts()

    assert os.path.exists(target_file)
    with open(target_file, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert len(saved) == 2

    # Update and save again to trigger backup mirroring
    am.accounts.append(GoogleAccount(email="test3@domain.com", display_name="Test 3"))
    am.save_accounts()

    assert os.path.exists(backup_file)
    with open(backup_file, "r", encoding="utf-8") as f:
        bk_saved = json.load(f)
    # Backup has the previous 2 accounts before the update
    assert len(bk_saved) == 2
