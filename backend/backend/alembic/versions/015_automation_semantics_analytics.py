"""Frozen additive schema for automation, semantic search and human activity."""
from alembic import op
import sqlalchemy as sa

revision='015'
down_revision='014'
branch_labels=None
depends_on=None

TABLES=('automation_jobs','automation_items','image_semantic_jobs','image_semantic_items','image_semantic_vectors','work_activity_intervals')
DDL=[
    '''CREATE TABLE image_semantic_jobs (
        id UUID PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id), collection_id UUID,
        caption_model JSONB, embedding_model JSONB, dimensions INTEGER NOT NULL,
        preprocessing VARCHAR(160) NOT NULL, prompt VARCHAR(2000) NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL)''',
    'CREATE INDEX ix_image_semantic_jobs_tenant_id ON image_semantic_jobs(tenant_id)',
    '''CREATE TABLE automation_jobs (
        id UUID PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL, kind VARCHAR(30) NOT NULL, status VARCHAR(30) NOT NULL,
        config JSONB NOT NULL, models JSONB NOT NULL, label_schema JSONB NOT NULL,
        total INTEGER NOT NULL, processed INTEGER NOT NULL, approved_count INTEGER NOT NULL,
        needs_review_count INTEGER NOT NULL, failed_count INTEGER NOT NULL, error VARCHAR(500),
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL)''',
    'CREATE INDEX idx_automation_job_tenant_created ON automation_jobs(tenant_id,created_at)',
    '''CREATE TABLE image_semantic_items (
        id UUID PRIMARY KEY, job_id UUID NOT NULL REFERENCES image_semantic_jobs(id) ON DELETE CASCADE,
        image_id UUID REFERENCES images(id) ON DELETE SET NULL, original_name VARCHAR(255) NOT NULL, content_hash VARCHAR(100) NOT NULL,
        status VARCHAR(20) NOT NULL, attempt INTEGER NOT NULL, caption VARCHAR(20000), error VARCHAR(500),
        CONSTRAINT uq_semantic_job_image UNIQUE(job_id,image_id))''',
    'CREATE INDEX ix_image_semantic_items_job_id ON image_semantic_items(job_id)',
    'CREATE INDEX ix_semantic_item_claim ON image_semantic_items(status,job_id,id)',
    '''CREATE TABLE image_semantic_vectors (
        id UUID PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id),
        image_id UUID NOT NULL REFERENCES images(id) ON DELETE CASCADE, content_hash VARCHAR(100) NOT NULL,
        model_id UUID NOT NULL REFERENCES model_services(id) ON DELETE CASCADE,
        revision INTEGER NOT NULL, dimensions INTEGER NOT NULL, preprocessing VARCHAR(160) NOT NULL,
        vector JSONB NOT NULL, caption VARCHAR(20000),
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT uq_semantic_vector_space UNIQUE(image_id,model_id,revision,dimensions,preprocessing))''',
    'CREATE INDEX ix_semantic_vector_space ON image_semantic_vectors(tenant_id,model_id,revision,dimensions,preprocessing)',
    '''CREATE TABLE automation_items (
        id UUID PRIMARY KEY, job_id UUID NOT NULL REFERENCES automation_jobs(id) ON DELETE CASCADE,
        asset_id UUID NOT NULL, content_hash VARCHAR(100) NOT NULL, annotation_id UUID,
        annotation_version INTEGER NOT NULL, review_baseline JSONB NOT NULL,
        status VARCHAR(30) NOT NULL, attempt INTEGER NOT NULL, reason VARCHAR(1000),
        evidence JSONB NOT NULL, result JSONB NOT NULL,
        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL)''',
    'CREATE INDEX idx_automation_item_claim ON automation_items(status,created_at)',
    'CREATE INDEX idx_automation_item_job ON automation_items(job_id,status)',
    '''CREATE TABLE work_activity_intervals (
        id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        session_id UUID NOT NULL, sequence INTEGER NOT NULL, activity VARCHAR(20) NOT NULL,
        started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, ended_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        received_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT uq_work_activity_heartbeat UNIQUE(user_id,session_id,sequence),
        CONSTRAINT ck_work_activity_sequence CHECK(sequence>0),
        CONSTRAINT ck_work_activity_kind CHECK(activity IN ('annotation','review')),
        CONSTRAINT ck_work_activity_time CHECK(ended_at>started_at))''',
    'CREATE INDEX idx_work_activity_project_user_time ON work_activity_intervals(project_id,user_id,started_at,ended_at)',
]


def upgrade():
    bind=op.get_bind()
    existing=[table for table in TABLES if sa.inspect(bind).has_table(table)]
    if existing:raise RuntimeError('015检测到已有同名表，禁止覆盖；请先核对结构与版本：'+','.join(existing))
    for statement in DDL:op.execute(sa.text(statement))


def downgrade():
    raise RuntimeError('015包含任务、审核证据、向量和人工时长，禁止自动删除业务数据')
