"""Excel template and input validation for batch POC recognition."""
from dataclasses import dataclass
from io import BytesIO
from zipfile import ZipFile

from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from app.employee_numbers import employee_number_cell


POC_IMPORT_LIMIT = 100
POC_FILE_LIMIT = 1024 * 1024
SHEET_NAME = "特别贡献认可"
HEADERS = ("姓名", "员工号", "认可原因", "认可周期", "POC分值")
PERIOD_TYPES = {"月度": "month", "季度": "quarter"}
POINT_OPTIONS = ("1", "2", "3", "4", "5")


@dataclass(frozen=True)
class PocWorkbookRow:
    line: int
    name: str
    employee_no: str
    reason: str
    period_type: str
    points: int


def poc_template() -> bytes:
    book = Workbook()
    data = book.active
    data.title = SHEET_NAME
    data.append(HEADERS)
    data.freeze_panes = "A2"
    for cell in data[1]:
        cell.fill = PatternFill(fill_type="solid", fgColor="146B58")
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(vertical="center")
    data.row_dimensions[1].height = 26
    for column, width in zip("ABCDE", (20, 18, 62, 18, 16)):
        data.column_dimensions[column].width = width
    for index in range(2, POC_IMPORT_LIMIT + 2):
        data[f"B{index}"].number_format = "@"
    periods = DataValidation(type="list", formula1='"月度,季度"', allow_blank=False, showErrorMessage=True,
                             errorTitle="认可周期", error="请从下拉列表选择月度或季度。")
    data.add_data_validation(periods)
    periods.add(f"D2:D{POC_IMPORT_LIMIT + 1}")
    scores = DataValidation(type="list", formula1='"' + ",".join(POINT_OPTIONS) + '"', allow_blank=False,
                            showErrorMessage=True, errorTitle="POC分值", error="请从下拉列表选择 1–5 分。")
    data.add_data_validation(scores)
    scores.add(f"E2:E{POC_IMPORT_LIMIT + 1}")
    notes = book.create_sheet("填写说明")
    notes.column_dimensions["A"].width = 76
    notes.append(["按工作台显示的员工姓名和员工号填写，员工号列已设为文本。"])
    notes.append(["每行填写一份认可，原因最多 100 字。周期和 POC 分值均从本行下拉选择。"])
    notes.append([f"最多填写 {POC_IMPORT_LIMIT} 行，保存 .xlsx 后在工作台导入原文件。"])
    notes.append(["认可日期在工作台选择。导入后核对收件人和每行分值，确认才会发信。"])
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def poc_row_errors(errors: list[dict]) -> None:
    if errors:
        raise HTTPException(400, {"message": "表格中有需要核对的内容，请修改原文件后重新导入。", "rows": errors})


def read_poc_workbook(content: bytes) -> list[PocWorkbookRow]:
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 100 or sum(item.file_size for item in entries) > 8 * POC_FILE_LIMIT:
                raise ValueError("Workbook exceeds import size")
        book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    except Exception as exc:
        raise HTTPException(400, "无法读取文件，请使用下载的 .xlsx 模板。") from exc

    rows = []
    errors = []
    try:
        if SHEET_NAME not in book.sheetnames:
            raise HTTPException(400, "找不到「特别贡献认可」工作表，请使用下载的模板。")
        data = book[SHEET_NAME]
        data.reset_dimensions()
        sheet_rows = data.iter_rows(max_col=len(HEADERS))
        if tuple(cell.value for cell in next(sheet_rows, ())) != HEADERS:
            raise HTTPException(400, "请重新下载带「POC分值」的最新模板并保留原表头。")
        for line, cells in enumerate(sheet_rows, start=2):
            if line > POC_IMPORT_LIMIT + 1:
                raise HTTPException(400, f"请在模板前 {POC_IMPORT_LIMIT} 个数据行内填写。")
            values = [cell.value for cell in cells]
            if all(value is None or (isinstance(value, str) and not value.strip()) for value in values):
                continue
            name, employee_no, reason, period = (
                str(values[0] or "").strip(), employee_number_cell(values[1]),
                str(values[2] or "").strip(), str(values[3] or "").strip(),
            )
            raw_points = values[4]
            points = str(int(raw_points)) if isinstance(raw_points, float) and raw_points.is_integer() else str(raw_points or "").strip()
            if any(cell.data_type in {"f", "e"} for cell in cells):
                message = "请填写可读的文本，表格公式和错误值不能导入。"
            elif not name or len(name) > 100 or not employee_no or len(employee_no) > 50:
                message = "请填写有效的姓名和员工号。"
            elif not reason or len(reason) > 100:
                message = "认可原因必填且最多 100 字。"
            elif period not in PERIOD_TYPES:
                message = "认可周期请选择月度或季度。"
            elif points not in POINT_OPTIONS:
                message = "请选择 1–5 分的 POC 分值。"
            else:
                rows.append(PocWorkbookRow(line, name, employee_no, reason, PERIOD_TYPES[period], int(points)))
                continue
            errors.append({"row": line, "message": message})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, "读取表格内容失败，请重新保存 .xlsx 后再导入。") from exc
    finally:
        book.close()
    poc_row_errors(errors)
    if not rows:
        raise HTTPException(400, "模板里还没有填写认可记录。")
    return rows
