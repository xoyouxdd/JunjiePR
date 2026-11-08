"""Association cards share monthly LOA policy without changing stored scores."""
from decimal import Decimal

import pytest

from test_loa_score_consistency import db, add_loa
from app.services.declaration_statistics import declaration_payload
from app.routers.declaration_statistics import build_declaration_workbook
from app.score_queries import employee_month_scores
from app.v2_models import DeductionLevel, DeductionRecord, DeductionUpgradeRequest


@pytest.mark.parametrize('result_code,result_points', [('MEMO',3),('WARNING_1',5)])
@pytest.mark.parametrize('start,end,status,excluded', [
    ('2026-08-31','2026-08-31','ended',(True,False)),
    ('2026-09-01','2026-09-01','ended',(False,True)),
    ('2026-08-31','2026-09-01','ended',(True,True)),
    ('2026-08-01',None,'active',(True,True)),
    ('2026-08-31','2026-09-01','cancelled',(False,False)),
])
def test_cross_month_card_loa_keeps_originals_exports_and_score_policy(
    db,result_code,result_points,start,end,status,excluded,
):
    db.add(DeductionLevel(id=2,code=result_code,name='备忘录' if result_code=='MEMO' else '一级警告',points=result_points))
    def record(month,role,points,level_id=1):
        row=DeductionRecord(employee_id=1,employee_no='1000001',employee_name='测试CM',
            employee_role_snapshot='CM',attraction_id_snapshot=1,deduction_type_id=1,deduction_type_name='安全',
            deduction_level_id=level_id,deduction_level_name='声明' if level_id==1 else result_code,
            points=Decimal(points),occurred_on=month+'-15',deduction_month=month,description='LOA跨月声明',
            document_file_id=1,submitter_id=2,submitter_name='测试GSM',submitter_role_snapshot='GSM',
            permission_scope_snapshot='test',status='active',material_status='ready',upgrade_role=role)
        db.add(row);db.flush();return row
    first=record('2026-08','source_first',1)
    second=record('2026-09','source_second',7)  # legacy stored value must remain visible but never score
    result=record('2026-09','result',result_points,2)
    work=DeductionUpgradeRequest(employee_id=1,deduction_type_id=1,first_deduction_id=first.id,
        second_deduction_id=second.id,result_deduction_id=result.id,result_level_id=2,
        reviewer_id=2,reviewer_name='测试GSM',submitted_by=2,submitted_by_name='测试GSM',status='approved')
    db.add(work);db.commit()
    ids=[first.id,second.id,result.id]
    original={r.id:(r.points,r.status,r.upgrade_role,r.document_file_id) for r in (first,second,result)}
    before={month:declaration_payload(db,month,None,None,None,'','') for month in ('2026-08','2026-09')}
    exports_before={month:list(build_declaration_workbook(data).worksheets[1].values) for month,data in before.items()}
    add_loa(db,start,end,status)
    score_before={month:employee_month_scores(db,month,[1])[0] for month in before}
    # Enter from either month; filtered views must still resolve all three cards.
    for month in before:
        for level in (None,1,2):
            data=declaration_payload(db,month,None,level,None,'','1000001')
            selected=[r for r in data['detail_records']]
            if not selected:continue
            linked={r['id']:r for r in data['related_records']}
            assert set(linked)==set(ids)
            for rid,expected_loa,points,effective in (
                (first.id,excluded[0],1,0 if excluded[0] else 1),
                (second.id,excluded[1],7,0),
                (result.id,excluded[1],result_points,0 if excluded[1] else result_points),
            ):
                row=linked[rid]
                assert row['registered_points']==points
                assert row['actual_points']==effective
                assert row['loa_excluded'] is expected_loa
                assert ('LOA当月不计分' in row['score_status']) is expected_loa
                assert row['deduction_month']==('2026-08' if rid==first.id else '2026-09')
                assert {r['id'] for r in row['upgrade_links'][0]['records']}==set(ids)
            assert linked[second.id]['counted_in_statistics'] is False
        data=declaration_payload(db,month,None,None,None,'','')
        for key in ('total_records','employee_count','levels','statuses','circles'):
            assert data[key]==before[month][key]
        assert list(build_declaration_workbook(data).worksheets[1].values)==exports_before[month]
        assert employee_month_scores(db,month,[1])[0]==score_before[month]
        assert score_before[month]['deduction_score']==(0 if excluded[0 if month=='2026-08' else 1] else 1 if month=='2026-08' else result_points)
    assert {r.id:(r.points,r.status,r.upgrade_role,r.document_file_id) for r in (first,second,result)}==original
