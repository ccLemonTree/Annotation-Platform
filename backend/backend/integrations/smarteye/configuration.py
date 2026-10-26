"""Main app database with an isolated SmartEye SQL namespace."""
from pathlib import Path
from config.settings import settings
from dotenv import dotenv_values
import os

def get_env(name, default=None):
    values = dotenv_values(Path(__file__).resolve().parents[2] / '.env.smarteye')
    return os.getenv('SMARTEYE_' + name, values.get(name, default))

def download_root():
    return Path(settings.STORAGE_LOCAL_PATH).resolve() / 'smarteye'

def connection_kwargs():
    return dict(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
                password=settings.DB_PASSWORD, database=settings.DB_NAME,
                options='-c search_path=smarteye', connect_timeout=10)
