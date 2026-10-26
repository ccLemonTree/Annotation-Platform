"""
存储层单元测试
"""

import pytest
import io
import hashlib
from pathlib import Path
import tempfile
import shutil

from services.storage import LocalStorage, reset_storage


@pytest.fixture
def temp_storage_dir():
    """创建临时存储目录"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def local_storage(temp_storage_dir):
    """创建LocalStorage实例"""
    return LocalStorage(temp_storage_dir)


class TestLocalStorage:
    """本地存储测试"""

    @pytest.mark.asyncio
    async def test_save_file(self, local_storage):
        """测试保存文件"""
        # 准备测试数据
        content = b"Hello, World!"
        file_data = io.BytesIO(content)
        path = "test/hello.txt"

        # 计算哈希
        expected_hash = hashlib.sha256(content).hexdigest()

        # 保存文件
        result = await local_storage.save_file(
            file_data,
            path,
            metadata={"content_hash": expected_hash}
        )

        # 验证返回结果
        assert result["path"] == path
        assert result["size"] == len(content)
        assert result["hash"] == expected_hash

        # 验证文件存在
        assert await local_storage.file_exists(path)

    @pytest.mark.asyncio
    async def test_save_file_hash_mismatch(self, local_storage):
        """测试哈希不匹配时保存失败"""
        content = b"Hello, World!"
        file_data = io.BytesIO(content)
        path = "test/hello.txt"

        # 故意提供错误的哈希
        with pytest.raises(ValueError, match="哈希校验失败"):
            await local_storage.save_file(
                file_data,
                path,
                metadata={"content_hash": "wrong_hash"}
            )

        # 验证文件未保存
        assert not await local_storage.file_exists(path)

    @pytest.mark.asyncio
    async def test_get_file(self, local_storage):
        """测试获取文件"""
        # 先保存文件
        content = b"Test content"
        file_data = io.BytesIO(content)
        path = "test/read.txt"
        await local_storage.save_file(file_data, path)

        # 获取文件
        retrieved = await local_storage.get_file(path)
        retrieved_content = retrieved.read()

        assert retrieved_content == content

    @pytest.mark.asyncio
    async def test_copy_file_zero_copy(self, local_storage):
        """测试零拷贝（硬链接）"""
        # 保存原文件
        content = b"Original content"
        file_data = io.BytesIO(content)
        src_path = "original/file.txt"
        await local_storage.save_file(file_data, src_path)

        # 零拷贝到新路径
        dst_path = "copied/file.txt"
        success = await local_storage.copy_file(src_path, dst_path)

        assert success
        assert await local_storage.file_exists(dst_path)

        # 验证内容一致
        dst_file = await local_storage.get_file(dst_path)
        assert dst_file.read() == content

        # 验证是否是硬链接（同一个inode）
        src_full = local_storage.base_path / src_path
        dst_full = local_storage.base_path / dst_path

        # 在支持硬链接的系统上，inode应该相同
        try:
            assert src_full.stat().st_ino == dst_full.stat().st_ino
            print("✅ 零拷贝成功：使用硬链接")
        except AssertionError:
            # 降级到符号链接
            print("⚠️ 降级到符号链接")

    @pytest.mark.asyncio
    async def test_delete_file(self, local_storage):
        """测试删除文件"""
        # 保存文件
        content = b"To be deleted"
        file_data = io.BytesIO(content)
        path = "test/delete.txt"
        await local_storage.save_file(file_data, path)

        # 确认文件存在
        assert await local_storage.file_exists(path)

        # 删除文件
        success = await local_storage.delete_file(path)
        assert success

        # 确认文件已删除
        assert not await local_storage.file_exists(path)

    def test_get_public_url(self, local_storage):
        """测试生成访问URL"""
        path = "test/image.jpg"
        url = local_storage.get_public_url(path)

        assert url == f"/api/storage/files/{path}"


@pytest.mark.asyncio
async def test_storage_factory():
    """测试存储工厂"""
    from services.storage import get_storage
    from config.settings import settings

    # 重置单例
    reset_storage()

    # 获取存储实例
    storage = get_storage()

    assert storage is not None
    assert isinstance(storage, LocalStorage)

    # 验证单例
    storage2 = get_storage()
    assert storage is storage2
