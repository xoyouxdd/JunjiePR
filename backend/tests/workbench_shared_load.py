"""Manual isolated mixed-load smoke, never production or sustained-capacity proof."""
import concurrent.futures, json, socket, subprocess, sys, tempfile, time
from datetime import date
from pathlib import Path
import httpx

root=Path(__file__).resolve().parents[2]
out=root/'.handover_review/task-workbench-20261024';out.mkdir(parents=True,exist_ok=True)
work=Path(tempfile.mkdtemp(prefix='fz-workbench-load-'))
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
base=f'http://127.0.0.1:{port}'
clients=[]
log=(out/'mixed-load-server.log').open('w',encoding='utf-8')
server=subprocess.Popen([sys.executable,str(root/'backend/tests/workbench_load_fixture.py'),str(work),str(port)],cwd=root,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
try:
    until=time.monotonic()+90
    while time.monotonic()<until:
        try:
            if httpx.get(base+'/health',timeout=1).is_success:break
        except httpx.HTTPError:pass
        if server.poll() is not None:raise RuntimeError('fixture stopped; inspect mixed-load-server.log')
        time.sleep(.2)
    else:raise RuntimeError('fixture startup timed out')
    for i in range(15):
        c=httpx.Client(base_url=base,timeout=60);clients.append(c)
        assert c.post('/api/login',json={'employee_no':str(9900100+i),'password':'fixture2026'}).is_success
        video=c.get('/api/video-learning/videos').json()['items'][0]
        notice=c.get('/api/video-learning/options').json()
        grant=c.post(f"/api/video-learning/videos/{video['id']}/consent",json={'accepted':True,'notice_version':notice['notice_version']}).json()
        c._video=video['id'];c._token=grant['token']
    manager=httpx.Client(base_url=base,timeout=60);clients.append(manager)
    assert manager.post('/api/login',json={'employee_no':'GSMTEST01','password':'fixture2026'}).is_success
    month=date.today().strftime('%Y-%m')
    preview=manager.get('/api/hr-monthly-reports/preview',params={'month':month});assert preview.is_success,preview.text
    config={'month':month,'template':'minimal','sections':['overview','groups','rankings'],'preview_digest':preview.json()['digest']}
    began=time.perf_counter()
    report=manager.post('/api/export-tasks',params={'target':'/api/hr-monthly-reports/export'},files={'config':(None,json.dumps(config))});assert report.status_code==202,report.text
    key=report.json()['id']
    def watch(c):
        start=time.perf_counter();path=f'/api/video-learning/videos/{c._video}'
        params={'token':c._token,'quality':'720'}
        prepared=c.post(path+'/playback',params=params);assert prepared.is_success,prepared.text
        assert c.get(path+'/authorize',params=params).is_success
        r=c.get(path+'/stream',params=params,headers={'Range':'bytes=0-65535'});assert r.status_code==206 and len(r.content)==65536
        statuses={p:c.get(p).status_code for p in ['/health','/api/action-center','/api/rotation-board/me']}
        assert set(statuses.values())=={200},statuses
        return {'elapsed_ms':round((time.perf_counter()-start)*1000,1),'video_bytes':len(r.content),'reads':statuses}
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as pool:
        result=list(pool.map(watch,clients[:15]))
    for _ in range(300):
        job=next(j for j in manager.get('/api/export-tasks').json()['items'] if j['id']==key)
        if job['status'] in {'completed','failed'}:break
        time.sleep(.1)
    assert job['status']=='completed',job
    ppt=manager.get(job['download_url']);assert ppt.is_success and ppt.content.startswith(b'PK')
    evidence={'scope':'isolated localhost, 15 distinct fictional TR accounts, 4-second synthetic clips with 64KiB Range requests plus actual PPTX export and personal rotation/todo reads; not sustained playback, live board writes or production capacity',
              'readers':15,'results':result,'ppt_bytes':len(ppt.content),'export_seconds':job['elapsed_seconds'],'total_seconds':round(time.perf_counter()-began,3)}
    (out/'mixed-load.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'passed','readers':15,'ppt_bytes':len(ppt.content),'seconds':evidence['total_seconds']}),flush=True)
finally:
    for c in clients:c.close()
    # A Windows venv launcher can own a child interpreter; terminate this
    # explicitly created test-only process tree, never a port's arbitrary PID.
    if server.poll() is None:
        if sys.platform=='win32':subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],capture_output=True)
        else:server.terminate()
        try:server.wait(timeout=8)
        except subprocess.TimeoutExpired:server.kill();server.wait()
    log.close()
