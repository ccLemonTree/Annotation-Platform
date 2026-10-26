"""
存储层统一接口
工厂方法：根据配置返回存储实例
"""

from typing import Optional
from .base import StorageBackend
from .local import LocalStorage
from .eos import EOSStorage


_storage_instance: Optional[StorageBackend] = None


def get_storage(config=None) -> StorageBackend:
    """
    单例工厂：根据配置返回存储实例

    使用示例:
        storage = get_storage()
        result = await storage.save_file(file_data, "path/to/file.jpg")
    """
    global _storage_instance

    if _storage_instance is None:
        # 延迟导入避免循环依赖
        if config is None:
            from config.settings import settings as config

        storage_type = config.STORAGE_TYPE

        if storage_type == "local":
            _storage_instance = LocalStorage(config.STORAGE_LOCAL_PATH)
            print(f"✅ 使用本地存储: {config.STORAGE_LOCAL_PATH}")

        elif storage_type == "eos":
            if not all([
                config.EOS_ENDPOINT,
                config.EOS_BUCKET,
                config.EOS_ACCESS_KEY,
                config.EOS_SECRET_KEY
            ]):
                raise ValueError("EOS配置不完整，请检查环境变量")

            _storage_instance = EOSStorage(
                endpoint=config.EOS_ENDPOINT,
                bucket=config.EOS_BUCKET,
                access_key=config.EOS_ACCESS_KEY,
                secret_key=config.EOS_SECRET_KEY,
                region=config.EOS_REGION
            )
            print(f"✅ 使用EOS存储: {config.EOS_ENDPOINT}/{config.EOS_BUCKET}")

        else:
            raise ValueError(f"不支持的存储类型: {storage_type}")

    return _storage_instance


def reset_storage():
    """重置存储实例（用于测试）"""
    global _storage_instance
    _storage_instance = None


__all__ = ['StorageBackend', 'LocalStorage', 'EOSStorage', 'get_storage', 'reset_storage']
