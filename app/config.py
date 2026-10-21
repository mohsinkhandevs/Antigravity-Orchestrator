import os
import sys
import json
import uuid
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List


def get_base_dir() -> str:
    """Returns base directory for bundled app or local source run."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return getattr(sys, "_MEIPASS")
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_user_data_dir() -> str:
    """Returns path to store user configuration and logs."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return get_base_dir()


def resolve_asset_path(filename: str) -> str:
    """Finds an asset file inside assets/ or root directory."""
    base = get_base_dir()
    candidates = [
        os.path.join(base, "assets", filename),
        os.path.join(base, filename),
        os.path.join(get_user_data_dir(), "assets", filename),
        os.path.join(get_user_data_dir(), filename),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(os.path.join(base, "assets", filename))


@dataclass
class AppConfig:
    window_title: str = "Antigravity"
    cdp_port: int = 9222
    target_project_name: str = "No Project"

    completion_stabilization_seconds: int = 3
    generation_timeout: int = 3600
    infinite_generation_timeout: bool = True
    emergency_hotkey: str = "F10"
    hud_hotkey: str = "Ctrl+Shift+H"
    check_quota_before_queue: bool = True
    check_quota_each_prompt: bool = True
    pause_if_quota_low: bool = True
    min_gemini_five_hour_quota: int = 15
    min_gemini_weekly_quota: int = 4
    low_quota_action: str = "change_account"  # "change_account" or "wait_for_reset"
    auto_switch_on_low_quota: bool = True
    auto_launch_antigravity: bool = True
    quota_retry_interval_seconds: int = 60
    quota_buffer_seconds: int = 60
    enable_auto_resume_same_chat: bool = True
    resume_mini_prompt: str = "Please resume where you left off and finish the remaining work."
    max_resumes_per_prompt: int = 2
    quota_cache_seconds: int = 60
    known_projects: List[str] = field(default_factory=lambda: [
        "No Project",
    ])
    max_concurrent_projects: int = 3
    polling_interval_seconds: int = 2
    enable_independent_project_resume: bool = True
    enable_foreground_hud: bool = True
    enable_pre_switch_hud: bool = True
    pre_switch_countdown_seconds: int = 30
    hud_x: int = -1
    hud_y: int = -1

    def __post_init__(self):
        self.completion_stabilization_seconds = 3
        if self.low_quota_action == "wait_for_reset" or not self.auto_switch_on_low_quota:
            self.low_quota_action = "wait_for_reset"
            self.auto_switch_on_low_quota = False
            self.pause_if_quota_low = True
        else:
            self.low_quota_action = "change_account"
            self.auto_switch_on_low_quota = True
            self.pause_if_quota_low = True

        try:
            val = int(self.quota_cache_seconds)
        except (ValueError, TypeError):
            val = 60
        self.quota_cache_seconds = max(30, min(120, val))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        if "low_quota_action" not in filtered:
            if filtered.get("auto_switch_on_low_quota") is False:
                filtered["low_quota_action"] = "wait_for_reset"
            else:
                filtered["low_quota_action"] = "change_account"
        return cls(**filtered)

    def save(self, filepath: str = None) -> str:
        if filepath is None:
            filepath = os.path.join(get_user_data_dir(), "config.json")
        dir_name = os.path.dirname(filepath) or get_user_data_dir()
        os.makedirs(dir_name, exist_ok=True)
        tmp_path = os.path.join(dir_name, f".config_{os.getpid()}_{uuid.uuid4().hex[:6]}.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=4)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            os.replace(tmp_path, filepath)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            raise
        return filepath

    @classmethod
    def load(cls, filepath: str = None) -> "AppConfig":
        if filepath is None:
            filepath = os.path.join(get_user_data_dir(), "config.json")
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return cls.from_dict(data)
            except Exception as e:
                print(f"[WARN] Failed to parse config.json, using defaults: {e}")
        return cls()


config = AppConfig.load()
