"""Covered sick leave is only hidden visually, never removed from API results."""
from pathlib import Path
import shutil
import subprocess

import pytest


APP_JS = Path(__file__).parents[1] / "app" / "static" / "js" / "app.js"
STYLE_CSS = Path(__file__).parents[1] / "app" / "static" / "css" / "style.css"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_covered_sick_group_only_collects_covered_sick_leave_and_is_closed_by_default():
    source = APP_JS.read_text(encoding="utf-8")
    functions = source[source.index("function splitCoveredSickRecords(rows){"):source.index("const sameDayDuplicateBadge")]
    checks = """
const rows = [
  {record_type:'sick_leave', status:'active'},
  {record_type:'sick_leave', status:'covered'},
  {record_type:'deduction', status:'covered'},
  {record_type:'sick_leave', status:'void'},
  {record_type:'sick_leave', status:'covered'},
];
const {visible,covered} = splitCoveredSickRecords(rows);
if (visible.length !== 3 || covered.length !== 2 || covered[0] !== rows[1] || covered[1] !== rows[4]) process.exit(1);
const html = coveredSickDisclosure(covered, '<article>history</article>');
if (!html.includes('<details class="covered-sick-disclosure">') || html.includes(' open') || !html.includes('已覆盖病假（2 条）') || !html.includes('<article>history</article>')) process.exit(2);
if (coveredSickDisclosure([], 'unused') !== '') process.exit(3);
"""
    subprocess.run([shutil.which("node"), "-e", functions + checks], check=True, capture_output=True, text=True)


def test_all_sick_leave_list_surfaces_use_the_same_disclosure():
    source = APP_JS.read_text(encoding="utf-8")
    css = STYLE_CSS.read_text(encoding="utf-8")
    assert "coveredSickDisclosure(covered,importTable(covered))" in source
    assert "coveredSickDisclosure(covered,coveredContent)" in source
    assert "coveredSickDisclosure(covered,`<div class=\"member-detail-list\">" in source
    assert ".covered-sick-disclosure[open] > summary span::before { content: '收起'; }" in css
