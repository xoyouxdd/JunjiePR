"""Isolated short-range concurrency smoke; never a production capacity guarantee."""
import concurrent.futures, json, math, os, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path
import httpx
import ctypes
from ctypes import wintypes
class Memory(ctypes.Structure):
    _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in ('peak','rss','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile')]
class FileTime(ctypes.Structure):
    _fields_=[('lo',wintypes.DWORD),('hi',wintypes.DWORD)]
def metrics(process):
    handle=wintypes.HANDLE(process)
    times=[FileTime() for _ in range(4)]
    if not ctypes.windll.kernel32.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]):raise ctypes.WinError()
    memory=Memory();memory.cb=ctypes.sizeof(memory)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(handle,ctypes.byref(memory),memory.cb):raise ctypes.WinError()
    cpu=sum((t.hi*2**32+t.lo)/1e7 for t in times[2:])
    return cpu,memory.rss
root=Path(__file__).resolve().parents[2]
out=root/'output'/'video-learning-20261022';out.mkdir(parents=True,exist_ok=True)
work=Path(tempfile.mkdtemp(prefix='fz-video-load-'))
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
base=f'http://127.0.0.1:{port}'
log=(out/'load-server.log').open('w',encoding='utf-8')
server=subprocess.Popen([sys.executable,str(root/'backend/tests/video_learning_fixture.py'),str(work),str(port)],cwd=root,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
clients=[];results=[];service_pid=None;service_handle=None
try:
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        try:
            if httpx.get(base+'/health',timeout=1).is_success:break
        except httpx.HTTPError:pass
        if server.poll() is not None:raise RuntimeError('fixture exited')
        time.sleep(.2)
    else:raise RuntimeError('fixture startup timed out')
    # A Windows venv executable may be a launcher with a child interpreter.
    child=subprocess.check_output(['powershell','-NoProfile','-Command',f"Get-CimInstance Win32_Process -Filter 'ParentProcessId={server.pid}' | Where-Object {{$_.Name -match '^python'}} | Select-Object -ExpandProperty ProcessId"],text=True).strip().splitlines()
    service_pid=int(child[0]) if child else server.pid
    ctypes.windll.kernel32.OpenProcess.restype=wintypes.HANDLE
    service_handle=ctypes.windll.kernel32.OpenProcess(0x410,False,service_pid)
    if not service_handle:raise ctypes.WinError()
    for index in range(50):
        client=httpx.Client(base_url=base,timeout=45);clients.append(client)
        assert client.post('/api/login',json={'employee_no':['SUPTEST01','TRTEST01','GSMTEST01'][index%3],'password':'fixture2026'}).is_success
        video=client.get('/api/video-learning/videos').json()['items'][0]
        notice=client.get('/api/video-learning/options').json()
        grant=client.post(f"/api/video-learning/videos/{video['id']}/consent",json={'accepted':True,'notice_version':notice['notice_version']}).json()
        client._fz_video=video['id'];client._fz_token=grant['token']
    for count in (10,30,50):
        barrier=threading.Barrier(count);latencies=[];health=[];business=[];rss=[];stop=threading.Event()
        def monitor():
            with httpx.Client(base_url=base,timeout=10,cookies=clients[0].cookies) as c:
                while not stop.is_set():
                    t=time.perf_counter();r=c.get('/health');health.append((time.perf_counter()-t,r.status_code));rss.append(metrics(service_handle)[1]);t=time.perf_counter();r=c.get('/api/options');business.append((time.perf_counter()-t,r.status_code));stop.wait(.05)
        thread=threading.Thread(target=monitor);thread.start()
        cpu=metrics(service_handle)[0];began=time.perf_counter()
        def watch(client):
            barrier.wait(timeout=30);t=time.perf_counter();path=f'/api/video-learning/videos/{client._fz_video}';params={'token':client._fz_token,'quality':'720'}
            prepared=client.post(path+'/playback',params=params);assert prepared.is_success and prepared.json()['status']=='ready'
            assert client.get(path+'/authorize',params=params).is_success
            response=client.get(path+'/stream',params=params,headers={'Range':'bytes=0-65535'});assert response.status_code==206 and len(response.content)==65536
            return time.perf_counter()-t
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:latencies=list(pool.map(watch,clients[:count]))
        finally:stop.set();thread.join()
        after=metrics(service_handle)[0];elapsed=time.perf_counter()-began
        row={'readers':count,'health_samples':len(health),'registration_options_samples':len(business),'seconds':round(elapsed,3),'watch_request_p95_ms':round(sorted(latencies)[max(0,math.ceil(len(latencies)*.95)-1)]*1000,1),
             'health_p95_ms':round(sorted(v[0] for v in health)[max(0,math.ceil(len(health)*.95)-1)]*1000,1),'health_statuses':sorted(set(v[1] for v in health)),
             'registration_options_p95_ms':round(sorted(v[0] for v in business)[max(0,math.ceil(len(business)*.95)-1)]*1000,1),'registration_options_statuses':sorted(set(v[1] for v in business)),
             'server_cpu_seconds':round(after-cpu,3),'peak_server_rss_mb':round(max(rss)/1024**2,1),'video_bytes':65536*count}
        results.append(row);print(json.dumps(row),flush=True)
    report={'scope':'localhost synthetic 4-second clips, prepare + authorize + 64KiB Range, concurrent registration options reads; not sustained playback or real registration writes','results':results}
    (out/'shared-load.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
finally:
    for client in clients:client.close()
    if service_pid and service_pid!=server.pid:
        subprocess.run(['taskkill','/PID',str(service_pid),'/T','/F'],capture_output=True)
    if service_handle:ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(service_handle))
    server.terminate()
    try:server.wait(timeout=8)
    except subprocess.TimeoutExpired:server.kill();server.wait()
    log.close()
