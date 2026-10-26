"""
存储接口基类
定义统一的存储操作接口
"""

from abc import ABC, abstractmethod
from typing import BinaryIO, Optional


class StorageBackend(ABC):
    """存储后端抽象基类"""

    @abstractmethod
    async def get_file(self, path: str) -> BinaryIO:
        """读取文件；调用者负责关闭返回的流。"""
        pass

    @abstractmethod
    async def save_file(self, file_data: BinaryIO, path: str) -> dict:
        """
        保存文件

        Args:
            file_data: 文件二进制数据
            path: 存储路径

        Returns:
            包含url和metadata的字典
        """
        pass

    @abstractmethod
    async def delete_file(self, path: str) -> bool:
        """
        删除文件

        Args:
            path: 文件路径

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def get_file_url(self, path: str, expires_in: int = 3600) -> str:
        """
        获取文件访问URL

        Args:
            path: 文件路径
            expires_in: 过期时间（秒）

        Returns:
            访问URL
        """
        pass

    @abstractmethod
    async def file_exists(self, path: str) -> bool:
        """
        检查文件是否存在

        Args:
            path: 文件路径

        Returns:
            是否存在
        """
        pass
