"""Named groups reference independent originals without copying files."""
import uuid
from sqlalchemy import String,Integer,ForeignKey,UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID,JSONB
from sqlalchemy.orm import Mapped,mapped_column
from .base import Base

class ImageCollection(Base):
    __tablename__='image_collections'
    id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),nullable=False)
    name:Mapped[str]=mapped_column(String(200),nullable=False)
    ingest_config:Mapped[dict|None]=mapped_column(JSONB)
    ingest_revision:Mapped[int]=mapped_column(Integer,nullable=False,default=1,server_default='1')
    __table_args__=(UniqueConstraint('tenant_id','name',name='uq_image_collection_name'),)

class ImageCollectionMember(Base):
    __tablename__='image_collection_members'
    collection_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey('image_collections.id',ondelete='CASCADE'),primary_key=True)
    image_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey('images.id',ondelete='CASCADE'),primary_key=True,index=True)
