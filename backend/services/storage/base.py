"""
统一存储抽象层 - 基类
"""

from abc import ABC, abstractmethod
from typing import BinaryIO, Optional, Dict
from pathlib import Path


class StorageBackend(ABC):
    """存储后端抽象接口"""

    @abstractmethod
    async def save_file(
        self,
        file_data: BinaryIO,
        path: str,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        保存文件

        Args:
            file_data: 文件数据流
            path: 相对路径，如 "smarteye/task_001/abc.jpg"
            metadata: 元数据 {content_hash, content_type, original_name}

        Returns:
            {
                "path": "存储路径",
                "size": 文件大小,
                "hash": "SHA256哈希"
            }
        """
        pass

    @abstractmethod
    async def get_file(self, path: str) -> BinaryIO:
        """获取文件流"""
        pass

    @abstractmethod
    async def file_exists(self, path: str) -> bool:
        """检查文件是否存在"""
        pass

    @abstractmethod
    def get_public_url(self, path: str, expires: int = 3600) -> str:
        """
        生成访问URL

        Args:
            path: 文件路径
            expires: 有效期（秒），0表示永久

        Returns:
            签名URL（EOS）或代理URL（Local）
        """
        pass

    @abstractmethod
    async def copy_file(self, src_path: str, dst_path: str) -> bool:
        """
        复制文件（内部操作，无需下载上传）
        用于：SmartEye图片 → 标注项目 的零拷贝
        """
        pass

    @abstractmethod
    async def delete_file(self, path: str) -> bool:
        """删除文件"""
        pass

    @abstractmethod
    async def ensure_directory(self, path: str) -> bool:
        """
        确保目录存在

        Args:
            path: 目录路径

        Returns:
            是否成功
        """
        pass
