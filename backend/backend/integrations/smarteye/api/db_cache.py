from integrations.smarteye.configuration import connection_kwargs
# -*- coding: utf-8 -*-
"""
数据库缓存和连接池管理模块
提供设备缓存、水位线管理等优化功能
"""
import os
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from integrations.smarteye.api.utils import logger


# 全局连接池
_connection_pool: Optional[pool.ThreadedConnectionPool] = None


def init_connection_pool(minconn=2, maxconn=20):
    """初始化数据库连接池"""
    global _connection_pool
    if _connection_pool is None:
        try:
            _connection_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn,
                maxconn,
                **connection_kwargs()
            )
            logger.info(f"✅ 数据库连接池初始化成功 (min={minconn}, max={maxconn})")
        except Exception as e:
            logger.error(f"❌ 数据库连接池初始化失败: {e}")
            _connection_pool = None


def get_pooled_connection():
    """从连接池获取连接"""
    if _connection_pool is None:
        init_connection_pool()
    try:
        return _connection_pool.getconn()
    except Exception as e:
        logger.error(f"获取数据库连接失败: {e}")
        return None


def return_pooled_connection(conn):
    """将连接归还到连接池"""
    if _connection_pool and conn:
        _connection_pool.putconn(conn)


def ensure_cache_tables():
    """创建缓存相关表"""
    from integrations.smarteye.api.db_utils import get_db_connection
    conn = get_db_connection()
    if not conn:
        return False

    # 设备缓存表（增强版，加入 updated_at 和 cache_key）
    create_devices_sql = """
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            device_name TEXT,
            alarm_type_names TEXT[],
            base_url TEXT,
            plat_id TEXT,
            platform_name TEXT,
            cache_key TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """

    # 索引：按平台查询、按缓存key查询、按更新时间清理
    create_devices_idx_sql = """
        CREATE INDEX IF NOT EXISTS idx_devices_plat_id ON devices(plat_id);
        CREATE INDEX IF NOT EXISTS idx_devices_cache_key ON devices(cache_key);
        CREATE INDEX IF NOT EXISTS idx_devices_updated_at ON devices(updated_at DESC);
    """

    # GIN 索引：加速 alarm_type_names 数组查询
    create_devices_gin_idx_sql = """
        CREATE INDEX IF NOT EXISTS idx_devices_alarm_type_names_gin
        ON devices USING GIN (alarm_type_names)
    """

    # 台账水位线表
    create_watermarks_sql = """
        CREATE TABLE IF NOT EXISTS ledger_watermarks (
            watermark_id SERIAL PRIMARY KEY,
            sched_id TEXT NOT NULL,
            plat_id TEXT NOT NULL,
            alarm_code TEXT NOT NULL,
            last_seen_time TIMESTAMP NOT NULL,
            last_analyse_id TEXT,
            record_count INTEGER DEFAULT 0,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(sched_id, plat_id, alarm_code)
        )
    """

    create_watermarks_idx_sql = """
        CREATE INDEX IF NOT EXISTS idx_ledger_watermarks_lookup
        ON ledger_watermarks(sched_id, plat_id, alarm_code);
    """

    try:
        with conn.cursor() as cursor:
            cursor.execute(create_devices_sql)
            # 旧表可能没有 cache_key / updated_at 列，用 ADD COLUMN IF NOT EXISTS 补充
            cursor.execute("""
                ALTER TABLE devices ADD COLUMN IF NOT EXISTS
                    cache_key TEXT NOT NULL DEFAULT '';
                ALTER TABLE devices ADD COLUMN IF NOT EXISTS
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW();
            """)
            cursor.execute(create_devices_idx_sql)
            cursor.execute(create_devices_gin_idx_sql)
            cursor.execute(create_watermarks_sql)
            cursor.execute(create_watermarks_idx_sql)
        conn.commit()
        logger.info("✅ 缓存表和索引创建成功")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ 创建缓存表失败: {e}")
        return False
    finally:
        conn.close()


def get_cached_devices(
    plat_id: str,
    alarm_codes: List[str],
    ttl_minutes: int = 30
) -> Optional[List[Dict]]:
    """
    从缓存获取设备列表

    Args:
        plat_id: 平台ID
        alarm_codes: 告警类型编码列表
        ttl_minutes: 缓存有效期（分钟）

    Returns:
        设备列表，如果缓存过期或不存在返回 None
    """
    cache_key = f"{plat_id}:{','.join(sorted(alarm_codes))}"
    conn = get_pooled_connection()
    if not conn:
        return None

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # 检查缓存是否存在且未过期
            check_sql = """
                SELECT COUNT(*) as cnt, MAX(updated_at) as last_update
                FROM devices
                WHERE cache_key = %s
            """
            cursor.execute(check_sql, (cache_key,))
            result = cursor.fetchone()

            if not result or result['cnt'] == 0:
                return None

            last_update = result['last_update']
            if datetime.now() - last_update > timedelta(minutes=ttl_minutes):
                logger.info(f"[缓存] 设备缓存已过期 (key={cache_key}, age={datetime.now() - last_update})")
                return None

            # 返回缓存的设备列表
            query_sql = """
                SELECT device_id, device_name, base_url, plat_id, platform_name
                FROM devices
                WHERE cache_key = %s
                ORDER BY device_name
            """
            cursor.execute(query_sql, (cache_key,))
            devices = cursor.fetchall()
            logger.info(f"[缓存] 命中设备缓存: {len(devices)} 台设备 (key={cache_key})")
            return [dict(d) for d in devices]

    except Exception as e:
        logger.error(f"读取设备缓存失败: {e}")
        return None
    finally:
        return_pooled_connection(conn)


def cache_devices(
    plat_id: str,
    platform_name: str,
    base_url: str,
    alarm_codes: List[str],
    devices: List[Dict]
) -> bool:
    """
    缓存设备列表

    Args:
        plat_id: 平台ID
        platform_name: 平台名称
        base_url: 平台根地址
        alarm_codes: 告警类型编码列表
        devices: 设备列表

    Returns:
        是否成功
    """
    if not devices:
        return False

    cache_key = f"{plat_id}:{','.join(sorted(alarm_codes))}"
    conn = get_pooled_connection()
    if not conn:
        return False

    try:
        with conn.cursor() as cursor:
            # 先删除旧缓存
            cursor.execute("DELETE FROM devices WHERE cache_key = %s", (cache_key,))

            # 批量插入新设备
            insert_sql = """
                INSERT INTO devices (
                    device_id, device_name, base_url, plat_id,
                    platform_name, cache_key, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (device_id) DO UPDATE SET
                    device_name = EXCLUDED.device_name,
                    base_url = EXCLUDED.base_url,
                    plat_id = EXCLUDED.plat_id,
                    platform_name = EXCLUDED.platform_name,
                    cache_key = EXCLUDED.cache_key,
                    updated_at = NOW()
            """

            for dev in devices:
                cursor.execute(insert_sql, (
                    dev.get('device_id'),
                    dev.get('device_name', ''),
                    base_url,
                    plat_id,
                    platform_name,
                    cache_key
                ))

        conn.commit()
        logger.info(f"[缓存] 已缓存 {len(devices)} 台设备 (key={cache_key})")
        return True

    except Exception as e:
        conn.rollback()
        logger.error(f"缓存设备列表失败: {e}")
        return False
    finally:
        return_pooled_connection(conn)


def get_ledger_watermark(
    sched_id: str,
    plat_id: str,
    alarm_code: str
) -> Optional[datetime]:
    """
    获取台账下载水位线

    Returns:
        上次最新记录的时间，如果不存在返回 None
    """
    conn = get_pooled_connection()
    if not conn:
        return None

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            sql = """
                SELECT last_seen_time
                FROM ledger_watermarks
                WHERE sched_id = %s AND plat_id = %s AND alarm_code = %s
            """
            cursor.execute(sql, (sched_id, plat_id, alarm_code))
            result = cursor.fetchone()

            if result:
                logger.info(f"[水位线] {sched_id}/{plat_id}/{alarm_code} = {result['last_seen_time']}")
                return result['last_seen_time']
            return None

    except Exception as e:
        logger.error(f"读取水位线失败: {e}")
        return None
    finally:
        return_pooled_connection(conn)


def update_ledger_watermark(
    sched_id: str,
    plat_id: str,
    alarm_code: str,
    last_seen_time: datetime,
    last_analyse_id: Optional[str] = None,
    record_count: int = 0
) -> bool:
    """
    更新台账下载水位线

    Args:
        sched_id: 调度器ID
        plat_id: 平台ID
        alarm_code: 告警类型编码
        last_seen_time: 最新记录时间
        last_analyse_id: 最新记录ID
        record_count: 本次处理的记录数

    Returns:
        是否成功
    """
    conn = get_pooled_connection()
    if not conn:
        return False

    try:
        with conn.cursor() as cursor:
            sql = """
                INSERT INTO ledger_watermarks (
                    sched_id, plat_id, alarm_code, last_seen_time,
                    last_analyse_id, record_count, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (sched_id, plat_id, alarm_code) DO UPDATE SET
                    last_seen_time = EXCLUDED.last_seen_time,
                    last_analyse_id = EXCLUDED.last_analyse_id,
                    record_count = ledger_watermarks.record_count + EXCLUDED.record_count,
                    updated_at = NOW()
            """
            cursor.execute(sql, (
                sched_id, plat_id, alarm_code,
                last_seen_time, last_analyse_id, record_count
            ))

        conn.commit()
        logger.info(f"[水位线] 更新成功: {sched_id}/{plat_id}/{alarm_code} -> {last_seen_time}")
        return True

    except Exception as e:
        conn.rollback()
        logger.error(f"更新水位线失败: {e}")
        return False
    finally:
        return_pooled_connection(conn)


def clean_expired_cache(days: int = 7) -> int:
    """
    清理过期缓存数据

    Args:
        days: 保留最近N天的数据

    Returns:
        清理的记录数
    """
    conn = get_pooled_connection()
    if not conn:
        return 0

    try:
        with conn.cursor() as cursor:
            # 清理设备缓存
            sql = """
                DELETE FROM devices
                WHERE updated_at < NOW() - INTERVAL '%s days'
            """
            cursor.execute(sql, (days,))
            deleted = cursor.rowcount

        conn.commit()
        if deleted > 0:
            logger.info(f"[缓存清理] 清理了 {deleted} 条过期设备缓存")
        return deleted

    except Exception as e:
        conn.rollback()
        logger.error(f"清理缓存失败: {e}")
        return 0
    finally:
        return_pooled_connection(conn)
