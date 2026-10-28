"""Bounded slow-API diagnostics without request fields or identifiers."""
from datetime import datetime, timezone
import json
import logging
from time import perf_counter


logger = logging.getLogger('uvicorn.error.request_timing')


class RequestTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope.get('path', '').startswith('/api/'):
            return await self.app(scope, receive, send)
        started = perf_counter()
        status, headers_ms, error = None, None, None

        async def measured_send(message):
            nonlocal status, headers_ms
            if message['type'] == 'http.response.start':
                status = message['status']
                headers_ms = (perf_counter() - started) * 1000
            await send(message)

        try:
            await self.app(scope, receive, measured_send)
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            # A streamed video's transfer duration is not API processing time.
            # Log slow headers, server errors and exceptions only; no per-poll log.
            if error or (status is not None and status >= 500) or (headers_ms is not None and headers_ms >= 1000):
                route = getattr(scope.get('route'), 'path', None)
                logger.info('slow_api_request %s', json.dumps({
                    'at_utc': datetime.now(timezone.utc).isoformat(),
                    'method': scope.get('method'), 'route': route or '<unmatched>',
                    'status': status, 'response_headers_ms': round(headers_ms, 2) if headers_ms is not None else None,
                    'total_ms': round((perf_counter() - started) * 1000, 2), 'error_type': error,
                }, separators=(',', ':')))
