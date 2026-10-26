"""Back up existing rows and apply only the retained 006 to 007 upgrade."""
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from sqlalchemy import text
from psycopg2 import sql
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend/backend'

def snapshot(conn, tables):
    return {table: {'count': len(rows), 'sha256': hashlib.sha256('\n'.join(sorted(rows)).encode()).hexdigest()}
        for table in tables
        for rows in [conn.execute(text(f'SELECT row_to_json(t)::text FROM public."{table}" t')).scalars().all()]}

def main():
    os.chdir(BACKEND); sys.path.insert(0, str(BACKEND))
    from integrations.smarteye.runtime import get_runtime
    runtime = get_runtime()
    with runtime.engine.connect() as conn:
        revision = conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        if revision != '006': raise RuntimeError('Expected retained 006 baseline; refusing other migrations')
        tables = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version' ORDER BY tablename")).scalars().all()
        before = snapshot(conn, tables)
    backup = BACKEND / '.backups' / ('p2-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True)
    with runtime.engine.connect() as conn:
        with conn.connection.driver_connection.cursor() as cursor:
            for table in tables:
                with (backup / (table + '.csv')).open('w',encoding='utf-8',newline='') as output:
                    cursor.copy_expert(sql.SQL('COPY public.{} TO STDOUT WITH CSV HEADER').format(sql.Identifier(table)),output)
    (backup/'metadata.json').write_text(json.dumps(before,indent=2),'utf-8')
    command.upgrade(Config(str(BACKEND/'alembic.ini')), '007')
    with runtime.engine.connect() as conn:
        after = snapshot(conn, tables)
    if before != after: raise RuntimeError('Old rows changed; inspect private backup before proceeding')
    evidence = dict(revision='007', old_rows_unchanged=True, retained_business_rows=after, private_backup_created=True)
    (ROOT/'docs/testing/p2-migration-verification.json').write_text(json.dumps(evidence,indent=2),'utf-8')
    print(json.dumps(dict(revision='007',old_rows_unchanged=True,tables_backed_up=len(tables))))

if __name__ == '__main__': main()
