"""Professional, scoped receipt reports using the existing PDF/XLSX runtime."""
from datetime import date, datetime
import hashlib
import math
from html import escape
from io import BytesIO

import fitz
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.v2_database import FILE_DIR, SessionLocal
from app.v2_models import StoredFile

STATUS = {"pending": "待查收", "completed": "已查收", "suspended": "暂缓", "exited": "退出范围",
          "superseded": "已替换", "withdrawn": "已撤下", "expired": "已过期"}


def text(value):
    if isinstance(value, str) and len(value) >= 19:
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            pass
    return value.isoformat(sep=" ", timespec="seconds") if isinstance(value, datetime) else str(value or "—")


def export_records(data, format, user, followup=False):
    return spreadsheet(data, user, followup) if format == "xlsx" else pdf(data, user)


def status_label(row):
    return "逾期未签" if row.get("overdue") else STATUS.get(row["status"], row["status"])


def excel_date(value):
    if not value:
        return None
    if isinstance(value, (date, datetime)):
        return value
    try:
        return datetime.fromisoformat(value) if len(value) > 10 else date.fromisoformat(value)
    except (TypeError, ValueError):
        return str(value)


def report_groups(data):
    """Derive groups only from already authorized export rows."""
    groups = {}
    for row in data["items"]:
        key = (row["attraction_name"], row.get("group_name") or "无小组")
        group = groups.setdefault(key, {"circle": key[0], "name": key[1], "items": [],
            "required": 0, "completed": 0, "pending": 0, "suspended": 0, "exited": 0})
        group["items"].append(row)
        if row["status"] in {"suspended", "exited"}:
            group[row["status"]] += 1
        else:
            group["required"] += 1
            group["completed" if row.get("confirmed_at") else "pending"] += 1
    return list(groups.values())


def spreadsheet(data, user, followup=False):
    workbook = Workbook()
    overview = workbook.active
    overview.title = "签收总览"
    announcement, counts = data["announcement"], data["counts"]
    now = datetime.now()
    historical = not announcement["effective"]
    required_label, pending_label = ("原应签", "未签") if historical else ("应签", "待签")
    scope_note = "当前管理范围内人员" if data["scoped"] else "全部有权查看人员"
    label = f"ANN-{announcement['id']:06d} / V{announcement['version']}"
    navy, teal, muted = "173F52", "176152", "526779"

    def heading(sheet, title, columns, note):
        last = get_column_letter(columns)
        for row, value in enumerate([title, announcement["title"], f"{label} · {announcement['scope_label']}",
             note, f"导出：{user.name}（{user.employee.employee_no}） · {now:%Y-%m-%d %H:%M:%S} · {scope_note}"], 1):
            sheet.cell(row, 1, value)
            sheet.merge_cells(f"A{row}:{last}{row}")
            sheet.row_dimensions[row].height = [32, max(36, math.ceil(len(announcement["title"])/45)*20), 28, 30, 26][row-1]
            sheet.cell(row, 1).font = Font(name="微软雅黑", size=18 if row == 1 else 11, bold=row in {1,2}, color=navy if row < 4 else muted)
        sheet.sheet_view.showGridLines = False
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
        sheet.print_title_rows = "1:6"
        sheet.oddFooter.center.text = f"{label} / {user.employee.employee_no} / &P / &N"

    def table(sheet, headers, widths, rows, visible_columns=None):
        sheet.append(headers)
        for values in rows:
            sheet.append(values)
        for cell in sheet[6]:
            cell.font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=navy)
        sheet.row_dimensions[6].height = 30
        for row in sheet.iter_rows(min_row=7):
            lines = []
            for cell,width in zip(row[:visible_columns or len(headers)],widths):
                value = text(cell.value)[:16] if isinstance(cell.value,datetime) else text(cell.value)
                lines.append(max(1,math.ceil(sum(2 if ord(c)>0x1100 else 1 for c in value)/(width-2))))
            sheet.row_dimensions[row[0].row].height = max(36,max(lines)*17+10)
            for cell in row:
                cell.font = Font(name="微软雅黑", size=11, color=navy)
                cell.fill = PatternFill("solid", fgColor="F0F5F8" if cell.row % 2 else "FFFFFF")
                cell.border = Border(bottom=Side(style="hair", color="D7E3EA"))
                if isinstance(cell.value, datetime):
                    cell.number_format = "yyyy-mm-dd hh:mm"
                elif isinstance(cell.value, date):
                    cell.number_format = "yyyy-mm-dd"
        for index, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        sheet.freeze_panes = "E7"
        sheet.auto_filter.ref = f"A6:{get_column_letter(len(headers))}{max(6,sheet.max_row)}"

    heading(overview, "公告签收总览", 8, "先看圈 / 小组进度，再到签收记录筛选人员；培训信息见跟进记录。")
    groups = report_groups(data)
    table(overview, ["景点圈", "小组", required_label, "已签", pending_label, "暂缓", "退出", "签收率"],
          [18,26,12,12,12,12,12,14],
          [[g["circle"],g["name"],g["required"],g["completed"],g["pending"],g["suspended"],g["exited"],
            g["completed"]/g["required"] if g["required"] else None] for g in groups])
    # Headline counts come from the same scoped records as the group breakdown.
    overview["A4"] = f"{required_label} {counts['required']}  ·  已签 {counts['completed']}  ·  {pending_label} {counts['pending']}  ·  暂缓 {counts['suspended']}  ·  退出 {counts['exited']}"
    overview["A4"].font = Font(name="微软雅黑", bold=True, size=13, color=teal)
    overview.freeze_panes = "C7"
    for row in overview.iter_rows(min_row=7):
        row[7].number_format = "0%"

    sheet = workbook.create_sheet("签收记录")
    heading(sheet, "人员签收记录", 9, "按景点圈、小组、工号排列；右侧折叠列保留接收来源、声明及培训凭据。")
    headers = ["景点圈","小组","工号","姓名","状态","阅读时间","确认时间","期限","跟进培训师",
               "层级","接收原因","接收时间","签字声明","培训师工号","培训批次","验码时间"]
    table(sheet, headers, [18,24,18,18,14,23,23,16,20,22,22,23,55,18,14,23],
          [[r["attraction_name"],r["group_name"],r["employee_no"],r["employee_name"],status_label(r),
            excel_date(r.get("seen_at")),excel_date(r.get("confirmed_at")),excel_date(r.get("due_on")),r.get("trainer_name") or "—",
            r["role_label"],r["reason"],excel_date(r.get("delivered_at")),r.get("signing_statement") or "—",
            r.get("trainer_no") or "—",r.get("training_session_id"),excel_date(r.get("training_verified_at"))] for r in data["items"]], visible_columns=9)
    sheet.column_dimensions.group("J","P", hidden=True)
    sheet.column_dimensions["Q"].collapsed = True
    sheet.print_area = f"A1:I{max(6,sheet.max_row)}"
    for row in sheet.iter_rows(min_row=7):
        row[12].alignment = Alignment(vertical="top", wrap_text=True)

    followup_sheet = workbook.create_sheet("跟进记录")
    heading(followup_sheet, "培训与签字跟进", 8, "只看谁跟进谁、验码与签字进度；右侧折叠列保留培训师工号、批次和期限。")
    table(followup_sheet, ["景点圈","小组","工号","姓名","状态","跟进培训师","验码时间","签字时间","培训师工号","培训批次","期限"],
          [18,24,18,18,14,20,23,23,18,14,16],
          [[r["attraction_name"],r["group_name"],r["employee_no"],r["employee_name"],status_label(r),r.get("trainer_name") or ("未验码" if announcement.get("training_required") else "不需培训码"),
            excel_date(r.get("training_verified_at")),excel_date(r.get("confirmed_at")),r.get("trainer_no") or "—",
            r.get("training_session_id"),excel_date(r.get("due_on"))] for r in data["items"]], visible_columns=8)
    followup_sheet.column_dimensions.group("I","K", hidden=True)
    followup_sheet.column_dimensions["L"].collapsed = True
    followup_sheet.print_area = f"A1:H{max(6,followup_sheet.max_row)}"

    info = workbook.create_sheet("公告内容")
    info.sheet_view.showGridLines = False
    info.append(["公告内容", label])
    metadata = [("标题",announcement["title"]),("板块",announcement["category"]),
        ("确认方式","培训码＋本人签字" if announcement.get("training_required") else {1:"阅读",2:"确认",3:"签字"}[announcement["confirmation_level"]]),
        ("范围",announcement["scope_label"]),("生效",excel_date(announcement["effective_on"])),
        ("截止",excel_date(announcement.get("due_on"))),("失效",excel_date(announcement.get("expires_on")) or "持续有效"),
        ("重点",announcement.get("summary") or "—"),("变更",announcement.get("change_summary") or "—")]
    for key, value in metadata:
        if isinstance(value,str) and len(value) > 240:
            for offset in range(0,len(value),240):
                info.append([key if offset == 0 else key+"（续）",value[offset:offset+240]])
        else:
            info.append([key,value])
    # Preserve paragraph breaks, and let each printed row fit at a readable size.
    for paragraph in announcement.get("body", "").split("\n"):
        for offset in range(0, max(1,len(paragraph)), 80):
            info.append(["正文" if offset == 0 else "续",paragraph[offset:offset+80]])
    info.column_dimensions["A"].width = 14
    info.column_dimensions["B"].width = 85
    for row in info:
        info.row_dimensions[row[0].row].height = max(30, math.ceil(len(str(row[1].value or ""))/40)*18+12)
        row[0].font = Font(name="微软雅黑",bold=True,color=navy,size=11)
        row[0].fill = PatternFill("solid",fgColor="EDF3F7")
        row[1].font = Font(name="微软雅黑",color=navy,size=11)
        if isinstance(row[1].value,datetime): row[1].number_format = "yyyy-mm-dd hh:mm"
        elif isinstance(row[1].value,date): row[1].number_format = "yyyy-mm-dd"
    info.freeze_panes = "B2"
    info.sheet_properties.pageSetUpPr.fitToPage = True
    info.page_setup.fitToWidth, info.page_setup.fitToHeight = 1, 0
    info.page_setup.paperSize = info.PAPERSIZE_A4
    info.print_area = f"A1:B{info.max_row}"
    info.oddFooter.center.text = f"{label} / &P / &N"
    for tab in (sheet,followup_sheet):
        for row in tab.iter_rows(min_row=7):
            row[2].number_format = "@"
            row[2].alignment = Alignment(horizontal="left",vertical="center",wrap_text=True)
            row[4].font = Font(name="微软雅黑",size=11,bold=True,color=teal if row[4].value == "已查收" else "9C3030" if row[4].value in {"待查收","逾期未签"} else muted)
    for tab in workbook:
        for row in tab:
            for cell in row:
                if isinstance(cell.value,str): cell.data_type = "s"
                if cell.alignment.wrap_text is not True:
                    cell.alignment = Alignment(vertical="center",wrap_text=True)
    if followup:
        workbook.active = workbook.index(followup_sheet)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def pdf(data, user):
    document = fitz.open()
    announcement, counts = data["announcement"], data["counts"]
    label = f"ANN-{announcement['id']:06d} / V{announcement['version']}"
    stamp = f"导出人：{user.name}（{user.employee.employee_no}） | {datetime.now():%Y-%m-%d %H:%M:%S}"
    css = "body{font-family:sans-serif;color:#173f52;font-size:10pt} h1{font-size:22pt;line-height:1.35;margin:12px 0} h2{font-size:14pt} p{line-height:1.55;margin:10px 0} table{border-collapse:collapse;width:100%;font-size:10pt} th{text-align:left;background:#173f52;color:white;padding:9px} td{border-bottom:1px solid #dce6ea;padding:9px;vertical-align:top;overflow-wrap:anywhere} .muted{color:#526779} .stats{font-size:15pt;background:#edf4f7;padding:18px;line-height:1.7}"
    def page(title):
        p = document.new_page(width=595, height=842)
        p.insert_htmlbox(fitz.Rect(40,30,355,65), f"<b>FZPR / {escape(title)}</b>", css=css)
        p.insert_htmlbox(fitz.Rect(355,30,555,65), f"<div style='text-align:right'>{escape(label)}</div>", css=css)
        p.draw_line((40,75),(555,75),color=(0.1,0.3,0.35),width=1)
        return p
    def insert(p, rect, html):
        spare, _ = p.insert_htmlbox(rect,html,css=css,scale_low=0.9)
        if spare < 0:
            raise ValueError("签收报告内容超出页面")
    required_label, pending_label = ("应签","待签") if announcement["effective"] else ("原应签","未签")
    scope_note = "当前管理范围内的接收人员" if data["scoped"] else "全部有权查看的接收人员"
    ratio = f"{counts['completed']/counts['required']:.0%}" if counts["required"] else "—"
    p = page("公告签收总览")
    insert(p,fitz.Rect(40,100,555,760),f"""<h1>{escape(announcement['title'])}</h1>
        <p class='muted'>{escape(announcement['category'])} · {escape(label)}</p>
        <table><tr><td>发布范围</td><td>{escape(announcement['scope_label'])}</td></tr>
        <tr><td>生效 / 失效</td><td>{escape(announcement['effective_on'])} / {escape(announcement.get('expires_on') or '持续有效')}</td></tr>
        <tr><td>确认方式</td><td>{'培训码＋本人签字' if announcement.get('training_required') else {1:'阅读',2:'明确确认',3:'手写签字'}[announcement['confirmation_level']]}</td></tr>
        <tr><td>当前状态</td><td>{'有效' if announcement['effective'] else '历史 / 已停止适用'}</td></tr></table>
        <div class='stats'>{required_label} <b>{counts['required']}</b>　已签 <b>{counts['completed']}</b>　{pending_label} <b>{counts['pending']}</b><br>签收率 <b>{ratio}</b></div>
        <p class='muted'>暂缓 {counts['suspended']} · 退出 {counts['exited']}（不计入{required_label}）</p>
        <h2>如何阅读</h2><p>先查看后续按圈 / 小组排列的人员签收明细。公告完整正文、变更说明和签字原图在附录中保留。</p>
        <p class='muted'>本报告包含{scope_note}。签收对应本次导出的版本，旧版签收不代替新版确认；已查收的有效要求持续适用。</p>""")
    training_columns = announcement.get("training_required") or any(r.get("trainer_name") for r in data["items"])
    columns = [40,165,255,390,555] if not training_columns else [40,160,245,345,445,555]
    titles = ["工号 / 姓名","签收状态","阅读时间","签收时间"] + (["培训师 / 验码"] if training_columns else [])
    def detail_page(group, continued=False, existing=None, top=95):
        p = existing if existing is not None else page("人员签收明细")
        insert(p,fitz.Rect(40,top,555,top+58),f"<b>{escape(group['circle'])} · {escape(group['name'])}{'（续）' if continued else ''}</b><br><span class='muted'>{required_label} {group['required']} · 已签 {group['completed']} · {pending_label} {group['pending']} · 暂缓 {group['suspended']} · 退出 {group['exited']}</span>")
        p.draw_rect(fitz.Rect(40,top+60,555,top+92),color=None,fill=(0.09,0.25,0.32))
        for i,title in enumerate(titles):
            insert(p,fitz.Rect(columns[i]+5,top+64,columns[i+1]-5,top+90),"<b style='color:white;font-size:9pt'>"+title+"</b>")
        return p,top+92
    groups = report_groups(data)
    if not groups:
        p = page("人员签收明细")
        insert(p,fitz.Rect(40,100,555,180),"<p>本版本尚无接收记录。</p>")
    def short_time(value):
        return text(value)[:16] if value else "—"
    detail_current,top = None,760
    for group in groups:
        if top+164 > 760:
            p,top = detail_page(group)
        else:
            p,top = detail_page(group,existing=detail_current,top=top+16)
        detail_current = p
        for index,r in enumerate(group["items"]):
            status = status_label(r)
            cells = [[r["employee_name"],r["employee_no"]],
                [status] + (["截止 "+r["due_on"]] if r.get("due_on") and not r.get("confirmed_at") and r["status"] not in {"suspended","exited"} else []),
                [short_time(r.get("seen_at"))],[short_time(r.get("confirmed_at"))]]
            if training_columns:
                cells.append([r["trainer_name"],short_time(r.get("training_verified_at"))] if r.get("trainer_name") else ["未验码" if announcement.get("training_required") else "—"])
            line_counts = []
            for i,cell in enumerate(cells):
                available = columns[i+1]-columns[i]-14
                line_counts.append(sum(max(1,math.ceil(sum(10 if ord(c)>0x1100 else 5.5 for c in str(value))/available)) for value in cell))
            height = max(56,max(line_counts)*15+16)
            if top+height > 760:
                p,top = detail_page(group,True)
                detail_current = p
            if index % 2 == 0:
                p.draw_rect(fitz.Rect(40,top,555,top+height),color=None,fill=(0.95,0.97,0.98))
            for i,cell in enumerate(cells):
                html = "<div style='font-size:10pt;line-height:1.4'>"+"<br>".join(escape(str(value)) for value in cell)+"</div>"
                insert(p,fitz.Rect(columns[i]+5,top+7,columns[i+1]-5,top+height-5),html)
            p.draw_line((40,top+height),(555,top+height),color=(0.85,0.9,0.91),width=0.5)
            top += height
    # Full content is an appendix; short blocks paginate without discarding text.
    sections = [("重点",announcement.get("summary")),("变更说明",announcement.get("change_summary")),("公告正文",announcement.get("body"))]
    blocks = []
    for title,value in sections:
        if not value:
            continue
        blocks.append(f"<h2>{title}</h2>")
        for paragraph in value.split("\n"):
            for offset in range(0,max(1,len(paragraph)),300):
                blocks.append("<p>"+(escape(paragraph[offset:offset+300]) or "&nbsp;")+"</p>")
    offset = 0
    while offset < len(blocks):
        count = min(12,len(blocks)-offset)
        while True:
            p = page("公告正文与更新说明")
            spare,_ = p.insert_htmlbox(fitz.Rect(40,100,555,760),"".join(blocks[offset:offset+count]),css=css,scale_low=0.9)
            if spare >= 0:
                break
            document.delete_page(-1)
            if count == 1:
                raise ValueError("公告正文超出签收报告页面")
            count = max(1,count//2)
        offset += count
    signed = [r for r in data["items"] if r.get("signature_url")]
    with SessionLocal() as db:
        for start in range(0, len(signed), 2):
            p = page("签字凭据")
            for position, r in enumerate(signed[start:start+2]):
                top = 100+position*320
                file_id = int(r["signature_url"].split("/")[-1].split("?")[0])
                stored = db.get(StoredFile, file_id)
                if not stored or not (FILE_DIR / stored.storage_key).is_file():
                    raise ValueError("签字凭据文件缺失，导出已停止")
                raw = (FILE_DIR / stored.storage_key).read_bytes()
                if hashlib.sha256(raw).hexdigest() != stored.sha256:
                    raise ValueError("签字凭据摘要不匹配，导出已停止")
                html = f"<b>{escape(r['employee_name'])} / {escape(r['employee_no'])}</b><br>{escape(text(r['confirmed_at']))}<br>{escape(r['signing_statement'])}"
                spare, _ = p.insert_htmlbox(fitz.Rect(40, top, 555, top+170), html, css=css, scale_low=0.85)
                if spare < 0:
                    raise ValueError("签字声明内容超出页面")
                p.draw_rect(fitz.Rect(40, top+172, 555, top+262), color=(0.85, 0.9, 0.9), fill=(1, 1, 1))
                p.insert_image(fitz.Rect(48, top+176, 547, top+258), stream=raw, keep_proportion=True)
                p.insert_text((40, top+283), "SHA256: "+stored.sha256, fontsize=7, color=(0.4, 0.45, 0.5))
    for index, p in enumerate(document):
        p.insert_htmlbox(fitz.Rect(40, 785, 555, 820), f"<span class='muted'>{escape(stamp)}<br>受控签收记录 | {index+1} / {document.page_count}</span>", css=css)
    content = document.tobytes(garbage=4, deflate=True)
    document.close()
    return content
