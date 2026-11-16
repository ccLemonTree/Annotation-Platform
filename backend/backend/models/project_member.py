"""Users allowed to annotate and review a project."""
from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
import uuid
from .base import Base


class ProjectMember(Base):
    __tablename__='project_members'
    project_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey('projects.id',ondelete='CASCADE'),primary_key=True)
    user_id:Mapped[int]=mapped_column(BigInteger,ForeignKey('users.id',ondelete='CASCADE'),primary_key=True,index=True)
