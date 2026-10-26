"""
平台管理器
管理各个子平台的配置和元数据
"""

from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class PlatformConfig:
    """平台配置"""
    name: str
    display_name: str
    base_url: str
    api_version: str
    requires_auth: bool
    supported_types: List[str]  # alarm, capture, reject


class PlatformManager:
    """平台管理器（从SmartEye迁移）"""

    # 平台配置（从SmartEye的platform_enum迁移）
    PLATFORMS: Dict[str, PlatformConfig] = {
        "xiaohongshu": PlatformConfig(
            name="xiaohongshu",
            display_name="小红书",
            base_url="https://api.xiaohongshu.com",
            api_version="v1",
            requires_auth=True,
            supported_types=["alarm", "capture"]
        ),
        "douyin": PlatformConfig(
            name="douyin",
            display_name="抖音",
            base_url="https://api.douyin.com",
            api_version="v1",
            requires_auth=True,
            supported_types=["alarm", "capture", "reject"]
        ),
        "weibo": PlatformConfig(
            name="weibo",
            display_name="微博",
            base_url="https://api.weibo.com",
            api_version="v2",
            requires_auth=True,
            supported_types=["capture"]
        ),
    }

    @classmethod
    def get_platform(cls, platform_name: str) -> Optional[PlatformConfig]:
        """获取平台配置"""
        return cls.PLATFORMS.get(platform_name)

    @classmethod
    def list_platforms(cls) -> List[Dict[str, any]]:
        """列出所有平台"""
        return [
            {
                "name": config.name,
                "display_name": config.display_name,
                "supported_types": config.supported_types
            }
            for config in cls.PLATFORMS.values()
        ]

    @classmethod
    def validate_platform(cls, platform_name: str) -> bool:
        """验证平台是否支持"""
        return platform_name in cls.PLATFORMS
