"""Compact receipt export from a consistent snapshot of awards, audits and points."""
from collections import defaultdict
from datetime import datetime
from math import ceil
from types import SimpleNamespace
import json

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter

from app.activities_models import ActivityAward, ActivityLot, ActivityPrize, ActivityDispatch, ActivityReceipt
from app.activity_points_models import ActivityPointsEntry
from app.v2_models import AuditLog, Employee
from app.excel_export_utils import display_width, excel_safe_value
from app.services.activities import now

HEADERS = ('凭证号', '姓名', '工号', '奖池/拍品', '奖品', '消耗积点', '领取状态', '发放时间', '发放人', '签收时间', '签收档案', '退款积点')
STATES = {'pending': '待开奖', 'ready': '待领取', 'claimed': '旧核销（无签名）', 'issued': '已发放，待签收', 'disputed': '签收异常', 'signed': '已签收', 'refunded': '已退款', 'expired': '逾期关闭'}
CLAIM_ACTION = '活动奖品核销'
AWARD_ACTIONS = {CLAIM_ACTION, '纠正活动核销状态', '活动缺货退款', '活动逾期关闭'}


def object_json(value):
    try:
        parsed = json.loads(value or '{}')
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def export_snapshot(db, event):
    records = db.query(ActivityAward, ActivityLot, Employee, ActivityPrize).join(
        ActivityLot, ActivityAward.lot_id == ActivityLot.id).join(Employee, ActivityAward.employee_id == Employee.id).outerjoin(
        ActivityPrize, ActivityAward.prize_id == ActivityPrize.id).filter(ActivityLot.activity_id == event.id).order_by(
        ActivityLot.id, ActivityAward.id).all()
    awards = {award.id: award for award, _, _, _ in records}
    latest = {}
    for log in db.query(AuditLog).filter(AuditLog.entity_type == 'activity', AuditLog.entity_id == str(event.id),
            AuditLog.action.in_(AWARD_ACTIONS)).order_by(AuditLog.id):
        award_id = object_json(log.after_json).get('award_id')
        if type(award_id) is int and award_id in awards:
            latest[award_id] = log
    movements = defaultdict(lambda: {'spent': 0, 'refund': 0, 'spend_count': 0, 'refund_count': 0, 'invalid_sign': False})
    if awards:
        people = {award.employee_id for award in awards.values()}
        for entry in db.query(ActivityPointsEntry).filter(ActivityPointsEntry.employee_id.in_(people),
                ActivityPointsEntry.origin == 'activity', ActivityPointsEntry.kind.in_(['activity_spend', 'activity_refund'])):
            detail = object_json(entry.detail_json)
            if detail.get('activity_id') != event.id:
                continue
            award_id = detail.get('award_id')
            if type(award_id) is not int:
                continue
            award = awards.get(award_id)
            if not award or entry.employee_id != award.employee_id or detail.get('lot_id') != award.lot_id:
                continue
            money = movements[award_id]
            spending = entry.kind == 'activity_spend'
            money['spent' if spending else 'refund'] += -entry.delta if spending else entry.delta
            money['spend_count' if spending else 'refund_count'] += 1
            money['invalid_sign'] |= entry.delta >= 0 if spending else entry.delta <= 0
    dispatches = {d.award_id: d for d in db.query(ActivityDispatch).filter(ActivityDispatch.award_id.in_(list(awards)))} if awards else {}
    from app.services.activity_receipts import archive_problem
    receipts = {}
    if awards:
        # Verify bounded batches, retaining metadata only instead of all signature blobs.
        for r in db.query(ActivityReceipt).filter(ActivityReceipt.award_id.in_(list(awards))).yield_per(25):
            receipts[r.award_id] = SimpleNamespace(id=r.id, employee_id=r.employee_id, signed_at=r.signed_at,
                snapshot_json=r.snapshot_json, problem=archive_problem(r, awards[r.award_id], event.id))
    rows = []
    for award, lot, employee, prize in records:
        money = movements[award.id]
        log = latest.get(award.id)
        has_claim = award.state == 'claimed' and log and log.action == CLAIM_ACTION
        notes = []
        dispatch, receipt = dispatches.get(award.id), receipts.get(award.id)
        if dispatch and object_json(dispatch.snapshot_json).get('recalled') and not receipt:
            dispatch = None
        signing_issue = (award.state in {'issued','disputed'} and not dispatch) or (award.state == 'signed' and not receipt)
        if dispatch and not object_json(dispatch.snapshot_json):
            signing_issue = True
        if receipt and (receipt.employee_id != award.employee_id or award.state not in {'signed','refunded'}):
            signing_issue = True
        if receipt and receipt.problem:
            signing_issue = True
        if signing_issue:
            notes.append('发放、签收档案与当前凭证不一致，请核对。')
        # Each participation has one debit and at most one full refund. Matching net
        # totals alone must not hide duplicate entries or entries with wrong signs.
        money_issue = (money['spent'] != award.cost or money['spend_count'] != 1 or money['invalid_sign'] or
            money['refund_count'] != (1 if award.state == 'refunded' else 0) or
            (money['refund'] != (money['spent'] if award.state == 'refunded' else 0)))
        if money_issue:
            notes.append('积点流水与奖品状态不一致，请按凭证号核对。')
        claim_issue = award.state == 'claimed' and (not has_claim or not log.created_at or not log.operator_name)
        if claim_issue:
            notes.append('未找到当前有效核销日志，未使用参与时间代替。')
        state = STATES.get(award.state, '待核对')
        if money_issue:
            state = '退款待核对' if money['refund_count'] or award.state == 'refunded' else '待核对'
        elif claim_issue:
            state = '核销待核对'
        elif signing_issue:
            state = '已退款/档案待核对' if award.state == 'refunded' else '签收待核对'
        elif award.state not in STATES:
            notes.append('奖品状态异常，请按凭证号核对。')
        frozen = object_json(receipt.snapshot_json) if receipt and not receipt.problem else {}
        issued_time = dispatch.issued_at if dispatch else ((log.created_at or '未记录') if has_claim else '')
        issued_name = dispatch.issued_name if dispatch else ((log.operator_name or '未记录') if has_claim else '')
        if receipt and not receipt.problem:
            # The immutable signed snapshot owns historical issuance details.
            issued_time = datetime.fromisoformat(frozen['issued_at'])
            issued_name = frozen['issued_name']
        if receipt and receipt.problem:
            issued_time = issued_name = '待核对'
        if award.state == 'claimed' and not has_claim:
            issued_time = issued_name = '未记录'
        rows.append({'values': [f'#{award.id}', frozen.get('employee_name', employee.name),
            str(frozen.get('employee_no', employee.employee_no)), frozen.get('lot',lot.name), frozen.get('prize',prize.name if prize else '未开奖'), money['spent'], state,
            issued_time, issued_name, ('待核对' if receipt.problem else receipt.signed_at) if receipt else '', receipt.id if receipt else '', money['refund']],
            'note': '\n'.join(notes), 'refund_reason': award.reason if award.state == 'refunded' else ''})
    return {'id': event.id, 'title': event.title, 'at': now(), 'rows': rows,
        'spent': sum(row['values'][5] for row in rows), 'refund': sum(row['values'][11] for row in rows),
        'claimed': sum(row['values'][6] == '已签收' for row in rows),
        'legacy_claimed': sum(row['values'][6] == '旧核销（无签名）' for row in rows),
        'issued': sum(row['values'][6] == '已发放，待签收' for row in rows),
        'disputed': sum(row['values'][6] == '签收异常' for row in rows),
        'refunded': sum(row['values'][6] in {'已退款','已退款/档案待核对'} for row in rows),
        'attention': sum(bool(row['note']) for row in rows)}



def safe(value):
    return excel_safe_value(ILLEGAL_CHARACTERS_RE.sub('', value)) if isinstance(value, str) else value


def build_workbook(data):
    """Use the application's existing openpyxl export stack; no runtime sidecar."""
    wb = Workbook()
    ws = wb.active
    ws.title = '领取核销'
    for row in range(1, 4):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(HEADERS))
    ws['A1'] = safe(data['title'])
    ws['A1'].font = Font(name='Microsoft YaHei', size=15, bold=True, color='FFFFFF')
    ws['A1'].fill = PatternFill('solid', fgColor='1F4E78')
    ws['A1'].alignment = Alignment(vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 38
    ws['A2'] = f"共{len(data['rows'])}份 · 已签收{data['claimed']} · 待签收{data['issued']} · 异常{data['disputed']} · 已退款{data['refunded']} · 消耗{data['spent']}点 · 退款{data['refund']}点"
    if data.get('legacy_claimed'):
        ws['A2'].value += f" · 旧核销{data['legacy_claimed']}"
    if data.get('attention'):
        ws['A2'].value += f" · 待核对{data['attention']}"
    ws['A3'] = f"导出：{data['at']:%Y-%m-%d %H:%M:%S}（北京时间） · 一份奖品一行"
    for row in (2, 3):
        ws.cell(row, 1).font = Font(name='Microsoft YaHei', size=10, color='526477')
        ws.row_dimensions[row].height = 20
    ws.append(list(HEADERS))
    widths = (9, 10, 12, 16, 20, 10, 16, 21, 12, 21, 22, 10)
    edge = Side(style='thin', color='DFE6EE')
    for index, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(index)].width = width
        cell = ws.cell(4, index)
        cell.font = Font(name='Microsoft YaHei', size=10, bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='315C83')
        cell.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[4].height = 26
    for index, record in enumerate(data['rows'], 5):
        ws.append([safe(value) for value in record['values']])
        for column, cell in enumerate(ws[index], 1):
            cell.font = Font(name='Microsoft YaHei', size=10)
            cell.alignment = Alignment(vertical='center', wrap_text=True, horizontal='center' if column in {1,3,6,7,8,10,12} else 'left')
            cell.border = Border(bottom=edge)
            if index % 2:
                cell.fill = PatternFill('solid', fgColor='F3F7FB')
        ws.cell(index, 3).number_format = '@'
        ws.cell(index, 10).number_format = 'yyyy-mm-dd hh:mm:ss'
        ws.cell(index, 8).number_format = 'yyyy-mm-dd hh:mm:ss'
        for column in (6, 12):
            ws.cell(index, column).number_format = '0'
        if record['refund_reason']:
            ws.cell(index, 12).comment = Comment(record['refund_reason'], '退款原因')
        if record['note']:
            ws.cell(index, 7).comment = Comment(record['note'], '核对提示')
            ws.cell(index, 7).font = Font(name='Microsoft YaHei', size=10, bold=True, color='9C3B10')
        lines = max(ceil(display_width(value.strftime('%Y-%m-%d %H:%M:%S') if isinstance(value, datetime) else value)/width)
                    for value, width in zip(record['values'], widths))
        ws.row_dimensions[index].height = max(26, lines*14)
    ws.freeze_panes = 'A5'
    ws.auto_filter.ref = f'A4:L{max(4, ws.max_row)}'
    ws.print_title_rows = '1:4'
    ws.print_options.horizontalCentered = True
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_area = f'A1:L{max(4, ws.max_row)}'
    return wb
