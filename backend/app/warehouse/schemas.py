from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints


Id = Annotated[int, Field(gt=0)]
PositionId = Annotated[int, Field(ge=1, le=9)]
Version = Annotated[StrictInt, Field(ge=0)]
Quantity = Annotated[StrictInt, Field(ge=0, le=1_000_000)]
PositiveQuantity = Annotated[StrictInt, Field(gt=0, le=1_000_000)]
PhotoId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
ClientId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]


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


class TxLine(Document):
    item_id: Id
    position_id: PositionId
    quantity: PositiveQuantity


class Receipt(Document):
    item_id: Id
    received: PositiveQuantity
    damaged: Quantity = 0
    direct_quantity: Quantity = 0
    direct_destination: Text = ""
    note: Text = ""


class Attachment(Document):
    photo_id: PhotoId
    position_id: PositionId | None = None
    item_id: Id | None = None


class DiscrepancyInput(Document):
    item_id: Id
    position_id: PositionId
    observed_quantity: Quantity | None = None
    note: Text = ""


class TransactionInput(Document):
    client_id: ClientId
    kind: Literal["outbound", "inbound"]
    position_versions: dict[PositionId, Version] = Field(default_factory=dict, max_length=9)
    lines: list[TxLine] = Field(default_factory=list, max_length=250)
    receipts: list[Receipt] = Field(default_factory=list, max_length=250)
    photos: list[Attachment] = Field(default_factory=list, max_length=250)
    discrepancies: list[DiscrepancyInput] = Field(default_factory=list, max_length=250)
    hold_if_insufficient: bool = False


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


class CorrectionInput(Document):
    item_id: Id
    position_id: PositionId | None = None
    quantity: Quantity
    version: Version | None = None
    reason: Reason


class LocationPhotoInput(Document):
    photo_id: PhotoId
