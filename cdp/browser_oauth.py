import time
import os
import re
from typing import Optional, Callable, Dict, Any, List
from playwright.sync_api import Page

from app.config import config
from app.account_manager import account_manager, GoogleAccount
from app.browser_ocr_automation import run_browser_account_selection
from cdp.state_manager import AntigravityStateManager, AntigravityUIState


class BrowserOAuthHandler:
    """
    Handles Antigravity account sign-out, OAuth sign-in launch, and post-authentication callback in Antigravity via CDP.
    """

    def __init__(self, antigravity_page: Page):
        self.page = antigravity_page

    def execute_account_switch(
        self,
        target_account_email: Optional[str] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
        step_callback: Optional[Callable[[int, bool, str], None]] = None,
        stop_event: Optional[Any] = None,
        pre_continue_callback: Optional[Callable[[str, str], bool]] = None,
    ) -> Dict[str, Any]:
        log = log_callback or (lambda lvl, msg: None)
        notify_step = step_callback or (lambda s_id, ok, msg: None)
        result = {
            "success": False,
            "previous_email": "",
            "switched_to_email": "",
            "error": "",
        }

        try:
            # 0. Check dynamic UI state
            state_mgr = AntigravityStateManager(self.page)
            initial_state = state_mgr.detect_state()

            current_email = ""
            already_logged_out = False

            if initial_state == AntigravityUIState.FINISH_AUTH_BUTTON:
                log("INFO", "Antigravity is already showing 'Finish' / 'Get started'. Clicking to complete authentication...")
                state_mgr._click_finish(log)
                time.sleep(1.0)
                self._close_settings_and_focus()
                result["success"] = True
                result["switched_to_email"] = target_account_email or account_manager.current_email or ""
                return result

            if initial_state == AntigravityUIState.AUTH_ERROR_SCREEN:
                log("WARN", "Detected account setup error screen. Clicking 'Continue with different account'...")
                state_mgr._click_different_account(log)
                time.sleep(0.8)
                already_logged_out = True

            if initial_state == AntigravityUIState.LOGGED_OUT:
                already_logged_out = True

            if already_logged_out:
                log("INFO", "⚡ Antigravity is already logged out. Skipping Settings & Sign-out steps...")
                self._click_back_if_oauth_pending(log)
                notify_step(3, True, "Account is already logged out")
                current_email = account_manager.current_email or ""
                result["previous_email"] = current_email
                if target_account_email:
                    target_acc = next(
                        (a for a in account_manager.accounts if a.email.lower() == target_account_email.lower()),
                        None,
                    )
                else:
                    target_acc = account_manager.get_next_account(current_email)
                    if not target_acc and account_manager.accounts:
                        target_acc = account_manager.accounts[0]

                if not target_acc:
                    err = "No eligible Google account available in queue."
                    log("ERROR", err)
                    notify_step(4, False, err)
                    result["error"] = err
                    return result

                target_email = target_acc.email
                result["switched_to_email"] = target_email
                log("INFO", f"Target account resolved: '{target_email}' (Already logged out)")
                notify_step(4, True, f"Target: {target_email} (Already logged out)")
            else:
                # 1. Open Settings -> Account / Profile Page
                log("INFO", "[Step 1/6] Navigating to Account / Profile in Settings...")
                opened = self._open_profile_page(log)
                if not opened:
                    # Double-check if opening settings failed because we are actually logged out
                    st = state_mgr.detect_state()
                    if st == AntigravityUIState.LOGGED_OUT:
                        already_logged_out = True
                        self._click_back_if_oauth_pending(log)
                        current_email = account_manager.current_email or ""
                        result["previous_email"] = current_email
                        target_acc = account_manager.get_next_account(current_email) or (account_manager.accounts[0] if account_manager.accounts else None)
                        if not target_acc:
                            err = "No eligible Google account available in queue."
                            result["error"] = err
                            return result
                        target_email = target_acc.email
                        result["switched_to_email"] = target_email
                        log("INFO", f"Antigravity is logged out. Target account: '{target_email}'")
                    else:
                        err = "Unable to open Settings -> Account / Profile page in Antigravity."
                        log("ERROR", err)
                        notify_step(3, False, err)
                        result["error"] = err
                        return result
                else:
                    notify_step(3, True, "Opened Settings -> Account page")

                    # 2. Read Current Account & Resolve Target Account
                    current_email = self._read_current_email(log)
                    result["previous_email"] = current_email
                    if current_email:
                        account_manager.current_email = current_email
                        account_manager.mark_account_used(current_email)
                        log("INFO", f"Detected active account in Antigravity: '{current_email}'")
                    else:
                        log("WARN", "Could not detect current email from Account page; falling back to queue order.")

                    if target_account_email:
                        target_acc = next(
                            (a for a in account_manager.accounts if a.email.lower() == target_account_email.lower()),
                            None,
                        )
                    else:
                        target_acc = account_manager.get_next_account(current_email)

                    if not target_acc:
                        err = "No eligible Google account available in queue."
                        log("ERROR", err)
                        notify_step(4, False, err)
                        result["error"] = err
                        return result

                    target_email = target_acc.email
                    result["switched_to_email"] = target_email
                    log("INFO", f"[Step 2/6] Target account: '{target_email}' (Current: '{current_email or 'Unknown'}')")
                    notify_step(4, True, f"Current: {current_email or 'Unknown'} -> Target: {target_email}")

                    # 3. Click Sign Out
                    log("INFO", "[Step 3/6] Clicking 'Sign Out' in Antigravity...")
                    signed_out = self._click_sign_out(log)
                    if not signed_out:
                        err = "Could not locate or click 'Sign Out' button on the Account page."
                        log("ERROR", err)
                        notify_step(5, False, err)
                        result["error"] = err
                        return result
                    notify_step(5, True, "Clicked 'Sign Out'")

            # Optional 30s Pre-Continue Countdown Gate before opening browser & taking mouse control
            if pre_continue_callback is not None:
                should_proceed = pre_continue_callback(current_email, target_email)
                if not should_proceed:
                    err = "Account switch cancelled during pre-switch countdown."
                    log("WARN", err)
                    notify_step(6, False, err)
                    result["error"] = err
                    return result

            # 4, 5 & 6: Google Sign-In, Browser OCR Selection & OAuth Return-Control Handshake
            # Includes automatic 'Back' -> 'Continue with Google' recovery and account rotation fallback
            tried_emails = set()
            per_account_retries: Dict[str, int] = {}
            final_email_holder: List[str] = []
            num_accounts = max(len(account_manager.accounts), 3)
            max_total_attempts = num_accounts * 2 + 2
            oauth_completed = False

            for attempt in range(max_total_attempts):
                if stop_event and stop_event.is_set():
                    err = "Account switch cancelled by user."
                    log("WARN", err)
                    notify_step(6, False, err)
                    result["error"] = err
                    return result

                # 4. Click 'Continue with Google' / 'Sign in' in Antigravity (auto-clicks 'Back' first if stuck spinning)
                log("INFO", f"[Step 4/6] Waiting for login screen and clicking 'Continue with Google' (Account: '{target_email}')...")
                clicked_google = self._click_continue_with_google(log)
                if not clicked_google:
                    err = "Login screen did not transition to 'Continue with Google' / 'Sign in'."
                    log("ERROR", err)
                    notify_step(6, False, err)
                    result["error"] = err
                    return result
                notify_step(6, True, f"Launched Google OAuth sign-in for '{target_email}'")

                # 5. Browser OCR Automation Hand-off
                log("INFO", f"[Step 5/6] Handing control to Browser OCR Automation for '{target_email}'...")
                browser_res = run_browser_account_selection(
                    target_email=target_email,
                    log_callback=log,
                    stop_event=stop_event,
                    early_completion_checker=self._is_antigravity_authenticated_or_ready,
                )
                if not browser_res.get("success"):
                    err = browser_res.get("error") or "Browser OCR automation failed."
                    # Bring Antigravity window back to front and reset spinning 'Continue with Google' via 'Back'
                    try:
                        self.page.bring_to_front()
                        time.sleep(0.4)
                    except Exception:
                        pass
                    self._click_back_if_oauth_pending(log)

                    if browser_res.get("is_2fa_challenge"):
                        tried_emails.add(target_email.lower())
                        account_manager.mark_account_auth_required(target_email, reason="Google 2FA Device Challenge")
                        log("WARN", f"⚠️ Account '{target_email}' encountered 2FA device challenge. Resetting via 'Back' and rotating to next account...")
                    elif browser_res.get("account_not_in_browser"):
                        tried_emails.add(target_email.lower())
                        log("WARN", f"⚠️ Account '{target_email}' is not signed into the browser profile ({err}). Resetting via 'Back' and rotating to next account...")
                    else:
                        # Transient browser/OCR failure (e.g. Phase 1/3 timeout, Google 500 error, focus lost):
                        # Retry the SAME account once via Back -> Continue with Google before rotating
                        retries = per_account_retries.get(target_email.lower(), 0)
                        if retries < 1:
                            per_account_retries[target_email.lower()] = retries + 1
                            log(
                                "WARN",
                                f"🔄 Browser OAuth did not complete for '{target_email}' ({err}). "
                                f"Clicked 'Back' in Antigravity and re-clicking 'Continue with Google' (Retry {retries + 1}/1)...",
                            )
                            time.sleep(0.6)
                            continue
                        tried_emails.add(target_email.lower())
                        log("WARN", f"⚠️ Account '{target_email}' failed after retry ({err}). Rotating to next eligible account...")

                    next_acc = account_manager.get_next_account(
                        current_email=target_email,
                        exclude_emails=list(tried_emails),
                    )
                    if next_acc and next_acc.email.lower() not in tried_emails:
                        log("INFO", f"🔄 Continuing with next eligible account: '{next_acc.email}'...")
                        target_email = next_acc.email
                        result["switched_to_email"] = target_email
                        time.sleep(0.5)
                        continue
                    else:
                        log("ERROR", f"Browser sign-in failed and no further eligible accounts remain: {err}")
                        notify_step(7, False, err)
                        result["error"] = err
                        return result

                notify_step(7, True, f"Selected '{target_email}' & confirmed sign-in")

                # 6. Handshake: Wait for Finish / Get Started Button or Authenticated State in Antigravity DOM
                log("INFO", "[Step 6/6] Awaiting OAuth callback & 'Finish' / 'Get started' button in Antigravity...")
                finished = self._wait_and_click_finish(
                    log,
                    target_email=target_email,
                    stop_event=stop_event,
                    final_email_container=final_email_holder,
                )
                if finished:
                    oauth_completed = True
                    notify_step(8, True, "OAuth callback completed & Finish / Get started clicked")
                    break

                # Browser two-step automation did NOT return control to Antigravity!
                # Reset via 'Back' button in Antigravity and re-click 'Continue with Google'
                log(
                    "WARN",
                    f"⚠️ Browser two-step OAuth did not return control to Antigravity for '{target_email}'. "
                    "Clicking 'Back' in Antigravity to reset...",
                )
                try:
                    self.page.bring_to_front()
                    time.sleep(0.4)
                except Exception:
                    pass
                self._click_back_if_oauth_pending(log)

                retries = per_account_retries.get(target_email.lower(), 0)
                if retries < 1:
                    per_account_retries[target_email.lower()] = retries + 1
                    log(
                        "INFO",
                        f"🔄 Retrying 'Continue with Google' for '{target_email}' after clicking 'Back' (Retry {retries + 1}/1)...",
                    )
                    time.sleep(0.6)
                    continue

                tried_emails.add(target_email.lower())
                next_acc = account_manager.get_next_account(
                    current_email=target_email,
                    exclude_emails=list(tried_emails),
                )
                if next_acc and next_acc.email.lower() not in tried_emails:
                    log("INFO", f"🔄 Advancing to next eligible account: '{next_acc.email}' after callback timeout...")
                    target_email = next_acc.email
                    result["switched_to_email"] = target_email
                    time.sleep(0.5)
                    continue
                else:
                    err = "'Finish' / 'Get started' button or authenticated state not reached within timeout."
                    log("ERROR", err)
                    notify_step(8, False, err)
                    result["error"] = err
                    return result

            if not oauth_completed:
                err = "'Finish' / 'Get started' button or authenticated state not reached within timeout."
                log("ERROR", err)
                notify_step(8, False, err)
                result["error"] = err
                return result

            # 7. Close Settings and verify ready
            self._close_settings_and_focus()
            notify_step(9, True, "Settings closed & chat ready")

            active_email = final_email_holder[-1] if final_email_holder else target_email
            account_manager.set_active_account(active_email)
            result["switched_to_email"] = active_email
            result["success"] = True
            log("SUCCESS", f"🎉 Account switch complete! Active Antigravity account is now '{active_email}'.")
            return result

        except Exception as e:
            err = f"Account switch failed: {e}"
            log("ERROR", err)
            result["error"] = err
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
            return result

    def _is_antigravity_authenticated_or_ready(self) -> bool:
        """
        Quick non-blocking probe to check if Antigravity has already completed OAuth
        callback (e.g. Finish/Get started button visible, terms prompt visible, or workspace ready).
        Used as early_completion_checker for browser OCR automation on already-consented accounts.
        """
        try:
            return bool(self.page.evaluate("""() => {
                const candidates = Array.from(document.querySelectorAll('button, a[role="button"], [role="button"]'));
                const hasFinish = candidates.some(b => {
                    const t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return (
                        t === 'get started' ||
                        t === 'finish' ||
                        t === 'done' ||
                        t.includes('agree and continue') ||
                        t.includes('accept and continue')
                    ) && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
                }) || Boolean(document.querySelector('[data-testid="finish-btn"]:not([disabled]), [data-testid="get-started-btn"]:not([disabled]), .finish-auth-button:not([disabled])'));

                if (hasFinish) return true;

                const chatInput = document.querySelector('[role="textbox"][contenteditable="true"], textarea');
                if (chatInput && (chatInput.offsetWidth > 0 || chatInput.offsetHeight > 0)) {
                    return true;
                }
                return false;
            }"""))
        except Exception:
            return False

    def _open_profile_page(self, log: Optional[Callable[[str, str], None]] = None) -> bool:
        """Opens the Settings dialog (if not already open) and navigates to the Account / Profile tab."""
        log = log or (lambda lvl, msg: None)
        try:
            # 1. Ensure Settings modal is open
            is_dialog_open = self.page.evaluate("""() => {
                return Boolean(document.querySelector('.settings-modal-container, [role="dialog"] button[data-testid^="settings-nav-item"]'));
            }""")
            if not is_dialog_open:
                log("INFO", "Opening Settings dialog (Ctrl+,)...")
                self.page.keyboard.press("Control+,")
                time.sleep(0.8)
                is_dialog_open = self.page.evaluate("""() => {
                    return Boolean(document.querySelector('.settings-modal-container, [role="dialog"] button[data-testid^="settings-nav-item"]'));
                }""")
                if not is_dialog_open:
                    settings_selectors = [
                        '[data-testid="settings-button"]',
                        '[data-testid="settings-btn"]',
                        'button:has-text("Settings")',
                        '[aria-label="Settings"]',
                    ]
                    for sel in settings_selectors:
                        loc = self.page.locator(sel).first
                        if loc.is_visible():
                            loc.click(force=True)
                            time.sleep(0.8)
                            break

            # 2. Click Account / Profile button in Settings sidebar
            log("INFO", "Clicking Account / Profile tab in Settings sidebar...")
            clicked_account = self.page.evaluate("""() => {
                const modal = document.querySelector('.settings-modal-container') ||
                              document.querySelector('[role="dialog"]') ||
                              document;
                const btn = modal.querySelector('button[data-testid="settings-nav-item-Account"]') ||
                            modal.querySelector('button[data-testid="settings-nav-item-Profile"]') ||
                            modal.querySelector('[data-testid="profile-tab"]') ||
                            modal.querySelector('.user-profile-card') ||
                            Array.from(modal.querySelectorAll('button')).find(
                                b => b.innerText && (
                                    b.innerText.includes('@') ||
                                    b.innerText.trim().toLowerCase() === 'account' ||
                                    b.innerText.trim().toLowerCase() === 'profile'
                                )
                            );
                if (btn) {
                    btn.click();
                    return true;
                }
                return false;
            }""")

            if not clicked_account:
                fallback_selectors = [
                    'button[data-testid="settings-nav-item-Account"]',
                    'button[data-testid="settings-nav-item-Profile"]',
                    'button:has-text("@")',
                    'button:has-text("Profile")',
                    '[data-testid="profile-tab"]',
                    '.user-profile-card',
                ]
                for sel in fallback_selectors:
                    loc = self.page.locator(sel).first
                    if loc.is_visible():
                        loc.click(force=True)
                        clicked_account = True
                        break

            time.sleep(0.6)

            # 3. Verify Account page or Sign Out button is visible
            is_account_view = self.page.evaluate("""() => {
                const modal = document.querySelector('.settings-modal-container') ||
                              document.querySelector('[role="dialog"]') ||
                              document.body;
                if (!modal) return false;
                const text = modal.innerText || '';
                const hasSignOut = Array.from(modal.querySelectorAll('button')).some(
                    b => b.innerText && b.innerText.trim().toLowerCase() === 'sign out'
                );
                return hasSignOut || text.includes('Manage your plan, credentials') || Boolean(modal.querySelector('button[data-testid="settings-nav-item-Account"]'));
            }""")
            return bool(clicked_account or is_account_view)
        except Exception as e:
            log("ERROR", f"Error opening Profile/Account page: {e}")
            return False

    def _read_current_email(self, log: Optional[Callable[[str, str], None]] = None) -> str:
        """Reads the active Google account email from the Settings -> Account DOM."""
        log = log or (lambda lvl, msg: None)
        try:
            # Ensure Account nav item or modal is available
            has_modal = self.page.evaluate("""() => {
                return Boolean(document.querySelector('.settings-modal-container, [role="dialog"], button[data-testid="settings-nav-item-Account"], .profile-email, .user-email, .settings-content'));
            }""")
            if not has_modal:
                self._open_profile_page(log)

            info = self.page.evaluate("""() => {
                const navBtn = document.querySelector('button[data-testid="settings-nav-item-Account"]');
                const modal = document.querySelector('.settings-modal-container') ||
                              document.querySelector('[role="dialog"]') ||
                              document.querySelector('.profile-email, .user-email, .settings-content') ||
                              document.body;
                return {
                    navText: navBtn ? (navBtn.innerText || '') : '',
                    modalText: modal ? (modal.innerText || '') : ''
                };
            }""")

            if isinstance(info, str):
                nav_text = info
                modal_text = info
            elif isinstance(info, dict):
                nav_text = info.get("navText", "") or ""
                modal_text = info.get("modalText", "") or ""
            else:
                nav_text = ""
                modal_text = ""

            combined = f"{nav_text}\n{modal_text}"

            # 1. Check navBtn text first for an exact email regex (most specific to the logged-in user)
            email_pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
            m_nav = re.search(email_pattern, nav_text)
            if m_nav:
                found_email = m_nav.group(0).strip().lower()
                for acc in account_manager.accounts:
                    if acc.email.lower() == found_email:
                        return acc.email
                return found_email

            # 2. Check after "Email" label in the Account pane
            m_email_section = re.search(r'Email\s*\n+\s*(' + email_pattern + r')', modal_text, re.IGNORECASE)
            if m_email_section:
                found_email = m_email_section.group(1).strip().lower()
                for acc in account_manager.accounts:
                    if acc.email.lower() == found_email:
                        return acc.email
                return found_email

            # 3. Match against known accounts in account_manager
            for acc in account_manager.accounts:
                if acc.email.lower() in combined.lower():
                    return acc.email

            # 4. General regex fallback in modalText
            m_any = re.search(email_pattern, modal_text)
            if m_any:
                return m_any.group(0).strip().lower()

        except Exception as e:
            log("WARN", f"Error reading current email from DOM: {e}")
        return ""

    def _click_sign_out(self, log: Optional[Callable[[str, str], None]] = None) -> bool:
        """Clicks the 'Sign Out' button inside the Settings -> Account pane and confirms any dialog."""
        log = log or (lambda lvl, msg: None)
        try:
            # Ensure Sign Out button is visible; if not, open Profile/Account page first
            has_sign_out = self.page.evaluate("""() => {
                const modal = document.querySelector('.settings-modal-container') ||
                              document.querySelector('[role="dialog"]') ||
                              document;
                return Array.from(modal.querySelectorAll('button')).some(
                    b => b.innerText && b.innerText.trim().toLowerCase() === 'sign out'
                ) || Boolean(document.querySelector('[data-testid="sign-out-btn"]'));
            }""")
            if not has_sign_out:
                self._open_profile_page(log)
                time.sleep(0.5)

            clicked = self.page.evaluate("""() => {
                const modal = document.querySelector('.settings-modal-container') ||
                              document.querySelector('[role="dialog"]') ||
                              document;
                const btn = Array.from(modal.querySelectorAll('button')).find(
                    b => b.innerText && b.innerText.trim().toLowerCase() === 'sign out'
                ) || document.querySelector('[data-testid="sign-out-btn"]');
                if (btn) {
                    btn.click();
                    return true;
                }
                return false;
            }""")

            if not clicked:
                sign_out_selectors = [
                    'button:has-text("Sign Out")',
                    'button:has-text("Sign out")',
                    '[data-testid="sign-out-btn"]',
                ]
                for sel in sign_out_selectors:
                    btn = self.page.locator(sel).first
                    if btn.is_visible():
                        btn.click(force=True)
                        clicked = True
                        break

            if not clicked:
                return False

            time.sleep(0.6)

            # Confirm any sign out confirmation modal if one appears
            self.page.evaluate("""() => {
                const dialogs = Array.from(document.querySelectorAll('[role="alertdialog"], [role="dialog"]'));
                for (const d of dialogs) {
                    const confirmBtn = Array.from(d.querySelectorAll('button')).find(
                        b => b.innerText && ['yes', 'confirm', 'sign out', 'log out'].includes(b.innerText.trim().toLowerCase())
                    );
                    if (confirmBtn && !d.classList.contains('settings-modal-container')) {
                        confirmBtn.click();
                        return true;
                    }
                }
                return false;
            }""")
            time.sleep(0.4)
            return True
        except Exception as e:
            log("ERROR", f"Error clicking Sign Out: {e}")
            return False

    def _handle_error_screen_continue_with_different_account(self, log: Optional[Callable[[str, str], None]] = None) -> bool:
        """
        Detects if Antigravity is showing the error screen:
        'There was an unexpected issue setting up your account.'
        and clicks 'Continue with different account' to return to the 'Continue with Google' screen.
        """
        log = log or (lambda lvl, msg: None)
        try:
            clicked = self.page.evaluate("""() => {
                const candidates = Array.from(document.querySelectorAll('button, a, div, span, [role="button"]'));
                const matching = candidates.filter(el => {
                    const t = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().toLowerCase();
                    return (
                        t.includes('different account') ||
                        t.includes('another account') ||
                        t.includes('other account')
                    ) && t.length <= 80;
                });
                if (matching.length > 0) {
                    // Sort by text length ascending to get innermost/leaf element
                    matching.sort((a, b) => {
                        const lenA = (a.innerText || a.textContent || '').trim().length;
                        const lenB = (b.innerText || b.textContent || '').trim().length;
                        return lenA - lenB;
                    });
                    const target = matching[0];
                    if (typeof target.scrollIntoView === 'function') {
                        target.scrollIntoView({ behavior: 'instant', block: 'center' });
                    }
                    if (typeof target.focus === 'function') target.focus();
                    ['mousedown', 'mouseup', 'click'].forEach(evtType => {
                        target.dispatchEvent(new MouseEvent(evtType, { bubbles: true, cancelable: true, view: window }));
                    });
                    if (typeof target.click === 'function') {
                        target.click();
                    }
                    return true;
                }
                const testIdBtn = document.querySelector('[data-testid="continue-different-account-btn"]');
                if (testIdBtn && testIdBtn.offsetWidth > 0) {
                    testIdBtn.click();
                    return true;
                }
                return false;
            }""")
            if clicked:
                log("WARN", "Detected account setup error screen ('There was an unexpected issue setting up your account'). Clicked 'Continue with different account'.")
                time.sleep(0.8)
                return True

            selectors = [
                'text="Continue with different account"',
                'text="different account"',
                'button:has-text("Continue with different account")',
                'button:has-text("different account")',
                '[data-testid="continue-different-account-btn"]',
            ]
            for sel in selectors:
                try:
                    loc = self.page.locator(sel).first
                    if loc.is_visible():
                        loc.click(force=True)
                        log("WARN", "Detected account setup error screen. Clicked 'Continue with different account'.")
                        time.sleep(0.8)
                        return True
                except Exception:
                    continue
        except Exception as e:
            log("DEBUG", f"Error checking for different account button: {e}")
        return False

    def _click_back_if_oauth_pending(self, log: Optional[Callable[[str, str], None]] = None) -> bool:
        """
        Checks if Antigravity is stuck on the 'Welcome to Antigravity' screen with a spinning
        'Continue with Google' button and a 'Back' button below it. If so, clicks 'Back' and
        waits for the 'Continue with Google' button to reset to its idle clickable state.
        """
        log = log or (lambda lvl, msg: None)
        try:
            state_mgr = AntigravityStateManager(self.page)
            if state_mgr.is_oauth_pending():
                log("INFO", "🔄 Detected in-flight/spinning 'Continue with Google' state. Clicking 'Back' to reset...")
                return state_mgr.reset_oauth_pending_via_back(log)
        except Exception as e:
            log("DEBUG", f"Error checking/clicking Back button: {e}")
        return False

    def _click_continue_with_google(self, log: Optional[Callable[[str, str], None]] = None) -> bool:
        """Waits for the Antigravity login screen and clicks 'Continue with Google' / 'Sign in'."""
        log = log or (lambda lvl, msg: None)
        deadline = time.time() + 8.0
        while time.time() < deadline:
            # 1. If error screen ('Unexpected issue') is blocking the login view, reset it first
            if self._handle_error_screen_continue_with_different_account(log):
                time.sleep(0.5)

            # 2. If 'Continue with Google' is already spinning/waiting with a 'Back' button below it,
            # click 'Back' first so 'Continue with Google' resets to a fresh clickable button
            if self._click_back_if_oauth_pending(log):
                time.sleep(0.4)

            clicked = self.page.evaluate("""() => {
                const buttons = Array.from(document.querySelectorAll('button, a[role="button"]'));
                const target = buttons.find(b => {
                    const t = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return t.includes('continue with google') ||
                           t.includes('sign in with google') ||
                           t === 'sign in' ||
                           t === 'log in';
                }) || document.querySelector('[data-testid="google-signin-btn"]');
                if (target && target.offsetWidth > 0) {
                    target.click();
                    return true;
                }
                return false;
            }""")
            if clicked:
                log("INFO", "Browser launched for Google OAuth authentication.")
                return True

            continue_selectors = [
                'button:has-text("Continue with Google")',
                'button:has-text("Sign in with Google")',
                'button:has-text("Sign in")',
                '[aria-label="Continue with Google"]',
                '[data-testid="google-signin-btn"]',
            ]
            for sel in continue_selectors:
                loc = self.page.locator(sel).first
                try:
                    if loc.is_visible():
                        loc.click(force=True)
                        log("INFO", "Browser launched for Google OAuth authentication.")
                        return True
                except Exception:
                    continue

            time.sleep(0.4)
        return False

    def _dismiss_post_onboarding_terms_if_present(self, log: Optional[Callable[[str, str], None]] = None) -> bool:
        """Clicks 'Accept and continue' on the Security Notice & Data Use modal if present."""
        log = log or (lambda lvl, msg: None)
        try:
            # 1. If an unchecked consent checkbox exists in the modal, check it first
            try:
                cb = self.page.locator('input[type="checkbox"], [role="checkbox"][data-state="unchecked"], [role="checkbox"][aria-checked="false"]').first
                if cb.is_visible() and not cb.is_checked():
                    cb.click()
                    time.sleep(0.2)
            except Exception:
                pass

            # 2. Try native Playwright locator first (sends trusted pointer events)
            for sel in [
                'button:has-text("Agree and continue")',
                '[role="button"]:has-text("Agree and continue")',
                'button:has-text("Accept and continue")',
                '[role="button"]:has-text("Accept and continue")',
                'button:has-text("I agree")',
                'button:has-text("Agree")',
                'button:has-text("Accept")',
                '[role="button"]:has-text("Accept")',
            ]:
                try:
                    loc = self.page.locator(sel).first
                    if loc.is_visible():
                        loc.click(timeout=2000)
                        log("INFO", "Auto-dismissed 'Security Notice & Data Use' dialog ('Agree/Accept and continue' clicked).")
                        time.sleep(0.8)
                        return True
                except Exception:
                    continue

            # 3. Fallback to JavaScript evaluate
            clicked = self.page.evaluate("""() => {
                const dialogEl = document.querySelector('[role="dialog"], [role="alertdialog"], .modal, .modal-dialog');
                if (dialogEl) {
                    const dt = (dialogEl.innerText || '').toLowerCase();
                    if (dt.includes('security') || dt.includes('data use') || dt.includes('terms') || dt.includes('notice') || dt.includes('privacy')) {
                        const dialogBtns = Array.from(dialogEl.querySelectorAll('button, a, div, span, [role="button"]'));
                        const match = dialogBtns.find(b => {
                            const t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
                            const clean = t.replace(/&/g, 'and').replace(/\\s+/g, ' ');
                            return ['agree', 'accept', 'continue', 'confirm', 'got it', 'understand', 'done'].some(k => clean.includes(k));
                        }) || dialogEl.querySelector('button.primary, button[type="submit"]');
                        if (match) { match.click(); return true; }
                    }
                }

                const candidates = Array.from(document.querySelectorAll('button, a, div, span, [role="button"]'));
                const btn = candidates.find(b => {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    const clean = t.replace(/&/g, 'and').replace(/\\s+/g, ' ');
                    return clean.includes('agree and continue') ||
                           clean.includes('accept and continue') ||
                           clean === 'agree' ||
                           clean === 'accept' ||
                           clean === 'i agree' ||
                           clean === 'continue';
                });
                if (btn && (btn.offsetWidth > 0 || btn.offsetHeight > 0)) {
                    btn.click();
                    return true;
                }
                return false;
            }""")
            if clicked:
                log("INFO", "Auto-dismissed 'Security Notice & Data Use' dialog ('Agree/Accept and continue' clicked via evaluate).")
                time.sleep(0.8)
                return True
        except Exception as e:
            log("DEBUG", f"Terms notice check: {e}")
        return False

    def _wait_and_click_finish(
        self,
        log: Optional[Callable[[str, str], None]] = None,
        timeout: float = 25.0,
        target_email: Optional[str] = None,
        stop_event: Optional[Any] = None,
        final_email_container: Optional[List[str]] = None,
    ) -> bool:
        """Waits for OAuth callback completion ('Finish' / 'Get started' button or main workspace ready)."""
        log = log or (lambda lvl, msg: None)
        deadline = time.time() + timeout
        current_account_retries = 0
        account_failover_count = 0
        max_retries_per_account = 3
        max_accounts = max(1, len(account_manager.accounts)) if account_manager.accounts else 5

        while time.time() < deadline:
            state = self.page.evaluate("""() => {
                const candidates = Array.from(document.querySelectorAll('button, a[role="button"], [role="button"]'));

                // 0. Check for "Agree and continue" / "Accept and continue" post-onboarding terms dialog
                const acceptBtn = candidates.find(b => {
                    const t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return t.includes('agree and continue') ||
                           t.includes('accept and continue') ||
                           t === 'agree' ||
                           t === 'accept' ||
                           t === 'i agree';
                });
                if (acceptBtn && (acceptBtn.offsetWidth > 0 || acceptBtn.offsetHeight > 0)) {
                    acceptBtn.click();
                    return 'clicked_accept';
                }

                // 1. Check for primary completion buttons: 'get started', 'finish', 'done'
                const completionBtn = candidates.find(b => {
                    const t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return (t === 'get started' || t === 'finish' || t === 'done') && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
                }) || document.querySelector('[data-testid="finish-btn"]:not([disabled]), [data-testid="get-started-btn"]:not([disabled]), .finish-auth-button:not([disabled])');

                if (completionBtn && (completionBtn.offsetWidth > 0 || completionBtn.offsetHeight > 0)) {
                    completionBtn.click();
                    return 'clicked_finish';
                }

                // 2. Check for onboarding 'skip' button if available
                const skipBtn = candidates.find(b => {
                    const t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return t === 'skip' && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
                });
                if (skipBtn && (skipBtn.offsetWidth > 0 || skipBtn.offsetHeight > 0)) {
                    skipBtn.click();
                    return 'clicked_finish';
                }

                // 3. Check if workspace / chat is already loaded and ready
                const chatInput = document.querySelector('[role="textbox"][contenteditable="true"], textarea');
                if (chatInput && (chatInput.offsetWidth > 0 || chatInput.offsetHeight > 0)) {
                    return 'workspace_ready';
                }
                return null;
            }""")

            if state == "clicked_accept":
                log("INFO", "Detected post-onboarding 'Security Notice & Data Use' dialog. Clicked 'Accept and continue'.")
                time.sleep(1.0)
                if final_email_container is not None and target_email and target_email not in final_email_container:
                    final_email_container.append(target_email)
                return True

            if state == "clicked_finish":
                log("INFO", "Clicked 'Get started' / 'Finish'. Awaiting post-onboarding stabilization and security notice (up to 6s)...")
                finish_deadline = time.time() + 6.0
                terms_dismissed = False
                while time.time() < finish_deadline:
                    if stop_event and stop_event.is_set():
                        return False
                    if self._dismiss_post_onboarding_terms_if_present(log):
                        terms_dismissed = True
                        time.sleep(0.8)
                        break
                    has_input = False
                    try:
                        has_input = bool(self.page.evaluate("""() => {
                            const inp = document.querySelector('div[contenteditable="true"], textarea');
                            return inp && (inp.offsetWidth > 0 || inp.offsetHeight > 0);
                        }"""))
                    except Exception:
                        pass
                    if has_input:
                        time.sleep(0.5)
                        break
                    time.sleep(0.5)
                if not terms_dismissed:
                    self._dismiss_post_onboarding_terms_if_present(log)
                if final_email_container is not None and target_email and target_email not in final_email_container:
                    final_email_container.append(target_email)
                return True

            if state == "workspace_ready":
                # Ensure no lingering "Accept and continue" modal is overlaying the workspace
                self._dismiss_post_onboarding_terms_if_present(log)
                time.sleep(0.5)
                if final_email_container is not None and target_email and target_email not in final_email_container:
                    final_email_container.append(target_email)
                return True

            # If setup error screen appears, retry same account 3 times, then failover to next account for 3 times
            if self._handle_error_screen_continue_with_different_account(log):
                current_account_retries += 1

                if current_account_retries < max_retries_per_account:
                    # Retry the SAME account (Attempt 1 or 2 of 3)
                    log("INFO", f"Setup error encountered for '{target_email}' (Attempt {current_account_retries}/{max_retries_per_account}). Re-clicking 'Continue with Google'...")
                else:
                    # Current account exhausted 3 tries! Advance to next queued account
                    account_failover_count += 1
                    if account_failover_count >= max_accounts:
                        log("ERROR", f"Exhausted {max_retries_per_account} tries across {account_failover_count} accounts without successful authentication.")
                        return False

                    next_acc = None
                    if target_email:
                        try:
                            next_acc = account_manager.get_next_account(target_email)
                        except Exception:
                            pass

                    if next_acc and next_acc.email.lower() != (target_email or "").lower():
                        log("WARN", f"Account '{target_email}' failed {max_retries_per_account} times. Advancing to next queued account: '{next_acc.email}' (3 tries)...")
                        target_email = next_acc.email
                        current_account_retries = 0
                    else:
                        log("ERROR", f"Account '{target_email}' failed {max_retries_per_account} times and no alternate account is available in queue.")
                        return False

                time.sleep(0.8)
                self._click_continue_with_google(log)
                if target_email:
                    log("INFO", f"Executing Browser OCR automation for '{target_email}'...")
                    run_browser_account_selection(
                        target_email=target_email,
                        log_callback=log,
                        stop_event=stop_event,
                    )
                deadline = max(deadline, time.time() + timeout)
                continue

            time.sleep(0.5)
        return False

    def _close_settings_and_focus(self) -> bool:
        """Closes Settings modal if open, dismisses any post-auth notice, and focuses prompt input."""
        try:
            self._dismiss_post_onboarding_terms_if_present()
            is_open = self.page.evaluate("""() => Boolean(document.querySelector('.settings-modal-container, [role="dialog"]'))""")
            if is_open:
                self.page.keyboard.press("Escape")
                time.sleep(0.4)
            self._dismiss_post_onboarding_terms_if_present()
            return True
        except Exception:
            return False

