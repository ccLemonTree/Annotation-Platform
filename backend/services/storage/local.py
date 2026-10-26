"""
本地文件系统存储实现
"""

import os
import hashlib
import shutil
from pathlib import Path
from typing import BinaryIO, Dict, Optional

from .base import StorageBackend


class LocalStorage(StorageBackend):
    """本地文件系统存储"""

    def __init__(self, base_path: str = "./data"):
        self.base_path = Path(base_path).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

    async def save_file(
        self,
        file_data: BinaryIO,
        path: str,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """保存文件到本地"""
        full_path = self.base_path / path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # 写入文件并计算哈希
        hasher = hashlib.sha256()
        file_size = 0

        with open(full_path, 'wb') as f:
            while chunk := file_data.read(8192):
                f.write(chunk)
                hasher.update(chunk)
                file_size += len(chunk)

        content_hash = hasher.hexdigest()

        # 校验元数据中的哈希（如果提供）
        if metadata and metadata.get('content_hash'):
            if content_hash != metadata['content_hash']:
                # 哈希不匹配，删除文件
                full_path.unlink(missing_ok=True)
                raise ValueError(f"哈希校验失败: 期望 {metadata['content_hash']}, 实际 {content_hash}")

        return {
            "path": path,
            "size": file_size,
            "hash": content_hash
        }

    async def get_file(self, path: str) -> BinaryIO:
        """获取文件流"""
        full_path = self.base_path / path
        if not full_path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        return open(full_path, 'rb')

    async def file_exists(self, path: str) -> bool:
        """检查文件是否存在"""
        return (self.base_path / path).exists()

    def get_public_url(self, path: str, expires: int = 3600) -> str:
        """本地存储通过API代理访问"""
        return f"/api/storage/files/{path}"

    async def copy_file(self, src_path: str, dst_path: str) -> bool:
        """
        零拷贝：硬链接或符号链接

        这是零拷贝的核心实现：
        1. 优先尝试硬链接（os.link）- 完全零拷贝，节省空间
        2. 降级到符号链接（os.symlink）- 引用原文件
        """
        src_full = self.base_path / src_path
        dst_full = self.base_path / dst_path

        if not src_full.exists():
            return False

        dst_full.parent.mkdir(parents=True, exist_ok=True)

        try:
            # 尝试硬链接（节省空间，完全零拷贝）
            os.link(src_full, dst_full)
            return True
        except OSError:
            # 降级到符号链接
            try:
                os.symlink(src_full, dst_full)
                return True
            except OSError:
                return False

    async def delete_file(self, path: str) -> bool:
        """删除文件"""
        full_path = self.base_path / path
        if full_path.exists():
            full_path.unlink()
            return True
        return False

    async def ensure_directory(self, path: str) -> bool:
        """确保目录存在"""
        full_path = self.base_path / path
        try:
            full_path.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False
