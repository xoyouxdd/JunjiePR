"""Downloadable snapshot of the single warehouse's current inventory."""
from collections import defaultdict
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.drawing.image import Image
from openpyxl.styles import Font
from sqlalchemy.orm import Session

from app.excel_export_utils import style_sheet
from app.warehouse.models import WarehouseBalance, WarehouseIssue, WarehouseIssueLine, WarehouseItem, WarehousePhoto, WarehousePosition
from app.warehouse.schemas import EXCEL_ITEM_COLUMNS


def _append(sheet, values):
    sheet.append([ILLEGAL_CHARACTERS_RE.sub("", value) if isinstance(value, str) else value for value in values])


def inventory_workbook(db: Session, snapshot_time: str) -> BytesIO:
    # Read all quantities and their associated photos from the same SQLite snapshot.
    with db.begin_nested():
        items = db.query(WarehouseItem).order_by(WarehouseItem.name, WarehouseItem.spec, WarehouseItem.id).all()
        counts = {(row.item_id, row.position_id): row.quantity for row in db.query(WarehouseBalance)}
        issues = set(db.query(WarehouseIssueLine.item_id, WarehouseIssueLine.position_id)
                     .join(WarehouseIssue).filter(WarehouseIssue.status != "closed").distinct().all())
        positions = db.query(WarehousePosition, WarehousePhoto, WarehousePhoto.content).outerjoin(
            WarehousePhoto, WarehousePhoto.id == WarehousePosition.latest_photo_id,
        ).order_by(WarehousePosition.shelf, WarehousePosition.level.desc()).all()

    by_position = defaultdict(list)
    positions_by_item = defaultdict(set)
    for item_id, position_id in counts.keys() | issues:
        positions_by_item[item_id].add(position_id)
    unplaced = []
    for item in items:
        item_positions = positions_by_item[item.id]
        if not item_positions:
            unplaced.append(item)
        for position_id in item_positions:
            key = (item.id, position_id)
            status = "待核对" if key in issues else "已停用" if not item.active else None
            by_position[position_id].append((item.name, item.spec, counts.get(key, 0), item.unit, status))

    book = Workbook()
    sheet = book.active
    sheet.title = "库存现场表"
    _append(sheet, (*EXCEL_ITEM_COLUMNS, "账面数量", "单位", "库存备注", "货位现场照", "照片说明"))
    image_heights = {}
    for position, photo, content in positions:
        inventory = by_position[position.id]
        first_row = sheet.max_row + 1
        note = "暂无现场照片"
        if photo is not None:
            moment = photo.captured_at.strftime("%Y-%m-%d %H:%M:%S")
            note = f"{photo.owner_name}\n{moment}"
            if position.latest_photo_version != position.version:
                note += "\n拍照后库存有变动"
        for index, (name, spec, quantity, unit, status) in enumerate(inventory or [(None, None, None, None, "无库存记录")]):
            _append(sheet, (name, spec, position.label, quantity, unit, status, None, note if index == 0 else None))
        if photo is not None:
            image = Image(BytesIO(content))
            scale = min(250 / image.width, 190 / image.height, 1)
            image.width, image.height = round(image.width * scale), round(image.height * scale)
            sheet.add_image(image, f"G{first_row}")
            image_heights[first_row] = image.height * 0.75 + 6
    for item in unplaced:
        _append(sheet, (item.name, item.spec, "未分配货位", 0, item.unit, "已停用" if not item.active else None))

    # Keep identifying text intact and store formula-like names or specs as literal strings.
    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    style_sheet(sheet, landscape=True)
    sheet.auto_filter.ref = None  # Keep each photo with the following rows for its location.
    sheet.column_dimensions["G"].width = 39
    sheet.column_dimensions["H"].width = max(25, sheet.column_dimensions["H"].width)
    for row, height in image_heights.items():
        sheet.row_dimensions[row].height = max(sheet.row_dimensions[row].height, height)
    sheet.append([])
    sheet.merge_cells(start_row=sheet.max_row + 1, start_column=1, end_row=sheet.max_row + 1, end_column=8)
    footer = sheet.cell(sheet.max_row, 1, f"库存快照时间（北京时间）  {snapshot_time}")
    footer.font = Font(name="Microsoft YaHei", color="52647B")
    output = BytesIO()
    book.save(output)
    output.seek(0)
    return output
