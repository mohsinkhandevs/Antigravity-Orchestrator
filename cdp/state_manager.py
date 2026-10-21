import time
from enum import Enum
from typing import Dict, Any, Optional, Callable
from playwright.sync_api import Page

from app.config import config
from app.account_manager import account_manager


class AntigravityUIState(str, Enum):
    LOGGED_OUT = "LOGGED_OUT"                       # "Continue with Google" / "Sign in" visible
    AUTH_ERROR_SCREEN = "AUTH_ERROR_SCREEN"         # "Unexpected issue" -> "Continue with different account"
    ONBOARDING_CAROUSEL = "ONBOARDING_CAROUSEL"     # Welcome slides with "Skip" / "Next"
    FINISH_AUTH_BUTTON = "FINISH_AUTH_BUTTON"       # Standalone "Get started" / "Finish" / "Done"
    TERMS_MODAL_ACTIVE = "TERMS_MODAL_ACTIVE"       # "Security Notice & Data Use" -> "Accept and continue"
    SETTINGS_MODAL_OPEN = "SETTINGS_MODAL_OPEN"     # Settings modal open (.settings-modal-container)
    GENERATING_IN_PROGRESS = "GENERATING_IN_PROGRESS"# Active generating spinner / Stop button active
    CHAT_QUOTA_EXHAUSTED = "CHAT_QUOTA_EXHAUSTED"   # Red quota limit banner visible in active chat
    CHAT_STEP_ERROR = "CHAT_STEP_ERROR"             # Step execution error banner visible in active chat
    WAITING_FOR_TOOL_APPROVAL = "WAITING_FOR_TOOL_APPROVAL" # "Allow" / "Run in Terminal" approval card
    NO_WORKSPACE_OPEN = "NO_WORKSPACE_OPEN"         # "Open Folder" welcome screen
    FRESH_CHAT_READY = "FRESH_CHAT_READY"           # Clean prompt input visible, no message history
    IDLE_EXISTING_CHAT = "IDLE_EXISTING_CHAT"       # Previous conversation history visible, agent idle
    UNKNOWN = "UNKNOWN"


class AntigravityStateManager:
    """
    High-performance state classifier and normalizer operating directly via Playwright CDP.
    Evaluates the Antigravity DOM in milliseconds and self-heals non-workspace states.
    """

    def __init__(self, page: Page):
        self.page = page

    def detect_state(self) -> AntigravityUIState:
        """
        Evaluates DOM state signatures in priority order in a single consolidated JS evaluation.
        Returns the active AntigravityUIState.
        """
        try:
            state_str = self.page.evaluate("""() => {
                // 1. Check for Logged Out / Welcome Sign-in screen
                const googleBtns = Array.from(document.querySelectorAll('button, a[role="button"], [role="button"]')).filter(b => {
                    const t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return t.includes('continue with google') ||
                           t.includes('sign in with google') ||
                           t === 'continue with google' ||
                           t === 'sign in with google';
                });
                const testIdGoogle = document.querySelector('[data-testid="google-signin-btn"]');
                if (googleBtns.length > 0 || (testIdGoogle && testIdGoogle.offsetWidth > 0)) {
                    return 'LOGGED_OUT';
                }

                // 2. Check for Account Setup Error screen ('There was an unexpected issue setting up your account')
                const diffAccountBtn = Array.from(document.querySelectorAll('button, a, div, span, [role="button"]')).find(el => {
                    const t = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().toLowerCase();
                    return (
                        t.includes('different account') ||
                        t.includes('another account') ||
                        t.includes('other account')
                    ) && t.length <= 80;
                }) || document.querySelector('[data-testid="continue-different-account-btn"]');
                if (diffAccountBtn && (diffAccountBtn.offsetWidth > 0 || diffAccountBtn.offsetHeight > 0)) {
                    return 'AUTH_ERROR_SCREEN';
                }

                // 3. Check for Security Notice & Data Use modal
                const dialogEl = document.querySelector('[role="dialog"], [role="alertdialog"], .modal, .modal-dialog');
                const isNoticeDialog = dialogEl && (() => {
                    const dt = (dialogEl.innerText || '').toLowerCase();
                    return dt.includes('security') || dt.includes('data use') || dt.includes('terms') || dt.includes('notice') || dt.includes('privacy');
                })();

                const acceptBtn = Array.from(document.querySelectorAll('button, a, div, span, [role="button"]')).find(b => {
                    const t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    const clean = t.replace(/&/g, 'and').replace(/\\s+/g, ' ');
                    const isTargetText = (
                        clean.includes('agree and continue') ||
                        clean.includes('accept and continue') ||
                        clean === 'agree' ||
                        clean === 'accept' ||
                        clean === 'i agree' ||
                        clean === 'continue' ||
                        clean === 'confirm' ||
                        clean === 'got it' ||
                        clean === 'i understand'
                    );
                    if (!isTargetText && isNoticeDialog && dialogEl.contains(b)) {
                        return clean.length <= 40 && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
                    }
                    return isTargetText && clean.length <= 60 && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
                });
                if (acceptBtn && (acceptBtn.offsetWidth > 0 || acceptBtn.offsetHeight > 0)) {
                    return 'TERMS_MODAL_ACTIVE';
                }

                // 4. Check for Finish / Get started completion screen
                const finishBtn = Array.from(document.querySelectorAll('button, a[role="button"], [role="button"]')).find(b => {
                    const t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return (t === 'get started' || t === 'finish' || t === 'done') && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
                }) || document.querySelector('[data-testid="finish-btn"]:not([disabled]), [data-testid="get-started-btn"]:not([disabled]), .finish-auth-button:not([disabled])');
                if (finishBtn && (finishBtn.offsetWidth > 0 || finishBtn.offsetHeight > 0)) {
                    return 'FINISH_AUTH_BUTTON';
                }

                // 5. Check for Onboarding Carousel / Slides ('Skip')
                const skipBtn = Array.from(document.querySelectorAll('button, [role="button"]')).find(b => {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    return t === 'skip' && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
                });
                if (skipBtn && (skipBtn.offsetWidth > 0 || skipBtn.offsetHeight > 0)) {
                    return 'ONBOARDING_CAROUSEL';
                }

                // 6. Check for Settings modal open (.settings-modal-container or role="dialog" for settings)
                const settingsModal = document.querySelector('.settings-modal-container') ||
                                      document.querySelector('[role="dialog"][aria-label*="Setting"]') ||
                                      (document.querySelector('[role="dialog"]') && document.querySelector('button[data-testid^="settings-nav-item"]'));
                if (settingsModal) {
                    const s = window.getComputedStyle(settingsModal);
                    if (s && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0') {
                        return 'SETTINGS_MODAL_OPEN';
                    }
                }

                // 7. Check if Agent is actively generating in active view or sidebar
                const stopBtn = document.querySelector('button[aria-label="Stop generating"], button[data-testid="stop-generation-button"], button[data-testid="stop-button"]');
                if (stopBtn && (stopBtn.offsetWidth > 0 || stopBtn.offsetHeight > 0)) {
                    return 'GENERATING_IN_PROGRESS';
                }
                const activeGenerating = document.querySelector('.generating-spinner, .progress-spinner, [data-testid="agent-generating"]');
                if (activeGenerating && activeGenerating.offsetWidth > 0) {
                    return 'GENERATING_IN_PROGRESS';
                }

                // 8. Check for Chat Quota Exhausted Banner in active chat
                const bodyText = (document.body ? document.body.innerText : '') || '';
                const quotaBanner = Array.from(document.querySelectorAll('.error-banner, .banner, [role="alert"], div, span')).some(el => {
                    const t = (el.innerText || el.textContent || '').toLowerCase();
                    return t.includes("you've reached your gemini limit") ||
                           t.includes("gemini quota limit reached") ||
                           t.includes("reached your quota limit") ||
                           t.includes("usage limit reached");
                });
                if (quotaBanner) {
                    return 'CHAT_QUOTA_EXHAUSTED';
                }

                // 9. Check for Chat Step / Execution Error Banner in active chat
                const stepError = Array.from(document.querySelectorAll('.error-banner, [role="alert"]')).some(el => {
                    const t = (el.innerText || el.textContent || '').toLowerCase();
                    return t.includes("tool execution error") ||
                           t.includes("action execution error") ||
                           t.includes("command failed with exit code");
                });
                if (stepError) {
                    return 'CHAT_STEP_ERROR';
                }

                // 10. Check for Waiting for Tool Approval
                const allowBtn = Array.from(document.querySelectorAll('button, [role="button"]')).find(b => {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    return t === 'allow' || t === 'always allow' || t === 'run in terminal' || t === 'approve';
                });
                if (allowBtn && (allowBtn.offsetWidth > 0 || allowBtn.offsetHeight > 0)) {
                    return 'WAITING_FOR_TOOL_APPROVAL';
                }

                // 11. Check if Workspace / Prompt input is ready
                const chatInput = document.querySelector('div[contenteditable="true"], [role="textbox"][contenteditable="true"], textarea, [data-testid="chat-input"]');
                if (chatInput && (chatInput.offsetWidth > 0 || chatInput.offsetHeight > 0)) {
                    const sidebar = document.querySelector('[data-testid="conversation-list-sidebar"]');
                    const turns = Array.from(document.querySelectorAll(
                        '[data-testid="turn-cards-container"], [data-testid="user-input-step"], [data-testid="cascade-system-message-toolbar"]'
                    )).filter(el => !sidebar || !sidebar.contains(el));

                    const currentUrl = window.location.href || '';
                    if (turns.length === 0 && !currentUrl.includes('/c/')) {
                        return 'FRESH_CHAT_READY';
                    } else {
                        return 'IDLE_EXISTING_CHAT';
                    }
                }

                // 12. Check for No Workspace / Open Folder welcome view
                const openFolderBtn = Array.from(document.querySelectorAll('button, a')).find(el => {
                    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                    return t.includes('open folder') || t.includes('clone repository');
                });
                if (openFolderBtn && openFolderBtn.offsetWidth > 0) {
                    return 'NO_WORKSPACE_OPEN';
                }

                return 'UNKNOWN';
            }""")
            if isinstance(state_str, str):
                return AntigravityUIState(state_str)
        except Exception:
            pass
        return AntigravityUIState.UNKNOWN

    def is_oauth_pending(self) -> bool:
        """
        Checks if Antigravity is on the 'Welcome to Antigravity' login screen with an
        in-flight/spinning 'Continue with Google' button and a visible 'Back' button below it.
        """
        try:
            return bool(self.page.evaluate("""() => {
                const allEls = Array.from(document.querySelectorAll('button, a, span, div, [role="button"]'));
                const hasGoogleBtn = allEls.some(b => {
                    const t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return (t.includes('continue with google') || t.includes('sign in with google')) &&
                           (b.offsetWidth > 0 || b.offsetHeight > 0);
                });
                if (!hasGoogleBtn) return false;

                const backBtn = allEls.find(el => {
                    const t = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (t !== 'back') return false;
                    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                    return (el.offsetWidth > 0 || el.offsetHeight > 0) && (!rect || (rect.width > 0 && rect.height > 0 && rect.width < 300 && rect.height < 100));
                });
                if (backBtn) return true;

                const googleBtn = Array.from(document.querySelectorAll('button, a[role="button"], [role="button"]')).find(b => {
                    const t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    return t.includes('continue with google') || t.includes('sign in with google');
                });
                if (googleBtn) {
                    if (googleBtn.disabled || googleBtn.getAttribute('aria-disabled') === 'true' || googleBtn.getAttribute('aria-busy') === 'true') {
                        return true;
                    }
                    const hasSpinner = Boolean(googleBtn.querySelector('svg[class*="spin"], .spinner, .loading, [class*="animate-spin"]'));
                    if (hasSpinner) return true;
                }
                return false;
            }"""))
        except Exception:
            return False

    def reset_oauth_pending_via_back(self, log: Optional[Callable[[str, str], None]] = None) -> bool:
        """
        Clicks the 'Back' button on the 'Welcome to Antigravity' screen when 'Continue with Google'
        is stuck spinning/waiting for browser callback, resetting it to an idle clickable state.
        """
        log = log or (lambda lvl, msg: None)
        try:
            clicked = bool(self.page.evaluate("""() => {
                const candidates = Array.from(document.querySelectorAll('button, a, [role="button"], span, div'));
                const backEl = candidates.find(el => {
                    const t = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (t !== 'back') return false;
                    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                    return (el.offsetWidth > 0 || el.offsetHeight > 0) && (!rect || (rect.width > 0 && rect.height > 0 && rect.width < 300 && rect.height < 100));
                });
                if (backEl) {
                    backEl.click();
                    return true;
                }
                return false;
            }"""))
            if not clicked:
                for sel in [
                    'button:text-is("Back")',
                    'a:text-is("Back")',
                    '[role="button"]:text-is("Back")',
                    'text="Back"',
                ]:
                    try:
                        loc = self.page.locator(sel).first
                        if loc.is_visible():
                            loc.click(force=True)
                            clicked = True
                            break
                    except Exception:
                        continue

            if clicked:
                log("INFO", "🔄 Clicked 'Back' on Welcome screen to cancel stuck OAuth wait state.")
                # Wait up to 2.5s for 'Back' button to disappear and 'Continue with Google' to reset
                reset_deadline = time.time() + 2.5
                while time.time() < reset_deadline:
                    if not self.is_oauth_pending():
                        break
                    time.sleep(0.2)
                time.sleep(0.3)
                return True
        except Exception as e:
            log("DEBUG", f"Error clicking Back on Welcome screen: {e}")
        return False

    def normalize_to_workspace(
        self,
        target_email: Optional[str] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
        oauth_handler: Optional[Any] = None,
        stop_event: Optional[Any] = None,
        pre_switch_callback: Optional[Callable[[str, str], bool]] = None,
        dom_controller: Optional[Any] = None,
    ) -> bool:
        """
        Self-heals any non-workspace state until Antigravity reaches a promptable workspace.
        Returns True when workspace is ready for prompt injection, False on cancellation/failure.
        """
        log = log_callback or (lambda lvl, msg: None)

        max_iterations = 6
        for iteration in range(max_iterations):
            if stop_event and stop_event.is_set():
                log("WARN", "State normalization stopped by user.")
                return False

            state = self.detect_state()
            log("INFO", f"🔍 [Pre-Flight State Detection] Current Antigravity UI State: {state.value}")

            # Desired terminal states:
            if state in (AntigravityUIState.FRESH_CHAT_READY, AntigravityUIState.IDLE_EXISTING_CHAT):
                log("SUCCESS", f"✓ Antigravity workspace is ready ({state.value}).")
                return True

            # 1. State: LOGGED_OUT
            if state == AntigravityUIState.LOGGED_OUT:
                log("WARN", "⚡ Detected Antigravity is LOGGED OUT! Initiating dynamic Google sign-in...")
                if oauth_handler:
                    res = oauth_handler.execute_account_switch(
                        target_account_email=target_email,
                        log_callback=log,
                        stop_event=stop_event,
                        pre_continue_callback=pre_switch_callback,
                    )
                    if not res.get("success"):
                        log("ERROR", f"Sign-in from logged-out state failed: {res.get('error')}")
                        return False
                else:
                    if self.is_oauth_pending():
                        log("WARN", "🔄 Antigravity is stuck on in-flight 'Continue with Google'. Clicking 'Back' first...")
                        self.reset_oauth_pending_via_back(log)
                    log("ERROR", "No OAuth handler available to sign in from logged-out state.")
                    return False
                time.sleep(1.0)
                continue

            # 2. State: AUTH_ERROR_SCREEN
            if state == AntigravityUIState.AUTH_ERROR_SCREEN:
                log("WARN", "Detected account setup error screen ('Unexpected issue'). Clicking 'Continue with different account'...")
                self._click_different_account(log)
                time.sleep(0.8)
                continue

            # 3. State: TERMS_MODAL_ACTIVE
            if state == AntigravityUIState.TERMS_MODAL_ACTIVE:
                log("INFO", "Auto-dismissing 'Security Notice & Data Use' modal ('Accept and continue')...")
                accepted = self._click_accept_terms(log)
                time.sleep(0.5)
                if not accepted:
                    check_ready = self.page.evaluate("""() => {
                        const chatInput = document.querySelector('div[contenteditable="true"], textarea');
                        return Boolean(chatInput && (chatInput.offsetWidth > 0 || chatInput.offsetHeight > 0));
                    }""")
                    if check_ready:
                        log("INFO", "Workspace chat input is visible and active; bypassing lingering terms state.")
                        return True
                continue

            # 4. State: FINISH_AUTH_BUTTON
            if state == AntigravityUIState.FINISH_AUTH_BUTTON:
                log("INFO", "Clicking 'Get started' / 'Finish' button to complete setup...")
                self._click_finish(log)
                time.sleep(1.0)
                continue

            # 5. State: ONBOARDING_CAROUSEL
            if state == AntigravityUIState.ONBOARDING_CAROUSEL:
                log("INFO", "Dismissing onboarding carousel / slides...")
                self._dismiss_carousel(log)
                time.sleep(0.5)
                continue

            # 6. State: SETTINGS_MODAL_OPEN
            if state == AntigravityUIState.SETTINGS_MODAL_OPEN:
                log("INFO", "Closing lingering Settings modal (pressing Escape)...")
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
                time.sleep(0.5)
                continue

            # 7. State: GENERATING_IN_PROGRESS
            if state == AntigravityUIState.GENERATING_IN_PROGRESS:
                log("WARN", "Agent is currently generating in Antigravity. Waiting for completion...")
                if dom_controller and hasattr(dom_controller, "wait_for_completion"):
                    finished = dom_controller.wait_for_completion(
                        timeout_seconds=3600,
                        stabilization_seconds=3,
                        is_infinite=True,
                        log_callback=log,
                    )
                    if not finished and stop_event and stop_event.is_set():
                        return False
                else:
                    time.sleep(2.0)
                continue

            # 8. State: CHAT_QUOTA_EXHAUSTED
            if state == AntigravityUIState.CHAT_QUOTA_EXHAUSTED:
                log("WARN", "Active chat shows Quota Exhausted banner. Initiating account rotation...")
                if oauth_handler:
                    res = oauth_handler.execute_account_switch(
                        log_callback=log,
                        stop_event=stop_event,
                        pre_continue_callback=pre_switch_callback,
                    )
                    if not res.get("success"):
                        return False
                time.sleep(1.0)
                continue

            # 9. State: WAITING_FOR_TOOL_APPROVAL
            if state == AntigravityUIState.WAITING_FOR_TOOL_APPROVAL:
                log("WARN", "Agent is waiting for tool approval.")
                # We do not blindly auto-approve dangerous commands unless confirmed; sleep briefly
                time.sleep(1.0)
                return True

            # Fallback for UNKNOWN / NO_WORKSPACE_OPEN
            time.sleep(0.5)

        # Final check
        final_state = self.detect_state()
        return final_state in (AntigravityUIState.FRESH_CHAT_READY, AntigravityUIState.IDLE_EXISTING_CHAT)

    def _click_different_account(self, log: Callable[[str, str], None]) -> bool:
        try:
            return bool(self.page.evaluate("""() => {
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
                    matching[0].click();
                    return true;
                }
                const btn = document.querySelector('[data-testid="continue-different-account-btn"]');
                if (btn) { btn.click(); return true; }
                return false;
            }"""))
        except Exception as e:
            log("DEBUG", f"Error clicking different account: {e}")
            return False

    def _click_accept_terms(self, log: Callable[[str, str], None]) -> bool:
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
                        log("SUCCESS", "Clicked 'Agree and continue' / 'Accept and continue' on terms notice.")
                        time.sleep(0.5)
                        return True
                except Exception:
                    continue

            # 3. Fallback to JavaScript evaluate
            clicked = bool(self.page.evaluate("""() => {
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

                const btns = Array.from(document.querySelectorAll('button, a, div, span, [role="button"]'));
                const btn = btns.find(b => {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    const clean = t.replace(/&/g, 'and').replace(/\\s+/g, ' ');
                    return clean.includes('agree and continue') ||
                           clean.includes('accept and continue') ||
                           clean === 'agree' ||
                           clean === 'accept' ||
                           clean === 'i agree' ||
                           clean === 'continue';
                });
                if (btn) { btn.click(); return true; }
                return false;
            }"""))
            if clicked:
                log("SUCCESS", "Clicked 'Agree and continue' / 'Accept' on terms notice via evaluate.")
                time.sleep(0.5)
                return True
            return False
        except Exception as e:
            log("DEBUG", f"Error clicking accept terms: {e}")
            return False

    def _click_finish(self, log: Callable[[str, str], None]) -> bool:
        try:
            return bool(self.page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button, a[role="button"], [role="button"]'));
                const btn = btns.find(b => {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    return t === 'get started' || t === 'finish' || t === 'done';
                }) || document.querySelector('[data-testid="finish-btn"], [data-testid="get-started-btn"]');
                if (btn) { btn.click(); return true; }
                return false;
            }"""))
        except Exception as e:
            log("DEBUG", f"Error clicking finish: {e}")
            return False

    def _dismiss_carousel(self, log: Callable[[str, str], None]) -> bool:
        try:
            return bool(self.page.evaluate("""() => {
                const skipBtn = Array.from(document.querySelectorAll('button, [role="button"]')).find(b => {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    return t === 'skip';
                });
                if (skipBtn) { skipBtn.click(); return true; }

                const nextBtn = Array.from(document.querySelectorAll('button, [role="button"]')).find(b => {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    return t === 'next';
                });
                if (nextBtn) { nextBtn.click(); return true; }
                return false;
            }"""))
        except Exception as e:
            log("DEBUG", f"Error dismissing carousel: {e}")
            return False
