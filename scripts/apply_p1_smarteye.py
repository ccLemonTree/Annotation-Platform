"""Back up the current business rows, apply only 006, and seed source configuration."""
import csv
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from dotenv import dotenv_values
import psycopg2
from psycopg2 import sql
from sqlalchemy import text
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend/backend'
sys.path.insert(0, str(BACKEND))
from integrations.smarteye.runtime import get_runtime
from config.settings import settings

def snapshot(conn, tables):
    result = {}
    for table in tables:
        rows = conn.execute(text(f'SELECT row_to_json(t)::text FROM public."{table}" t')).scalars().all()
        digest = hashlib.sha256('\n'.join(sorted(rows)).encode()).hexdigest()
        result[table] = dict(count=len(rows), sha256=digest)
    return result

def main():
    import os
    os.chdir(BACKEND)
    runtime = get_runtime()
    with runtime.engine.connect() as conn:
        revision = conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        tables = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version' ORDER BY tablename")).scalars().all()
        before = snapshot(conn, tables)
    if revision not in ('005', '006'):
        raise RuntimeError('Expected retained 005 baseline; refusing old destructive migrations')
    backup = BACKEND / '.backups' / ('p1-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True)
    with runtime.engine.connect() as conn:
        raw = conn.connection.driver_connection
        with raw.cursor() as cur:
            for table in tables:
                with (backup / (table + '.csv')).open('w', encoding='utf-8', newline='') as output:
                    cur.copy_expert(sql.SQL('COPY public.{} TO STDOUT WITH CSV HEADER').format(sql.Identifier(table)), output)
    (backup / 'metadata.json').write_text(json.dumps(before, indent=2), 'utf-8')
    if revision == '005':
        command.upgrade(Config(str(BACKEND / 'alembic.ini')), '006')
    with runtime.engine.connect() as conn:
        after = snapshot(conn, tables)
    if after != before:
        raise RuntimeError('Existing business rows changed; inspect private backup before continuing')

    source_env = Path('F:/hzxf/auto-img-downloader/.env')
    values = dotenv_values(source_env)
    source = psycopg2.connect(host=values.get('DB_HOST'), port=values.get('DB_PORT') or 5432,
        user=values.get('DB_USER'), password=values.get('DB_PASSWORD'), dbname=values.get('DB_NAME'), connect_timeout=10)
    source.set_session(readonly=True)
    with source.cursor() as cur:
        cur.execute('SELECT platform_name, plat_id, base_url, username, password, login_url, state FROM public.platforms')
        platforms = cur.fetchall()
        cur.execute('SELECT plat_id, alarm_name, alarm_code FROM public.alarm_types')
        alarms = cur.fetchall()
    source.close()
    with runtime.engine.begin() as conn:
        for row in platforms:
            conn.execute(text('''INSERT INTO smarteye.platforms
                (platform_name,plat_id,base_url,username,password,login_url,state)
                VALUES (:name,:id,:url,:user,:password,:login,:state)
                ON CONFLICT (plat_id) DO NOTHING'''), dict(zip(('name','id','url','user','password','login','state'),row)))
        for row in alarms:
            conn.execute(text('''INSERT INTO smarteye.alarm_types (plat_id,alarm_name,alarm_code)
                VALUES (:id,:name,:code) ON CONFLICT (plat_id,alarm_code) DO NOTHING'''),
                dict(zip(('id','name','code'),row)))
    evidence = dict(revision='006', retained_business_rows=after,
        source_platforms=len(platforms), source_alarm_types=len(alarms), old_rows_unchanged=True,
        source_database_readonly=True, backup_created=True)
    (ROOT / 'docs/testing/p1-migration-verification.json').write_text(json.dumps(evidence, indent=2), 'utf-8')
    print(json.dumps({'revision': '006', 'old_rows_unchanged': True,
        'source_platforms': len(platforms), 'source_alarm_types': len(alarms), 'backup_created': True}))

if __name__ == '__main__':
    main()
