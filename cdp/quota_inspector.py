import time
import re
from typing import Dict, Any, Optional, Callable
from playwright.sync_api import Page, Locator

from app.config import config


def parse_reset_time_seconds(reset_str: str) -> Optional[int]:
    """Parses duration strings like '2 hours, 59 minutes' into total seconds."""
    if not reset_str or not isinstance(reset_str, str):
        return None
    clean = reset_str.lower().strip()
    if clean in ("unknown", "never", "n/a", "none"):
        return None

    # Handle qualitative short reset strings (e.g. 'less than a minute', 'a few seconds')
    if any(q in clean for q in ("less than a minute", "under a minute", "few seconds", "moment", "shortly")):
        return 60

    total_seconds = 0
    matched = False

    m_days = re.search(r'(\d+)\s*(?:d\b|day|days)', clean)
    if m_days:
        total_seconds += int(m_days.group(1)) * 86400
        matched = True
    m_hours = re.search(r'(\d+)\s*(?:h\b|hr|hour|hours)', clean)
    if m_hours:
        total_seconds += int(m_hours.group(1)) * 3600
        matched = True
    m_mins = re.search(r'(\d+)\s*(?:m\b|min|minute|minutes)', clean)
    if m_mins:
        total_seconds += int(m_mins.group(1)) * 60
        matched = True
    m_secs = re.search(r'(\d+)\s*(?:s\b|sec|second|seconds)', clean)
    if m_secs:
        total_seconds += int(m_secs.group(1))
        matched = True

    if matched:
        # If duration was parsed as 0 (e.g. '0 minutes', '0 seconds'), return 30s fallback
        return total_seconds if total_seconds > 0 else 30

    return None


class DOMQuotaInspector:
    """
    Direct DOM-based Gemini quota inspector.
    Navigates to Settings -> Models & Usage, clicks refresh, and parses limits in milliseconds.
    """

    def __init__(self, page: Page):
        self.page = page

    def inspect_model_quotas(
        self,
        close_settings: bool = True,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Dict[str, Any]:
        log = log_callback or (lambda lvl, msg: None)
        result: Dict[str, Any] = {
            "success": False,
            "gemini": {
                "success": False,
                "five_hour_percent": None,
                "five_hour_refresh": "Unknown",
                "weekly_percent": None,
                "weekly_refresh": "Unknown",
            },
            "is_safe_to_prompt": True,
            "safety_reason": "",
            "summary": "",
            "read_failed": False,
            "is_low_quota": False,
        }

        try:
            # 0. Check if Antigravity is logged out before touching Settings
            from cdp.state_manager import AntigravityStateManager, AntigravityUIState
            state_mgr = AntigravityStateManager(self.page)
            if state_mgr.detect_state() == AntigravityUIState.LOGGED_OUT:
                log("WARN", "⚠️ Cannot inspect quota: Antigravity is currently logged out.")
                result["is_safe_to_prompt"] = False
                result["read_failed"] = False
                result["is_logged_out"] = True
                result["is_low_quota"] = True
                result["safety_reason"] = "Account is logged out. Google login required."
                result["refresh_time_text"] = "Unknown"
                result["refresh_wait_seconds"] = None
                return result

            # 0b. Ensure Antigravity window has focus
            try:
                self.page.bring_to_front()
            except Exception:
                pass

            # 1. Open Settings Page with active verification
            log("INFO", "Opening Settings dialog...")
            is_dialog_open = self._ensure_settings_dialog_open(log)
            if not is_dialog_open:
                # Double-check if opening settings failed because the session was logged out
                if state_mgr.detect_state() == AntigravityUIState.LOGGED_OUT:
                    log("WARN", "⚠️ Antigravity transitioned to logged out. Skipping Settings inspection.")
                    result["is_safe_to_prompt"] = False
                    result["read_failed"] = False
                    result["is_logged_out"] = True
                    result["is_low_quota"] = True
                    result["safety_reason"] = "Account is logged out. Google login required."
                    result["refresh_time_text"] = "Unknown"
                    result["refresh_wait_seconds"] = None
                    return result

                err = "Unable to open Settings dialog from Antigravity DOM."
                log("WARN", f"⚠️ QUOTA READ FAILED: {err}")
                result["is_safe_to_prompt"] = False
                result["read_failed"] = True
                result["is_low_quota"] = False
                result["safety_reason"] = err
                result["refresh_time_text"] = "Unknown"
                result["refresh_wait_seconds"] = None
                return result

            # 2. Navigate to Models & Usage Tab with active verification
            log("INFO", "Navigating to 'Models' tab in Settings...")
            models_mounted = self._ensure_models_tab_active(log)
            if not models_mounted:
                log("WARN", "⚠️ Models pane did not mount cleanly; proceeding with extraction attempts...")

            # 3. Locate and click Refresh button (⟳) inside Settings container
            log("INFO", "Clicking circular refresh icon (⟳) to update live quotas...")
            self._click_refresh_button(log)

            # 4. Extract Gemini Quotas directly from DOM (polls up to 5.0s with 0.25s intervals)
            log("INFO", "Extracting Gemini model limits from DOM...")
            try:
                gemini_data = self._extract_gemini_limits_from_dom(max_wait_seconds=5.0)
            except TypeError:
                gemini_data = self._extract_gemini_limits_from_dom()
            result["gemini"] = gemini_data
            result["success"] = gemini_data.get("success", False)

            # 4b. Extract Active Account Email directly from Settings DOM
            active_email = self._extract_active_email_from_dom()
            if active_email:
                result["current_email"] = active_email
                log("INFO", f"Active account detected in Settings DOM: '{active_email}'")

            # 5. Close Settings if requested
            if close_settings:
                self._close_settings_dialog()

            # 6. Evaluate against configured thresholds
            five_pct = gemini_data.get("five_hour_percent")
            five_ref = gemini_data.get("five_hour_refresh", "Unknown")
            week_pct = gemini_data.get("weekly_percent")
            week_ref = gemini_data.get("weekly_refresh", "Unknown")

            five_str = f"{five_pct}%" if five_pct is not None else "Unknown"
            week_str = f"{week_pct}%" if week_pct is not None else "Unknown"
            result["summary"] = f"Gemini 5-Hour: {five_str} ({five_ref}) | Weekly: {week_str} ({week_ref})"

            log("INFO", "══════════════ GEMINI MODEL LIMITS READ ══════════════")
            log("INFO", f"  • Five-Hour Limit Remaining: {five_str} (Refreshes in {five_ref})")
            log("INFO", f"  • Weekly Limit Remaining:    {week_str} (Refreshes in {week_ref})")
            log("INFO", "═══════════════════════════════════════════════════════")

            min_five = getattr(config, "min_gemini_five_hour_quota", 30)
            min_week = getattr(config, "min_gemini_weekly_quota", 4)

            if five_pct is None and week_pct is None:
                result["is_safe_to_prompt"] = False
                result["read_failed"] = True
                result["is_low_quota"] = False
                reason = "Unable to read Gemini quota limits from Settings DOM."
                result["safety_reason"] = reason
                result["refresh_time_text"] = "Unknown"
                result["refresh_wait_seconds"] = None
                log("WARN", f"⚠️ QUOTA READ FAILED: {reason}")
                return result

            result["read_failed"] = False
            is_low_five = (five_pct is not None and min_five > 0 and five_pct < min_five)
            is_low_week = (week_pct is not None and min_week > 0 and week_pct < min_week)

            if is_low_five or is_low_week:
                result["is_safe_to_prompt"] = False
                result["is_low_quota"] = True
                active_ref = five_ref if is_low_five else week_ref
                if is_low_five:
                    reason = f"Gemini 5-Hour Limit ({five_pct}%) is BELOW safety threshold ({min_five}%)! Resets in {five_ref}."
                else:
                    reason = f"Gemini Weekly Limit ({week_pct}%) is BELOW safety threshold ({min_week}%)! Resets in {week_ref}."

                result["safety_reason"] = reason
                result["refresh_time_text"] = active_ref
                result["refresh_wait_seconds"] = parse_reset_time_seconds(active_ref)
                log("WARN", f"🛑 QUOTA SAFETY TRIGGERED: {reason}")
            else:
                result["is_safe_to_prompt"] = True
                result["is_low_quota"] = False
                log("SUCCESS", f"✓ Gemini quota healthy (5-Hour: {five_str} >= {min_five}%, Weekly: {week_str} >= {min_week}%).")

            return result

        except Exception as e:
            err = f"Error during DOM quota inspection: {e}"
            log("ERROR", err)
            result["is_safe_to_prompt"] = False
            result["read_failed"] = True
            result["is_low_quota"] = False
            result["safety_reason"] = err
            self._close_settings_dialog()
            return result

    def _is_dialog_open(self) -> bool:
        """Checks if the Antigravity Settings modal dialog is currently visible and active in DOM."""
        try:
            return bool(self.page.evaluate("""() => {
                const el = document.querySelector('.settings-modal-container') ||
                           document.querySelector('[role="dialog"][aria-label*="Setting"]') ||
                           document.querySelector('[role="dialog"]');
                if (!el) return false;
                const style = window.getComputedStyle(el);
                return Boolean(
                    style &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.opacity !== '0'
                );
            }"""))
        except Exception:
            return False

    def _ensure_settings_dialog_open(self, log: Callable[[str, str], None], max_wait_seconds: float = 4.0) -> bool:
        """Opens Settings dialog using multi-strategy retries (bring_to_front, JS click, Ctrl+,, locator click)."""
        if self._is_dialog_open():
            return True

        try:
            self.page.bring_to_front()
        except Exception:
            pass

        # Strategy 1: Direct JS click on [data-testid="settings-button"] (fastest, immune to window focus issues)
        try:
            self.page.evaluate("""() => {
                const btn = document.querySelector('[data-testid="settings-button"]') ||
                            document.querySelector('button[aria-label="Settings"]') ||
                            Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Settings');
                if (btn) btn.click();
            }""")
        except Exception:
            pass

        start = time.time()
        while time.time() - start < 0.8:
            if self._is_dialog_open():
                return True
            time.sleep(0.1)

        # Strategy 2: Keyboard shortcut Control+,
        try:
            self.page.keyboard.press("Control+,")
        except Exception:
            pass

        start = time.time()
        while time.time() - start < 1.0:
            if self._is_dialog_open():
                return True
            time.sleep(0.1)

        # Strategy 3: Playwright locator click
        for sel in [
            '[data-testid="settings-button"]',
            'button:has-text("Settings")',
            '[aria-label="Settings"]',
        ]:
            try:
                loc = self.page.locator(sel).first
                if loc.is_visible():
                    loc.click(timeout=1000)
                    time.sleep(0.4)
                    if self._is_dialog_open():
                        return True
            except Exception:
                pass

        # Strategy 4: If sidebar was collapsed, try toggle sidebar and click settings
        try:
            sidebar_toggle = self.page.locator('[data-testid="sidebar-toggle"]').first
            if sidebar_toggle.is_visible():
                sidebar_toggle.click(timeout=1000)
                time.sleep(0.3)
                self.page.evaluate("() => document.querySelector('[data-testid=\"settings-button\"]')?.click()")
                time.sleep(0.5)
                if self._is_dialog_open():
                    return True
        except Exception:
            pass

        # Final check with polling up to max_wait_seconds
        poll_start = time.time()
        while time.time() - poll_start < 1.5:
            if self._is_dialog_open():
                return True
            time.sleep(0.1)

        return self._is_dialog_open()

    def _is_models_pane_mounted(self) -> bool:
        """Verifies if the Models & Usage pane is actively mounted and visible within the Settings modal."""
        try:
            return bool(self.page.evaluate("""() => {
                const container = document.querySelector('.settings-modal-container') ||
                                  document.querySelector('[role="dialog"]');
                if (!container) return false;

                // Check if refresh button is actively visible in viewport (not hidden with offsetParent === null)
                const refreshBtn = container.querySelector('button[aria-label="Refresh quota and credits data"]');
                if (refreshBtn && refreshBtn.offsetParent !== null) {
                    return true;
                }

                // Check if Models nav button has active styling
                const navBtn = container.querySelector('button[data-testid="settings-nav-item-Models"]');
                if (navBtn && (navBtn.className || '').includes('bg-sidebar-secondary')) {
                    return true;
                }

                // Check if visible pane contains 'Models & Usage' or 'Gemini Models'
                const allElements = Array.from(container.querySelectorAll('h1, h2, h3, div, span'));
                return allElements.some(el => {
                    const t = (el.innerText || '').trim();
                    return (t === 'Models & Usage' || t === 'Gemini Models') && el.offsetParent !== null;
                });
            }"""))
        except Exception:
            return False

    def _ensure_models_tab_active(self, log: Callable[[str, str], None], max_wait_seconds: float = 4.0) -> bool:
        """Actively navigates to Models tab with verification and re-click polling."""
        start = time.time()
        while time.time() - start < max_wait_seconds:
            # Priority 1: JS click inside settings modal
            try:
                self.page.evaluate("""() => {
                    const btn = document.querySelector('button[data-testid="settings-nav-item-Models"]') ||
                                Array.from(document.querySelectorAll('.settings-modal-container button, [role="dialog"] button')).find(
                                    b => (b.innerText || '').trim() === 'Models'
                                );
                    if (btn) btn.click();
                }""")
            except Exception:
                pass

            time.sleep(0.2)
            if self._is_models_pane_mounted():
                return True

            # Priority 2: Playwright locator click
            for tab_sel in [
                'button[data-testid="settings-nav-item-Models"]',
                '.settings-modal-container button:has-text("Models")',
                '[role="dialog"] button:has-text("Models")',
            ]:
                try:
                    tab = self.page.locator(tab_sel).first
                    if tab.is_visible():
                        tab.click(timeout=1000)
                        break
                except Exception:
                    pass

            time.sleep(0.2)
            if self._is_models_pane_mounted():
                return True

        return self._is_models_pane_mounted()

    def _click_refresh_button(self, log: Callable[[str, str], None]):
        """Clicks the circular refresh icon (⟳) in the Models pane."""
        clicked = False
        try:
            clicked = bool(self.page.evaluate("""() => {
                const container = document.querySelector('.settings-modal-container') ||
                                  document.querySelector('[role="dialog"]') ||
                                  document;
                const btn = container.querySelector('button[aria-label="Refresh quota and credits data"]') ||
                            container.querySelector('button[aria-label*="Refresh"]') ||
                            container.querySelector('[data-testid="models-refresh-btn"]');
                if (btn) {
                    btn.click();
                    return true;
                }
                return false;
            }"""))
        except Exception:
            pass

        if not clicked:
            for r_sel in [
                'button[aria-label="Refresh quota and credits data"]',
                'button[aria-label*="Refresh"]',
                '[data-testid="models-refresh-btn"]',
            ]:
                try:
                    loc = self.page.locator(f'.settings-modal-container {r_sel}, [role="dialog"] {r_sel}').first
                    if loc.is_visible():
                        loc.click(timeout=1000)
                        clicked = True
                        break
                except Exception:
                    pass

        # Brief delay (0.3s) for the network request to initiate
        time.sleep(0.3)

    def _close_settings_dialog(self):
        """Safely closes the Settings modal using Escape and close button fallback."""
        try:
            self.page.keyboard.press("Escape")
            time.sleep(0.2)
            if self._is_dialog_open():
                self.page.evaluate("""() => {
                    const closeBtn = document.querySelector('.settings-modal-container button[aria-label="Close"]') ||
                                     document.querySelector('[role="dialog"] button[aria-label="Close"]');
                    if (closeBtn) closeBtn.click();
                }""")
                time.sleep(0.2)
        except Exception:
            pass

    def _extract_gemini_limits_from_dom(self, max_wait_seconds: float = 5.0) -> Dict[str, Any]:
        """Extracts text content from the Models & Usage DOM subtree with resilient polling."""
        data = {
            "success": False,
            "five_hour_percent": None,
            "five_hour_refresh": "Unknown",
            "weekly_percent": None,
            "weekly_refresh": "Unknown",
        }

        start_time = time.time()
        while True:
            try:
                # Prioritize the Settings modal container before falling back to document.body
                page_text = self.page.evaluate("""() => {
                    const container = document.querySelector('.settings-modal-container') ||
                                      document.querySelector('[role="dialog"]') ||
                                      document.querySelector('.models-usage-container') ||
                                      document.querySelector('.settings-content') ||
                                      document.body;
                    return container ? container.innerText : '';
                }""")

                if not page_text or not isinstance(page_text, str):
                    if time.time() - start_time >= max_wait_seconds:
                        break
                    time.sleep(0.25)
                    continue

                # 1. Isolate Gemini Models section so Claude/GPT limits below don't contaminate
                gemini_text = page_text
                matches = list(re.finditer(r'Gemini\s+Models', page_text, re.IGNORECASE))
                if matches:
                    start_match = matches[-1]
                    after_start = page_text[start_match.start():]
                    end_match = re.search(r'Claude\s*(?:&|and)\s*GPT', after_start, re.IGNORECASE)
                    if end_match:
                        gemini_text = after_start[:end_match.start()]
                    else:
                        gemini_text = after_start[:2500]
                else:
                    # 'Gemini Models' section has not mounted yet (API call in flight); poll again
                    if time.time() - start_time >= max_wait_seconds:
                        break
                    time.sleep(0.25)
                    continue

                # Segment into isolated card blocks to prevent regex bleed between Weekly and 5-Hour limits
                m_wk_header = re.search(r'Weekly\s+Limit', gemini_text, re.IGNORECASE)
                m_5h_header = re.search(r'(?:five[- ]hour|5[- ]hour)\s+Limit', gemini_text, re.IGNORECASE)

                if m_wk_header and m_5h_header:
                    if m_wk_header.start() < m_5h_header.start():
                        weekly_block = gemini_text[m_wk_header.start():m_5h_header.start()]
                        five_block = gemini_text[m_5h_header.start():]
                    else:
                        five_block = gemini_text[m_5h_header.start():m_wk_header.start()]
                        weekly_block = gemini_text[m_wk_header.start():]
                elif m_5h_header:
                    five_block = gemini_text[m_5h_header.start():]
                    weekly_block = gemini_text
                elif m_wk_header:
                    weekly_block = gemini_text[m_wk_header.start():]
                    five_block = gemini_text
                else:
                    five_block = gemini_text
                    weekly_block = gemini_text

                # 2. Parse Gemini 5-Hour Limit strictly within five_block
                m_five = re.search(r'(?:five[- ]hour|5[- ]hour)\s+Limit[^\n\r%]*?(\d{1,3})\s*%', five_block, re.IGNORECASE)
                if not m_five:
                    m_five = re.search(
                        r'(?:five[- ]hour|5[- ]hour)\s+Limit(?:\s+Remaining)?.*?(\d{1,3})\s*%',
                        five_block,
                        re.DOTALL | re.IGNORECASE,
                    )
                if m_five:
                    data["five_hour_percent"] = int(m_five.group(1))

                m_five_ref = re.search(
                    r'(?:five[- ]hour|5[- ]hour)\s+Limit.*?(?:resets?|refreshes?|refresh)\s+in\s+([^\n\r.)]+)',
                    five_block,
                    re.DOTALL | re.IGNORECASE,
                )
                if not m_five_ref:
                    m_five_ref = re.search(
                        r'(?:resets?|refreshes?|refresh)\s+in\s+([^\n\r.)]+)',
                        five_block,
                        re.IGNORECASE,
                    )
                if m_five_ref:
                    data["five_hour_refresh"] = m_five_ref.group(1).strip("() \t.")

                # 3. Parse Gemini Weekly Limit strictly within weekly_block
                m_week = re.search(r'Weekly\s+Limit[^\n\r%]*?(\d{1,3})\s*%', weekly_block, re.IGNORECASE)
                if not m_week:
                    m_week = re.search(
                        r'Weekly\s+Limit(?:\s+Remaining)?.*?(\d{1,3})\s*%',
                        weekly_block,
                        re.DOTALL | re.IGNORECASE,
                    )
                if m_week:
                    data["weekly_percent"] = int(m_week.group(1))

                m_week_ref = re.search(
                    r'Weekly\s+Limit.*?(?:resets?|refreshes?|refresh)\s+in\s+([^\n\r.)]+)',
                    weekly_block,
                    re.DOTALL | re.IGNORECASE,
                )
                if not m_week_ref:
                    m_week_ref = re.search(
                        r'(?:resets?|refreshes?|refresh)\s+in\s+([^\n\r.)]+)',
                        weekly_block,
                        re.IGNORECASE,
                    )
                if m_week_ref:
                    data["weekly_refresh"] = m_week_ref.group(1).strip("() \t.")

                if data["five_hour_percent"] is not None or data["weekly_percent"] is not None:
                    data["success"] = True
                    break

                if time.time() - start_time >= max_wait_seconds:
                    break
                time.sleep(0.25)

            except Exception as e:
                print(f"[WARN] DOM quota parsing error: {e}")
                break

        return data

    def _extract_active_email_from_dom(self) -> str:
        """Extracts the logged-in Google account email from the Settings DOM profile area."""
        try:
            raw_email = self.page.evaluate(r"""() => {
                const emailRegex = /[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+/g;
                const isIgnore = (email) => {
                    const lower = (email || '').toLowerCase();
                    return lower.includes('example.com') ||
                           lower.includes('antigravity.dev') ||
                           lower.includes('@sample') ||
                           lower.includes('google.internal');
                };

                // Priority 1: Check Account nav button / profile item in Settings
                const accNav = document.querySelector('button[data-testid="settings-nav-item-Account"], [aria-label*="Account"], [data-testid*="account"], .profile-email, .user-email');
                if (accNav) {
                    const txt = ((accNav.innerText || '') + ' ' + (accNav.getAttribute('aria-label') || '') + ' ' + (accNav.getAttribute('title') || '')).trim();
                    const matches = txt.match(emailRegex);
                    if (matches) {
                        for (const m of matches) {
                            if (!isIgnore(m)) return m.toLowerCase();
                        }
                    }
                }

                // Priority 2: Check any element inside settings modal or dialog
                const modal = document.querySelector('.settings-modal-container') ||
                              document.querySelector('[role="dialog"]') ||
                              document.querySelector('.settings-content');
                if (modal) {
                    const elements = Array.from(modal.querySelectorAll('button, div, span, a, p'));
                    for (const el of elements) {
                        const combined = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '')).trim();
                        if (combined.includes('@')) {
                            const matches = combined.match(emailRegex);
                            if (matches) {
                                for (const m of matches) {
                                    if (!isIgnore(m)) return m.toLowerCase();
                                }
                            }
                        }
                    }
                }

                // Priority 3: Check activity bar / status bar / accounts indicators
                const allCandidates = Array.from(document.body.querySelectorAll('[aria-label*="@"], [title*="@"], .statusbar, .activitybar, [id*="account"]'));
                for (const el of allCandidates) {
                    const combined = ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '')).trim();
                    if (combined.includes('@')) {
                        const matches = combined.match(emailRegex);
                        if (matches) {
                            for (const m of matches) {
                                if (!isIgnore(m)) return m.toLowerCase();
                            }
                        }
                    }
                }

                // Priority 4: Full body text fallback
                const bodyMatches = (document.body ? document.body.innerText : '').match(emailRegex);
                if (bodyMatches) {
                    for (const m of bodyMatches) {
                        if (!isIgnore(m)) return m.toLowerCase();
                    }
                }
                return '';
            }""")
            active_email = (raw_email or "").strip().lower()
            if not active_email:
                try:
                    from app.account_manager import account_manager
                    body_text = self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
                    for acc in account_manager.accounts:
                        if acc.email.lower() in body_text.lower():
                            return acc.email.lower()
                except Exception:
                    pass
            return active_email
        except Exception as e:
            print(f"[WARN] Failed to extract active email from Settings DOM: {e}")
            return ""
