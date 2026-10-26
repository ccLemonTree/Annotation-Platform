from datetime import datetime,timezone
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field,field_serializer,field_validator

class ImageQuery(BaseModel):
    q: str | None = Field(None,max_length=255)
    source_type: str | None = None
    batch_id: str | None = None
    collection_id: UUID | None = None
    scene: str | None = Field(None,max_length=60)
    scene_status: str | None = Field(None,pattern='^(unclassified|pending|processing|completed|failed)$')
    created_from: datetime | None = None
    created_to: datetime | None = None

class ImageResponse(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id: UUID
    blob_path: str
    content_hash: str
    original_name: str
    file_size: int
    width: int | None
    height: int | None
    format: str | None
    source_type: str
    batch_id: str | None
    capture_time: datetime | None
    device_info: dict | None
    quality_score: int | None
    created_at: datetime
    scene_labels: list[str] = Field(default_factory=list)
    scene_status: str = 'unclassified'
    scene_error: str | None = None
    scene_metadata: dict | None = None
    collection_names: list[str] = Field(default_factory=list)
    has_imported_annotation: bool = False
    imported_annotation_trusted: bool = False

    @field_serializer('created_at','capture_time')
    def utc_datetime(self,value):
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value

class ImageAssociationRequest(BaseModel):
    image_ids: list[UUID] = Field(min_length=1,max_length=1000)
    collection_id: UUID | None = None

class ImageAssociation(BaseModel):
    project_id: UUID
    image_ids: list[UUID] = Field(min_length=1,max_length=1000)
    include_imported_annotations: bool = False
class CollectionCreate(BaseModel):
    name: str = Field(min_length=1,max_length=200)

    @field_validator('name')
    @classmethod
    def valid_name(cls,value):
        value=value.strip()
        if not value:
            raise ValueError('图片集名称不能为空')
        return value

class CollectionAssignment(CollectionCreate):
    image_ids: list[UUID] = Field(min_length=1,max_length=1000)

