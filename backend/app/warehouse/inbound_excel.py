"""Single-sheet Excel input for an inbound draft. Only confirmation posts stock."""
from io import BytesIO
import re
from uuid import uuid4
from zipfile import ZipFile

from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy.orm import Session

from app.excel_export_utils import LABEL_FILL, TITLE_FILL, TITLE_FONT, configure_sheet
from app.warehouse.models import GROUND_SHELF, WarehouseItem, WarehousePosition
from app.warehouse.schemas import EXCEL_ITEM_COLUMNS, INBOUND_BATCH_KEY, INBOUND_BATCH_PREFIX, SingleInboundInput
from app.warehouse.service import (
    RECEIVING_UNITS, get_item, get_position, receiving_item_candidates, receiving_item_index, receiving_name_and_unit,
    require_new_inbound_batch, warehouse_item_key,
)


SHEET_NAME = "批量入库"
HEADERS = (*EXCEL_ITEM_COLUMNS, "入库数量")
LEGACY_HEADERS = ("物资名称", "规格", "货位", "入库数量")
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
    sheet.merge_cells("B1:C1")
    sheet.append(("每行填一个物资货位。新物资确认入库时自动建档，数量为 0 的行跳过。新到货请重新下载模板。",))
    sheet.merge_cells("A2:C2")
    sheet.append(HEADERS)
    for column, width in zip("ABC", (34, 30, 21)):
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
    for row in sheet.iter_rows(min_row=FIRST_DATA_ROW, max_row=FIRST_DATA_ROW + MAX_ROWS - 1, max_col=2):
        for cell in row:
            cell.number_format = "@"
    positions = db.query(WarehousePosition).order_by(WarehousePosition.shelf, WarehousePosition.level).all()
    choices = ",".join(position_code(position) for position in positions)
    validations = {
        "B": DataValidation(type="list", formula1=f'"{choices}"', showErrorMessage=True,
                            error="请选择模板所列货位。"),
        "C": DataValidation(type="whole", operator="between", formula1=0, formula2=MAX_QUANTITY,
                            showErrorMessage=True, error="请输入 0 至 1000000 之间的整数。"),
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
        if 0 <= value <= MAX_QUANTITY and value == int(value):
            return int(value)
    elif isinstance(value, str):
        digits = value.strip()
        if len(digits) <= 7 and digits.isdecimal() and int(digits) <= MAX_QUANTITY:
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
        rows = sheet.iter_rows(max_col=len(LEGACY_HEADERS))
        metadata = next(rows, ())
        client_id = metadata[1].value if len(metadata) > 1 else None
        if not metadata or metadata[0].value != BATCH_LABEL or not isinstance(client_id, str) or not INBOUND_BATCH_KEY.fullmatch(client_id):
            raise HTTPException(400, "模板批次编号缺失或已改动，请重新下载模板。")
        next(rows, ())  # The instructions occupy row 2.
        header = tuple(cell.value for cell in next(rows, ()))
        legacy = header == LEGACY_HEADERS
        if not legacy and header != (*HEADERS, None):
            raise HTTPException(400, "表头与入库模板不一致，请保留下载模板的列标题。")
        require_new_inbound_batch(db, client_id)
        items, skipped = parse_rows(db, rows, legacy)
        return {"client_id": client_id, "items": items, "skipped_zero": skipped}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, "读取表格内容失败，请重新保存 .xlsx 后再导入。") from exc
    finally:
        book.close()


def inbound_item(candidates: list[WarehouseItem], name: str, spec: str = "") -> dict:
    if not name or len(name) > 100 or len(spec) > 100:
        raise ValueError("请填写不超过 100 字的物资名称，旧表规格最多 100 字。")
    named_unit = next((value for value in reversed(re.findall(r"[（(]([^()（）]+)[）)]", name)) if value in RECEIVING_UNITS), "")
    unit = receiving_name_and_unit(name)[1] or named_unit or (spec if spec in RECEIVING_UNITS else "")
    active = [row for row in candidates if row.active]
    if spec:
        matches = [row for row in active if row.spec_key == warehouse_item_key(spec)]
        if not matches and spec in RECEIVING_UNITS:
            matches = [row for row in active if row.unit == unit]
        active = matches
    if unit and active:
        active = [row for row in active if row.unit == unit]
    if len(active) == 1:
        return {"item_id": active[0].id, "expected_unit": active[0].unit}
    if candidates:
        raise ValueError("同名档案有多个、已停用或单位不符，请在入库页搜索并选择准确档案。")
    return {"item_id": -1, "expected_unit": unit or "件",
            "new_item": {"name": name, "unit": unit or "件", "spec": "" if spec in RECEIVING_UNITS else spec}}


def single_inbound_preview(db: Session, body: SingleInboundInput) -> dict:
    get_position(db, body.position_id)
    name_key = warehouse_item_key(body.name)
    if body.item_id is not None:
        item = get_item(db, body.item_id, active=True)
        if name_key != item.name_key:
            raise HTTPException(409, "物资名称刚有变动，请重新选择档案。")
        row = {"item_id": item.id, "expected_unit": item.unit}
    else:
        catalog = receiving_item_index(db)
        try:
            row = inbound_item(receiving_item_candidates(catalog, body.name), body.name)
            if "new_item" in row and not body.confirm_new:
                require_full_receiving_name(catalog, name_key)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {**row, "received": body.quantity, "locations": [{"position_id": body.position_id, "quantity": body.quantity}]}


def require_full_receiving_name(catalog: dict[str, list[WarehouseItem]], name_key: str) -> None:
    if any(name_key in alias and name_key != alias and any(item.active for item in items)
           for alias, items in catalog.items()):
        raise ValueError("名称像已有物资的关键词，请选用已有档案或填写完整名称。")


def parse_rows(db: Session, rows, legacy: bool) -> tuple[list[dict], int]:
    by_name = receiving_item_index(db)
    locations = {}
    for place in db.query(WarehousePosition):
        for alias in (position_code(place), place.label):
            locations[warehouse_item_key(alias).replace(" ", "")] = place.id

    grouped = {}
    proposals = {}
    seen = {}
    errors = []
    skipped = 0
    for line, cells in enumerate(rows, start=FIRST_DATA_ROW):
        if line >= FIRST_DATA_ROW + MAX_ROWS:
            raise HTTPException(400, f"一份模板最多填 {MAX_ROWS} 行，请分批导入。")
        values = [cell.value for cell in cells]
        if all(value is None or isinstance(value, str) and not value.strip() for value in values):
            continue
        name = value_text(values[0])
        spec = value_text(values[1]) if legacy else ""
        location, raw_amount = values[2:] if legacy else values[1:3]
        position_id = locations.get(warehouse_item_key(value_text(location)).replace(" ", ""))
        amount = inbound_quantity(raw_amount)
        message = ""
        if any(cell.data_type in {"f", "e"} for cell in cells):
            message = "请粘贴为数值或文本，公式和错误值不能导入。"
        elif not legacy and value_text(values[3]):
            message = "新版模板只填前三列，请把数量放在第 C 列。"
        elif amount == 0:
            skipped += 1
            continue
        elif not name:
            message = "请填写物资名称。"
        elif position_id is None:
            message = "货位无效，请填写模板下拉中的货位。"
        elif amount is None:
            message = "入库数量须为 0 至 1000000 的整数。"
        else:
            try:
                name_key = warehouse_item_key(name)
                item = inbound_item(receiving_item_candidates(by_name, name), name, spec)
                if "new_item" in item:
                    require_full_receiving_name(by_name, name_key)
            except ValueError as exc:
                errors.append({"row": line, "message": str(exc)})
                continue
            if "new_item" in item:
                proposal = item["new_item"]
                proposed_key, _ = receiving_name_and_unit(name)
                prior = proposals.get(proposed_key)
                if prior and (prior["new_item"]["unit"], prior["new_item"]["spec"]) != (proposal["unit"], proposal["spec"]):
                    errors.append({"row": line, "message": "同一新物资的单位或规格不一致，请统一后再导入。"})
                    continue
                item = proposals.setdefault(proposed_key, {**item, "item_id": -len(proposals) - 1})
            item_id = item["item_id"]
            key = (item_id, position_id)
            group = grouped.setdefault(item_id, {**item, "received": 0, "locations": []})
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
        raise HTTPException(400, "模板里没有数量大于 0 的入库明细。")
    return list(grouped.values()), skipped
