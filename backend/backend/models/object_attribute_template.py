import uuid
from datetime import datetime
from sqlalchemy import ForeignKey,String,DateTime,UniqueConstraint,func
from sqlalchemy.dialects.postgresql import UUID,JSONB
from sqlalchemy.orm import Mapped,mapped_column
from .base import Base


class ObjectAttributeTemplate(Base):
    __tablename__='object_attribute_templates'
    id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    tenant_id:Mapped[int]=mapped_column(ForeignKey('tenants.id',ondelete='CASCADE'))
    name:Mapped[str]=mapped_column(String(60))
    config:Mapped[dict]=mapped_column(JSONB)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
    __table_args__=(UniqueConstraint('tenant_id','name',name='uq_attribute_template_name'),)
