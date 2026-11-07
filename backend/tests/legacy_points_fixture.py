"""Isolated historical-import UI fixture. All staff and opening balances are simulated."""
import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_supervisor_performance import app, SessionLocal
from fastapi import File, UploadFile
from fastapi.responses import Response
from openpyxl import load_workbook
from app.v2_models import Employee, EmployeeRoleAssignment, Role, UserAccount
from app.activity_points_models import LegacyPointsBalance, ActivityPointsEntry
from app.services.legacy_points_xlsx import tag, cell


@app.on_event('startup')
def seed_legacy_people():
    with SessionLocal() as db:
        actor = db.query(Employee).filter_by(employee_no='GSMTEST01').one()
        sample_password = db.query(UserAccount).filter_by(employee_id=actor.id).one().password_hash
        circle = actor.attraction_id
        for e in db.query(Employee).filter(Employee.id != actor.id):e.attraction_id=None
        actor.hired_on='2025-01-01'
        for assignment in db.query(EmployeeRoleAssignment).filter_by(employee_id=actor.id):assignment.starts_on='2025-01-01'
        for number, name, code, archived in [('7300001','林晓','CM',False),('7300002','陈晨','SUPERVISOR',False),('7300003','周宁','TR',True)]:
            employee=Employee(employee_no=number,name=name,attraction_id=circle,hired_on='2025-01-01',
                is_active=not archived,terminated_on='2026-10-15' if archived else None)
            db.add(employee);db.flush()
            if not archived:
                db.add(UserAccount(employee_id=employee.id,login_account=number,password_hash=sample_password,
                    enabled=True,must_change_password=False,credential_initialized=True))
            db.add(EmployeeRoleAssignment(employee_id=employee.id,role_id=db.query(Role).filter_by(code=code).one().id,
                starts_on='2025-01-01',assignment_type='permanent',status='active'))
            if number=='7300002':
                db.add(LegacyPointsBalance(employee_id=employee.id,total=12,revision=1,updated_by=actor.id))
                db.add(ActivityPointsEntry(employee_id=employee.id,recognition_id=None,revision=1,delta=12,
                    kind='legacy_import',origin='legacy_import',business_date='2026-11-01',detail_json='{"category":"历史积点补录","signer_name":"示例GSM"}'))
        db.commit()


@app.post('/__fixture/legacy-filled')
async def filled(file: UploadFile = File(...), review: bool = False):
    content=await file.read()
    book=load_workbook(BytesIO(content),read_only=True)
    rows={str(r[0].value):r[0].row for r in book['名单'].iter_rows(min_row=4)};book.close()
    edits={f"H{rows['7300001']}":18,f"H{rows['7300002']}":15,f"I{rows['7300002']}":'纸卡复核，补足3点'}
    if review:
        edits={f"H{rows['7300001']}":18,f"H{rows['7300002']}":12,f"H{rows['GSMTEST01']}":'请核对'}
    with ZipFile(BytesIO(content)) as archive:files={name:archive.read(name) for name in archive.namelist()}
    root=ET.fromstring(files['xl/worksheets/sheet1.xml'])
    for address,value in edits.items():
        number=''.join(c for c in address if c.isdigit());row=root.find(f"{tag('sheetData')}/{tag('row')}[@r='{number}']")
        old=row.find(f"{tag('c')}[@r='{address}']");new=cell(address,value,old.attrib.get('s') if old is not None else None)
        if old is not None:row.remove(old)
        row.append(new)
    files['xl/worksheets/sheet1.xml']=ET.tostring(root,encoding='utf-8')
    output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as archive:
        for name,value in files.items():archive.writestr(name,value)
    return Response(output.getvalue(),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
