"""Resumable transport and short-lived import receipts; originals remain independent."""
import uuid
from datetime import datetime
from sqlalchemy import String,Integer,BigInteger,Boolean,DateTime,ForeignKey,Index,func,text
from sqlalchemy.dialects.postgresql import UUID,JSONB
from sqlalchemy.orm import Mapped,mapped_column
from .base import Base

class ImageUpload(Base):
    __tablename__='image_uploads'
    id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id'),nullable=False)
    user_id:Mapped[int]=mapped_column(ForeignKey('users.id'),nullable=False)
    collection_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey('image_collections.id',ondelete='CASCADE'),nullable=False)
    annotation_format:Mapped[str|None]=mapped_column(String(20),nullable=True)
    trust_annotations:Mapped[bool]=mapped_column(Boolean,default=False,server_default=text('false'))
    filename:Mapped[str]=mapped_column(String(255))
    size:Mapped[int]=mapped_column(BigInteger)
    chunk_hashes:Mapped[list]=mapped_column(JSONB)
    resume_key:Mapped[str]=mapped_column(String(64))
    min_long_edge:Mapped[int]=mapped_column(Integer)
    status:Mapped[str]=mapped_column(String(20),default='uploading',server_default='uploading')
    cursor:Mapped[int]=mapped_column(Integer,default=0,server_default='0')
    attempt:Mapped[int]=mapped_column(Integer,default=0,server_default='0')
    unique_count:Mapped[int]=mapped_column(Integer,default=0,server_default='0')
    result:Mapped[dict]=mapped_column(JSONB,default=lambda:dict(imported=0,duplicates=0,filtered=0,invalid=0,ignored=0,errors=[]))
    error:Mapped[str|None]=mapped_column(String(500))
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
    updated_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now(),onupdate=func.now())
    __table_args__=(Index('ix_upload_resume','tenant_id','user_id','resume_key','created_at'),
        Index('ix_upload_status','status','updated_at'),
        Index('uq_upload_active','tenant_id','user_id','resume_key',unique=True,
            postgresql_where=text("status != 'completed'"),sqlite_where=text("status != 'completed'")))

class ImageUploadItem(Base):
    __tablename__='image_upload_items'
    upload_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey('image_uploads.id',ondelete='CASCADE'),primary_key=True)
    image_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey('images.id',ondelete='CASCADE'),primary_key=True)
