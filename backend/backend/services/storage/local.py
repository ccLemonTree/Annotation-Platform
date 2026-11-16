"""Local storage with root confinement and atomic writes."""
import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote
from starlette.concurrency import run_in_threadpool
from .base import StorageBackend

class LocalStorage(StorageBackend):
    def __init__(self, base_path: str = "./data"):
        self.base_path = Path(base_path).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

    def resolve_path(self, path: str) -> Path:
        result = (self.base_path / path).resolve()
        if not result.is_relative_to(self.base_path):
            raise ValueError("Storage path leaves the configured root")
        return result

    async def save_file(self, file_data: BinaryIO, path: str) -> dict:
        target = self.resolve_path(path)

        def save():
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
            try:
                with os.fdopen(descriptor, "wb") as file:
                    shutil.copyfileobj(file_data, file, length=1024 * 1024)
                    size = file.tell()
                os.replace(temporary, target)
                return size
            finally:
                Path(temporary).unlink(missing_ok=True)

        size = await run_in_threadpool(save)
        return {"url": await self.get_file_url(path), "path": str(target), "size": size}

    async def get_file(self, path: str) -> BinaryIO:
        return self.resolve_path(path).open("rb")

    async def delete_file(self, path: str) -> bool:
        target = self.resolve_path(path)
        try:
            target.unlink()
            return True
        except FileNotFoundError:
            return False

    async def get_file_url(self, path: str, expires_in: int = 3600) -> str:
        self.resolve_path(path)
        return f"/files/{quote(path)}"

    async def file_exists(self, path: str) -> bool:
        return self.resolve_path(path).is_file()

