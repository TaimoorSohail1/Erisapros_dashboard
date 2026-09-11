import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.config import Settings, get_settings
from app.models import FTWilliamsReview
from app.services.ftwilliams_automation import FTWBringForwardResult


class PlaywrightFTWBringForwardAgent:
    """Use an existing demo-account browser session for FTW's native action.

    The browser click is never treated as proof that data changed. The caller
    must perform a fresh ftwLink query before any later decision or update.
    """

    _BRING_FORWARD_TEXT = re.compile(
        r"bring\s+forward\s+(?:prior[-\s]*year|\d{4})\s+data(?:\s+to\s+\d{4})?\s+for\s+this\s+plan\s+only",
        re.IGNORECASE,
    )
    _LOGIN_TEXT = re.compile(
        r"(?:\b(sign\s*in|log\s*in|multi[-\s]*factor|verification\s+code)\b|badpage\s*\(\s*error\s*\))",
        re.IGNORECASE,
    )
    _ALERT_FAILURE_TEXT = re.compile(r"\b(error|unable|failed|not\s+permitted)\b", re.IGNORECASE)
    _BRING_FORWARD_FAILURE_TEXT = re.compile(
        r"(?:bring\s+forward.{0,120}(?:error|unable|failed|not\s+permitted)|"
        r"(?:error|unable|failed|not\s+permitted).{0,120}bring\s+forward)",
        re.IGNORECASE | re.DOTALL,
    )
    # FT Williams may rotate its authenticated cookie during navigation. Keep
    # the refreshed state in the long-running API/worker process so a later
    # filing does not reuse the stale deployment-secret snapshot.
    _runtime_storage_state: dict | None = None
    _SESSION_STORAGE_KEY = "_erisapros_session_storage"
    _USER_AGENT_KEY = "_erisapros_user_agent"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    async def bring_forward(self, filing_id: str, review: FTWilliamsReview) -> FTWBringForwardResult:
        target_error = self._target_error(review)
        if target_error:
            return FTWBringForwardResult(False, "INVALID_TARGET", target_error)

        storage_state, storage_error = self._load_storage_state()
        if storage_error:
            return FTWBringForwardResult(
                False,
                "LOGIN_REQUIRED",
                storage_error,
            )

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return FTWBringForwardResult(
                False,
                "WORKER_UNAVAILABLE",
                "Playwright is not installed for the FT Williams Bring Forward worker.",
            )

        audit_directory = Path(self.settings.ftw_browser_audit_directory).expanduser()
        audit_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
        safe_filing_id = re.sub(r"[^A-Za-z0-9_.-]", "-", str(filing_id))[:80]
        before_path = audit_directory / f"{safe_filing_id}-{stamp}-before.png"
        after_path = audit_directory / f"{safe_filing_id}-{stamp}-after.png"
        timeout_ms = max(5, self.settings.ftw_browser_timeout_seconds) * 1000

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=self.settings.ftw_browser_headless)
                browser_state, session_storage, user_agent = self._split_storage_state(storage_state)
                context_options = {"storage_state": browser_state}
                if user_agent:
                    context_options["user_agent"] = user_agent
                context = await browser.new_context(**context_options)
                await self._restore_session_storage(context, session_storage)
                page = await context.new_page()
                page.set_default_timeout(timeout_ms)
                page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
                try:
                    navigation_started = asyncio.get_running_loop().time()
                    await page.goto(str(review.ftw_plan_url), wait_until="domcontentloaded", timeout=timeout_ms)
                    navigation_elapsed_ms = int(
                        (asyncio.get_running_loop().time() - navigation_started) * 1_000
                    )
                    identity_timeout_ms = max(1_000, timeout_ms - navigation_elapsed_ms)
                    page_text, identity_error, login_required = await self._wait_for_verified_target(
                        page,
                        review,
                        timeout_ms=identity_timeout_ms,
                    )
                    if not login_required:
                        await self._remember_storage_state(context, page)
                    await page.screenshot(path=str(before_path), full_page=True)
                    if login_required:
                        return FTWBringForwardResult(
                            False,
                            "LOGIN_REQUIRED",
                            "The saved FT Williams demo login session has expired or requires MFA.",
                            str(before_path),
                        )
                    if identity_error:
                        return FTWBringForwardResult(
                            False,
                            "INVALID_TARGET",
                            identity_error,
                            str(before_path),
                        )
                    locator = await self._bring_forward_locator(page)
                    if locator is None:
                        return FTWBringForwardResult(
                            False,
                            "PAGE_LAYOUT_CHANGED",
                            "The FT Williams Bring Forward action was not found; no page action was attempted.",
                            str(before_path),
                        )
                    await locator.click(timeout=timeout_ms)
                    await page.wait_for_timeout(1_000)
                    await self._remember_storage_state(context, page)
                    await page.screenshot(path=str(after_path), full_page=True)
                    if await self._visible_failure(page):
                        return FTWBringForwardResult(
                            False,
                            "FTW_REJECTED",
                            "FT Williams displayed an error after Bring Forward; current data was not assumed to exist.",
                            str(after_path),
                        )
                    return FTWBringForwardResult(
                        True,
                        "SUBMITTED",
                        "FT Williams Bring Forward was submitted; ftwLink re-query will verify the current-year record.",
                        str(after_path),
                    )
                finally:
                    await context.close()
                    await browser.close()
        except Exception as exc:
            return FTWBringForwardResult(
                False,
                "WORKER_FAILED",
                f"FT Williams Bring Forward stopped safely: {type(exc).__name__}: {exc}",
                str(before_path) if before_path.exists() else None,
            )

    async def _wait_for_verified_target(
        self,
        page,
        review: FTWilliamsReview,
        *,
        timeout_ms: int,
    ) -> tuple[str, str | None, bool]:
        """Wait for FTW's delayed legacy plan frame before checking identity."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(1_000, timeout_ms) / 1_000
        page_text = ""
        identity_error: str | None = None
        while True:
            if await self._login_required(page):
                return page_text, None, True
            page_text = await self._page_text(page)
            identity_error = self._page_identity_error(review, page_text)
            if identity_error is None:
                return page_text, None, False
            remaining_ms = int((deadline - loop.time()) * 1_000)
            if remaining_ms <= 0:
                return page_text, identity_error, False
            await page.wait_for_timeout(min(500, remaining_ms))

    def _load_storage_state(self) -> tuple[str | dict | None, str | None]:
        runtime_state = type(self)._runtime_storage_state
        if runtime_state is not None:
            return runtime_state, None

        inline_state = str(self.settings.ftw_browser_storage_state_json or "").strip()
        if inline_state:
            try:
                parsed = json.loads(inline_state)
            except (TypeError, ValueError):
                return None, "The saved FT Williams demo login session is invalid and must be refreshed."
            if not isinstance(parsed, dict) or not isinstance(parsed.get("cookies", []), list) or not isinstance(parsed.get("origins", []), list):
                return None, "The saved FT Williams demo login session has an invalid format."
            return parsed, None

        path_value = str(self.settings.ftw_browser_storage_state_path or "").strip()
        storage_path = Path(path_value).expanduser() if path_value else None
        if storage_path and storage_path.is_file():
            try:
                parsed = json.loads(storage_path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                return None, "The saved FT Williams demo login session is invalid and must be refreshed."
            if not isinstance(parsed, dict):
                return None, "The saved FT Williams demo login session has an invalid format."
            return parsed, None
        return None, "A saved FT Williams login session is required for the dedicated demo account."

    @classmethod
    def _split_storage_state(
        cls,
        storage_state: dict,
    ) -> tuple[dict, dict[str, list[dict[str, str]]], str | None]:
        browser_state = dict(storage_state)
        raw_session_storage = browser_state.pop(cls._SESSION_STORAGE_KEY, {})
        raw_user_agent = browser_state.pop(cls._USER_AGENT_KEY, None)
        user_agent = str(raw_user_agent or "").strip()
        if not user_agent or len(user_agent) > 512 or "\n" in user_agent or "\r" in user_agent:
            user_agent = None
        session_storage: dict[str, list[dict[str, str]]] = {}
        if isinstance(raw_session_storage, dict):
            for origin, entries in raw_session_storage.items():
                try:
                    parsed = urlsplit(str(origin))
                    host = (parsed.hostname or "").lower().rstrip(".")
                except ValueError:
                    continue
                if parsed.scheme != "https" or not (host == "ftwilliam.com" or host.endswith(".ftwilliam.com")):
                    continue
                if not isinstance(entries, list):
                    continue
                safe_entries = [
                    {"name": str(entry["name"]), "value": str(entry["value"])}
                    for entry in entries
                    if isinstance(entry, dict) and "name" in entry and "value" in entry
                ]
                session_storage[str(origin)] = safe_entries
        return browser_state, session_storage, user_agent

    @staticmethod
    async def _restore_session_storage(context, session_storage: dict[str, list[dict[str, str]]]) -> None:
        if not session_storage:
            return
        payload = json.dumps(session_storage, separators=(",", ":"))
        await context.add_init_script(
            """
            (() => {
              const saved = %s;
              const entries = saved[window.location.origin] || [];
              for (const entry of entries) {
                window.sessionStorage.setItem(entry.name, entry.value);
              }
            })();
            """ % payload
        )

    @staticmethod
    async def _capture_session_storage(page) -> dict[str, list[dict[str, str]]]:
        captured: dict[str, list[dict[str, str]]] = {}
        for frame in page.frames:
            try:
                parsed = urlsplit(str(frame.url))
                host = (parsed.hostname or "").lower().rstrip(".")
            except ValueError:
                continue
            if parsed.scheme != "https" or not (host == "ftwilliam.com" or host.endswith(".ftwilliam.com")):
                continue
            origin = f"{parsed.scheme}://{parsed.netloc}"
            try:
                values = await frame.evaluate(
                    "Object.entries(window.sessionStorage).map(([name, value]) => ({name, value}))"
                )
            except Exception:
                continue
            if isinstance(values, list):
                captured[origin] = [
                    {"name": str(entry["name"]), "value": str(entry["value"])}
                    for entry in values
                    if isinstance(entry, dict) and "name" in entry and "value" in entry
                ]
        return captured

    async def _remember_storage_state(self, context, page=None) -> None:
        state = await context.storage_state(indexed_db=True)
        if (
            isinstance(state, dict)
            and isinstance(state.get("cookies", []), list)
            and isinstance(state.get("origins", []), list)
        ):
            if page is not None:
                state[self._SESSION_STORAGE_KEY] = await self._capture_session_storage(page)
                try:
                    user_agent = str(await page.evaluate("navigator.userAgent") or "").strip()
                except Exception:
                    user_agent = ""
                if user_agent:
                    state[self._USER_AGENT_KEY] = user_agent
            type(self)._runtime_storage_state = state

    async def _bring_forward_locator(self, page):
        for frame in page.frames:
            candidates = [
                frame.get_by_role("button", name=self._BRING_FORWARD_TEXT),
                frame.get_by_role("link", name=self._BRING_FORWARD_TEXT),
                frame.get_by_text(self._BRING_FORWARD_TEXT),
                frame.locator(
                    "input[type='button'][value*='Bring Forward' i], "
                    "input[type='submit'][value*='Bring Forward' i]"
                ),
            ]
            for candidate in candidates:
                try:
                    if await candidate.count() == 1 and await candidate.is_visible():
                        return candidate
                except Exception:
                    continue
        return None

    async def _login_required(self, page) -> bool:
        for frame in page.frames:
            try:
                if await frame.locator("input[type='password']").count():
                    return True
                text = await self._frame_body_text(frame)
                if self._LOGIN_TEXT.search(text[:10_000]):
                    return True
            except Exception:
                continue
        return False

    async def _visible_failure(self, page) -> bool:
        for frame in page.frames:
            try:
                alerts = frame.locator(
                    "[role='alert'], .alert-danger, .error-message, .validation-summary-errors"
                )
                for alert_text in await alerts.all_inner_texts():
                    if self._ALERT_FAILURE_TEXT.search(alert_text):
                        return True
                text = await self._frame_body_text(frame)
                if self._BRING_FORWARD_FAILURE_TEXT.search(text[:10_000]):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _page_text(page) -> str:
        texts: list[str] = []
        for frame in page.frames:
            try:
                text = await PlaywrightFTWBringForwardAgent._frame_body_text(frame)
                if text:
                    texts.append(text)
                form_values = await frame.locator(
                    "input:not([type='password']), textarea, select"
                ).evaluate_all(
                    """
                    elements => elements.flatMap(element => {
                      if (element.tagName === "SELECT") {
                        return [element.value, ...Array.from(element.selectedOptions).map(option => option.text)];
                      }
                      return [element.value];
                    }).filter(value => typeof value === "string" && value.trim())
                    """
                )
                texts.extend(str(value) for value in form_values if str(value).strip())
            except Exception:
                continue
        return "\n".join(texts)

    @staticmethod
    async def _frame_body_text(frame) -> str:
        body = frame.locator("body")
        try:
            return await body.inner_text(timeout=5_000)
        except Exception:
            return (await body.text_content(timeout=5_000)) or ""

    @staticmethod
    def _page_identity_error(review: FTWilliamsReview, page_text: str) -> str | None:
        lookup = review.plan_lookup
        expected_name = str(lookup.plan_name if lookup else "").strip()
        expected_ein = re.sub(r"\D", "", str(lookup.company_employer_id if lookup else ""))
        expected_plan_number = str(lookup.plan_number if lookup else "").strip().zfill(3)
        expected_year = str(review.year or (lookup.year if lookup else "") or "").strip()
        if not all([expected_name, expected_ein, expected_plan_number, expected_year]):
            return "The FT Williams browser target is missing a confirmed plan name, EIN, plan number, or year."

        normalized_page = re.sub(
            r"\s+",
            " ",
            re.sub(r"[,.;:]", " ", str(page_text or "").replace("&", " and ").casefold()),
        ).strip()
        normalized_name = re.sub(
            r"\s+",
            " ",
            re.sub(r"[,.;:]", " ", expected_name.replace("&", " and ").casefold()),
        ).strip()
        if normalized_name not in normalized_page:
            return "The FT Williams browser page does not match the confirmed plan name."

        ein_match = re.search(r"\bEIN\s*:\s*([0-9-]+)", page_text or "", re.IGNORECASE)
        if not ein_match or re.sub(r"\D", "", ein_match.group(1)) != expected_ein:
            return "The FT Williams browser page does not match the confirmed EIN."

        plan_number_match = re.search(r"\bPN\s*:\s*(\d{1,3})", page_text or "", re.IGNORECASE)
        if not plan_number_match or plan_number_match.group(1).zfill(3) != expected_plan_number:
            return "The FT Williams browser page does not match the confirmed plan number."

        if not re.search(rf"\b5500\s*-\s*{re.escape(expected_year)}\b", page_text or "", re.IGNORECASE):
            return "The FT Williams browser page does not match the confirmed filing year."
        return None

    @staticmethod
    def _target_error(review: FTWilliamsReview) -> str | None:
        if not review.bring_forward_required:
            return "Bring Forward is not required for this filing."
        if not review.browser_mapping_confirmed:
            return "The FT Williams browser plan mapping has not been confirmed."
        value = str(review.ftw_plan_url or "").strip()
        try:
            parsed = urlsplit(value)
        except ValueError:
            return "The FT Williams plan URL is invalid."
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not (host == "ftwilliam.com" or host.endswith(".ftwilliam.com")):
            return "Bring Forward is restricted to a verified ftwilliam.com HTTPS plan URL."
        if parsed.username or parsed.password or parsed.port not in {None, 443}:
            return "The FT Williams plan URL contains unsafe credentials or port information."
        target = unquote(value).casefold()
        required_values = [review.ftw_browser_customer_id, review.ftw_browser_plan_id, review.year]
        if any(not str(item or "").strip() for item in required_values):
            return "The FT Williams browser customer and plan IDs must be mapped separately from ftwLink IDs."
        if any(str(item or "").strip().casefold() not in target for item in required_values):
            return "The FT Williams plan URL does not match the confirmed browser customer, plan, and year."
        return None
