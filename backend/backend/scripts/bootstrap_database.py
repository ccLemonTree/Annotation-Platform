"""Initialize an explicitly selected, empty PostgreSQL database at current head.

Run with DATABASE_BOOTSTRAP_URL (postgresql+psycopg2://...) set. Never replay 001-004.
Optional first administrator credentials are supplied explicitly, never defaulted.
"""
import argparse
import os
from pathlib import Path
import sys

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from models import Base, Tenant, User
from config.settings import settings
from schemas.user import UserCreate
from services.auth import get_password_hash
from integrations.smarteye.runtime import metadata as smarteye_metadata
from integrations.smarteye.schema import upgrade_schema

# These unused legacy ORM tables disagree with the embedded SmartEye schema.
LEGACY_TABLES = {'smarteye_tasks', 'smarteye_downloads'}


def bootstrap_database(conn, admin=None):
    """Caller must use engine.begin(): schema creation and exact stamp are one transaction."""
    if conn.dialect.name != 'postgresql':
        raise RuntimeError('Bootstrap requires PostgreSQL')
    # Include other user schemas, views, sequences and standalone types in the empty check.
    if conn.execute(text("""SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
        UNION ALL SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
        LIMIT 1""")).first():
        raise RuntimeError('Bootstrap requires an empty database; existing objects were not modified')
    conn.execute(text('SET LOCAL search_path TO public'))
    config = Config()
    config.set_main_option('script_location', str(ROOT / 'alembic'))
    scripts = ScriptDirectory.from_config(config)
    head = scripts.get_current_head()
    Base.metadata.create_all(conn, tables=[t for t in Base.metadata.sorted_tables if t.name not in LEGACY_TABLES])
    smarteye_metadata.create_all(conn)
    upgrade_schema(conn)
    if admin is not None:
        conn.execute(insert(Tenant).values(id=settings.DEFAULT_TENANT_ID, tenant_name='默认租户'))
        conn.execute(insert(User).values(tenant_id=settings.DEFAULT_TENANT_ID, username=admin.username,
            email=admin.email, full_name=admin.full_name, password_hash=get_password_hash(admin.password),
            role='admin', is_active=True))
    MigrationContext.configure(conn).stamp(scripts, head)
    return head


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', default=os.environ.get('DATABASE_BOOTSTRAP_URL'))
    parser.add_argument('--admin-username')
    parser.add_argument('--admin-email')
    args = parser.parse_args()
    if not args.database_url:
        parser.error('Set DATABASE_BOOTSTRAP_URL or pass --database-url explicitly; no application database default')
    admin = None
    if args.admin_username or args.admin_email:
        password = os.environ.get('BOOTSTRAP_ADMIN_PASSWORD')
        if not args.admin_username or not args.admin_email or not password:
            parser.error('First admin requires --admin-username, --admin-email and BOOTSTRAP_ADMIN_PASSWORD')
        try:
            admin = UserCreate(username=args.admin_username, email=args.admin_email, password=password)
        except ValueError:
            parser.error('First admin fields/password do not satisfy account validation')
    url = make_url(args.database_url)
    if url.get_backend_name() != 'postgresql':
        parser.error('PostgreSQL URL required')
    engine = create_engine(url.set(drivername='postgresql+psycopg2'), poolclass=NullPool)
    try:
        with engine.begin() as conn:
            head = bootstrap_database(conn, admin=admin)
        print(f'Initialized empty database; alembic revision {head}')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
