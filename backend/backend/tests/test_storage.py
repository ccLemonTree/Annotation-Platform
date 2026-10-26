import os
from io import BytesIO

import pytest
from services.storage import LocalStorage, get_storage, reset_storage

@pytest.fixture
def local_storage(tmp_path):
    return LocalStorage(str(tmp_path / "storage"))

@pytest.mark.asyncio
async def test_save_and_read(local_storage):
    saved = await local_storage.save_file(BytesIO(b"Hello, World!"), "nested/file.txt")
    assert saved["size"] == 13
    try:
        file = await local_storage.get_file("nested/file.txt")
    except AttributeError as error:
        pytest.fail(str(error))
    with file:
        assert file.read() == b"Hello, World!"
    assert await local_storage.file_exists("nested/file.txt")

@pytest.mark.asyncio
async def test_delete_local(local_storage):
    await local_storage.save_file(BytesIO(b"hello"), "test.txt")
    assert await local_storage.delete_file("test.txt")
    assert not await local_storage.file_exists("test.txt")
    assert not await local_storage.delete_file("test.txt")

@pytest.mark.asyncio
async def test_file_url(local_storage):
    assert await local_storage.get_file_url("test.jpg") == "/files/test.jpg"

@pytest.mark.asyncio
async def test_large_file_and_nested_directory(local_storage):
    data = b"x" * (1024 * 1024)
    saved = await local_storage.save_file(BytesIO(data), "one/two/large.bin")
    assert os.path.getsize(saved["path"]) == len(data)

@pytest.mark.asyncio
async def test_factory_uses_configured_root(tmp_path):
    from config.settings import Settings
    config = Settings(STORAGE_TYPE="local", STORAGE_LOCAL_PATH=str(tmp_path / "factory"))
    reset_storage()
    try:
        storage = get_storage(config)
        assert isinstance(storage, LocalStorage)
        assert storage.base_path.resolve() == (tmp_path / "factory").resolve()
    finally:
        reset_storage()

@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["save", "read", "exists", "delete"])
async def test_parent_traversal_rejected(local_storage, operation):
    try:
        with pytest.raises(ValueError):
            if operation == "save":
                await local_storage.save_file(BytesIO(b"x"), "../outside.txt")
            elif operation == "read":
                await local_storage.get_file("../outside.txt")
            elif operation == "exists":
                await local_storage.file_exists("../outside.txt")
            else:
                await local_storage.delete_file("../outside.txt")
    except AttributeError as error:
        pytest.fail(str(error))

@pytest.mark.asyncio
async def test_absolute_path_outside_root_rejected(local_storage, tmp_path):
    with pytest.raises(ValueError):
        await local_storage.save_file(BytesIO(b"x"), str(tmp_path / "outside.txt"))

@pytest.mark.asyncio
async def test_failed_replace_preserves_original(local_storage, monkeypatch):
    saved = await local_storage.save_file(BytesIO(b"original"), "item.bin")
    def fail(*args):
        raise OSError("planned write failure")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        await local_storage.save_file(BytesIO(b"replacement"), "item.bin")
    with open(saved["path"], "rb") as file:
        assert file.read() == b"original"

