from integrations.smarteye.api.db_utils import get_all_platforms_from_db

# 定义一个内部缓存变量
_PLATFORM_CACHE = None

def get_combined_platform_mapping():
    """获取平台映射（优先从内存读取，内存没有则读数据库）"""
    global _PLATFORM_CACHE

    # 1. 如果内存中已经有数据，直接返回
    if _PLATFORM_CACHE is not None:
        return _PLATFORM_CACHE

    # 2. 如果内存没有，尝试从数据库加载
    try:
        data = get_all_platforms_from_db()
        if data:
            _PLATFORM_CACHE = data
            return _PLATFORM_CACHE
    except Exception as e:
        print(f"读取数据库配置失败: {e}")

    return {}


def refresh_local_cache(new_data: dict):
    """手动刷新内存缓存"""
    global _PLATFORM_CACHE
    _PLATFORM_CACHE = new_data
