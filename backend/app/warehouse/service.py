from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from unicodedata import normalize
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.json_digest import canonical_json
from app.v2_auth import V2User
from app.v2_models import Employee
from app.warehouse.models import (
    GROUND_SHELF, SHELF_ZONES, WarehouseBalance, WarehouseIssue, WarehouseIssueLine, WarehouseItem, WarehouseMovement,
    WarehousePhoto, WarehousePosition, WarehouseTransaction, WarehouseTransactionPhoto, warehouse_now,
)
from app.warehouse.schemas import INBOUND_BATCH_KEY, AdjustmentInput, Attachment, CalibrationInput, CorrectionInput, ItemInput, ResolveInput, TransactionInput


def stamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def get_item(db: Session, item_id: int, *, active: bool = False) -> WarehouseItem:
    item = db.get(WarehouseItem, item_id)
    if item is None or (active and not item.active):
        raise HTTPException(404, "物资不存在或已停用")
    return item


def get_position(db: Session, position_id: int) -> WarehousePosition:
    position = db.get(WarehousePosition, position_id)
    if position is None:
        raise HTTPException(404, "货位不存在")
    return position


def get_quantity(db: Session, key: tuple[int, int]) -> int:
    balance = db.get(WarehouseBalance, key)
    return balance.quantity if balance else 0


def photo_data(photo: WarehousePhoto) -> dict:
    return {"id": photo.id, "captured_at": stamp(photo.captured_at), "updated_at": stamp(photo.uploaded_at),
            "updated_by": photo.owner_name, "url": f"/api/warehouse/photos/{photo.id}"}


def position_data(db: Session, row: WarehousePosition) -> dict:
    photo = db.get(WarehousePhoto, row.latest_photo_id) if row.latest_photo_id else None
    current = photo_data(photo) if photo else None
    if current is not None:
        current.update(updated_at=stamp(row.latest_photo_set_at), stale=row.latest_photo_version != row.version,
                       transaction_id=row.latest_photo_transaction_id)
    return {"id": row.id, "shelf": row.shelf, "level": row.level, "label": row.label,
            "kind": "ground" if row.shelf == GROUND_SHELF else "shelf", "zone": SHELF_ZONES.get(row.shelf),
            "version": row.version, "updated_at": stamp(row.updated_at), "photo": current}


def balances_by_item(db: Session) -> dict[int, list[dict]]:
    open_lines = {(item_id, position_id) for item_id, position_id in db.query(
        WarehouseIssueLine.item_id, WarehouseIssueLine.position_id,
    ).join(WarehouseIssue).filter(WarehouseIssue.status != "closed").all()}
    result = defaultdict(list)
    for balance in db.query(WarehouseBalance).order_by(WarehouseBalance.position_id).all():
        key = (balance.item_id, balance.position_id)
        result[balance.item_id].append({"position_id": balance.position_id, "quantity": balance.quantity,
                                        "reported": key in open_lines})
        open_lines.discard(key)
    for item_id, position_id in sorted(open_lines):
        result[item_id].append({"position_id": position_id, "quantity": 0, "reported": True})
    return result


def item_data(item: WarehouseItem, locations: list[dict]) -> dict:
    return {"id": item.id, "name": item.name, "spec": item.spec, "category": item.category, "unit": item.unit,
            "aliases": item.aliases, "active": item.active, "opened_at": stamp(item.opened_at),
            "total": sum(row["quantity"] for row in locations), "locations": locations}


def warehouse_item_key(value: str) -> str:
    return normalize("NFKC", " ".join(value.split())).casefold()


def save_item(db: Session, body: ItemInput, item: WarehouseItem | None = None) -> WarehouseItem:
    name_key, spec_key = warehouse_item_key(body.name), warehouse_item_key(body.spec)
    duplicate = db.query(WarehouseItem.id).filter_by(name_key=name_key, spec_key=spec_key).scalar()
    if duplicate is not None and (item is None or item.id != duplicate):
        raise HTTPException(409, "已有同名同规格的物资，请使用现有档案")
    if item is not None:
        if item.opened_at is not None and item.unit != body.unit:
            raise HTTPException(409, "已有库存记录的物资须沿用原单位；不同单位请单独建档")
        if not body.active and db.query(WarehouseBalance).filter_by(item_id=item.id).filter(WarehouseBalance.quantity > 0).first():
            raise HTTPException(409, "货位仍有余量，请核对库存后再停用物资")
        open_issue = db.query(WarehouseIssue.id).join(WarehouseIssueLine).filter(
            WarehouseIssueLine.item_id == item.id, WarehouseIssue.status != "closed",
        ).first()
        if not body.active and open_issue:
            raise HTTPException(409, "还有待核对记录，请处理后再停用物资")
    else:
        item = WarehouseItem()
        db.add(item)
    item.name, item.name_key = body.name, name_key
    item.spec, item.spec_key = body.spec, spec_key
    item.category = body.category or "其他"
    item.unit = body.unit
    item.aliases = list(dict.fromkeys(alias for alias in body.aliases if alias))
    item.active = body.active
    db.flush()
    return item


def fresh_state(db: Session, keys: set[tuple[int, int]], position_ids: set[int] | None = None) -> dict:
    ids = position_ids if position_ids is not None else {position_id for _, position_id in keys}
    return {
        "positions": [{"id": position_id, "version": get_position(db, position_id).version} for position_id in sorted(ids)],
        "balances": [{"item_id": item_id, "position_id": position_id, "quantity": get_quantity(db, (item_id, position_id))}
                     for item_id, position_id in sorted(keys) if position_id in ids],
    }


def require_versions(db: Session, versions: dict[int, int], keys: set[tuple[int, int]],
                     position_ids: set[int] | None = None) -> None:
    ids = position_ids if position_ids is not None else {position_id for _, position_id in keys}
    changed = {position_id for position_id in ids if versions.get(position_id) != get_position(db, position_id).version}
    if changed:
        raise HTTPException(409, {"code": "STALE_POSITIONS", "message": "货位已有新收发，请核对标出的层位",
                                  **fresh_state(db, keys, changed)})


def staged_photo(db: Session, photo_id: str, user: V2User) -> WarehousePhoto:
    row = db.get(WarehousePhoto, photo_id)
    if row is None or row.owner_id != user.id or row.attached_at is not None:
        raise HTTPException(409, {"code": "PHOTO_UNAVAILABLE", "message": "照片尚未上传完成或已使用，请重新上传此照片",
                                  "photo_id": photo_id})
    return row


def update_latest_photo(db: Session, position: WarehousePosition, photo: WarehousePhoto,
                        now: datetime, *, transaction_id: int | None, fresh: bool = True) -> None:
    previous = db.get(WarehousePhoto, position.latest_photo_id) if position.latest_photo_id else None
    if previous and (photo.captured_at, photo.uploaded_at, photo.id) <= (previous.captured_at, previous.uploaded_at, previous.id):
        return
    position.latest_photo_id = photo.id
    position.latest_photo_version = position.version if fresh else None
    position.latest_photo_set_at = now
    position.latest_photo_transaction_id = transaction_id


def touch_positions(db: Session, position_ids: set[int], now: datetime) -> None:
    for position_id in position_ids:
        position = get_position(db, position_id)
        position.version += 1
        position.updated_at = now


def new_transaction(db: Session, user: V2User, kind: str, now: datetime,
                    *, reason: str = "", related_id: int | None = None, transaction_input: TransactionInput | None = None) -> WarehouseTransaction:
    tx = WarehouseTransaction(kind=kind, status="pending", actor_id=user.id, actor_name=user.name,
                              client_id=transaction_input.client_id if transaction_input else uuid4().hex,
                              payload=transaction_input.model_dump(mode="json") if transaction_input else {},
                              fingerprint=fingerprint(transaction_input) if transaction_input else "",
                              reason=reason, related_id=related_id, created_at=now)
    db.add(tx)
    db.flush()
    return tx


def fingerprint(body: TransactionInput) -> str:
    payload = body.model_dump(mode="json", exclude={"position_versions", "hold_if_insufficient"})
    return sha256(canonical_json(payload, compact=True).encode("utf-8")).hexdigest()


def mark_posted(tx: WarehouseTransaction, user: V2User, now: datetime) -> None:
    tx.status = "posted"
    tx.posted_at = now
    tx.posted_by_id = user.id


def add_movement(db: Session, tx: WarehouseTransaction, item_id: int, position_id: int | None,
                 kind: str, delta: int, now: datetime, *, usage_delta: int = 0, direct_quantity: int = 0,
                 usage_at: datetime | None = None) -> WarehouseMovement:
    before = 0
    after = 0
    if position_id is not None:
        balance = db.get(WarehouseBalance, (item_id, position_id))
        before = balance.quantity if balance else 0
        after = before + delta
        if after < 0:
            raise HTTPException(409, "当前余量不足，无法完成入账")
        if balance is None:
            balance = WarehouseBalance(item_id=item_id, position_id=position_id)
            db.add(balance)
        balance.quantity = after
    item = get_item(db, item_id)
    if item.opened_at is None and kind in {"opening", "inbound", "direct", "adjustment", "receipt"}:
        item.opened_at = now
    movement = WarehouseMovement(transaction_id=tx.id, item_id=item_id, position_id=position_id,
                                 kind=kind, delta=delta, quantity_before=before, quantity_after=after,
                                 usage_delta=usage_delta, direct_quantity=direct_quantity,
                                 occurred_at=now, usage_at=usage_at)
    db.add(movement)
    db.flush()
    return movement


def tx_photos(db: Session, tx_id: int) -> list[dict]:
    pairs = db.query(WarehouseTransactionPhoto, WarehousePhoto).join(WarehousePhoto).filter(
        WarehouseTransactionPhoto.transaction_id == tx_id,
    ).order_by(WarehousePhoto.captured_at, WarehousePhoto.id).all()
    return [{**photo_data(photo), "photo_id": photo.id, "item_id": link.item_id, "position_id": link.position_id}
            for link, photo in pairs]


def transaction_data(db: Session, tx: WarehouseTransaction, *, detail: bool = True) -> dict:
    lines = tx.payload.get("lines", [])
    receipts = tx.payload.get("receipts", [])
    data = {"id": tx.id, "client_id": tx.client_id, "kind": tx.kind, "status": tx.status,
            "actor_id": tx.actor_id, "actor_name": tx.actor_name, "created_at": stamp(tx.created_at),
            "posted_at": stamp(tx.posted_at), "posted_by_id": tx.posted_by_id, "reason": tx.reason,
            "related_id": tx.related_id, "lines": lines, "receipts": receipts}
    if detail:
        keys = {(row["item_id"], row["position_id"]) for row in lines}
        movements = db.query(WarehouseMovement).filter_by(transaction_id=tx.id).order_by(WarehouseMovement.id).all()
        data.update(fresh_state(db, keys))
        data["issue_id"] = db.query(WarehouseIssue.id).filter_by(transaction_id=tx.id).scalar()
        data["photos"] = tx_photos(db, tx.id)
        data["movements"] = [{"id": row.id, "item_id": row.item_id, "position_id": row.position_id, "kind": row.kind,
                              "delta": row.delta, "quantity_before": row.quantity_before, "quantity_after": row.quantity_after,
                              "usage_delta": row.usage_delta, "direct_quantity": row.direct_quantity,
                              "occurred_at": stamp(row.occurred_at)} for row in movements]
    return data


def issue_data(db: Session, issue: WarehouseIssue) -> dict:
    tx = db.get(WarehouseTransaction, issue.transaction_id) if issue.transaction_id else None
    assignee = db.get(Employee, issue.assignee_id) if issue.assignee_id else None
    lines = db.query(WarehouseIssueLine).filter_by(issue_id=issue.id).order_by(WarehouseIssueLine.id).all()
    title = "待补记出库" if tx and tx.status == "pending" else "货位账实核对"
    return {"id": issue.id, "status": issue.status, "title": title, "reporter_id": issue.reporter_id,
            "reporter_name": issue.reporter_name, "assignee_id": issue.assignee_id,
            "assignee_name": assignee.name if assignee else "", "transaction_id": issue.transaction_id,
            "transaction_status": tx.status if tx else None, "opened_at": stamp(issue.opened_at),
            "closed_at": stamp(issue.closed_at), "reason": issue.reason, "note": issue.note,
            "resolution": issue.resolution, "photo_id": issue.photo_id,
            "lines": [{"item_id": line.item_id, "position_id": line.position_id, "book_quantity": line.book_quantity,
                       "expected_quantity": line.expected_quantity, "observed_quantity": line.observed_quantity,
                       "note": line.note} for line in lines]}


def open_issue(db: Session, user: V2User, now: datetime, *, reason: str, note: str = "", tx_id: int | None = None,
               photo: WarehousePhoto | None = None, lines: list[dict]) -> WarehouseIssue:
    issue = WarehouseIssue(reporter_id=user.id, reporter_name=user.name, transaction_id=tx_id,
                           photo_id=photo.id if photo else None, reason=reason, note=note, opened_at=now)
    db.add(issue)
    db.flush()
    db.add_all([WarehouseIssueLine(issue_id=issue.id, **line) for line in lines])
    if photo is not None and photo.attached_at is None:
        photo.attached_at = now
    db.flush()
    return issue


def validate_transaction(db: Session, body: TransactionInput) -> tuple[dict, dict]:
    quantities: dict[tuple[int, int], int] = {}
    for row in body.lines:
        key = (row.item_id, row.position_id)
        if key in quantities:
            raise HTTPException(422, "同一物资和货位只能填写一行")
        get_item(db, row.item_id, active=True)
        get_position(db, row.position_id)
        quantities[key] = row.quantity

    if body.kind == "outbound":
        if not quantities or body.receipts:
            raise HTTPException(422, "出库须填写实际取货物资、货位和数量")
        keys = [(row.item_id, row.position_id) for row in body.discrepancies]
        if len(set(keys)) != len(keys) or any(key not in quantities for key in keys):
            raise HTTPException(422, "账实差异应对应本单的物资货位")
        return quantities, {}

    if body.discrepancies or not body.receipts:
        raise HTTPException(422, "入库须填写实收明细")
    receipts = {row.item_id: row for row in body.receipts}
    if len(receipts) != len(body.receipts):
        raise HTTPException(422, "每种物资只填写一项实收数量")
    shelved = Counter()
    for (item_id, _), quantity in quantities.items():
        shelved[item_id] += quantity
    if not shelved.keys() <= receipts.keys():
        raise HTTPException(422, "上架物资须有对应实收记录")
    for item_id, receipt in receipts.items():
        get_item(db, item_id, active=True)
        if receipt.received - receipt.damaged != shelved[item_id] + receipt.direct_quantity:
            raise HTTPException(422, "完好实收量必须等于上架量与直用量之和")
        if receipt.direct_quantity and not receipt.direct_destination:
            raise HTTPException(422, "请填写到货直用的去向")
        if receipt.damaged and not receipt.note:
            raise HTTPException(422, "请注明破损或异常实收情况")
    return quantities, receipts


def transaction_photo_selection(db: Session, user: V2User, body: TransactionInput,
                                quantities: dict, receipts: dict) -> list[tuple[Attachment, WarehousePhoto]]:
    inbound = body.kind == "inbound"
    photos = body.photos
    if inbound:
        invalid = any(row.position_id is not None or row.item_id is None for row in photos)
        picked = {row.item_id for row in photos}
        required = set(receipts)
    else:
        invalid = any(row.item_id is not None or row.position_id is None for row in photos)
        picked = {row.position_id for row in photos}
        required = {position_id for _, position_id in quantities}
    if invalid or len(picked) != len(photos) or len({row.photo_id for row in photos}) != len(photos) or picked != required:
        raise HTTPException(422, {"code": "MISSING_PHOTOS", "message": "请为每种入库物资拍一张照片" if inbound else "请为每个实际取货层拍一张照片",
                                  "missing": sorted(required - picked)})
    return [(attachment, staged_photo(db, attachment.photo_id, user)) for attachment in photos]


def attach_transaction_photos(db: Session, tx: WarehouseTransaction, photos: list[tuple[Attachment, WarehousePhoto]], now: datetime) -> None:
    for link, photo in photos:
        photo.attached_at = now
        db.add(WarehouseTransactionPhoto(transaction_id=tx.id, photo_id=photo.id, position_id=link.position_id, item_id=link.item_id))
    db.flush()


def get_transaction_by_client(db: Session, user: V2User, client_id: str) -> WarehouseTransaction | None:
    return db.query(WarehouseTransaction).filter_by(actor_id=user.id, client_id=client_id).one_or_none()


def require_new_inbound_batch(db: Session, client_id: str) -> None:
    posted = db.query(WarehouseTransaction.id).filter_by(kind="inbound", client_id=client_id).first()
    if posted:
        raise HTTPException(409, {"code": "INBOUND_BATCH_POSTED", "transaction_id": posted.id,
                                  "message": f"这份 Excel 已按入库单 {posted.id} 入账。新一批到货请重新下载模板。"})


def post_outbound(db: Session, tx: WarehouseTransaction, user: V2User, now: datetime,
                  quantities: dict[tuple[int, int], int], *, adjusted_positions: set[int] | None = None) -> None:
    positions = {position_id for _, position_id in quantities}
    occurred_before = {position_id: get_position(db, position_id).updated_at for position_id in positions}
    for (item_id, position_id), quantity in quantities.items():
        add_movement(db, tx, item_id, position_id, "outbound", -quantity, now,
                     usage_delta=quantity, usage_at=tx.created_at)
    touch_positions(db, positions | (adjusted_positions or set()), now)
    mark_posted(tx, user, now)
    for link, photo in db.query(WarehouseTransactionPhoto, WarehousePhoto).join(WarehousePhoto).filter(
        WarehouseTransactionPhoto.transaction_id == tx.id,
    ):
        checkpoint = occurred_before[link.position_id]
        fresh = checkpoint is None or photo.captured_at >= checkpoint
        update_latest_photo(db, get_position(db, link.position_id), photo, now, transaction_id=tx.id, fresh=fresh)


def confirm_transaction(db: Session, user: V2User, body: TransactionInput, now: datetime) -> tuple[WarehouseTransaction, set[int]]:
    imported = body.kind == "inbound" and INBOUND_BATCH_KEY.fullmatch(body.client_id)
    prior = get_transaction_by_client(db, user, body.client_id)
    if prior is not None:
        if prior.fingerprint != fingerprint(body):
            if imported:
                require_new_inbound_batch(db, body.client_id)
            raise HTTPException(409, {"code": "TRANSACTION_KEY_REUSED", "message": "该编号已经用于另一张单，请查看原单状态"})
        return prior, set()
    if imported:
        # The transaction route holds the warehouse writer lock across this check and posting.
        require_new_inbound_batch(db, body.client_id)

    quantities, receipts = validate_transaction(db, body)
    keys = set(quantities)
    before = {key: get_quantity(db, key) for key in keys}
    insufficient = {key for key, quantity in quantities.items() if quantity > before[key]} if body.kind == "outbound" else set()
    holding = bool(insufficient and body.hold_if_insufficient)
    if body.kind == "outbound" and not holding:
        require_versions(db, body.position_versions, keys)
    if insufficient and not holding:
        raise HTTPException(409, {"code": "INSUFFICIENT_STOCK", "message": "当前库存不足，请核对实物；已取货可提交待核对",
                                  "item_ids": sorted({key[0] for key in insufficient}),
                                  **fresh_state(db, keys, {key[1] for key in insufficient})})

    photos = transaction_photo_selection(db, user, body, quantities, receipts)
    tx = new_transaction(db, user, body.kind, now, transaction_input=body)
    attach_transaction_photos(db, tx, photos, now)
    if body.kind == "inbound":
        for (item_id, position_id), quantity in quantities.items():
            add_movement(db, tx, item_id, position_id, "inbound", quantity, now)
        for item_id, receipt in receipts.items():
            if receipt.direct_quantity:
                add_movement(db, tx, item_id, None, "direct", 0, now, direct_quantity=receipt.direct_quantity, usage_at=now)
            elif not any(key[0] == item_id for key in quantities):
                add_movement(db, tx, item_id, None, "receipt", 0, now)
        touch_positions(db, {position_id for _, position_id in keys}, now)
        mark_posted(tx, user, now)
        return tx, set(receipts)

    if not holding:
        post_outbound(db, tx, user, now, quantities)
    discrepancies = {(row.item_id, row.position_id): row for row in body.discrepancies}
    reported_keys = sorted(keys if holding else discrepancies.keys())
    if reported_keys:
        condition = "货品已取走，出库待核对" if holding else "出库后实物与预计余量不符"
        first_position = reported_keys[0][1]
        first_photo = next((photo for link, photo in photos if link.position_id == first_position), None)
        issue_lines = []
        for item_id, position_id in reported_keys:
            key = (item_id, position_id)
            row = discrepancies.get(key)
            issue_lines.append(dict(item_id=item_id, position_id=position_id, book_quantity=before[key],
                                    expected_quantity=before[key] - quantities[key], observed_quantity=row.observed_quantity if row else None,
                                    note=row.note if row else ""))
        open_issue(db, user, now, reason="mismatch", note=condition, tx_id=tx.id, photo=first_photo, lines=issue_lines)
    return tx, {item_id for item_id, _ in keys} if not holding else set()


def require_no_pending_outbound(db: Session, position_id: int, item_ids: set[int]) -> None:
    if not item_ids:
        return
    pending = db.query(WarehouseIssueLine.id).join(WarehouseIssue).join(
        WarehouseTransaction, WarehouseTransaction.id == WarehouseIssue.transaction_id,
    ).filter(WarehouseIssue.status != "closed", WarehouseTransaction.kind == "outbound",
             WarehouseTransaction.status == "pending", WarehouseIssueLine.position_id == position_id,
             WarehouseIssueLine.item_id.in_(item_ids)).first()
    if pending:
        raise HTTPException(409, "该货位的物资有待补记出库，请先在负责人待办核对原单，再调整数量")


def apply_adjustment(db: Session, user: V2User, body: AdjustmentInput, now: datetime) -> WarehouseTransaction:
    key = (body.item_id, body.position_id)
    get_item(db, body.item_id, active=True)
    require_versions(db, {body.position_id: body.version}, {key})
    require_no_pending_outbound(db, body.position_id, {body.item_id})
    if body.kind == "opening" and db.get(WarehouseBalance, key) is not None:
        raise HTTPException(409, "此货位已有库存记录，请使用盘点调整")
    tx = new_transaction(db, user, body.kind, now, reason=body.reason)
    tx.payload = {"lines": [{"item_id": body.item_id, "position_id": body.position_id, "quantity": body.quantity}]}
    quantity = get_quantity(db, key)
    add_movement(db, tx, *key, body.kind, body.quantity - quantity, now)
    if body.quantity != quantity or body.kind == "opening":
        touch_positions(db, {body.position_id}, now)
    mark_posted(tx, user, now)
    return tx


def calibrate_position(db: Session, user: V2User, position_id: int, body: CalibrationInput,
                       now: datetime) -> tuple[WarehouseTransaction, set[int]]:
    counts = {line.item_id: line.quantity for line in body.items}
    if len(counts) != len(body.items) or (not counts and not body.photo_id):
        raise HTTPException(422, "请填写实盘数量或上传现况照，同一物资只录入一次")
    signature = sha256(canonical_json({
        "position_id": position_id, "items": counts, "reason": body.reason, "photo_id": body.photo_id,
    }, compact=True).encode("utf-8")).hexdigest()
    prior = get_transaction_by_client(db, user, body.client_id)
    if prior:
        if prior.kind != "calibration" or prior.fingerprint != signature:
            raise HTTPException(409, "这个单号已用于另一条记录，请先核对原校准单")
        return prior, set()

    position = get_position(db, position_id)
    keys = {(item_id, position_id) for item_id in counts}
    require_versions(db, {position_id: body.version}, keys, {position_id})
    require_no_pending_outbound(db, position_id, set(counts))
    for item_id in counts:
        get_item(db, item_id, active=True)
    photo = staged_photo(db, body.photo_id, user) if body.photo_id else None
    if photo:
        previous = db.get(WarehousePhoto, position.latest_photo_id) if position.latest_photo_id else None
        if (position.updated_at and photo.captured_at < position.updated_at) or (
            previous and photo.captured_at < previous.captured_at
        ):
            raise HTTPException(409, "现场照早于货位最新记录，请核对后重拍")

    tx = new_transaction(db, user, "calibration", now, reason=body.reason)
    tx.client_id, tx.fingerprint = body.client_id, signature
    tx.payload = {"lines": [{"item_id": item_id, "position_id": position_id, "quantity": value}
                            for item_id, value in counts.items()]}
    changed = False
    for item_id, target in counts.items():
        balance = db.get(WarehouseBalance, (item_id, position_id))
        current = balance.quantity if balance else 0
        changed |= balance is None or target != current
        add_movement(db, tx, item_id, position_id, "adjustment", target - current, now)
    if changed:
        touch_positions(db, {position_id}, now)
    if photo:
        attach_transaction_photos(db, tx, [(Attachment(photo_id=photo.id, position_id=position_id), photo)], now)
        update_latest_photo(db, position, photo, now, transaction_id=tx.id)
    mark_posted(tx, user, now)
    return tx, set(counts)


def resolve_issue(db: Session, user: V2User, issue: WarehouseIssue, body: ResolveInput, now: datetime) -> set[int]:
    observations = {(row.item_id, row.position_id): row for row in body.observations}
    if len(observations) != len(body.observations):
        raise HTTPException(422, "同一物资和货位只能填写一项实盘数")
    known_items = {item for (item,) in db.query(WarehouseIssueLine.item_id).filter_by(issue_id=issue.id).all()}
    if any(item_id not in known_items for item_id, _ in observations):
        raise HTTPException(422, "实盘数量须对应报告中的物资")
    for row in observations.values():
        require_versions(db, {row.position_id: row.version}, {(row.item_id, row.position_id)})

    tx = db.get(WarehouseTransaction, issue.transaction_id) if issue.transaction_id else None
    recovered = tx is not None and tx.status == "pending"
    pending_quantities = {(row["item_id"], row["position_id"]): row["quantity"] for row in tx.payload["lines"]} if recovered else {}
    if not pending_quantities.keys() <= observations.keys():
        raise HTTPException(422, "请填写原单所有物资货位的实际剩余数量")

    adjusted_positions = set()
    correction = None
    for key, row in observations.items():
        target_before_pending = row.quantity + pending_quantities.get(key, 0)
        delta = target_before_pending - get_quantity(db, key)
        if delta == 0:
            continue
        if correction is None:
            correction = new_transaction(db, user, "adjustment", now, reason=body.reason, related_id=issue.transaction_id)
        add_movement(db, correction, *key, "adjustment", delta, now)
        adjusted_positions.add(row.position_id)

    if correction is not None:
        correction.payload = {"lines": [{"item_id": item_id, "position_id": position_id, "quantity": row.quantity}
                                        for (item_id, position_id), row in observations.items() if position_id in adjusted_positions]}
        mark_posted(correction, user, now)
    if recovered:
        post_outbound(db, tx, user, now, pending_quantities, adjusted_positions=adjusted_positions)
    else:
        touch_positions(db, adjusted_positions, now)

    issue.status = "closed"
    issue.closed_at = now
    issue.closed_by_id = user.id
    issue.resolution = body.reason
    return known_items | {item_id for item_id, _ in observations}


def correct_transaction(db: Session, user: V2User, original: WarehouseTransaction,
                        body: CorrectionInput, now: datetime) -> WarehouseTransaction:
    if original.status != "posted" or original.kind not in {"inbound", "outbound"}:
        raise HTTPException(409, "请先核对原单状态；仅已入账的出入库单可纠正")
    if body.position_id is None and original.kind != "inbound":
        raise HTTPException(422, "请选择出库原单的货位")
    key = (body.item_id, body.position_id)
    if body.position_id is not None:
        require_versions(db, {body.position_id: body.version}, {key})
    base = db.query(WarehouseMovement).filter_by(transaction_id=original.id, item_id=body.item_id, position_id=body.position_id).one_or_none()
    if base is None or base.kind not in {"inbound", "outbound", "direct"}:
        raise HTTPException(404, "原单没有这项物资货位或直用记录")
    related = db.query(WarehouseMovement).join(
        WarehouseTransaction, WarehouseMovement.transaction_id == WarehouseTransaction.id,
    ).filter(WarehouseTransaction.related_id == original.id, WarehouseTransaction.kind == "correction",
             WarehouseMovement.item_id == body.item_id, WarehouseMovement.position_id == body.position_id).all()
    if base.kind == "direct":
        previous_quantity = base.direct_quantity + sum(row.direct_quantity for row in related)
    elif base.kind == "outbound":
        previous_quantity = base.usage_delta + sum(row.usage_delta for row in related)
    else:
        previous_quantity = base.delta + sum(row.delta for row in related)
    change = body.quantity - previous_quantity
    if change == 0:
        raise HTTPException(409, "新数量与当前有效原单数量相同")
    tx = new_transaction(db, user, "correction", now, reason=body.reason, related_id=original.id)
    if base.kind == "direct":
        add_movement(db, tx, body.item_id, None, "correction", 0, now, direct_quantity=change, usage_at=base.usage_at)
    else:
        tx.payload = {"lines": [{"item_id": body.item_id, "position_id": body.position_id, "quantity": body.quantity}]}
        outbound = base.kind == "outbound"
        add_movement(db, tx, *key, "correction", -change if outbound else change, now,
                     usage_delta=change if outbound else 0, usage_at=base.usage_at)
        touch_positions(db, {body.position_id}, now)
    mark_posted(tx, user, now)
    return tx
