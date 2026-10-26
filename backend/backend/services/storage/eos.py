"""
EOS对象存储实现
用于生产环境
"""

import boto3
from botocore.client import Config
from typing import BinaryIO
from .base import StorageBackend


class EOSStorage(StorageBackend):
    """EOS对象存储（兼容S3协议）"""

    async def get_file(self, path: str) -> BinaryIO:
        from botocore.exceptions import ClientError
        try:
            return self.client.get_object(Bucket=self.bucket, Key=path)["Body"]
        except ClientError as error:
            if error.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise FileNotFoundError(path) from error
            raise

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1"
    ):
        self.bucket = bucket
        self.client = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version='s3v4')
        )

    async def save_file(self, file_data: BinaryIO, path: str) -> dict:
        """上传文件到EOS"""
        content = file_data.read()

        self.client.put_object(
            Bucket=self.bucket,
            Key=path,
            Body=content
        )

        url = self.client.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket, 'Key': path},
            ExpiresIn=3600
        )

        return {
            "url": url,
            "path": path,
            "size": len(content)
        }

    async def delete_file(self, path: str) -> bool:
        """删除EOS文件"""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=path)
            return True
        except Exception:
            return False

    async def get_file_url(self, path: str, expires_in: int = 3600) -> str:
        """生成临时访问URL"""
        url = self.client.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket, 'Key': path},
            ExpiresIn=expires_in
        )
        return url

    async def file_exists(self, path: str) -> bool:
        """检查文件是否存在"""
        try:
            self.client.head_object(Bucket=self.bucket, Key=path)
            return True
        except Exception:
            return False
