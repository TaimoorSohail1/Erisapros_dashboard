"""Real isolated Chromium contexts, synthetic pages, zero live vendor requests."""
import asyncio
import time
from unittest.mock import patch

from playwright.async_api import async_playwright

from app.services.ftwilliams_local_agent_runtime import PersistentFTWBrowser, FTWLocalAgentRunner
from test_ftwilliams_agent_pause_resume import ControlledApi


HTML = """<!doctype html><title>Local FTW fixture</title>
<p>HighlandTech</p><label>Plan search <input name='plan-search'></label>"""


def test_real_context_pause_resume_keeps_other_profile_and_tab_untouched(tmp_path):
    async def exercise():
        requests = []

        async def local_response(route):
            requests.append(route.request.url)
            await route.fulfill(status=200, content_type="text/html", body=HTML)

        real = await async_playwright().start()

        class IsolatedChromium:
            async def launch_persistent_context(self, directory, **kwargs):
                # Production remains headed; tests never show a native window.
                kwargs["headless"] = True
                context = await real.chromium.launch_persistent_context(directory, **kwargs)
                await context.route("**/*", local_response)
                return context

        class Runtime:
            chromium = IsolatedChromium()

            async def stop(self):
                pass  # The test owns and closes the shared Playwright driver.

        class Starter:
            async def start(self):
                return Runtime()

        agent = PersistentFTWBrowser(tmp_path / "agent", expected_account="HighlandTech")
        external = PersistentFTWBrowser(tmp_path / "external", expected_account="HighlandTech")
        api = ControlledApi()
        runner = FTWLocalAgentRunner(api, agent)
        try:
            with patch("playwright.async_api.async_playwright", return_value=Starter()):
                await external.start()
                await external._page.goto("https://www.ftwilliam.com/synthetic-personal-search")
                await external._page.get_by_label("Plan search").fill("Keep my search")
                external_url = external._page.url
                await agent.start()
                await agent._page.goto("https://www.ftwilliam.com/synthetic-agent-search")
                await agent._context.add_cookies([{"name": "test-agent-only", "value": "synthetic", "url": "https://www.ftwilliam.com", "expires": time.time() + 86400}])
                for _ in range(5):
                    assert await agent.session_ready()
                assert agent._page.url.endswith("synthetic-agent-search")
                old_agent_page = agent._page
                api.paused = True
                await runner.run_once()
                assert old_agent_page.is_closed()
                assert agent._context is None
                assert external._page.url == external_url
                assert await external._page.get_by_label("Plan search").input_value() == "Keep my search"
                assert not any(cookie["name"] == "test-agent-only" for cookie in await external._context.cookies())
                api.paused = False
                await runner.run_once()
                assert not agent._page.is_closed()
                assert agent._page is not old_agent_page
                assert any(cookie["name"] == "test-agent-only" for cookie in await agent._context.cookies())
                assert external._page.url == external_url
                assert await external._page.get_by_label("Plan search").input_value() == "Keep my search"
                # Every request was intercepted with synthetic HTML; no network
                # request was forwarded to ftwilliam.com.
                assert requests
        finally:
            await agent.close()
            await external.close()
            await real.stop()

    asyncio.run(exercise())


def test_resume_automatic_login_uses_only_synthetic_dedicated_page(tmp_path):
    async def exercise():
        real = await async_playwright().start()

        async def local_response(route):
            await route.fulfill(status=200, content_type="text/html", body="""
                <label>Company <input name='company'></label>
                <label>User <input name='username'></label>
                <label>Password <input type='password'></label>
                <button onclick="document.body.innerHTML='<p>HighlandTech</p><h2>Plan Search</h2><input placeholder=&quot;Name or ID ...&quot;><h2>Search Results</h2>'">Log In</button>
            """)

        class Chromium:
            async def launch_persistent_context(self, directory, **kwargs):
                kwargs["headless"] = True
                context = await real.chromium.launch_persistent_context(directory, **kwargs)
                await context.route("**/*", local_response)
                return context

        class Runtime:
            chromium = Chromium()

            async def stop(self):
                pass

        class Starter:
            async def start(self):
                return Runtime()

        browser = PersistentFTWBrowser(tmp_path / "auto-login", expected_account="HighlandTech", login_credentials={
            "company_code": "synthetic-company", "username": "synthetic-user", "password": "synthetic-password",
        })
        api = ControlledApi()
        runner = FTWLocalAgentRunner(api, browser)
        try:
            with patch("playwright.async_api.async_playwright", return_value=Starter()):
                api.paused = True
                await runner.run_once()
                assert browser._context is None
                api.paused = False
                await runner.run_once()
                assert "HighlandTech" in await browser._page.locator("body").inner_text()
                assert browser._automatic_login_attempts == 0
                assert api.claims == 1
                assert not await browser._page.locator("input[type='password']").count()
        finally:
            await browser.close()
            await real.stop()

    asyncio.run(exercise())
