"""Single-sheet Excel input for an inbound draft. Only confirmation posts stock."""
from collections import defaultdict
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy.orm import Session

from app.excel_export_utils import LABEL_FILL, TITLE_FILL, TITLE_FONT, configure_sheet
from app.warehouse.models import GROUND_SHELF, WarehouseItem, WarehousePosition
from app.warehouse.schemas import EXCEL_ITEM_COLUMNS, INBOUND_BATCH_KEY, INBOUND_BATCH_PREFIX
from app.warehouse.service import require_new_inbound_batch, warehouse_item_key


SHEET_NAME = "批量入库"
HEADERS = (*EXCEL_ITEM_COLUMNS, "入库数量")
BATCH_LABEL = "批次编号"
FIRST_DATA_ROW = 4
MAX_ROWS = 250
MAX_QUANTITY = 1_000_000
MAX_FILE_BYTES = 1024 * 1024


def position_code(position: WarehousePosition) -> str:
    return position.label if position.shelf == GROUND_SHELF else f"{position.shelf}{position.level}"


def inbound_template(db: Session) -> BytesIO:
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_NAME
    sheet.append((BATCH_LABEL, INBOUND_BATCH_PREFIX + uuid4().hex))
    sheet.merge_cells("B1:D1")
    sheet.append(("每行填一个物资货位。数量填本次上架量。同一批次沿用此文件，新到货重新下载。",))
    sheet.merge_cells("A2:D2")
    sheet.append(HEADERS)
    for column, width in zip("ABCD", (29, 24, 22, 15)):
        sheet.column_dimensions[column].width = width
    for cell in sheet[1]:
        cell.fill = LABEL_FILL
    sheet["A2"].alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[1].height = 22
    sheet.row_dimensions[2].height = 28
    for cell in sheet[3]:
        cell.fill = TITLE_FILL
        cell.font = TITLE_FONT
        cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[3].height = 24
    for row in sheet.iter_rows(min_row=FIRST_DATA_ROW, max_row=FIRST_DATA_ROW + MAX_ROWS - 1, max_col=3):
        for cell in row:
            cell.number_format = "@"
    positions = db.query(WarehousePosition).order_by(WarehousePosition.shelf, WarehousePosition.level).all()
    choices = ",".join(position_code(position) for position in positions)
    validations = {
        "C": DataValidation(type="list", formula1=f'"{choices}"', showErrorMessage=True,
                            error="请选择模板所列货位。"),
        "D": DataValidation(type="whole", operator="between", formula1=1, formula2=MAX_QUANTITY,
                            showErrorMessage=True, error="请输入 1 至 1000000 之间的整数。"),
    }
    for column, validation in validations.items():
        sheet.add_data_validation(validation)
        validation.add(f"{column}{FIRST_DATA_ROW}:{column}{FIRST_DATA_ROW + MAX_ROWS - 1}")
    configure_sheet(sheet, freeze="A4", landscape=True)
    sheet.print_title_rows = "1:3"
    output = BytesIO()
    book.save(output)
    output.seek(0)
    return output


def value_text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    return str(value) if type(value) in {int, float} else ""


def inbound_quantity(value) -> int | None:
    if type(value) in {int, float}:
        if 0 < value <= MAX_QUANTITY and value == int(value):
            return int(value)
    elif isinstance(value, str):
        digits = value.strip()
        if len(digits) <= 7 and digits.isdecimal() and 0 < int(digits) <= MAX_QUANTITY:
            return int(digits)
    return None


def read_inbound_workbook(db: Session, content: bytes) -> dict:
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, "Excel 文件须小于 1 MB，请分批导入。")
    try:
        with ZipFile(BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > 100 or sum(member.file_size for member in members) > 8 * MAX_FILE_BYTES:
                raise ValueError("Workbook exceeds import size")
            for member in members:
                if member.filename.lower().endswith((".xml", ".rels")):
                    # Excel does not need DTDs. Removing NUL bytes also detects UTF-16/32 declarations.
                    if b"<!DOCTYPE" in archive.read(member).replace(b"\x00", b""):
                        raise ValueError("Workbook contains an XML document type")
        book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    except Exception as exc:
        raise HTTPException(400, "无法读取文件，请使用下载的 .xlsx 入库模板。") from exc

    try:
        if SHEET_NAME not in book.sheetnames:
            raise HTTPException(400, "找不到「批量入库」工作表，请使用下载的模板。")
        sheet = book[SHEET_NAME]
        sheet.reset_dimensions()
        rows = sheet.iter_rows(max_col=len(HEADERS))
        metadata = next(rows, ())
        client_id = metadata[1].value if len(metadata) > 1 else None
        if not metadata or metadata[0].value != BATCH_LABEL or not isinstance(client_id, str) or not INBOUND_BATCH_KEY.fullmatch(client_id):
            raise HTTPException(400, "模板批次编号缺失或已改动，请重新下载模板。")
        next(rows, ())  # The instructions occupy row 2.
        if tuple(cell.value for cell in next(rows, ())) != HEADERS:
            raise HTTPException(400, "表头与入库模板不一致，请保留原来的四列标题。")
        require_new_inbound_batch(db, client_id)
        return {"client_id": client_id, "items": parse_rows(db, rows)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, "读取表格内容失败，请重新保存 .xlsx 后再导入。") from exc
    finally:
        book.close()


def parse_rows(db: Session, rows) -> list[dict]:
    by_name = defaultdict(list)
    for item in db.query(WarehouseItem).filter_by(active=True):
        by_name[item.name_key].append(item)
    locations = {}
    for place in db.query(WarehousePosition):
        for alias in (position_code(place), place.label):
            locations[warehouse_item_key(alias).replace(" ", "")] = place.id

    grouped = {}
    seen = {}
    errors = []
    for line, cells in enumerate(rows, start=FIRST_DATA_ROW):
        if line >= FIRST_DATA_ROW + MAX_ROWS:
            raise HTTPException(400, f"一份模板最多填 {MAX_ROWS} 行，请分批导入。")
        values = [cell.value for cell in cells]
        if all(value is None or isinstance(value, str) and not value.strip() for value in values):
            continue
        name, spec, location = (warehouse_item_key(value_text(value)) for value in values[:3])
        candidates = by_name.get(name, [])
        item = next((found for found in candidates if found.spec_key == spec), None)
        if item is None and not spec and len(candidates) == 1:
            item = candidates[0]
        position_id = locations.get(location.replace(" ", ""))
        amount = inbound_quantity(values[3])
        message = ""
        if any(cell.data_type in {"f", "e"} for cell in cells):
            message = "请粘贴为数值或文本，公式和错误值不能导入。"
        elif not name:
            message = "请填写物资名称。"
        elif item is None:
            message = "规格与在用档案不匹配，请核对规格。" if candidates else "找不到同名在用物资，请核对档案名称。"
        elif position_id is None:
            message = "货位无效，请填写模板下拉中的货位。"
        elif amount is None:
            message = "入库数量须为 1 至 1000000 的整数。"
        else:
            key = (item.id, position_id)
            group = grouped.setdefault(item.id, {"item_id": item.id, "received": 0, "locations": []})
            if key in seen:
                message = f"同一物资和货位已在第 {seen[key]} 行填写，请合并数量。"
            elif group["received"] + amount > MAX_QUANTITY:
                message = "这一物资在本单的入库数量合计超过 1000000。"
            else:
                seen[key] = line
                group["received"] += amount
                group["locations"].append({"position_id": position_id, "quantity": amount})
        if message:
            errors.append({"row": line, "message": message})
    if errors:
        raise HTTPException(400, {"message": "请按下方行号修改 Excel，再导入同一份文件。", "rows": errors})
    if not grouped:
        raise HTTPException(400, "模板里还没有填写入库明细。")
    return list(grouped.values())
