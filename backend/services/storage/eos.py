"""
EOS对象存储实现（S3兼容）
"""

import boto3
from botocore.client import Config
from typing import BinaryIO, Dict, Optional

from .base import StorageBackend


class EOSStorage(StorageBackend):
    """EOS对象存储（S3兼容）"""

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1"
    ):
        self.s3_client = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version='s3v4')
        )
        self.bucket = bucket

    async def save_file(
        self,
        file_data: BinaryIO,
        path: str,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """上传文件到EOS"""
        extra_args = {}
        if metadata:
            extra_args['Metadata'] = {
                'sha256': metadata.get('content_hash', ''),
                'original-name': metadata.get('original_name', '')
            }
            if metadata.get('content_type'):
                extra_args['ContentType'] = metadata['content_type']

        self.s3_client.upload_fileobj(
            file_data,
            self.bucket,
            path,
            ExtraArgs=extra_args
        )

        # 获取对象元数据
        head = self.s3_client.head_object(Bucket=self.bucket, Key=path)

        return {
            "path": path,
            "size": head['ContentLength'],
            "hash": head['Metadata'].get('sha256', '')
        }

    async def get_file(self, path: str) -> BinaryIO:
        """获取文件流"""
        response = self.s3_client.get_object(Bucket=self.bucket, Key=path)
        return response['Body']

    async def file_exists(self, path: str) -> bool:
        """检查文件是否存在"""
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=path)
            return True
        except:
            return False

    def get_public_url(self, path: str, expires: int = 3600) -> str:
        """
        生成访问URL
        - expires=0: 永久URL（需要bucket开启公开读）
        - expires>0: 预签名URL
        """
        if expires == 0:
            # 永久URL（需要bucket开启公开读）
            return f"{self.s3_client.meta.endpoint_url}/{self.bucket}/{path}"
        else:
            # 预签名URL
            return self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket, 'Key': path},
                ExpiresIn=expires
            )

    async def copy_file(self, src_path: str, dst_path: str) -> bool:
        """
        EOS内部复制（服务端操作）

        这是EOS的零拷贝实现：
        使用S3的copy_object，在服务端完成复制，无需下载上传
        """
        try:
            self.s3_client.copy_object(
                CopySource={'Bucket': self.bucket, 'Key': src_path},
                Bucket=self.bucket,
                Key=dst_path
            )
            return True
        except Exception as e:
            print(f"EOS复制失败: {e}")
            return False

    async def delete_file(self, path: str) -> bool:
        """删除文件"""
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=path)
            return True
        except:
            return False

    async def ensure_directory(self, path: str) -> bool:
        """
        EOS对象存储无需创建目录
        对象存储使用扁平命名空间，目录只是key的前缀
        """
        return True  # EOS无需创建目录，始终返回成功
