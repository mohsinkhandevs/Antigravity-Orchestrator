import os
import pytest
from app.account_manager import account_manager
from app.config import get_user_data_dir


@pytest.fixture(autouse=True)
def guard_production_accounts(monkeypatch):
    """
    Global safeguard ensuring that running any test in the test suite
    can NEVER mutate or overwrite the user's production accounts.json.
    """
    prod_path = os.path.abspath(os.path.join(get_user_data_dir(), "accounts.json"))
    orig_save = account_manager.save_accounts

    def safe_save_accounts(*args, **kwargs):
        if hasattr(account_manager, "filepath") and os.path.abspath(account_manager.filepath) == prod_path:
            return  # Block production file write during any test
        return orig_save(*args, **kwargs)

    monkeypatch.setattr(account_manager, "save_accounts", safe_save_accounts)


@pytest.fixture(scope="session")
def qapp():
    """Provides a shared QApplication instance for tests requiring Qt widgets/signals."""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        return app
    except ImportError:
        return None
