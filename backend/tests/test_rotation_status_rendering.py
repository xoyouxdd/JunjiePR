"""Exercise the formal board and screen rendering."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(not NODE, reason="Node is needed for the real JavaScript renderers")
def test_forecast_shows_unclaimed_meal_badge_on_posts_and_pool_cards():
    script = r"""
const fs=require('fs'),vm=require('vm'),files=JSON.parse(fs.readFileSync(0,'utf8'));
const ctx=vm.createContext({location:{pathname:'/rotation-board'},localStorage:{getItem:()=>null},
  document:{body:{classList:{contains:()=>false}},getElementById:()=>null,addEventListener:()=>{}},isManager:()=>true});
for(const file of files)vm.runInContext(fs.readFileSync(file,'utf8'),ctx);
vm.runInContext(`
  const p={pid:'1',name:'员工',state:'onpost',start:435,end:826,flags:[],tags:[],mealEligible:true,ate:false};
  const post={key:'A#0',name:'入口',open:true,occ:p.pid};
  const line={id:'A',active:true,posts:[post]};
  RT.data={day:{ruleVersion:2,persons:[p],lines:[line]}};
  const board=()=>forecastLineCard(line,600,true).includes('未休饭');
  const pool=()=>forecastPools(600).includes('未休饭');
  output=[board()];
  post.occ=null;p.state='rest';output.push(pool());
  p.ate=true;output.push(pool());
  p.ate=false;p.mealEligible=false;output.push(pool());
`,ctx);
process.stdout.write(JSON.stringify(ctx.output));
"""
    files = [str(ROOT / 'app/static/js' / f'rotation-{name}.js') for name in ('common', 'workflow', 'forecast')]
    result = subprocess.run([NODE, '-e', script], input=json.dumps(files), encoding='utf-8', capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [True, True, False, False]


@pytest.mark.skipif(not NODE, reason="Node is needed for the real JavaScript renderers")
def test_formal_arrival_log_uses_recorded_outcomes():
    script = r"""
const fs=require('fs'),vm=require('vm');
const file=JSON.parse(fs.readFileSync(0,'utf8'));
const ctx=vm.createContext({RT:{},esc:value=>String(value??''),localStorage:{getItem:()=>null},
  document:{getElementById:()=>({addEventListener:()=>{}})},rtApi:()=>new Promise(()=>{})});
vm.runInContext(fs.readFileSync(file,'utf8'),ctx);
vm.runInContext(`
  const log=(type,outcome)=>detailText({type,detail:{reason:'轮岗',outcome}});
  output=[log('pushed_off','done'),log('pushed_off','walkback'),log('pushed_out','away'),log('pushed_out')];
`,ctx);
process.stdout.write(JSON.stringify(ctx.output));
"""
    manager = str(ROOT / "app/static/js/rotation.js")
    result = subprocess.run([NODE, "-e", script], input=json.dumps(manager), encoding="utf-8", capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["轮岗，结束当日轮岗", "轮岗，回休息室确认", "轮岗，出圈", "轮岗"]


@pytest.mark.skipif(not NODE, reason="Node is needed for the real JavaScript renderer")
@pytest.mark.parametrize('state,expected',[
    ('future','10:00 加岗'),('matching','准备加岗'),('preparing','准备加岗'),
    ('occupied','worker'),('vacant','空岗'),('cancelled','未开岗'),
])
def test_opening_label_is_shared_by_board_and_screen(state,expected):
    script=r"""
const fs=require('fs'),vm=require('vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
let screen=false;
const ctx=vm.createContext({console,location:{pathname:'/rotation-board'},
  sessionStorage:{getItem:()=>null},document:{body:{classList:{contains:()=>screen}},getElementById:()=>null,addEventListener:()=>{}},input,isManager:()=>true});
for(const file of input.files)vm.runInContext(fs.readFileSync(file,'utf8'),ctx);
vm.runInContext(`
const post={name:'new-post',key:'new-key',open:!['future','cancelled'].includes(input.state),
  openAt:['future','matching','preparing'].includes(input.state)?'10:00':null,opened:input.state!=='future',occ:input.state==='occupied'?'worker':null,
  openingStatus:['matching','preparing'].includes(input.state)?input.state:null};
const line={id:'A1',active:true,posts:[post]};
const day={ruleVersion:2,persons:[{pid:'worker',name:'worker',state:'onpost',start:420,end:830,flags:[],tags:[]}],lines:[line]};
RT.data={day};
output=[forecastLineCard(line,599,true)];
`,ctx);
screen=true;
vm.runInContext('output.push(forecastLineCard(line,599,false));',ctx);
process.stdout.write(JSON.stringify(ctx.output));
"""
    files = [str(ROOT / 'app/static/js' / f'rotation-{name}.js') for name in ('common', 'workflow', 'forecast')]
    result = subprocess.run([NODE, '-e', script], input=json.dumps({'state': state, 'files': files}), encoding='utf-8', capture_output=True)
    assert result.returncode==0,result.stderr
    for markup in json.loads(result.stdout):
        assert expected in markup
        if state in ('matching','preparing'):assert '>空岗</span>' not in markup
