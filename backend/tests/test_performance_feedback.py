from pathlib import Path
import json
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'app/static/js/app.js'


def test_feedback_uses_server_status_and_neutral_deduction_copy():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    source = SCRIPT.read_text(encoding='utf-8')
    helper = source.split('function performanceFeedbackData', 1)[1].split('function showPerformanceRegistrationFeedback', 1)[0]
    cases = [
        ['recognition', {'status': 'confirmed', 'fraction': 1, 'credited_fraction': .5}],
        ['recognition', {'status': 'pending', 'fraction': 1}],
        ['deduction', {'status': 'active', 'points': 2}],
        ['deduction', {'status': 'pending_material', 'material_status': 'missing'}],
        ['deduction', {'status': 'material_processing', 'material_status': 'processing'}],
        ['upgrade', {'status': 'pending_upgrade', 'points': 0}],
    ]
    code = 'function performanceFeedbackData' + helper + '\nconsole.log(JSON.stringify(' + json.dumps(cases) + '.map(x=>performanceFeedbackData(...x))))'
    result = subprocess.run([node, '-e', code], check=True, capture_output=True, text=True, encoding='utf-8')
    rows = json.loads(result.stdout)
    assert rows[0]['score'] == '+0.50 分'
    assert rows[0]['status'] == '已生效'
    assert rows[1]['status'] == '待审核'
    assert rows[2]['title'] == '登记成功，感谢你的认真记录。'
    assert rows[3]['status'] == '待补材料'
    assert rows[4]['status'] == '材料处理中'
    assert rows[5]['score'] == '审核后确定'
    assert all(not rows[i]['active'] for i in (1, 3, 4, 5))


def test_feedback_is_scoped_and_reuses_accessible_dialog():
    source = SCRIPT.read_text(encoding='utf-8')
    assert "showPerformanceRegistrationFeedback('recognition',result)" in source
    assert "showPerformanceRegistrationFeedback('deduction',out)" in source
    assert "showPerformanceRegistrationFeedback('upgrade',out)" in source
    helper = source.split('function showPerformanceRegistrationFeedback', 1)[1].split('\nfunction ', 1)[0]
    assert "state.me?.role_code" in helper
    assert "result?.duplicate" in helper
    assert 'bindDialogLayer' in helper
    assert 'aria-modal="true"' in helper
    assert 'aria-label="关闭反馈"' in helper
    style = (ROOT / 'app/static/css/style.css').read_text(encoding='utf-8')
    assert '.modal-card.performance-feedback' in style
    assert '.performance-feedback-continue { width: 100%; min-height: 44px; }' in style
