"""Check the active backend without creating tables or business records."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend" / "backend"))
os.chdir(Path(__file__).resolve().parent / "backend" / "backend")


async def main():
    try:
        from config.settings import settings
        from database import engine
        from main import app
        from models import Base
        from sqlalchemy import text
        from services.storage import get_storage
    except Exception as error:
        print(f"Backend import failed: {type(error).__name__}")
        return 1
    try:
        print(f"Database target: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
        print(f"Storage: {type(get_storage()).__name__}")
        print(f"OpenAPI paths: {len(app.openapi()['paths'])}")
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
            present = set(result.scalars())
            from integrations.smarteye.runtime import metadata
            expected = set(Base.metadata.tables) - {"smarteye_tasks", "smarteye_downloads"}
            expected.update(metadata.tables)
            missing = sorted(expected - present)
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        print(f"Migration: {version}")
        if missing:
            print("Unfinished model tables: " + ", ".join(missing))
            print("Follow docs/development/TODO.md; do not recreate obsolete tables to hide API failures.")
            return 1
        print("Imports and table presence passed; business workflows require separate tests.")
        return 0
    except Exception as error:
        print(f"Environment check failed: {type(error).__name__}")
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
