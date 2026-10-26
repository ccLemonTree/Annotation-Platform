"""
配置管理
使用 Pydantic Settings 管理所有环境变量
"""

from typing import Optional, Literal
import json
from pathlib import Path
from pydantic import Field
from sqlalchemy.engine import URL
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"  # 忽略.env中的额外字段
    )

    # ==================== 数据库配置 ====================
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USER: str = "postgres"
    DB_PASSWORD: str = Field(default="", repr=False)
    DB_NAME: str = "data_platform"

    @property
    def DATABASE_URL(self) -> str:
        """构建数据库连接字符串"""
        return URL.create("postgresql+asyncpg", username=self.DB_USER, password=self.DB_PASSWORD,
                          host=self.DB_HOST, port=self.DB_PORT, database=self.DB_NAME).render_as_string(hide_password=False)

    # ==================== 存储配置 ====================
    STORAGE_TYPE: Literal["local", "eos"] = "local"
    STORAGE_LOCAL_PATH: str = "./data"
    UPLOAD_DIR: str = "./uploads"  # 文件上传目录
    MAX_UPLOAD_SIZE: int = Field(default=10 * 1024 * 1024, ge=1)
    MAX_ARCHIVE_UPLOAD_SIZE: int = Field(default=10 * 1024**3, ge=1)
    MAX_ARCHIVE_EXPANDED_SIZE: int = Field(default=20 * 1024**3, ge=1)
    MAX_ARCHIVE_FILES: int = Field(default=50000, ge=1)
    UPLOAD_RETENTION_DAYS: int = Field(default=7, ge=1)

    # EOS配置
    EOS_ENDPOINT: Optional[str] = None
    EOS_BUCKET: Optional[str] = None
    EOS_ACCESS_KEY: Optional[str] = None
    EOS_SECRET_KEY: Optional[str] = None
    EOS_REGION: str = "us-east-1"

    # ==================== 模型服务配置 ====================
    QWEN_API_URL: str = "http://localhost:8000/v1"
    QWEN_API_KEY: str = ""
    SCENE_CLASSIFICATION_CONCURRENCY: int = Field(default=4, ge=1, le=16)
    AUTOMATION_CONCURRENCY: int = Field(default=2, ge=1, le=4)

    EMBEDDING_API_URL: str = "http://localhost:8001/v1"
    EMBEDDING_API_KEY: str = ""
    MILVUS_URL: str = "http://192.168.1.115:19530"
    MILVUS_COLLECTION: str = "image_platform_annotations_v1"

    RERANKER_API_URL: str = "http://localhost:8002/v1"
    RERANKER_API_KEY: str = ""

    # ==================== 训练配置 ====================
    GPU_SSH_MANAGER_ENTRY: str = ""
    GPU_SSH_NODE_BINARY: str = "node"
    GPU_SERVER_NAME: str = "绍兴a100"
    MODEL_ARCHIVE_PATH: str = str(Path(__file__).resolve().parents[1] / 'data' / 'model-releases')
    GPU_CREDENTIAL_KEY: str = Field(default="", repr=False)
    K8S_KUBECONFIG: str = "~/.kube/config"
    TRAINING_NAMESPACE: str = "training"
    TRAINING_GPU_TYPE: str = "nvidia.com/gpu"

    # ==================== 应用配置 ====================
    APP_ENV: Literal["development", "production"] = "development"
    PROJECT_NAME: str = "图像标注数据管理平台"
    VERSION: str = "1.0.0"
    JWT_SECRET_KEY: str = Field(min_length=32, repr=False)
    JWT_EXPIRATION: int = Field(default=3600, ge=1)
    DEFAULT_TENANT_ID: int = Field(default=1, ge=1)
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def CORS_ORIGINS_LIST(self) -> list[str]:
        """解析CORS允许的域名列表"""
        values = json.loads(self.CORS_ORIGINS) if self.CORS_ORIGINS.lstrip().startswith('[') else self.CORS_ORIGINS.split(',')
        if not isinstance(values, list) or any(not isinstance(origin, str) for origin in values):
            raise ValueError('CORS_ORIGINS必须为逗号分隔的来源或JSON字符串数组')
        return [origin.strip() for origin in values if origin.strip()]

    # ==================== Redis配置 ====================
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0

    @property
    def REDIS_URL(self) -> str:
        """构建Redis连接字符串"""
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ==================== Celery配置 ====================
    CELERY_BROKER_URL: Optional[str] = None
    CELERY_RESULT_BACKEND: Optional[str] = None

    @property
    def CELERY_BROKER(self) -> str:
        """Celery Broker URL"""
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def CELERY_BACKEND(self) -> str:
        """Celery Result Backend URL"""
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例（带缓存）"""
    return Settings()


# 全局配置实例
settings = get_settings()


if __name__ == "__main__":
    # 测试配置
    print("📋 配置信息:")
    print(f"  数据库: {settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    print(f"  存储类型: {settings.STORAGE_TYPE}")
    print(f"  存储路径: {settings.STORAGE_LOCAL_PATH}")
    print(f"  CORS: {settings.CORS_ORIGINS_LIST}")
    print(f"  Redis: {settings.REDIS_HOST}:{settings.REDIS_PORT}")
