from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database.connection import get_db, write_transaction
from app.employee_numbers import employee_number
from app.excel_export_utils import content_disposition
from app.services.attachments import run_image_work
from app.services.audit import write_audit
from app.v2_auth import V2User, current_user
from app.warehouse.access import (
    active_owner_ids, can_assign, can_be_manager, can_dispatch, eligible_manager, find_candidates, is_manager, manager_people,
    person_data, require_access, require_assign, require_dispatch, require_manager,
)
from app.warehouse.media import jpeg_bytes, stage_photo
from app.warehouse.photo_tasks import PHOTO_REPORT_ACTION, open_photo_tasks, photo_task_list, release_owner_photo_tasks, request_location_photo
from app.warehouse.export import inventory_workbook
from app.warehouse.inbound_excel import MAX_FILE_BYTES, inbound_template, read_inbound_workbook, single_inbound_preview
from app.warehouse.models import (
    WarehouseIssue, WarehouseItem, WarehouseOwner, WarehousePhoto, WarehousePosition, WarehousePositionPhotoReuse,
    WarehousePositionPhotoTask, WarehouseTransaction, WarehouseTransactionPhoto, warehouse_now,
)
from app.warehouse.schemas import AdjustmentInput, ArchiveItemInput, CalibrationInput, CorrectionInput, IssueInput, ItemInput, LocationMoveInput, LocationPhotoInput, LocationPhotoReportInput, LocationPhotoTaskInput, ManagerInput, PhotoId, ResolveInput, SingleInboundInput, TransactionInput
from app.warehouse.service import (
    apply_adjustment, balances_by_item, calibrate_position, confirm_transaction, correct_transaction, get_item, get_position, get_quantity,
    get_transaction_by_client, issue_data, item_data, latest_receipt_photos, mark_posted, move_stock, new_transaction, open_issue, photo_data,
    position_data, resolve_issue, save_current_location_photo, staged_photo, stamp, transaction_data, update_latest_photo,
)


router = APIRouter(prefix="/warehouse", tags=["warehouse"])
DB = Annotated[Session, Depends(get_db)]
User = Annotated[V2User, Depends(current_user)]
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sync_inventory_alerts(db: Session, items: set[int]) -> None:
    from app.warehouse.assessment import sync_alerts

    if items:
        db.flush()
        sync_alerts(db, items)


@router.get("/bootstrap")
def bootstrap(db: DB, user: User):
    from app.warehouse.assessment import WarehouseThreshold, stock_level

    require_access(user)
    # Keep each position's balance, physical photo and pending state in the same read snapshot.
    with db.begin_nested():
        manager = is_manager(db, user)
        locations = balances_by_item(db)
        catalog = db.query(WarehouseItem).order_by(WarehouseItem.category, WarehouseItem.name, WarehouseItem.spec)
        items = catalog.all() if manager else catalog.filter(WarehouseItem.active.is_(True)).all()
        active_ids = {item.id for item in items if item.active}
        recent_photos = latest_receipt_photos(db, active_ids)
        settings = {row.item_id: row for row in db.query(WarehouseThreshold).filter(WarehouseThreshold.item_id.in_(active_ids))}
        item_rows = []
        for item in items:
            data = item_data(item, locations.get(item.id, []))
            rule = settings.get(item.id)
            data.update(
                stock_level=stock_level(data["total"], rule) if item.active and item.opened_at else "unset",
                minimum=rule.minimum if rule is not None else None,
                receipt_photo=recent_photos.get(item.id),
            )
            item_rows.append(data)
        pending_photos = {task.position_id for task in open_photo_tasks(db)}
        return {
            "operator": {"id": user.id, "name": user.name},
            "permissions": {"manage_assignments": can_assign(user), "manage_warehouse": manager, "dispatch": can_dispatch(user)},
            "photo_task_count": len(pending_photos) if can_dispatch(user) else 0,
            "now": stamp(warehouse_now()),
            "managers": person_data(db, manager_people(db)),
            "positions": [{**position_data(db, row), "needs_photo": row.id in pending_photos} for row in db.query(WarehousePosition).order_by(
                WarehousePosition.shelf, WarehousePosition.level.desc())],
            "items": item_rows,
        }


@router.get("/inventory/export.xlsx")
def export_inventory(db: DB, user: User):
    require_manager(db, user)
    now = warehouse_now()
    suffix = now.strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        inventory_workbook(db, now.strftime("%Y-%m-%d %H:%M:%S")),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": content_disposition(f"warehouse_inventory_{suffix}.xlsx", f"物资库存_{suffix}.xlsx"),
                 "Cache-Control": "private, no-store"},
    )


@router.get("/inbound/template.xlsx")
def download_inbound_template(db: DB, user: User):
    require_manager(db, user)
    return StreamingResponse(
        inbound_template(db), media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": content_disposition("warehouse_inbound_template.xlsx", "物资批量入库模板.xlsx"),
                 "Cache-Control": "private, no-store"},
    )


@router.post("/inbound/import")
def preview_inbound_import(db: DB, user: User, file: Annotated[UploadFile, File()]):
    require_manager(db, user)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "请选择 .xlsx 入库模板。")
    return read_inbound_workbook(db, file.file.read(MAX_FILE_BYTES + 1))


@router.post("/inbound/single")
def preview_single_inbound(body: SingleInboundInput, db: DB, user: User):
    require_manager(db, user)
    return single_inbound_preview(db, body)


@router.get("/manager-candidates")
def manager_candidates(db: DB, user: User, q: Annotated[str, Query(max_length=100)] = ""):
    require_assign(user)
    return {"items": find_candidates(db, q)}


@router.post("/managers")
def add_manager(body: ManagerInput, db: DB, user: User):
    with write_transaction(db):
        require_assign(user)
        if not can_be_manager(db, body.employee_id):
            raise HTTPException(422, "仅可安排在职且账号有效的 TALEAD、LEAD 及以上人员")
        if db.get(WarehouseOwner, body.employee_id) is None:
            db.add(WarehouseOwner(employee_id=body.employee_id, assigned_by_id=user.id))
            write_audit(db, user.employee, "安排仓库负责人", "warehouse_owner", body.employee_id)
        db.commit()
    return {"managers": person_data(db, manager_people(db))}


@router.delete("/managers/{employee_id}")
def remove_manager(employee_id: int, db: DB, user: User):
    from app.warehouse.assessment import release_owner_alerts

    with write_transaction(db):
        require_assign(user)
        row = db.get(WarehouseOwner, employee_id)
        if row is not None:
            active = active_owner_ids(db)
            eligible = active - {employee_id}
            if active == {employee_id}:
                raise HTTPException(409, "这是最后一位有效的仓库负责人，请先安排接任人再撤下")
            db.delete(row)
            for issue in db.query(WarehouseIssue).filter_by(status="claimed", assignee_id=employee_id):
                issue.status = "open"
                issue.assignee_id = None
            release_owner_alerts(db, employee_id)
            if eligible:
                release_owner_photo_tasks(db, employee_id, min(eligible))
            write_audit(db, user.employee, "撤下仓库负责人", "warehouse_owner", employee_id)
        db.commit()
    return {"managers": person_data(db, manager_people(db))}


@router.post("/items")
def create_item(body: ItemInput, db: DB, user: User):
    from app.warehouse.service import save_item

    with write_transaction(db):
        require_manager(db, user)
        item = save_item(db, body)
        write_audit(db, user.employee, "新增仓库物资", "warehouse_item", item.id, after=body.model_dump())
        db.commit()
    return item_data(item, [])


@router.put("/items/{item_id}")
def edit_item(item_id: int, body: ItemInput, db: DB, user: User):
    from app.warehouse.service import save_item

    with write_transaction(db):
        require_manager(db, user)
        previous = get_item(db, item_id)
        if previous.active and not body.active:
            raise HTTPException(409, "请使用归档操作并确认本人的员工号")
        before = item_data(previous, [])
        update = body.model_copy(update={"active": previous.active})
        item = save_item(db, update, previous)
        write_audit(db, user.employee, "更新仓库物资", "warehouse_item", item.id, before=before, after=update.model_dump())
        sync_inventory_alerts(db, {item_id})
        db.commit()
    return item_data(item, balances_by_item(db).get(item_id, []))


@router.post("/items/{item_id}/archive")
def archive_item(item_id: int, body: ArchiveItemInput, db: DB, user: User):
    from app.warehouse.service import save_item

    with write_transaction(db):
        require_manager(db, user)
        if employee_number(body.employee_no).casefold() != employee_number(user.employee.employee_no).casefold():
            raise HTTPException(422, "请输入当前登录账号本人的员工号")
        item = get_item(db, item_id)
        if item.active:
            before = item_data(item, [])
            update = ItemInput(name=item.name, spec=item.spec, category=item.category, unit=item.unit,
                               aliases=item.aliases, active=False)
            save_item(db, update, item)
            write_audit(db, user.employee, "归档仓库物资", "warehouse_item", item.id, before=before, after=item_data(item, []))
            sync_inventory_alerts(db, {item.id})
        db.commit()
    return item_data(item, balances_by_item(db).get(item_id, []))


@router.post("/items/{item_id}/restore")
def restore_item(item_id: int, db: DB, user: User):
    with write_transaction(db):
        require_manager(db, user)
        item = get_item(db, item_id)
        if not item.active:
            item.active = True
            write_audit(db, user.employee, "恢复仓库物资", "warehouse_item", item.id, after=item_data(item, []))
            sync_inventory_alerts(db, {item.id})
        db.commit()
    return item_data(item, balances_by_item(db).get(item_id, []))


@router.post("/photos")
async def upload_photo(db: DB, user: User, file: Annotated[UploadFile, File()], captured_at: Annotated[str, Form(max_length=45)] = ""):
    await run_in_threadpool(require_dispatch, user)
    content = await run_image_work(jpeg_bytes, file)
    return await run_in_threadpool(_save_photo, db, user, content, captured_at)


def _save_photo(db: Session, user: V2User, content: bytes, captured_at: str):
    with write_transaction(db):
        require_dispatch(user)
        row = stage_photo(db, user, content, captured_at)
        db.commit()
    return photo_data(row)


@router.get("/photos/{photo_id}")
def read_photo(photo_id: PhotoId, db: DB, user: User):
    require_access(user)
    row = db.get(WarehousePhoto, photo_id)
    if row is None:
        raise HTTPException(404, "照片不存在")
    if row.owner_id != user.id:
        can_read = row.attached_at is not None and (is_manager(db, user) or db.query(
            WarehouseTransactionPhoto.photo_id,
        ).join(WarehouseTransaction).filter(WarehouseTransactionPhoto.photo_id == photo_id,
                                             WarehouseTransaction.status == "posted").first())
        if not can_read:
            raise HTTPException(404, "照片不存在")
    content = row.content
    if not content:
        raise HTTPException(410, "照片已按保留规则清理")
    return Response(content, media_type="image/jpeg", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.put("/positions/{position_id}/photo")
def replace_location_photo(position_id: int, body: LocationPhotoInput, db: DB, user: User):
    with write_transaction(db):
        require_manager(db, user)
        now = warehouse_now()
        position = get_position(db, position_id)
        reuse = db.get(WarehousePositionPhotoReuse, body.photo_id)
        if reuse is not None:
            if reuse.position_id != position_id or reuse.owner_id != user.id:
                raise HTTPException(409, "照片已在其他货位或账号的操作中使用，请重拍后上传")
            data = position_data(db, position)
            db.commit()
            return {**data, "photo_reused": True}
        previous = db.get(WarehousePhoto, position.latest_photo_id) if position.latest_photo_id else None
        same_upload = previous is not None and body.photo_id == previous.id and previous.owner_id == user.id
        photo = previous if same_upload and position.latest_photo_version == position.version else staged_photo(db, body.photo_id, user)
        if previous and position.latest_photo_version == position.version and previous.content == photo.content:
            if not same_upload:
                db.add(WarehousePositionPhotoReuse(photo_id=photo.id, position_id=position_id, owner_id=user.id))
                db.delete(photo)
            data = position_data(db, position)
            db.commit()
            return {**data, "photo_reused": True}
        tx = new_transaction(db, user, "photo", now, reason="更新货位现况照")
        mark_posted(tx, user, now)
        photo.attached_at = now
        db.add(WarehouseTransactionPhoto(transaction_id=tx.id, photo_id=photo.id, position_id=position_id))
        save_current_location_photo(db, position, photo, now, tx.id, position.updated_at)
        db.commit()
    return position_data(db, position)


@router.get("/photo-tasks")
def list_photo_tasks(db: DB, user: User):
    require_dispatch(user)
    return {"items": photo_task_list(db, include_reports=is_manager(db, user))}


@router.put("/positions/{position_id}/photo-task")
def complete_photo_task(position_id: int, body: LocationPhotoTaskInput, db: DB, user: User):
    with write_transaction(db):
        require_dispatch(user)
        position = get_position(db, position_id)
        task = db.get(WarehousePositionPhotoTask, position_id)
        if task is not None and task.closed_by_id == user.id and task.photo_id == body.photo_id:
            return position_data(db, position)
        if task is None or task.closed_at is not None:
            raise HTTPException(409, "此货位补照待办已结束，请刷新查看")
        if body.version != position.version or body.requested_at != task.requested_at.isoformat():
            raise HTTPException(409, "货位或待办已更新，请重新打开并拍摄最新现场")
        photo = staged_photo(db, body.photo_id, user)
        cutoff = max(task.requested_at, position.updated_at or task.requested_at)
        if photo.captured_at < cutoff:
            raise HTTPException(409, "照片早于该货位最新变化，请拍摄现在的货位")
        now = warehouse_now()
        tx = new_transaction(db, user, "photo", now, reason="补拍货位现况照")
        mark_posted(tx, user, now)
        photo.attached_at = now
        db.add(WarehouseTransactionPhoto(transaction_id=tx.id, photo_id=photo.id, position_id=position_id))
        update_latest_photo(db, position, photo, now, transaction_id=tx.id)
        if task.closed_at is None:
            raise HTTPException(409, "照片与当前现场照相同或早于现有记录，请重新拍摄现在的货位")
        db.commit()
    return position_data(db, position)


@router.post("/positions/{position_id}/photo-task/report")
def report_position_photo(position_id: int, body: LocationPhotoReportInput, db: DB, user: User):
    with write_transaction(db):
        require_dispatch(user)
        position = get_position(db, position_id)
        if body.photo_id != position.latest_photo_id:
            raise HTTPException(409, "货位现场照刚刚更新，请刷新后核对")
        task = db.get(WarehousePositionPhotoTask, position_id)
        now = warehouse_now()
        if task is None or task.closed_at is not None:
            old_photo = db.get(WarehousePhoto, body.photo_id)
            owners = active_owner_ids(db)
            prior_owner = old_photo.owner_id if old_photo is not None else None
            owner = user.id if user.id in owners else prior_owner if prior_owner in owners else min(owners, default=None)
            task = request_location_photo(db, position, now, owner, position.latest_photo_transaction_id)
        task.photo_id = body.photo_id
        write_audit(db, user.employee, PHOTO_REPORT_ACTION, "warehouse_position", position_id,
                    after={"photo_id": body.photo_id, "note": body.note, "reported_at": stamp(now)})
        db.commit()
    return {"ok": True}


@router.post("/moves")
def move_inventory(body: LocationMoveInput, db: DB, user: User):
    with write_transaction(db):
        require_manager(db, user)
        tx = move_stock(db, user, body, warehouse_now())
        db.commit()
    return transaction_data(db, tx)


@router.post("/transactions")
def save_transaction(body: TransactionInput, db: DB, user: User):
    with write_transaction(db):
        if body.kind == "inbound":
            require_manager(db, user)
        else:
            require_dispatch(user)
        tx, items = confirm_transaction(db, user, body, warehouse_now())
        sync_inventory_alerts(db, items)
        db.commit()
    return transaction_data(db, tx)


@router.get("/transactions")
def transactions(db: DB, user: User, limit: Annotated[int, Query(ge=1, le=300)] = 100):
    require_access(user)
    query = db.query(WarehouseTransaction)
    if not is_manager(db, user):
        query = query.filter(or_(WarehouseTransaction.status == "posted", WarehouseTransaction.actor_id == user.id))
    rows = query.order_by(WarehouseTransaction.created_at.desc(), WarehouseTransaction.id.desc()).limit(limit).all()
    return {"items": [transaction_data(db, tx, detail=False) for tx in rows]}


@router.get("/transactions/by-client/{client_id}")
def transaction_by_client(client_id: str, db: DB, user: User):
    require_access(user)
    tx = get_transaction_by_client(db, user, client_id)
    if tx is None:
        raise HTTPException(404, "该单尚未入账")
    return transaction_data(db, tx)


@router.get("/transactions/{transaction_id}")
def transaction_info(transaction_id: int, db: DB, user: User):
    require_access(user)
    tx = db.get(WarehouseTransaction, transaction_id)
    if tx is None or (tx.status != "posted" and tx.actor_id != user.id and not is_manager(db, user)):
        raise HTTPException(404, "单据不存在")
    return transaction_data(db, tx)


@router.post("/transactions/{transaction_id}/correct")
def correct_order(transaction_id: int, body: CorrectionInput, db: DB, user: User):
    with write_transaction(db):
        require_manager(db, user)
        original = db.get(WarehouseTransaction, transaction_id)
        if original is None:
            raise HTTPException(404, "原单不存在")
        tx = correct_transaction(db, user, original, body, warehouse_now())
        sync_inventory_alerts(db, {body.item_id})
        db.commit()
    return transaction_data(db, tx)


@router.post("/adjustments")
def adjust_stock(body: AdjustmentInput, db: DB, user: User):
    with write_transaction(db):
        require_manager(db, user)
        tx = apply_adjustment(db, user, body, warehouse_now())
        sync_inventory_alerts(db, {body.item_id})
        db.commit()
    return {"transaction_id": tx.id, "quantity": get_quantity(db, (body.item_id, body.position_id)),
            "version": get_position(db, body.position_id).version}


@router.post("/positions/{position_id}/calibration")
def save_calibration(position_id: int, body: CalibrationInput, db: DB, user: User):
    with write_transaction(db):
        require_manager(db, user)
        tx, items = calibrate_position(db, user, position_id, body, warehouse_now())
        if items:
            write_audit(db, user.employee, "校准货位库存", "warehouse_transaction", tx.id,
                        after={"position_id": position_id, "items": tx.payload["lines"], "photo_id": body.photo_id})
        sync_inventory_alerts(db, items)
        db.commit()
    return transaction_data(db, tx)


@router.post("/issues")
def report_issue(body: IssueInput, db: DB, user: User):
    with write_transaction(db):
        require_dispatch(user)
        get_item(db, body.item_id, active=True)
        get_position(db, body.position_id)
        photo = staged_photo(db, body.photo_id, user) if body.photo_id else None
        book = get_quantity(db, (body.item_id, body.position_id))
        actual = 0 if body.reason == "missing" and body.observed_quantity is None else body.observed_quantity
        row = open_issue(db, user, warehouse_now(), reason=body.reason, note=body.note, photo=photo,
                         lines=[dict(item_id=body.item_id, position_id=body.position_id, book_quantity=book,
                                     expected_quantity=book, observed_quantity=actual, note=body.note)])
        db.commit()
    return issue_data(db, row)


@router.get("/issues")
def issues(db: DB, user: User, limit: Annotated[int, Query(ge=1, le=300)] = 150):
    require_manager(db, user)
    rows = db.query(WarehouseIssue).order_by((WarehouseIssue.status == "closed"), WarehouseIssue.opened_at.desc(), WarehouseIssue.id.desc()).limit(limit)
    active = {row.id for row in manager_people(db)}
    result = []
    for row in rows:
        view = issue_data(db, row)
        if view["status"] == "claimed" and view["assignee_id"] not in active:
            view.update(status="open", assignee_id=None, assignee_name="")
        result.append(view)
    return {"items": result}


def editable_issue(db: Session, issue_id: int, user: V2User) -> WarehouseIssue:
    require_manager(db, user)
    row = db.get(WarehouseIssue, issue_id)
    if row is None:
        raise HTTPException(404, "待办不存在")
    return row


def require_assignee(issue: WarehouseIssue, user: V2User) -> None:
    if issue.status != "claimed" or issue.assignee_id != user.id:
        raise HTTPException(409, "请由当前接手的负责人继续处理")


@router.post("/issues/{issue_id}/claim")
def claim_issue(issue_id: int, db: DB, user: User):
    with write_transaction(db):
        row = editable_issue(db, issue_id, user)
        if row.status == "closed":
            raise HTTPException(409, "此待办已经处理")
        if row.assignee_id and row.assignee_id != user.id and eligible_manager(db, row.assignee_id):
            raise HTTPException(409, "已有其他负责人接手")
        row.status = "claimed"
        row.assignee_id = user.id
        db.commit()
    return issue_data(db, row)


@router.post("/issues/{issue_id}/transfer")
def transfer_issue(issue_id: int, body: ManagerInput, db: DB, user: User):
    with write_transaction(db):
        row = editable_issue(db, issue_id, user)
        require_assignee(row, user)
        if not eligible_manager(db, body.employee_id):
            raise HTTPException(422, "请选择有效的仓库负责人")
        if row.assignee_id != body.employee_id:
            write_audit(db, user.employee, "转交仓库核对待办", "warehouse_issue", row.id,
                        before={"assignee_id": row.assignee_id}, after={"assignee_id": body.employee_id})
            row.assignee_id = body.employee_id
        db.commit()
    return issue_data(db, row)


@router.post("/issues/{issue_id}/resolve")
def resolve_report(issue_id: int, body: ResolveInput, db: DB, user: User):
    with write_transaction(db):
        row = editable_issue(db, issue_id, user)
        if row.status == "closed" and row.closed_by_id == user.id:
            return issue_data(db, row)
        require_assignee(row, user)
        items = resolve_issue(db, user, row, body, warehouse_now())
        sync_inventory_alerts(db, items)
        db.commit()
    return issue_data(db, row)
