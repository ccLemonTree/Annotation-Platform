"""
配置管理模块
使用 pydantic-settings 管理环境变量
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """应用配置"""

    # ===== 应用配置 =====
    APP_NAME: str = "数据管理平台"
    APP_ENV: str = "development"
    DEBUG: bool = True
    API_PREFIX: str = "/api"

    # ===== 数据库配置 =====
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"
    DB_NAME: str = "data_platform"

    @property
    def DATABASE_URL(self) -> str:
        """构建数据库连接URL"""
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    # ===== 存储配置 =====
    STORAGE_TYPE: str = "local"  # local / eos
    STORAGE_LOCAL_PATH: str = "./data"

    # EOS配置
    EOS_ENDPOINT: Optional[str] = None
    EOS_BUCKET: Optional[str] = None
    EOS_ACCESS_KEY: Optional[str] = None
    EOS_SECRET_KEY: Optional[str] = None
    EOS_REGION: str = "us-east-1"

    # ===== SmartEye配置 =====
    SMARTEYE_API_BASE_URL: Optional[str] = None
    SMARTEYE_USERNAME: Optional[str] = None
    SMARTEYE_PASSWORD: Optional[str] = None
    SMARTEYE_MAX_WORKERS: int = 10

    # ===== 模型服务配置 =====
    QWEN_API_URL: Optional[str] = "http://localhost:8000/v1"
    QWEN_API_KEY: Optional[str] = None

    EMBEDDING_API_URL: Optional[str] = None
    RERANKER_API_URL: Optional[str] = None

    # ===== Redis配置（可选）=====
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # ===== JWT配置 =====
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ===== CORS配置 =====
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # ===== 文件上传限制 =====
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100MB

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True
    )


# 全局配置实例
settings = Settings()
