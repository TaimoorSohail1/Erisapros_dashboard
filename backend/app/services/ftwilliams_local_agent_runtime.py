"""Client-local FT Williams agent runtime.

The browser click is only a submitted action. The dashboard remains responsible
for proving success through a fresh ftwLink query and record-ID comparison.
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import httpx

from app.services.ftwilliams_local_agent import LocalFTWTarget, verify_local_ftw_identity


AGENT_VERSION = "0.3.0"
_FTW_HOME_URL = "https://www.ftwilliam.com/cgi-bin/index.cgi?#go=home"
_AUTOMATIC_LOGIN_RETRY_SECONDS = 300.0
_AUTOMATIC_LOGIN_MAX_ATTEMPTS = 2
_MANUAL_VERIFICATION_TEXT = re.compile(
    r"(?:multi[-\s]*factor|two[-\s]*factor|verification\s+code|security\s+code|captcha)",
    re.IGNORECASE,
)
_BRING_FORWARD_TEXT = re.compile(
    r"bring\s+forward\s+(?:prior[-\s]*year|\d{4})\s+data(?:\s+to\s+\d{4})?\s+for\s+this\s+plan\s+only",
    re.IGNORECASE,
)
_FAILURE_TEXT = re.compile(
    r"(?:bring\s+forward.{0,120}(?:error|unable|failed|not\s+permitted)|"
    r"(?:error|unable|failed|not\s+permitted).{0,120}bring\s+forward)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class LocalAgentActionResult:
    state: str
    message: str


class LocalAgentApiClient:
    def __init__(
        self,
        server_url: str,
        device_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        base = _validated_server_base(server_url)
        self._client = httpx.AsyncClient(
            base_url=base,
            headers={"Authorization": f"Bearer {device_token}"},
            timeout=httpx.Timeout(45.0),
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def heartbeat(
        self,
        *,
        browser_ready: bool,
        login_required: bool = False,
        last_error: str | None = None,
    ) -> dict:
        response = await self._client.post(
            "api/ftwilliams/local-agent/agent/heartbeat",
            json={
                "agent_version": AGENT_VERSION,
                "browser_ready": browser_ready,
                "login_required": login_required,
                "last_error": last_error,
            },
        )
        response.raise_for_status()
        return response.json()

    async def claim(self) -> dict:
        response = await self._client.post("api/ftwilliams/local-agent/agent/jobs/claim")
        response.raise_for_status()
        return response.json()

    async def revoke_self(self) -> dict:
        response = await self._client.post("api/ftwilliams/local-agent/agent/revoke")
        response.raise_for_status()
        return response.json()

    async def complete(self, job_id: str, claim_token: str, result: LocalAgentActionResult) -> dict:
        response = await self._client.post(
            f"api/ftwilliams/local-agent/agent/jobs/{job_id}/complete",
            json={
                "claim_token": claim_token,
                "state": result.state,
                "message": result.message,
            },
            # Completion performs the authoritative ftwLink re-query/read-back.
            # Keep this below CloudFront's 120-second origin timeout while
            # allowing more time than ordinary agent API calls.
            timeout=httpx.Timeout(115.0),
        )
        response.raise_for_status()
        return response.json()


class PersistentFTWBrowser:
    def __init__(
        self,
        profile_dir: str | Path,
        *,
        expected_account: str,
        timeout_ms: int = 45_000,
        login_credentials: dict[str, str] | None = None,
    ):
        self.profile_dir = Path(profile_dir).expanduser().resolve()
        self.expected_account = str(expected_account or "").strip()
        self.timeout_ms = max(5_000, timeout_ms)
        self.login_credentials = self._validated_login_credentials(login_credentials)
        self.login_required_message = "Sign in to FT Williams in the dedicated ERISAPros browser window."
        self._automatic_login_attempts = 0
        self._next_automatic_login_at = 0.0
        self._manual_verification_pending = False
        self._playwright = None
        self._context = None
        self._page = None

    async def start(self) -> None:
        page_is_open = bool(
            self._context is not None
            and self._page is not None
            and not self._page.is_closed()
        )
        if page_is_open:
            return
        if self._context is not None or self._playwright is not None or self._page is not None:
            # A client may close the dedicated browser window while the agent
            # keeps running. Dispose the stale Playwright objects so the next
            # poll reopens the same persistent profile and resumes safely.
            await self.close()
        from playwright.async_api import async_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=False,
                args=[],
            )
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            self._page.set_default_timeout(self.timeout_ms)
            self._page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        context, playwright = self._context, self._playwright
        self._context = None
        self._page = None
        self._playwright = None
        try:
            if context is not None:
                await context.close()
        finally:
            if playwright is not None:
                await playwright.stop()

    async def session_ready(self) -> bool:
        await self.start()
        preserve_verification_page = (
            self._manual_verification_pending
            and self._is_ftw_url(getattr(self._page, "url", ""))
        )
        if not preserve_verification_page:
            await self._page.goto(
                _FTW_HOME_URL,
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1_000
        while asyncio.get_running_loop().time() < deadline:
            text, password_visible = await self._page_text()
            normalized = re.sub(r"\s+", " ", text.casefold())
            if self.expected_account.casefold() in normalized:
                self._automatic_login_attempts = 0
                self._next_automatic_login_at = 0.0
                self._manual_verification_pending = False
                self.login_required_message = ""
                return True
            if password_visible:
                self._manual_verification_pending = False
                if not self.login_credentials:
                    self.login_required_message = "Sign in to FT Williams in the dedicated ERISAPros browser window."
                    return False
                now = asyncio.get_running_loop().time()
                if self._automatic_login_attempts >= _AUTOMATIC_LOGIN_MAX_ATTEMPTS:
                    self.login_required_message = (
                        "Automatic login was paused after repeated failures. Update the saved login or sign in manually."
                    )
                    return False
                if now < self._next_automatic_login_at:
                    self.login_required_message = (
                        "Automatic login is waiting before another safe attempt. You can sign in manually now."
                    )
                    return False
                self._automatic_login_attempts += 1
                self._next_automatic_login_at = now + _AUTOMATIC_LOGIN_RETRY_SECONDS
                self.login_required_message = "Automatic login is in progress."
                try:
                    await self._submit_saved_login()
                except Exception:
                    self.login_required_message = (
                        "Automatic login could not use the current FT Williams page. Sign in manually; no password was sent elsewhere."
                    )
                    return False
                await self._page.wait_for_timeout(1_000)
                continue
            if _MANUAL_VERIFICATION_TEXT.search(text[:20_000]):
                self._manual_verification_pending = True
                self.login_required_message = (
                    "Complete the FT Williams verification prompt in the dedicated browser; work will resume automatically."
                )
                return False
            await self._page.wait_for_timeout(500)
        self.login_required_message = (
            "FT Williams did not confirm the expected account. Complete login in the dedicated browser."
        )
        return False

    async def _submit_saved_login(self) -> None:
        if not self.login_credentials:
            raise RuntimeError("No saved FT Williams login is available.")
        if not self._is_ftw_url(str(self._page.url)):
            raise RuntimeError("Saved login was blocked because the browser is not on FT Williams.")

        for frame in self._page.frames:
            password = frame.locator("input[type='password']:visible")
            if not await password.count():
                continue
            company = await self._first_visible_locator(
                frame,
                "input[name*='company' i], input[id*='company' i]",
                fallback_index=0,
            )
            username = await self._first_visible_locator(
                frame,
                "input[name*='user' i], input[id*='user' i]",
                fallback_index=1,
            )
            if company is None or username is None:
                raise RuntimeError("The FT Williams login page layout was not recognized.")
            await company.fill(self.login_credentials["company_code"])
            await username.fill(self.login_credentials["username"])
            await password.first.fill(self.login_credentials["password"])
            await password.first.press("Enter")
            return
        raise RuntimeError("The FT Williams password field was not found.")

    @staticmethod
    async def _first_visible_locator(frame, selector: str, *, fallback_index: int):
        candidates = frame.locator(selector)
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            if await candidate.is_visible():
                return candidate
        fallback = frame.locator("input[type='text']:visible, input:not([type]):visible")
        if await fallback.count() > fallback_index:
            return fallback.nth(fallback_index)
        return None

    @staticmethod
    def _validated_login_credentials(credentials: dict[str, str] | None) -> dict[str, str] | None:
        if not isinstance(credentials, dict):
            return None
        normalized = {
            key: str(credentials.get(key) or "").strip()
            for key in ("company_code", "username", "password")
        }
        return normalized if all(normalized.values()) else None

    @staticmethod
    def _is_ftw_url(url: str) -> bool:
        host = (urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
        return host == "ftwilliam.com" or host.endswith(".ftwilliam.com")

    async def execute(self, job: dict) -> LocalAgentActionResult:
        if str(job.get("expected_account") or "").strip().casefold() != self.expected_account.casefold():
            return LocalAgentActionResult(
                "INVALID_TARGET",
                "The job belongs to a different FT Williams account; no navigation or click was attempted.",
            )
        target = LocalFTWTarget.from_dict(
            {
                "label": str(job.get("filing_id") or job.get("id") or "FT Williams job"),
                "url": job.get("target_url"),
                "plan_name": job.get("expected_plan_name"),
                "ein": job.get("expected_ein"),
                "plan_number": job.get("expected_plan_number"),
                "year": job.get("expected_year"),
            }
        )
        await self.start()
        await self._page.goto(target.url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        verification = await self._wait_for_identity(target)
        if not verification.success:
            return LocalAgentActionResult(verification.state, verification.message)

        candidate = await self._single_bring_forward_candidate()
        if candidate is None:
            return LocalAgentActionResult(
                "PAGE_LAYOUT_CHANGED",
                "The single expected FT Williams Bring Forward action was not found; no click was attempted.",
            )
        await candidate.click(timeout=self.timeout_ms)
        await self._page.wait_for_timeout(1_000)
        text, _ = await self._page_text()
        if _FAILURE_TEXT.search(text[:20_000]):
            return LocalAgentActionResult(
                "FAILED",
                "FT Williams displayed a Bring Forward error; the dashboard will not assume success.",
            )
        return LocalAgentActionResult(
            "SUBMITTED",
            "Bring Forward was submitted from the client computer; ftwLink verification is required.",
        )

    async def _wait_for_identity(self, target: LocalFTWTarget):
        deadline = asyncio.get_running_loop().time() + self.timeout_ms / 1_000
        last_result = None
        while asyncio.get_running_loop().time() < deadline:
            text, password_visible = await self._page_text()
            last_result = verify_local_ftw_identity(
                target,
                text,
                expected_account=self.expected_account,
                password_visible=password_visible,
            )
            if last_result.success or last_result.state in {"LOGIN_REQUIRED", "WRONG_ACCOUNT"}:
                return last_result
            await self._page.wait_for_timeout(500)
        return last_result

    async def _single_bring_forward_candidate(self):
        matches = []
        for frame in self._page.frames:
            elements = frame.locator("a, button, input[type='button'], input[type='submit']")
            try:
                values = await elements.evaluate_all(
                    """
                    nodes => nodes.map((node, index) => ({
                      index,
                      text: (node.innerText || node.value || node.textContent || '').trim(),
                      visible: !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length)
                    }))
                    """
                )
            except Exception:
                continue
            for value in values:
                if value.get("visible") and _BRING_FORWARD_TEXT.fullmatch(str(value.get("text") or "").strip()):
                    matches.append(elements.nth(int(value["index"])))
        return matches[0] if len(matches) == 1 else None

    async def _page_text(self) -> tuple[str, bool]:
        texts: list[str] = []
        password_visible = False
        for frame in self._page.frames:
            try:
                password_visible = password_visible or bool(
                    await frame.locator("input[type='password']").count()
                )
                texts.append((await frame.locator("body").inner_text(timeout=5_000)) or "")
                values = await frame.locator(
                    "input:not([type='password']), textarea, select"
                ).evaluate_all(
                    """
                    elements => elements.flatMap(element => {
                      if (element.tagName === 'SELECT') {
                        return [element.value, ...Array.from(element.selectedOptions).map(option => option.text)];
                      }
                      return [element.value];
                    }).filter(value => typeof value === 'string' && value.trim())
                    """
                )
                texts.extend(str(value) for value in values if str(value).strip())
            except Exception:
                continue
        return "\n".join(texts), password_visible


class FTWLocalAgentRunner:
    def __init__(self, api: LocalAgentApiClient, browser: PersistentFTWBrowser):
        self.api = api
        self.browser = browser

    async def run_once(self) -> bool:
        try:
            ready = await self.browser.session_ready()
        except Exception as exc:
            await self.api.heartbeat(browser_ready=False, last_error=f"Browser unavailable: {type(exc).__name__}")
            return False
        if not ready:
            await self.api.heartbeat(
                browser_ready=False,
                login_required=True,
                last_error=(
                    getattr(self.browser, "login_required_message", "")
                    or "Sign in to FT Williams in the dedicated ERISAPros browser window."
                ),
            )
            return False

        await self.api.heartbeat(browser_ready=True)
        claim = await self.api.claim()
        job = claim.get("job")
        token = claim.get("claim_token")
        if not job or not token:
            return False
        try:
            result = await self.browser.execute(job)
        except Exception as exc:
            result = LocalAgentActionResult(
                "FAILED",
                f"The local browser stopped safely before completion: {type(exc).__name__}",
            )
        await self.api.complete(str(job["id"]), str(token), result)
        return True

    async def run_forever(self, *, poll_seconds: float = 10.0) -> None:
        delay = max(2.0, poll_seconds)
        while True:
            try:
                await self.run_once()
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(min(60.0, delay * 2))

    async def close(self) -> None:
        await self.browser.close()
        await self.api.close()


async def pair_device(
    server_url: str,
    pairing_code: str,
    device_name: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    base = _validated_server_base(server_url)
    async with httpx.AsyncClient(base_url=base, timeout=45.0, transport=transport) as client:
        response = await client.post(
            "api/ftwilliams/local-agent/pair",
            json={
                "pairing_code": pairing_code,
                "device_name": device_name,
                "agent_version": AGENT_VERSION,
            },
        )
        response.raise_for_status()
        return response.json()


def _validated_server_base(server_url: str) -> str:
    value = str(server_url or "").strip()
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValueError("The local agent server URL is invalid.") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    local_http = parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}
    if (
        not host
        or (parsed.scheme != "https" and not local_http)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("The local agent requires an HTTPS server origin (localhost is allowed for testing).")
    return value.rstrip("/") + "/"
