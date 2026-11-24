from re import compile
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StringConstraints
from app.warehouse.models import WAREHOUSE_LAYOUT


Id = Annotated[int, Field(gt=0)]
PositionId = Annotated[int, Field(ge=1, le=len(WAREHOUSE_LAYOUT))]
Version = Annotated[StrictInt, Field(ge=0)]
Quantity = Annotated[StrictInt, Field(ge=0, le=1_000_000)]
PositiveQuantity = Annotated[StrictInt, Field(gt=0, le=1_000_000)]
PhotoId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
ClientId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
INBOUND_BATCH_PREFIX = "inbound-import-"
INBOUND_BATCH_KEY = compile(INBOUND_BATCH_PREFIX + r"[0-9a-f]{32}")
EXCEL_ITEM_COLUMNS = ("物资名称", "货位")
NewItemId = Annotated[int, Field(ge=-250, le=-1)]
OrderItemId = Id | NewItemId


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManagerInput(Document):
    employee_id: Id


class ItemInput(Document):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    spec: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    category: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] = "其他"
    unit: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
    aliases: Annotated[list[Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)]], Field(max_length=30)] = Field(default_factory=list)
    active: bool = True


class ArchiveItemInput(Document):
    employee_no: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class TxLine(Document):
    item_id: OrderItemId
    position_id: PositionId
    quantity: PositiveQuantity


class Receipt(Document):
    item_id: OrderItemId
    received: PositiveQuantity
    damaged: Quantity = 0
    direct_quantity: Quantity = 0
    direct_destination: Text = ""
    note: Text = ""
    expected_unit: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)] | None = None


class Attachment(Document):
    photo_id: PhotoId
    position_id: PositionId | None = None
    item_id: OrderItemId | None = None
    started_at: AwareDatetime | None = None
    position_started_at: AwareDatetime | None = None


class DiscrepancyInput(Document):
    item_id: Id
    position_id: PositionId
    observed_quantity: Quantity | None = None
    note: Text = ""


class NewInboundItem(Document):
    item_id: NewItemId
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    spec: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    unit: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)] = "件"


class SingleInboundInput(Document):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    position_id: PositionId
    quantity: PositiveQuantity
    item_id: Id | None = None
    confirm_new: bool = False


class TransactionInput(Document):
    client_id: ClientId
    kind: Literal["outbound", "inbound"]
    position_versions: dict[PositionId, Version] = Field(default_factory=dict, max_length=len(WAREHOUSE_LAYOUT))
    lines: list[TxLine] = Field(default_factory=list, max_length=250)
    receipts: list[Receipt] = Field(default_factory=list, max_length=250)
    new_items: list[NewInboundItem] = Field(default_factory=list, max_length=250)
    photos: list[Attachment] = Field(default_factory=list, max_length=250)
    discrepancies: list[DiscrepancyInput] = Field(default_factory=list, max_length=250)
    hold_if_insufficient: bool = False
    defer_location_photos: bool = False
    photo_started_at: AwareDatetime | None = None


class IssueInput(DiscrepancyInput):
    reason: Literal["mismatch", "missing", "misplaced", "map"]
    photo_id: PhotoId | None = None


class Observation(Document):
    item_id: Id
    position_id: PositionId
    quantity: Quantity
    version: Version


class ResolveInput(Document):
    reason: Reason
    observations: list[Observation] = Field(default_factory=list, max_length=250)


class AdjustmentInput(Observation):
    reason: Reason
    kind: Literal["opening", "adjustment"] = "adjustment"
    defer_location_photo: bool = False


class CalibrationItem(Document):
    item_id: Id
    quantity: Quantity


class CalibrationInput(Document):
    client_id: ClientId
    version: Version
    reason: Reason
    items: list[CalibrationItem] = Field(default_factory=list, max_length=250)
    photo_id: PhotoId | None = None


class CorrectionInput(Document):
    item_id: Id
    position_id: PositionId | None = None
    quantity: Quantity
    version: Version | None = None
    reason: Reason


class LocationPhotoInput(Document):
    photo_id: PhotoId


class LocationPhotoTaskInput(LocationPhotoInput):
    version: Version
    requested_at: Annotated[str, StringConstraints(max_length=40)]


class LocationPhotoReportInput(Document):
    photo_id: PhotoId
    note: Text = ""


class LocationMoveInput(Document):
    client_id: ClientId
    item_id: Id
    from_position_id: PositionId
    to_position_id: PositionId
    quantity: PositiveQuantity
    from_version: Version
    to_version: Version
