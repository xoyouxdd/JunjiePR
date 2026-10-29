"""Exercise the shared board/screen card, badges and personnel modal renderers."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(not NODE, reason="Node is needed for the real JavaScript renderer")
@pytest.mark.parametrize('state,expected',[
    ('future','待加 10:00'),('matching','匹配中'),('preparing','准备加岗'),
    ('occupied','worker'),('vacant','空岗'),('cancelled','未开'),
])
def test_opening_label_is_shared_by_board_and_screen(state,expected):
    script=r"""
const fs=require('fs'),vm=require('vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const ctx=vm.createContext({console,location:{pathname:'/rotation-board'},
  sessionStorage:{getItem:()=>null},document:{getElementById:()=>null,addEventListener:()=>{}},input});
vm.runInContext(fs.readFileSync(input.common,'utf8'),ctx);
vm.runInContext(`
const post={name:'new-post',key:'new-key',open:!['future','cancelled'].includes(input.state),
  openAt:input.state==='vacant'?null:'10:00',opened:input.state!=='future',occ:input.state==='occupied'?'worker':null,
  openingStatus:['matching','preparing'].includes(input.state)?input.state:null};
const day={taskWorkflow:1,persons:[{pid:'worker',name:'worker',state:'onpost',flags:[],tags:[]}],lines:[]};
RT.data={day};
const line={id:'A1',active:true,posts:[post]};
output=[lineCard(line,600,true),lineCard(line,600,false)];
if(input.state==='future')output=[lineCard(line,599,true),lineCard(line,599,false)];
`,ctx);
process.stdout.write(JSON.stringify(ctx.output));
"""
    result=subprocess.run([NODE,'-e',script],input=json.dumps({'state':state,'common':str(ROOT/'app/static/js/rotation-common.js')}),encoding='utf-8',capture_output=True)
    assert result.returncode==0,result.stderr
    for markup in json.loads(result.stdout):
        assert f'>{expected}</span>' in markup
        if state in ('matching','preparing'):assert '>空岗</span>' not in markup


@pytest.mark.skipif(not NODE, reason="Node is needed for the real JavaScript renderers")
@pytest.mark.parametrize("kind,expected", [("推7点", "准备休息"), ("推下班", "准备下班"), ("推出圈", "准备出圈"), (None, None)])
def test_pending_replacement_has_consistent_status_in_every_view(kind, expected):
    script = r"""
const fs=require('fs'), vm=require('vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const context=vm.createContext({location:{pathname:'/rotation'},localStorage:{getItem:()=>null},
  document:{getElementById:()=>({addEventListener:()=>{}}),addEventListener:()=>{}},captured:'',input});
vm.runInContext(fs.readFileSync(input.common,'utf8'),context);
const manager=fs.readFileSync(input.manager,'utf8');
vm.runInContext(manager.slice(0,manager.lastIndexOf('(async () => {')),context);
vm.runInContext(`
  const p={pid:'early',name:'早班员工',state:'onpost',role:'rotation',start:420,end:810,
    line:'B2',post:'小C',lineStart:420,preparing:input.kind,flags:['7点岗'],tags:[],
    visited:['B2'],absences:[],mealEligible:false,ate:false};
  const line={id:'B2',active:true,group:'B',posts:[{name:'小C',open:true,occ:'early'}]};
  RT.data={day:{persons:[p],lines:[line]}};
  openModal=html=>captured=html;
  personMenu(p);
  output={card:lineCard(line,436,true),screen:lineCard(line,436,false),badge:badges(p),modal:captured};
`,context);
process.stdout.write(JSON.stringify(context.output));
"""
    result = subprocess.run([NODE, "-e", script], input=json.dumps({"kind": kind,
        "common": str(ROOT / "app/static/js/rotation-common.js"),
        "manager": str(ROOT / "app/static/js/rotation.js")}), encoding="utf-8", capture_output=True)
    assert result.returncode == 0, result.stderr
    views = json.loads(result.stdout)
    for name, html in views.items():
        if expected:
            assert expected in html, name
        else:
            assert "准备" not in html, name
        if kind == "推7点":
            assert "准备下班" not in html and "准备推7点" not in html, name
    for name in ("card", "screen"):
        assert ">7点</span>" in views[name]
        assert ">16 分</span>" in views[name]


@pytest.mark.skipif(not NODE, reason="Node is needed for the real JavaScript renderers")
@pytest.mark.parametrize("minute,early,state,ready_at,assigned,expected,can,opening", [
    (485.45, 1, "ready", 480, True, "rest", False, False),  # Screenshot: 08:05:27, depart 08:15
    (493.98, 1, "ready", 480, True, "rest", False, False),
    (494, 1, "ready", 480, True, "ready", True, False),
    (494, 0, "ready", 480, True, "rest", False, False),
    (495, 0, "ready", 480, True, "ready", True, False),
    (494, 1, "ready", 500, True, "rest", False, False),  # Rest cannot be shortened
    (494, 1, "rest", 495, True, "rest", False, False),
    (485, 1, "ready", 480, False, "ready", False, False),  # No destination yet
    (487, 1, "ready", 480, True, "rest", False, True),  # Manual opening cannot happen early
    (488, 1, "ready", 480, True, "ready", True, True),
])
def test_future_departures_stay_in_rest_until_allowed_window(minute, early, state, ready_at, assigned, expected, can, opening):
    script = r"""
const fs=require('fs'), vm=require('vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
function render(file, screen) {
  const context=vm.createContext({location:{pathname:'/rotation'},localStorage:{getItem:()=>null},
    document:{getElementById:()=>({addEventListener:()=>{}}),addEventListener:()=>{}},captured:'',input});
  vm.runInContext(fs.readFileSync(input.common,'utf8'),context);
  const source=fs.readFileSync(file,'utf8');
  vm.runInContext(source.slice(0,source.lastIndexOf('(async () => {')),context);
  vm.runInContext(`
    const p={pid:'waiter',name:'定时开岗员工',state:input.state,role:'rotation',start:435,end:1080,
      readyAt:input.ready_at,breakKind:'rest',flags:[],tags:[],visited:[],absences:[],
      assign:input.assigned?{line:'C2',mode:input.opening?'opening':'push',departAt:input.opening?488:495,notBefore:input.opening?488:0}:null};
    RT.data={clock:{minute:input.minute,paused:true},settings:{departEarly:input.early},
      day:{status:'live',persons:[p],lines:[],alerts:[],notices:[]},person:p,notices:[]};
    openModal=html=>captured=html;
    output={state:poolState(p,input.minute),can:canDepartNow(p,input.minute)};
  `, context);
  if (screen) vm.runInContext('output.pool=screenView();',context);
  else vm.runInContext('output.pool=poolView(input.minute); personMenu(p); output.modal=captured; output.member=memberView();',context);
  return context.output;
}
process.stdout.write(JSON.stringify({manager:render(input.manager,false),screen:render(input.screen,true)}));
"""
    payload = dict(minute=minute, early=early, state=state, ready_at=ready_at, assigned=assigned, opening=opening,
        common=str(ROOT / "app/static/js/rotation-common.js"),
        manager=str(ROOT / "app/static/js/rotation.js"), screen=str(ROOT / "app/static/js/rotation-screen.js"))
    result = subprocess.run([NODE, "-e", script], input=json.dumps(payload), encoding="utf-8", capture_output=True)
    assert result.returncode == 0, result.stderr
    views = json.loads(result.stdout)
    import re
    for name, view in views.items():
        assert view["state"] == expected and view["can"] is can, name
        forecasts=re.findall(r'<details class="rt-departure-preview".*?</details>',view['pool'],re.S)
        for forecast in forecasts:
            assert '仅预告' in forecast and 'data-act=' not in forecast
        primary=re.sub(r'<details class="rt-departure-preview".*?</details>','',view['pool'],flags=re.S)
        sections = re.findall(r"<section[^>]*>(.*?)</section>", primary, re.S)
        section = next(s for s in sections if "定时开岗员工" in s)
        assert ("<h2>休息区" if expected == "rest" else "<h2>待出发") in section
        if expected == "rest":
            assert "data-act=\"depart\"" not in section
            assert ("08:08" if opening else "08:20" if ready_at == 500 else "08:15") in section
        if minute == 485.45:
            assert "10 分钟" in section
    modal = views["manager"]["modal"]
    assert ('data-a="depart"' in modal) is can
    if expected == "rest":
        assert "休息至" in modal and "休息至" in views["manager"]["member"]
