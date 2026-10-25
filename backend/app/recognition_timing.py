"""Request-local recognition diagnostics; never log submitted fields or credentials."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from time import perf_counter
from uuid import uuid4


logger = logging.getLogger('uvicorn.error.recognition_timing')
_current = ContextVar('recognition_timing', default=None)


@contextmanager
def stage(name):
    trace = _current.get()
    started = perf_counter()
    try:
        yield
    finally:
        if trace is not None:
            trace['stages_ms'][name] = trace['stages_ms'].get(name, 0) + (perf_counter()-started)*1000


def identify(employee_id):
    trace = _current.get()
    if trace is not None:
        trace['operator_id'] = int(employee_id)


class RecognitionTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or scope.get('path') not in (
            '/api/recognitions', '/api/recognitions/batch',
        ):
            return await self.app(scope, receive, send)
        started = perf_counter()
        trace = {'request_id': uuid4().hex, 'started_at_utc': datetime.now(timezone.utc).isoformat(),
                 'path': scope['path'], 'status': None,
                 'received_bytes': 0, 'receive_wait_ms': 0, 'body_complete_ms': None,
                 'response_start_ms': None, 'stages_ms': {}}
        token = _current.set(trace)

        async def measured_receive():
            at = perf_counter()
            message = await receive()
            trace['receive_wait_ms'] += (perf_counter()-at)*1000
            if message['type'] == 'http.request':
                trace['received_bytes'] += len(message.get('body', b''))
                if not message.get('more_body', False):
                    trace['body_complete_ms'] = (perf_counter()-started)*1000
            elif message['type'] == 'http.disconnect':
                trace['disconnected'] = True
            return message

        async def measured_send(message):
            if message['type'] == 'http.response.start':
                trace['status'] = message['status']
                trace['response_start_ms'] = (perf_counter()-started)*1000
                message = {**message, 'headers': [*message.get('headers', []),
                           (b'x-recognition-request-id', trace['request_id'].encode('ascii'))]}
            await send(message)

        try:
            await self.app(scope, measured_receive, measured_send)
        except BaseException as exc:
            trace['error_type'] = type(exc).__name__
            raise
        finally:
            trace['total_ms'] = (perf_counter()-started)*1000
            if trace['body_complete_ms'] is not None and trace['response_start_ms'] is not None:
                trace['after_body_ms'] = max(0, trace['response_start_ms']-trace['body_complete_ms'])
            trace['stages_ms'] = {k: round(v, 2) for k, v in trace['stages_ms'].items()}
            for key, value in trace.items():
                if isinstance(value, float):
                    trace[key] = round(value, 2)
            _current.reset(token)
            logger.info('recognition_request_timing %s', json.dumps(trace, separators=(',', ':')))
