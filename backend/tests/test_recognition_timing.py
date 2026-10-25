"""Measure streaming reads and failures without retaining sensitive request data."""
import asyncio
import json
import logging

import pytest

from app.recognition_timing import RecognitionTimingMiddleware, identify, stage, _current


def test_streaming_timing_is_correlated_and_does_not_log_input(caplog):
    async def exercise():
        messages=iter([{'type':'http.request','body':b'private-image','more_body':True},
                       {'type':'http.request','body':b'secret-content','more_body':False}])
        sent=[]
        async def receive():
            await asyncio.sleep(.001)
            return next(messages)
        async def send(message): sent.append(message)
        async def app(scope,receive,send):
            await receive();await receive();identify(532)
            with stage('commit_ms'): await asyncio.sleep(.001)
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'{}'})
        scope={'type':'http','method':'POST','path':'/api/recognitions/batch',
               'query_string':b'token=secret-query','headers':[(b'authorization',b'secret-auth')]}
        await RecognitionTimingMiddleware(app)(scope,receive,send)
        assert _current.get() is None
        return sent
    with caplog.at_level(logging.INFO,logger='uvicorn.error.recognition_timing'):
        sent=asyncio.run(exercise())
    records=[r.message for r in caplog.records if r.message.startswith('recognition_request_timing ')]
    assert len(records)==1
    trace=json.loads(records[0].split(' ',1)[1])
    assert trace['received_bytes']==27 and trace['operator_id']==532
    assert trace['status']==200 and trace['receive_wait_ms']>0
    assert trace['total_ms']>=trace['body_complete_ms']>0
    assert trace['stages_ms']['commit_ms']>0
    assert dict(sent[0]['headers'])[b'x-recognition-request-id'].decode()==trace['request_id']
    assert not any(secret in records[0] for secret in ('private-image','secret-content','secret-query','secret-auth'))


def test_failed_request_preserves_exception_and_clears_context(caplog):
    async def exercise():
        async def app(*args): raise ValueError('private-exception-content')
        with pytest.raises(ValueError,match='private-exception-content'):
            await RecognitionTimingMiddleware(app)({'type':'http','method':'POST','path':'/api/recognitions'},None,None)
        assert _current.get() is None
    with caplog.at_level(logging.INFO,logger='uvicorn.error.recognition_timing'):
        asyncio.run(exercise())
    trace=json.loads(caplog.records[-1].message.split(' ',1)[1])
    assert trace['error_type']=='ValueError' and trace['status'] is None
    assert 'private-exception-content' not in caplog.text


def test_unrelated_requests_are_not_instrumented(caplog):
    async def app(*args): assert _current.get() is None
    asyncio.run(RecognitionTimingMiddleware(app)({'type':'http','method':'GET','path':'/health'},None,None))
    assert not caplog.records


def test_concurrent_requests_do_not_share_identity_or_timing(caplog):
    async def exercise():
        async def app(scope,receive,send):
            identify(scope['operator'])
            with stage('record_ms'): await asyncio.sleep(.001)
            assert _current.get()['operator_id']==scope['operator']
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b''})
        async def send(message): pass
        middleware=RecognitionTimingMiddleware(app)
        await asyncio.gather(*(middleware({'type':'http','method':'POST','path':'/api/recognitions',
                                          'operator':op},None,send) for op in (10,20)))
        assert _current.get() is None
    with caplog.at_level(logging.INFO,logger='uvicorn.error.recognition_timing'):
        asyncio.run(exercise())
    traces=[json.loads(r.message.split(' ',1)[1]) for r in caplog.records]
    assert {t['operator_id'] for t in traces}=={10,20}
    assert len({t['request_id'] for t in traces})==2
