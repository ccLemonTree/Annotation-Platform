"""Schema creation is executed only by the explicit Alembic migration."""
import ast
from pathlib import Path
from sqlalchemy import text

def upgrade_schema(conn):
    conn.execute(text('CREATE SCHEMA IF NOT EXISTS smarteye'))
    conn.execute(text('SET LOCAL search_path TO smarteye'))
    conn.execute(text('''CREATE TABLE IF NOT EXISTS platforms (
        id SERIAL PRIMARY KEY, platform_name TEXT UNIQUE NOT NULL, plat_id TEXT UNIQUE NOT NULL,
        base_url TEXT DEFAULT '', username TEXT DEFAULT '', password TEXT DEFAULT '',
        login_url TEXT DEFAULT '/api/oauth/anyTenant/login', state TEXT DEFAULT '1')'''))
    conn.execute(text('''CREATE TABLE IF NOT EXISTS alarm_types (
        id SERIAL PRIMARY KEY, plat_id TEXT NOT NULL, alarm_name TEXT NOT NULL, alarm_code TEXT NOT NULL,
        UNIQUE(plat_id, alarm_code))'''))
    # Keep the exact vetted source DDL; importing its workers is unnecessary for migration.
    names = {'ensure_ai_image_tables', 'ensure_devices_table', 'ensure_ledger_watch_tables', 'ensure_cache_tables'}
    statements = []
    for path in [Path(__file__).parent / 'api/db_utils.py', Path(__file__).parent / 'api/db_cache.py']:
        tree = ast.parse(path.read_text('utf-8'))
        for fn in tree.body:
            if isinstance(fn, ast.FunctionDef) and fn.name in names:
                for node in ast.walk(fn):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        sql = node.value.strip()
                        if sql.startswith(('CREATE TABLE', 'CREATE INDEX', 'ALTER TABLE')):
                            statements.append(sql)
    for prefix in ('CREATE TABLE', 'ALTER TABLE', 'CREATE INDEX'):
        for sql in statements:
            if sql.startswith(prefix):
                for statement in sql.split(';'):
                    if statement.strip():
                        conn.execute(text(statement))
    conn.execute(text('SET LOCAL search_path TO public'))

