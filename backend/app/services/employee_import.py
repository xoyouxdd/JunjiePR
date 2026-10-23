"""V3 employee workbook preflight. No writes until the caller confirms the exact batch."""
import hashlib
import json
from io import BytesIO

from fastapi import HTTPException
from openpyxl import load_workbook

from app.services.employee_import_dates import normalize_import_date
from app.services.employee_types import normalize, is_due, business_today
from app.services.identity import role_at
from app.v2_models import Employee, UserAccount, Role, Attraction

HEADERS = ['员工号', '姓名', '角色代码', '景点圈', 'Type', '实习结束日期']
ROLE_MAP = {'CM':'CM', 'TR':'TR', 'LEAD':'SUPERVISOR', 'GSM':'GSM', 'AM':'AM', 'OM':'OM'}


def preflight(db, user, content, allowed_circles, allowed_roles):
    try:
        wb=load_workbook(BytesIO(content),read_only=True,data_only=True)
    except Exception as exc:
        raise HTTPException(400,'Excel文件无法读取') from exc
    try:
        if not wb.worksheets:
            raise HTTPException(400,'Excel文件没有员工工作表')
        sheet=wb.worksheets[0]
        header=[str(v or '').strip() for v in next(sheet.iter_rows(min_row=1,max_row=1,values_only=True))]
        while header and not header[-1]: header.pop()
        if len(header)!=len(HEADERS) or len(set(header))!=len(header) or set(header)!=set(HEADERS):
            raise HTTPException(400,'表头与V3模板不一致，请重新下载六列模板；不再导入密码、在职、账号启用或任职期限列')
        positions={name:header.index(name) for name in HEADERS}
        roles={r.code:r for r in db.query(Role).all()}
        circles={c.name:c for c in db.query(Attraction).filter(Attraction.active.is_(True),Attraction.employee_circle.is_(True)).all()}
        parsed=[];duplicates=[];expired=[];errors=[];seen=set()
        for rownum,values in enumerate(sheet.iter_rows(min_row=2,values_only=True),2):
            if rownum>10001: raise HTTPException(400,'单次最多导入10000行，请拆分文件')
            values=list(values)+[None]*max(0,len(header)-len(values))
            raw={name:values[i] for name,i in positions.items()}
            if not any(v is not None and str(v).strip() for v in raw.values()): continue
            no=str(raw['员工号'] or '').strip();name=str(raw['姓名'] or '').strip()
            role=ROLE_MAP.get(str(raw['角色代码'] or '').strip().upper())
            circle=circles.get(str(raw['景点圈'] or '').strip())
            prefix=f'第{rownum}行：'
            if len(no)!=7 or not no.isascii() or not no.isdigit(): errors.append(prefix+'员工号必须为7位纯数字');continue
            if no in seen: errors.append(prefix+f'文件内部员工号{no}重复，请先修正');continue
            seen.add(no)
            if not name or len(name)>100: errors.append(prefix+'姓名必填，且不能超过100个字符');continue
            if not role or role not in roles: errors.append(prefix+'角色仅支持CM、TR、Lead、GSM、AM、OM；TA职务请在页面设置');continue
            if role not in allowed_roles: errors.append(prefix+'当前HR权限仅可导入Lead及以下员工');continue
            if not circle or allowed_circles is not None and circle.id not in allowed_circles:
                errors.append(prefix+'请选择权限范围内的有效景点圈');continue
            old=db.query(Employee).filter_by(employee_no=no).first()
            account=db.query(UserAccount).filter_by(login_account=no).first()
            if old or account:
                if old and old.attraction_id in ({circle.id} if allowed_circles is None else allowed_circles):
                    old_role=role_at(db,old.id)
                    if old_role and old_role.code not in allowed_roles:
                        errors.append(prefix+'员工号已被占用，超出当前可管理范围');continue
                    if old.account_deleted_at:
                        errors.append(prefix+f'员工号{no}已删除留档，请通过新建员工的重新入职流程注册');continue
                    duplicates.append({'row':rownum,'employee_no':no,'name':old.name,'id':old.id})
                    continue
                errors.append(prefix+'员工号已被占用，无法导入');continue
            try:
                from app.rotation.temporary import pending
                pending(db,no,name,circle.id)
                kind,end=normalize(raw['Type'],normalize_import_date(raw['实习结束日期']))
            except (ValueError,HTTPException) as exc:
                errors.append(prefix+str(exc.detail if isinstance(exc,HTTPException) else exc));continue
            row={'row':rownum,'employee_no':no,'name':name,'role_code':role,'attraction_id':circle.id,
                 'employee_type':kind,'internship_ends_on':end}
            parsed.append(row)
            if is_due(kind,end): expired.append({'row':rownum,'employee_no':no,'name':name,'internship_ends_on':end})
        if errors: raise HTTPException(400,'；'.join(errors[:20]))
        if not seen: raise HTTPException(400,'文件中没有员工数据')
        digest=hashlib.sha256(content+json.dumps({'user':user.id,'date':business_today().isoformat(),
            'rows':parsed,'duplicates':duplicates},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        return {'rows':parsed,'duplicates':duplicates,'expired':expired,'digest':digest,'would_create':len(parsed)}
    finally:
        wb.close()
