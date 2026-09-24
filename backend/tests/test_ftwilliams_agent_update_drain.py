import asyncio

from app.services.ftwilliams_local_agent_runtime import FTWLocalAgentRunner, LocalAgentActionResult


class Api:
    def __init__(self):
        self.claims = 0
        self.completions = 0
        self.fail_completion = False
        self.heartbeats = []

    async def control(self):
        return {'pause_requested': False, 'browser_allowed': True}

    async def heartbeat(self, **kwargs):
        self.heartbeats.append(kwargs)

    async def claim(self):
        self.claims += 1
        return {'job': {'id': 'test-job'}, 'claim_token': 'test-token'}

    async def complete(self, *_args):
        self.completions += 1
        if self.fail_completion:
            raise OSError('synthetic network failure')

    async def close(self):
        pass


class Browser:
    def __init__(self, stop):
        self.stop = stop
        self.actions = 0
        self.closed = 0
        self.stop_during_readiness = False

    async def session_ready(self):
        if self.stop_during_readiness:
            self.stop[0] = True
        return True

    async def execute(self, _job):
        self.actions += 1
        self.stop[0] = True
        return LocalAgentActionResult('SUBMITTED', 'Synthetic success')

    async def close(self):
        self.closed += 1


def test_idle_update_closes_browser_without_claiming():
    stop = [True]
    api, browser = Api(), Browser(stop)
    runner = FTWLocalAgentRunner(api, browser, stop_requested=lambda: stop[0])
    asyncio.run(runner.run_forever())
    assert api.claims == api.completions == browser.actions == 0
    assert browser.closed > 0
    assert api.heartbeats[-1]['waiting'] is True


def test_active_update_finishes_and_reports_once_before_exiting():
    stop = [False]
    api, browser = Api(), Browser(stop)
    runner = FTWLocalAgentRunner(api, browser, stop_requested=lambda: stop[0])
    asyncio.run(runner.run_forever())
    assert api.claims == api.completions == browser.actions == 1
    assert runner._pending_completion is None
    assert browser.closed > 0


def test_stop_during_login_check_prevents_next_claim():
    stop = [False]
    api, browser = Api(), Browser(stop)
    browser.stop_during_readiness = True
    runner = FTWLocalAgentRunner(api, browser, stop_requested=lambda: stop[0])
    asyncio.run(runner.run_forever())
    assert api.claims == browser.actions == 0


def test_unacknowledged_result_is_retained_not_reexecuted():
    async def scenario():
        stop = [False]
        api, browser = Api(), Browser(stop)
        api.fail_completion = True
        runner = FTWLocalAgentRunner(api, browser, stop_requested=lambda: stop[0])
        try:
            await runner.run_once()
        except OSError:
            pass
        assert runner._pending_completion is not None
        api.fail_completion = False
        await runner.run_forever()
        assert api.claims == browser.actions == 1
        assert api.completions == 2
        assert runner._pending_completion is None
    asyncio.run(scenario())


def test_stop_during_last_remote_control_check_prevents_claim():
    stop = [False]
    api, browser = Api(), Browser(stop)
    class RacingApi(Api):
        def __init__(self):
            super().__init__()
            self.controls = 0
        async def control(self):
            self.controls += 1
            if self.controls == 2:
                stop[0] = True
            return await super().control()
    api = RacingApi()
    runner = FTWLocalAgentRunner(api, browser, stop_requested=lambda: stop[0])
    asyncio.run(runner.run_forever())
    assert api.claims == browser.actions == 0
