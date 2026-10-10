"""Professional, scoped receipt reports using the existing PDF/XLSX runtime."""
from datetime import datetime
import hashlib
import math
from html import escape
from io import BytesIO

import fitz
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
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


def export_records(data, format, user):
    return spreadsheet(data, user) if format == "xlsx" else pdf(data, user)


def spreadsheet(data, user):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "签收记录"
    announcement, counts = data["announcement"], data["counts"]
    sheet.append(["公告签收记录"])
    sheet.merge_cells("A1:K1")
    sheet["A1"].font = Font(size=20, bold=True, color="173F52")
    sheet.row_dimensions[1].height = 35
    sheet.append([announcement["title"]])
    sheet.merge_cells("A2:K2")
    sheet["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[2].height = 44
    sheet.append([f"编号 ANN-{announcement['id']:06d} / V{announcement['version']} / {announcement['scope_label']}"])
    sheet.merge_cells("A3:K3")
    sheet.append([f"{'应签' if announcement['effective'] else '原应签'} {counts['required']} · 已签 {counts['completed']} · {'待签' if announcement['effective'] else '未签'} {counts['pending']} · 暂缓 {counts['suspended']} · 退出 {counts['exited']}"])
    sheet.merge_cells("A4:K4")
    sheet.append([f"导出：{user.name}（{user.employee.employee_no}） / {datetime.now():%Y-%m-%d %H:%M:%S} / {'管理范围内人员' if data['scoped'] else '全部接收人员'}"])
    sheet.merge_cells("A5:K5")
    sheet.append(["工号", "姓名", "层级", "景点圈", "状态", "接收原因", "接收时间", "阅读时间", "确认时间", "期限", "签字声明"])
    for row in data["items"]:
        sheet.append([row["employee_no"], row["employee_name"], row["role_label"], row["attraction_name"],
                      STATUS.get(row["status"], row["status"]), row["reason"], text(row["delivered_at"]), text(row["seen_at"]),
                      text(row["confirmed_at"]), row["due_on"] or "—", row["signing_statement"] or "—"])
    for cell in sheet[6]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="173F52")
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"  # User input must never become an Excel formula.
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for index, width in enumerate([18, 20, 22, 16, 14, 22, 22, 22, 22, 16, 45], 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A7"
    sheet.auto_filter.ref = f"A6:K{max(6,sheet.max_row)}"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
    sheet.print_title_rows = "1:6"
    sheet.oddFooter.center.text = f"受控签收记录 / {user.employee.employee_no} / &P / &N"
    info = workbook.create_sheet("公告内容")
    info.append(["版本", f"ANN-{announcement['id']:06d} / V{announcement['version']}"])
    for key, value in [("标题", announcement["title"]), ("板块", announcement["category"]),
                       ("范围", announcement["scope_label"]), ("生效", announcement["effective_on"]),
                       ("失效", announcement["expires_on"] or "持续有效"), ("重点", announcement.get("summary", "")),
                       ("变更", announcement.get("change_summary", ""))]:
        info.append([key, value])
    body = announcement.get("body", "")
    for offset in range(0, len(body), 100):
        info.append(["正文" if offset == 0 else "", body[offset:offset+100]])
    info.column_dimensions["A"].width = 12
    info.column_dimensions["B"].width = 75
    for row in info:
        info.row_dimensions[row[0].row].height = 48
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    info.freeze_panes = "B2"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def pdf(data, user):
    document = fitz.open()
    announcement, counts = data["announcement"], data["counts"]
    label = f"ANN-{announcement['id']:06d} / V{announcement['version']}"
    stamp = f"导出人：{user.name}（{user.employee.employee_no}） | {datetime.now():%Y-%m-%d %H:%M:%S}"
    css = "body{font-family:sans-serif;color:#173f52;font-size:10pt} h1{font-size:22pt} h2{font-size:15pt} p{line-height:1.6} table{border-collapse:collapse;width:100%;font-size:9pt} th{text-align:left;background:#173f52;color:white;padding:8px} td{border-bottom:1px solid #dce6ea;padding:7px;vertical-align:top;overflow-wrap:anywhere} .muted{color:#60757e} .stats{font-size:14pt;background:#edf4f1;padding:18px}"
    def page(title):
        p = document.new_page(width=595, height=842)
        p.insert_htmlbox(fitz.Rect(40, 30, 355, 65), f"<b>FZPR / {escape(title)}</b>", css=css)
        p.insert_htmlbox(fitz.Rect(355, 30, 555, 65), f"<div style='text-align:right'>{escape(label)}</div>", css=css)
        p.draw_line((40, 75), (555, 75), color=(0.1, 0.3, 0.35), width=1)
        return p
    p = page("公告签收记录")
    ratio = f"{counts['completed']/counts['required']:.0%}" if counts["required"] else "—"
    html = f"""<h1>{escape(announcement['title'])}</h1><p>{escape(announcement['category'])} | {escape(label)}</p>
        <p class='muted'>范围：{escape(announcement['scope_label'])}<br>生效：{escape(announcement['effective_on'])} / 失效：{escape(text(announcement['expires_on']))}<br>
        确认方式：{ {1:'阅读',2:'明确确认',3:'手写签字'}[announcement['confirmation_level']] } / 状态：{'有效' if announcement['effective'] else '历史 / 已停止适用'}</p>
        <div class='stats'>{'应签' if announcement['effective'] else '原应签'} {counts['required']} / 已签 {counts['completed']} / {'待签' if announcement['effective'] else '未签'} {counts['pending']}<br>签收率 {ratio}</div>
        <p>暂缓 {counts['suspended']} / 退出范围 {counts['exited']}（不计入当前应签人数）</p>
        <h2>记录口径</h2><p>签收与本次导出的公告版本逐一对应，旧版签收不代替新版确认。已查收公告的有效要求持续适用。</p>
        <p>{'本报告仅含导出人管理范围内的接收人员。' if data['scoped'] else '本报告包含该版本全部已接收人员。'}历史接收范围、阅读及确认时间保留原始记录。</p>
        <p class='muted'>{escape(stamp)}</p>"""
    spare, _ = p.insert_htmlbox(fitz.Rect(40, 100, 555, 740), html, css=css, scale_low=0.85)
    if spare < 0:
        raise ValueError("签收报告封面内容超出页面")
    body = announcement.get("body", "")
    parts = [body[i:i+500] for i in range(0, len(body), 500)]
    body_offset = 0
    while body_offset < len(parts):
        count = min(6, len(parts)-body_offset)
        while True:
            p = page("公告正文")
            html = "<p>"+"".join(escape(x).replace("\n", "<br>") for x in parts[body_offset:body_offset+count])+"</p>"
            spare, _ = p.insert_htmlbox(fitz.Rect(40, 100, 555, 765), html, css=css, scale_low=0.85)
            if spare >= 0:
                break
            document.delete_page(-1)
            if count == 1:
                raise ValueError("公告正文超出签收报告页面")
            count = max(1, count//2)
        body_offset += count
    rows, offset = data["items"], 0
    if not rows:
        p = page("接收明细")
        p.insert_htmlbox(fitz.Rect(40, 100, 555, 740), "<p>本版本尚无接收记录。</p>", css=css)
    columns = [40, 205, 290, 390, 555]
    titles = ["人员 / 层级", "景点圈", "状态 / 来源", "阅读 / 确认"]
    def detail_page():
        p = page("接收明细")
        p.draw_rect(fitz.Rect(40, 100, 555, 133), color=None, fill=(0.09, 0.25, 0.32))
        for i, title in enumerate(titles):
            p.insert_htmlbox(fitz.Rect(columns[i]+5, 105, columns[i+1]-5, 133),
                             "<b style='color:white;font-size:9pt'>"+title+"</b>", css=css)
        return p
    if rows:
        p, top = detail_page(), 133
    for index, r in enumerate(rows):
        cells = [[r["employee_name"], r["employee_no"], r["role_label"]], [r["attraction_name"]],
                 [STATUS.get(r["status"],r["status"]), r["reason"]],
                 [text(r["seen_at"]), text(r["confirmed_at"]), "截止 "+text(r["due_on"])]]
        line_counts = []
        for i, cell in enumerate(cells):
            available = columns[i+1]-columns[i]-14
            line_counts.append(sum(max(1,math.ceil(sum(9 if ord(c)>0x1100 else 5 for c in str(value))/available)) for value in cell))
        height = max(62, max(line_counts)*15+18)
        if top+height > 765:
            p, top = detail_page(), 133
        if index % 2 == 0:
            p.draw_rect(fitz.Rect(40, top, 555, top+height), color=None, fill=(0.96,0.98,0.98))
        for i, cell in enumerate(cells):
            html = "<div style='font-size:9pt;line-height:1.4'>"+"<br>".join(escape(str(value)) for value in cell)+"</div>"
            spare, _ = p.insert_htmlbox(fitz.Rect(columns[i]+5, top+7, columns[i+1]-5, top+height-5), html, css=css, scale_low=0.85)
            if spare < 0:
                raise ValueError("人员记录超出签收报告页面")
        p.draw_line((40, top+height), (555, top+height), color=(0.85,0.9,0.91), width=0.5)
        top += height
    signed = [r for r in rows if r["signature_url"]]
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
