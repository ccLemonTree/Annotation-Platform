"""Project membership shared by annotation reads and writes."""
from sqlalchemy import select, true
from models import Project, Asset, ProjectMember, LibraryImage


def project_condition(db):
    user=db.info.get('current_user')
    if user is None or user['role']=='admin':return true()
    return (Project.tenant_id==user['tenant_id']) & Project.id.in_(
        select(ProjectMember.project_id).where(ProjectMember.user_id==user['id']))


def image_condition(db):
    user=db.info.get('current_user')
    if user is None or user['role']=='admin':return true()
    return select(Asset.id).join(Project).where(Asset.image_id==LibraryImage.id,project_condition(db)).exists()
