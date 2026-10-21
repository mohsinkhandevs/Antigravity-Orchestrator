"""Quota inspection bridge for Antigravity CDP."""

from typing import Dict, Any, Callable, Optional
from cdp.client import AntigravityCDPClient
from cdp.quota_inspector import DOMQuotaInspector, parse_reset_time_seconds


def inspect_model_quotas(
    close_settings: bool = True,
    log_callback: Optional[Callable[[str, str], None]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Inspects Gemini model limits via CDP DOM inspection."""
    client = AntigravityCDPClient()
    ok, msg = client.connect()
    if not ok:
        if log_callback:
            log_callback("WARN", f"CDP connection note during quota check: {msg}")
        return {
            "success": False,
            "gemini": {
                "success": False,
                "five_hour_percent": None,
                "five_hour_refresh": "Unknown",
                "weekly_percent": None,
                "weekly_refresh": "Unknown",
            },
            "is_safe_to_prompt": True,
            "safety_reason": msg,
        }

    try:
        page = client.get_page()
        inspector = DOMQuotaInspector(page)
        return inspector.inspect_model_quotas(close_settings=close_settings, log_callback=log_callback)
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def format_remaining_duration(reset_at_str: str, now: Optional[Any] = None) -> str:
    """Calculates dynamic remaining time until reset_at_str with multi-unit precision."""
    from datetime import datetime
    if not reset_at_str:
        return "—"
    try:
        reset_dt = datetime.strptime(reset_at_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return "—"

    current = now or datetime.now()
    diff = (reset_dt - current).total_seconds()
    if diff <= 0:
        return "Reset now"

    days = int(diff // 86400)
    hours = int((diff % 86400) // 3600)
    mins = int((diff % 3600) // 60)

    if days > 0:
        if hours > 0:
            return f"in {days}d {hours}h"
        return f"in {days}d"
    elif hours > 0:
        if mins > 0:
            return f"in {hours}h {mins}m"
        return f"in {hours}h"
    elif mins > 0:
        return f"in {mins}m"
    else:
        return "in <1m"


def format_verbose_remaining_duration(reset_at_str: str, now: Optional[Any] = None) -> str:
    """Calculates human-readable remaining time (e.g. '4 hours, 16 minutes') until reset_at_str."""
    from datetime import datetime
    if not reset_at_str:
        return "Unknown"
    try:
        reset_dt = datetime.strptime(reset_at_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return "Unknown"

    current = now or datetime.now()
    diff = (reset_dt - current).total_seconds()
    if diff <= 0:
        return "Reset now"

    days = int(diff // 86400)
    hours = int((diff % 86400) // 3600)
    mins = int((diff % 3600) // 60)

    if days > 0:
        d_str = f"{days} day" if days == 1 else f"{days} days"
        if hours > 0:
            h_str = f"{hours} hour" if hours == 1 else f"{hours} hours"
            return f"{d_str}, {h_str}"
        return d_str
    elif hours > 0:
        h_str = f"{hours} hour" if hours == 1 else f"{hours} hours"
        if mins > 0:
            m_str = f"{mins} minute" if mins == 1 else f"{mins} minutes"
            return f"{h_str}, {m_str}"
        return h_str
    elif mins > 0:
        return f"{mins} minute" if mins == 1 else f"{mins} minutes"
    else:
        return "less than a minute"


def get_smallest_unit_buffer_note(refresh_str: str) -> str:
    """Returns human-friendly buffer note string based on the smallest unit present in refresh_str."""
    if not refresh_str or not isinstance(refresh_str, str):
        return ""
    clean = refresh_str.lower().strip()
    if clean in ("unknown", "never", "n/a", "none", ""):
        return ""

    import re
    # Check if seconds
    if re.search(r'(\d+)\s*(?:s\b|sec|second|seconds)', clean):
        return "(+1 second buffer)"
    # Check if minutes or "less than a minute"
    if re.search(r'(\d+)\s*(?:m\b|min|minute|minutes)', clean) or any(q in clean for q in ("less than a minute", "under a minute", "few seconds", "moment")):
        return "(+1 minute buffer)"
    # Check if hours
    if re.search(r'(\d+)\s*(?:h\b|hr|hour|hours)', clean):
        return "(+1 hour buffer)"
    # Check if days
    if re.search(r'(\d+)\s*(?:d\b|day|days)', clean):
        return "(+1 day buffer)"
    return "(+1 buffer)"


def format_time_ago(timestamp_str: str, now: Optional[Any] = None) -> str:
    """Calculates human-readable relative time (e.g. 'just now', '5m ago', '2h 10m ago') from a timestamp."""
    from datetime import datetime
    if not timestamp_str or not isinstance(timestamp_str, str):
        return "never"
    clean = timestamp_str.strip()
    if not clean or clean.lower() in ("unknown", "never", "none", "n/a", "—"):
        return "never"

    try:
        ts_dt = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            ts_dt = datetime.fromisoformat(clean)
        except Exception:
            return clean

    current = now or datetime.now()
    diff = (current - ts_dt).total_seconds()
    if diff < 0 or diff < 60:
        return "just now"
    elif diff < 3600:
        m = int(diff // 60)
        return f"{m}m ago"
    elif diff < 86400:
        h = int(diff // 3600)
        m = int((diff % 3600) // 60)
        return f"{h}h {m}m ago" if m > 0 else f"{h}h ago"
    else:
        d = int(diff // 86400)
        return f"{d}d ago"


def build_percentage_tooltip(
    limit_name: str,
    percent: Optional[int] = None,
    last_checked_str: str = "",
    check_reason: str = "",
    reset_at_str: str = "",
    raw_refresh: str = "",
    now: Optional[Any] = None,
) -> str:
    """Builds a formatted multi-line tooltip for a quota percentage pill showing when and why it was updated."""
    pct_text = f"{percent}% remaining" if percent is not None else "Pending quota check"
    lines = [f"{limit_name} Quota: {pct_text}"]

    if last_checked_str and last_checked_str.strip() not in ("unknown", "never", "none", "n/a", "—", ""):
        ago = format_time_ago(last_checked_str, now=now)
        lines.append(f"• Last Updated: {last_checked_str} ({ago})")
    else:
        lines.append("• Last Updated: Not yet inspected")

    if check_reason and check_reason.strip():
        lines.append(f"• Trigger: {check_reason.strip()}")

    if reset_at_str:
        verbose_rem = format_verbose_remaining_duration(reset_at_str, now=now)
        lines.append(f"• Reset In: {verbose_rem}")
        note = get_smallest_unit_buffer_note(raw_refresh)
        buf_suffix = f" {note}" if note else ""
        lines.append(f"• Reset Time: {reset_at_str}{buf_suffix}")
    elif raw_refresh and raw_refresh.lower() not in ("unknown", "n/a", "none", ""):
        lines.append(f"• Reset In: ~{raw_refresh}")

    return "\n".join(lines)


def build_quota_tooltip(
    limit_name: str,
    reset_at_str: Optional[str] = None,
    raw_refresh: str = "",
    buffer_note: str = "",
    now: Optional[Any] = None,
    last_checked_str: str = "",
    check_reason: str = "",
) -> str:
    """Builds a formatted multi-line tooltip showing verbose remaining time, reset datetime, and last check info."""
    lines = [f"{limit_name} Limit Reset:"]
    if reset_at_str:
        verbose_rem = format_verbose_remaining_duration(reset_at_str, now=now)
        lines.append(f"• Remaining: {verbose_rem}")
        note = buffer_note or get_smallest_unit_buffer_note(raw_refresh)
        buf_suffix = f" {note}" if note else ""
        lines.append(f"• Reset Time: {reset_at_str}{buf_suffix}")
    elif raw_refresh and raw_refresh.lower() not in ("unknown", "n/a", "none", ""):
        lines.append(f"• Remaining: ~{raw_refresh}")
        lines.append("• Reset Time: Pending quota check")
    else:
        lines.append("• No reset data recorded (inspect quota to update)")

    if last_checked_str and last_checked_str.strip() not in ("unknown", "never", "none", "n/a", "—", ""):
        ago = format_time_ago(last_checked_str, now=now)
        lines.append(f"• Last Checked: {last_checked_str} ({ago})")
    if check_reason and check_reason.strip():
        lines.append(f"• Trigger: {check_reason.strip()}")

    return "\n".join(lines)


def calculate_reset_timestamp_with_smallest_unit_buffer(refresh_str: str, base_time: Optional[Any] = None) -> Optional[Any]:
    """
    Calculates reset timestamp by dynamically incrementing the smallest time unit present in the string:
    - Days & Hours (e.g. '6 days, 23 hours') -> increments hour (+1 hour)
    - Hours & Minutes (e.g. '3 hours, 10 minutes') -> increments minute (+1 minute)
    - Minutes only (e.g. '45 minutes') -> increments minute (+1 minute)
    - Hours only (e.g. '23 hours') -> increments hour (+1 hour)
    - Days only (e.g. '1 day') -> increments day (+1 day)
    - 'less than a minute' -> +1 minute
    """
    from datetime import datetime, timedelta
    import re

    if not refresh_str or not isinstance(refresh_str, str):
        return None
    clean = refresh_str.lower().strip()
    if clean in ("unknown", "never", "n/a", "none", ""):
        return None

    days = 0
    hours = 0
    mins = 0
    secs = 0
    matched_units = []

    m_days = re.search(r'(\d+)\s*(?:d\b|day|days)', clean)
    if m_days:
        days = int(m_days.group(1))
        matched_units.append("day")

    m_hours = re.search(r'(\d+)\s*(?:h\b|hr|hour|hours)', clean)
    if m_hours:
        hours = int(m_hours.group(1))
        matched_units.append("hour")

    m_mins = re.search(r'(\d+)\s*(?:m\b|min|minute|minutes)', clean)
    if m_mins:
        mins = int(m_mins.group(1))
        matched_units.append("minute")

    m_secs = re.search(r'(\d+)\s*(?:s\b|sec|second|seconds)', clean)
    if m_secs:
        secs = int(m_secs.group(1))
        matched_units.append("second")

    is_sub_minute = any(q in clean for q in ("less than a minute", "under a minute", "few seconds", "moment"))
    if is_sub_minute and not matched_units:
        matched_units.append("minute")
        mins = 0

    if not matched_units:
        return None

    # Increment the smallest unit present
    if "second" in matched_units:
        secs += 1
    elif "minute" in matched_units:
        mins += 1
    elif "hour" in matched_units:
        hours += 1
    elif "day" in matched_units:
        days += 1

    ref = base_time or datetime.now()
    return ref + timedelta(days=days, hours=hours, minutes=mins, seconds=secs)


def parse_hours_from_string(refresh_str: str) -> Optional[int]:
    """Extracts actual hours (from days and hours) from a refresh duration string."""
    if not refresh_str or not isinstance(refresh_str, str):
        return None
    clean = refresh_str.lower().strip()
    if clean in ("unknown", "never", "n/a", "none", ""):
        return None

    import re
    total_hours = 0
    matched = False

    m_days = re.search(r'(\d+)\s*(?:d\b|day|days)', clean)
    if m_days:
        total_hours += int(m_days.group(1)) * 24
        matched = True
    m_hours = re.search(r'(\d+)\s*(?:h\b|hr|hour|hours)', clean)
    if m_hours:
        total_hours += int(m_hours.group(1))
        matched = True

    if matched:
        return total_hours
    if re.search(r'(\d+)\s*(?:m\b|min|minute|minutes)', clean):
        return 0
    return None


def parse_minutes_from_string(refresh_str: str) -> Optional[int]:
    """Extracts actual minutes (from days, hours and minutes) from a refresh duration string."""
    if not refresh_str or not isinstance(refresh_str, str):
        return None
    clean = refresh_str.lower().strip()
    if clean in ("unknown", "never", "n/a", "none", ""):
        return None

    import re
    total_mins = 0
    matched = False

    m_days = re.search(r'(\d+)\s*(?:d\b|day|days)', clean)
    if m_days:
        total_mins += int(m_days.group(1)) * 1440
        matched = True
    m_hours = re.search(r'(\d+)\s*(?:h\b|hr|hour|hours)', clean)
    if m_hours:
        total_mins += int(m_hours.group(1)) * 60
        matched = True
    m_mins = re.search(r'(\d+)\s*(?:m\b|min|minute|minutes)', clean)
    if m_mins:
        total_mins += int(m_mins.group(1))
        matched = True

    if matched:
        return total_mins
    if any(q in clean for q in ("less than a minute", "under a minute", "few seconds", "moment", "second")):
        return 0
    return None


def calculate_weekly_reset_timestamp(refresh_str: str, base_time: Optional[Any] = None) -> Optional[Any]:
    """
    Calculates weekly limit reset timestamp using dynamic smallest-unit buffer.
    e.g. '6 days, 23 hours' -> +1 hour buffer (168h); '3 hours, 10 minutes' -> +1 minute buffer (3h 11m).
    """
    return calculate_reset_timestamp_with_smallest_unit_buffer(refresh_str, base_time=base_time)


def calculate_five_hour_reset_timestamp(refresh_str: str, base_time: Optional[Any] = None) -> Optional[Any]:
    """
    Calculates 5-hour limit reset timestamp using dynamic smallest-unit buffer.
    e.g. '4 hours, 4 minutes' -> +1 minute buffer (245m); '45 minutes' -> +1 minute buffer (46m).
    """
    return calculate_reset_timestamp_with_smallest_unit_buffer(refresh_str, base_time=base_time)


def calculate_reset_timestamp(refresh_str: str, base_time: Optional[Any] = None) -> Optional[Any]:
    """Generic fallback for backwards compatibility."""
    return calculate_reset_timestamp_with_smallest_unit_buffer(refresh_str, base_time=base_time)



