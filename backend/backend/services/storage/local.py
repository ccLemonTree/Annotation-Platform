"""Local storage with root confinement and atomic writes."""
import os
import tempfile
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote
import aiofiles
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
        target.parent.mkdir(parents=True, exist_ok=True)
        content = file_data.read()
        descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
        os.close(descriptor)
        try:
            async with aiofiles.open(temporary, "wb") as file:
                await file.write(content)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"url": await self.get_file_url(path), "path": str(target), "size": len(content)}

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

