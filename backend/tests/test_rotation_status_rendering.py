"""Exercise the shared board/screen card, badges and personnel modal renderers."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
ROOT = Path(__file__).parents[1]


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
