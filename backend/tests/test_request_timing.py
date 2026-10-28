import asyncio
import json
from types import SimpleNamespace

import pytest

from app import request_timing as timing


@pytest.mark.parametrize('status,delay,expected', [(200, .01, False), (200, 1.2, True), (503, .01, True)])
def test_slow_logging_preserves_messages_and_excludes_user_data(monkeypatch, caplog, status, delay, expected):
    clock = [0.0]
    monkeypatch.setattr(timing, 'perf_counter', lambda: clock[0])
    sent = []
    messages = [{'type': 'http.response.start', 'status': status, 'headers': []},
                {'type': 'http.response.body', 'body': b'unchanged'}]
    async def endpoint(scope, receive, send):
        scope['route'] = SimpleNamespace(path='/api/videos/{video_id}/media')
        clock[0] = delay
        for message in messages:
            await send(message)
    async def send(message):
        sent.append(message)
    scope = {'type': 'http', 'method': 'GET', 'path': '/api/videos/private-id/media',
             'query_string': b'token=secret', 'headers': [(b'authorization', b'private')]}
    with caplog.at_level('INFO', logger=timing.logger.name):
        asyncio.run(timing.RequestTimingMiddleware(endpoint)(scope, None, send))
    assert sent == messages
    assert bool(caplog.records) == expected
    if expected:
        record = json.loads(caplog.records[0].message.split(' ', 1)[1])
        assert record['route'] == '/api/videos/{video_id}/media'
        assert record['response_headers_ms'] == delay * 1000
        assert all(secret not in caplog.text for secret in ('private-id', 'secret', 'authorization'))


def test_slow_stream_does_not_trigger_slow_processing_log(monkeypatch, caplog):
    clock = [0.0]
    monkeypatch.setattr(timing, 'perf_counter', lambda: clock[0])
    async def endpoint(scope, receive, send):
        clock[0] = .02
        await send({'type': 'http.response.start', 'status': 200})
        clock[0] = 600
        await send({'type': 'http.response.body', 'body': b'video'})
    async def send(message):
        pass
    with caplog.at_level('INFO', logger=timing.logger.name):
        asyncio.run(timing.RequestTimingMiddleware(endpoint)(
            {'type': 'http', 'method': 'GET', 'path': '/api/videos/media'}, None, send))
    assert not caplog.records
