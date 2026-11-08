"""Versioned score configuration. Existing permissions and legacy defaults remain authoritative."""
from datetime import date
from decimal import Decimal
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import func
from app.v2_models import ScoringPolicy, RecognitionRecord, RecognitionType, DeductionType, DeductionLevel, RecognitionScoreRule
from app.recognition_policy import SPECIAL_RECOGNITION_TYPES, MONTHLY_CATEGORY_CAP_CODES, MONTHLY_CATEGORY_CAP_EFFECTIVE_DATE


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def resolve(db, kind, scope, code, on_date):
    from app.date_utils import parse_iso_date
    if parse_iso_date(on_date).isoformat() != on_date:
        raise HTTPException(400, '日期格式必须为YYYY-MM-DD，不能绕过月份规则')
    row = db.query(ScoringPolicy).filter_by(kind=kind, scope=scope, code=code).filter(
        ScoringPolicy.effective_date <= on_date).order_by(ScoringPolicy.effective_date.desc()).first()
    return json.loads(row.policy_json) if row else None


def category(db, kind, scope, code, on_date):
    from app.deduction_policy import REPEAT_CONTROLLED_DEDUCTION_CODES, DIRECT_HIDDEN_DEDUCTION_CODES, upgrade_type_codes
    if kind == 'recognition':
        special = SPECIAL_RECOGNITION_TYPES.get(code)
        base = dict(mode='fixed' if special else 'role', score=float(special['score']) if special else 0,
                    count=special['monthly_limit'] if special else None, overflow='block',
                    cap=5 if code in MONTHLY_CATEGORY_CAP_CODES and on_date >= MONTHLY_CATEGORY_CAP_EFFECTIVE_DATE.isoformat() else None,
                    active=not code.startswith('CUSTOM_'), configured=False)
        scope = 'supervisor' if scope == 'supervisor' else 'frontline'
    else:
        base = dict(mode='level', score=1, repeat='upgrade' if code in REPEAT_CONTROLLED_DEDUCTION_CODES else 'sum',
                    group=sorted(upgrade_type_codes(code)), direct=code not in DIRECT_HIDDEN_DEDUCTION_CODES,
                    manager=True, active=not code.startswith('CUSTOM_'), configured=False)
        scope = 'all'
    value = resolve(db, kind, scope, code, on_date)
    if value:
        base.update(value, configured=True)
    return base


def role_score(db, code, on_date, recipient_base_code):
    value = resolve(db, 'role', 'supervisor' if recipient_base_code == 'SUPERVISOR' else 'frontline', code, on_date)
    return Decimal(str(value['score'])) if value else None


def level_score(db, level, on_date, snapshot=None):
    if snapshot and 'levels' in snapshot:
        return Decimal(str(snapshot['levels'][level.code]))
    policy = resolve(db, 'level', 'all', level.code, on_date)
    return Decimal(str(policy['score'])) if policy else Decimal(level.points)


def deduction_snapshot(db, deduction_type, on_date):
    policy = category(db, 'deduction', 'all', deduction_type.code, on_date)
    policy['levels'] = {level.code: str(level_score(db, level, on_date)) for level in db.query(DeductionLevel).all()}
    return policy


def deduction_points(db, deduction_type, level, on_date, snapshot=None):
    policy = snapshot or category(db, 'deduction', 'all', deduction_type.code, on_date)
    return Decimal(str(policy['score'])) if policy['mode'] == 'fixed' else level_score(db, level, on_date, policy)


def stored_deduction_points(db, row, level):
    """Old records predate configuration; never apply a later policy to them."""
    if not row.scoring_policy_json:
        return Decimal(level.points)
    return deduction_points(db, db.get(DeductionType, row.deduction_type_id), level,
                            row.occurred_on, json.loads(row.scoring_policy_json))


def validate_statement_upgrade_pair(db, row, first_id=None):
    """Validate a pair of statements; memo/warning history never raises its level.

    The retained minimum fields describe the allowed result range (memo or
    first warning), not a recursive progression through disciplinary history.
    """
    from app.date_utils import add_calendar_months, subtract_calendar_months
    from app.deduction_policy import upgrade_type_codes
    from app.v2_models import DeductionRecord, DeductionUpgradeRequest
    first = db.get(DeductionRecord, first_id) if first_id else None
    first_level = db.get(DeductionLevel, first.deduction_level_id) if first else None
    own_level = db.get(DeductionLevel, row.deduction_level_id) if row.deduction_level_id else None
    policy = json.loads(row.scoring_policy_json) if row.scoring_policy_json else None
    code = db.get(DeductionType, row.deduction_type_id).code
    group = policy['group'] if policy else sorted(upgrade_type_codes(code))
    day = date.fromisoformat(row.occurred_on)
    if (not first or first.id == row.id or first.employee_id != row.employee_id
            or first.status != 'active' or first.legacy_upgrade_excluded
            or not first_level or first_level.code != 'STATEMENT'
            or (own_level and own_level.code != 'STATEMENT')
            or db.get(DeductionType, first.deduction_type_id).code not in group
            or not subtract_calendar_months(day,3).isoformat() <= first.occurred_on <= add_calendar_months(day,3).isoformat()):
        raise HTTPException(409, '关联声明条件已变化，请刷新后核对两张原声明')
    if first.upgrade_request_id:
        work = db.get(DeductionUpgradeRequest, first.upgrade_request_id)
        if not work or work.first_deduction_id != first.id or work.second_deduction_id != row.id or work.status not in {'pending','awaiting_material'}:
            raise HTTPException(409, '历史声明已用于其他升级工单，请刷新后重试')
    elif first.upgrade_role not in (None,'source_first') or first.upgrade_state not in (None,'eligible','pending','material_processing','awaiting_material'):
        raise HTTPException(409, '历史声明已用于升级，请刷新后重试')
    available = {item.code:item for item in db.query(DeductionLevel).filter(DeductionLevel.code.in_(['MEMO','WARNING_1']),DeductionLevel.active.is_(True))}
    level = available.get('MEMO') or available.get('WARNING_1')
    if not level:
        raise HTTPException(409, '升级结果等级当前不可用，请联系管理员')
    return level


def validate_material_repeat(db, row, *, allow_statement_upgrade=False):
    """Only an unused statement can pair with another unused statement."""
    from app.date_utils import add_calendar_months, subtract_calendar_months
    from app.v2_models import DeductionRecord
    level = db.get(DeductionLevel, row.deduction_level_id)
    if not level or level.code != 'STATEMENT':
        return False
    if row.scoring_policy_json:
        policy = json.loads(row.scoring_policy_json)
    else:
        from app.deduction_policy import REPEAT_CONTROLLED_DEDUCTION_CODES, upgrade_type_codes
        code = db.get(DeductionType, row.deduction_type_id).code
        policy = dict(repeat='upgrade' if code in REPEAT_CONTROLLED_DEDUCTION_CODES else 'sum', group=sorted(upgrade_type_codes(code)))
    if policy['repeat'] != 'upgrade':
        return False
    day = date.fromisoformat(row.occurred_on)
    first = db.query(DeductionRecord).join(DeductionType).filter(
        DeductionRecord.employee_id == row.employee_id, DeductionRecord.id != row.id,
        DeductionType.code.in_(policy['group']), DeductionRecord.status == 'active',
        DeductionRecord.deduction_level_id == level.id, DeductionRecord.upgrade_role.is_(None),
        DeductionRecord.upgrade_request_id.is_(None),
        DeductionRecord.upgrade_state.in_(['eligible']) | DeductionRecord.upgrade_state.is_(None),
        DeductionRecord.legacy_upgrade_excluded.is_(False),
        DeductionRecord.occurred_on >= subtract_calendar_months(day,3).isoformat(),
        DeductionRecord.occurred_on <= add_calendar_months(day,3).isoformat(),
    ).order_by(DeductionRecord.occurred_on, DeductionRecord.id).first()
    if not first:
        return False
    if allow_statement_upgrade:
        validate_statement_upgrade_pair(db,row,first.id)
        return first
    raise HTTPException(409, '材料生效时检测到可配对的同类声明，请通过声明升级入口提交，当前记录暂不计分')


def deduction_allowed(db, user, deduction_type, on_date):
    from app.deduction_policy import DIRECT_HIDDEN_DEDUCTION_CODES, direct_only_deduction_user
    policy = category(db, 'deduction', 'all', deduction_type.code, on_date)
    direct = direct_only_deduction_user(user)
    if not policy['active'] or not policy['direct' if direct else 'manager']:
        raise HTTPException(403, '该类别当前未向此登记角色开放')
    # Configuration may narrow capabilities; it never expands hard permission boundaries.
    if direct and (deduction_type.code in DIRECT_HIDDEN_DEDUCTION_CODES or policy['mode'] == 'fixed'):
        raise HTTPException(403, '主管只能按原有声明权限登记此类别')
    return policy


def repeats(db, deduction_type, on_date):
    return category(db, 'deduction', 'all', deduction_type.code, on_date)['repeat'] == 'upgrade'


def repeat_codes(db, deduction_type, on_date):
    return category(db, 'deduction', 'all', deduction_type.code, on_date)['group']


def recognition_types(db, scope, on_date):
    result = []
    for row in db.query(RecognitionType).filter_by(active=True).order_by(RecognitionType.id):
        policy = category(db, 'recognition', scope, row.code, on_date)
        if not policy['active']:
            continue
        result.append(dict(id=row.id, code=row.code, name=policy.get('name', row.name),
                           fixed_score=policy['score'] if policy['mode'] == 'fixed' else None,
                           monthly_limit=policy['count'], dedicated_entry=row.code == 'POC'))
    return result


def reserve_count(db, row, policy):
    if not policy.get('configured') or policy['count'] is None or policy['overflow'] != 'block':
        return
    count = db.query(func.count(RecognitionRecord.id)).filter(
        RecognitionRecord.employee_id == row.employee_id, RecognitionRecord.recognition_type_id == row.recognition_type_id,
        RecognitionRecord.recognition_month == row.recognition_month, RecognitionRecord.status.in_(['pending', 'confirmed']),
        RecognitionRecord.id != row.id).scalar()
    if count >= policy['count']:
        raise HTTPException(409, f"本类别每名员工每月最多计分{policy['count']}次，已达到限制")


def apply_credit(db, row):
    policy = json.loads(row.scoring_policy_json) if row.scoring_policy_json else None
    if not policy or not policy.get('configured'):
        return False
    rows = db.query(RecognitionRecord).filter(RecognitionRecord.employee_id == row.employee_id,
        RecognitionRecord.recognition_type_id == row.recognition_type_id, RecognitionRecord.recognition_month == row.recognition_month,
        RecognitionRecord.status == 'confirmed', RecognitionRecord.id != row.id).all()
    prior = sum((Decimal(record.credited_fraction or 0) for record in rows), Decimal(0))
    count = len(rows)
    raw = Decimal(row.fraction or 0)
    credit = raw
    if policy['count'] is not None and count >= policy['count']:
        credit = Decimal(0)
    if policy['cap'] is not None:
        credit = min(credit, max(Decimal(0), Decimal(str(policy['cap'])) - prior))
    row.credited_fraction = credit
    row.monthly_cap_rule_code = 'configured_category'
    row.monthly_cap_status = 'within_limit' if credit == raw else 'partially_capped' if credit else 'fully_capped'
    row.monthly_cap_limit = Decimal(str(policy['cap'])) if policy['cap'] is not None else None
    row.monthly_cap_confirmed_before = prior
    row.monthly_cap_reason = None if credit == raw else '已按本类别月度次数或分值上限限制计分'
    from datetime import datetime
    row.monthly_cap_evaluated_at = datetime.now()
    return True


def revision(db):
    items = [(r.id, r.version, r.policy_json) for r in db.query(ScoringPolicy).order_by(ScoringPolicy.id)]
    items.extend((r.id, str(r.score), r.effective_date, r.active) for r in db.query(RecognitionScoreRule).order_by(RecognitionScoreRule.id))
    items.extend(('level', r.id, str(r.points), r.active) for r in db.query(DeductionLevel).order_by(DeductionLevel.id))
    for kind, model in (('recognition', RecognitionType), ('deduction', DeductionType)):
        items.extend((kind, r.id, r.code, r.name, r.active) for r in db.query(model).order_by(model.id))
    return hashlib.sha256(dump(items).encode()).hexdigest()


def number(value, minimum=0, integer=False):
    if isinstance(value, bool):
        raise HTTPException(422, '分值或次数格式不正确')
    try:
        result = Decimal(str(value))
        maximum=Decimal('2147483647') if integer else Decimal('99999999.99')
        if not result.is_finite() or result < Decimal(str(minimum)) or result > maximum or result.as_tuple().exponent < -2 or integer and result != result.to_integral_value():
            raise ValueError()
    except Exception as exc:
        raise HTTPException(422, '分值须为有效非负数字（最多两位小数），次数须为正整数，且不能超过存储范围') from exc
    return int(result) if integer else float(result)


def validate(db, kind, scope, code, policy):
    if kind in ('role', 'level'):
        if set(policy) != {'score'}:
            raise HTTPException(422, '分值设置字段无效')
        if kind == 'role':
            allowed = {'TA_SUPERVISOR','SUPERVISOR','TA_GSM','GSM','AM','OM'} if scope == 'frontline' else {'TA_GSM','GSM','AM','OM'}
            if code not in allowed or scope not in ('frontline', 'supervisor'):
                raise HTTPException(422, '认可角色或计分人群无效')
        elif scope != 'all' or not db.query(DeductionLevel).filter_by(code=code).first():
            raise HTTPException(422, '处分等级无效')
        return {'score': number(policy['score'])}
    fields = {'name','active','mode','score','count','cap','overflow'} if kind == 'recognition' else {'name','active','mode','score','repeat','group','direct','manager'}
    if set(policy) != fields or not isinstance(policy.get('name'), str) or not 1 <= len(policy['name'].strip()) <= 30:
        raise HTTPException(422, '请填写30字以内的类别名称及完整规则')
    value = dict(policy, name=policy['name'].strip(), score=number(policy['score']))
    if type(value['active']) is not bool:
        raise HTTPException(422, '启用状态无效')
    if kind == 'recognition':
        if scope not in ('frontline','supervisor') or value['mode'] not in ('role','fixed') or value['overflow'] not in ('zero','block') or code == 'POC':
            raise HTTPException(422, '计分方式无效，POC保留专用流程')
        value['count'] = None if policy['count'] is None else number(policy['count'], 1, True)
        value['cap'] = None if policy['cap'] is None else number(policy['cap'], .01)
    else:
        from app.deduction_policy import DIRECT_HIDDEN_DEDUCTION_CODES, upgrade_type_codes
        if scope != 'all' or value['mode'] not in ('level','fixed') or value['repeat'] not in ('sum','upgrade') or any(type(value[k]) is not bool for k in ('direct','manager')):
            raise HTTPException(422, '扣分规则无效')
        if value['active'] and not (value['direct'] or value['manager']):
            raise HTTPException(422, '至少保留一种登记角色')
        if value['direct'] and (code in DIRECT_HIDDEN_DEDUCTION_CODES or value['mode'] == 'fixed'):
            raise HTTPException(422, '不能扩大主管的原有登记权限，固定扣分仅限经理端')
        if value['mode'] == 'fixed' and value['repeat'] != 'sum':
            raise HTTPException(422, '固定扣分须逐条累计，处分升级仍按等级处理')
        # Cross-category upgrade groups remain the existing business-defined groups.
        if policy['group'] != sorted(upgrade_type_codes(code)):
            raise HTTPException(422, '重复判定组须沿用原有分类，不能改变跨类别升级边界')
    return value
