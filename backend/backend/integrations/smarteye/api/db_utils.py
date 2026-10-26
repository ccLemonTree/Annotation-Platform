from integrations.smarteye.configuration import connection_kwargs
# -*- coding: utf-8 -*-
import json
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from integrations.smarteye.api.utils import logger


def get_db_connection():
    """获取数据库连接"""
    try:
        conn = psycopg2.connect(**connection_kwargs())
        return conn
    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        return None

def get_all_platforms_from_db():
    """
    从数据库获取所有平台及告警类型配置，
    并转换为原 JSON 结构的格式。
    """
    conn = get_db_connection()
    if not conn:
        return {}

    mapping = {}
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # 1. 获取所有平台
            cursor.execute("SELECT id, platform_name, plat_id FROM platforms")
            platforms = cursor.fetchall()

            for plat in platforms:
                plat_name = plat['platform_name']
                plat_id = plat['plat_id']
                
                # 初始化平台结构
                mapping[plat_name] = {
                    "platId": plat_id,
                    "alarmTypes": {}
                }

                # 2. 获取该平台下的所有告警类型
                cursor.execute(
                    "SELECT alarm_name, alarm_code FROM alarm_types WHERE plat_id = %s",
                    (plat_id,)
                )
                alarm_types = cursor.fetchall()
                
                for at in alarm_types:
                    # 映射结构: "告警名称": "告警编码"
                    mapping[plat_name]["alarmTypes"][at['alarm_name']] = at['alarm_code']

        return mapping

    except Exception as e:
        logger.error(f"从数据库读取配置失败: {e}")
        return {}
    finally:
        if conn:
            conn.close()

def save_platform_config_to_db(data):
    """
    将最新的爬虫数据保存/更新到数据库。
    data 结构: { "平台名": { "platId": "...", "alarmTypes": { "name": "code" } } }
    """
    conn = get_db_connection()
    if not conn:
        return

    try:
        with conn.cursor() as cursor:
            for plat_name, plat_info in data.items():
                plat_id = plat_info.get("platId")
                alarm_types = plat_info.get("alarmTypes", {})

                if not plat_id:
                    continue

                # 1. 仅插入新平台，忽略已存在的
                # 注意：plat_id 和 platform_name 都是 UNIQUE
                insert_plat_sql = """
                    INSERT INTO platforms (platform_name, plat_id)
                    VALUES (%s, %s)
                    ON CONFLICT (plat_id) DO UPDATE SET platform_name = EXCLUDED.platform_name
                """
                try:
                    cursor.execute(insert_plat_sql, (plat_name, plat_id))
                except psycopg2.errors.UniqueViolation:
                    # 如果仍然冲突（比如由于 platform_name 的唯一约束），则回滚并跳过此平台
                    conn.rollback()
                    logger.warning(f"跳过平台插入，由于名称冲突: {plat_name}")
                    continue

                # 2. 插入或更新告警类型
                insert_alarm_sql = """
                    INSERT INTO alarm_types (plat_id, alarm_name, alarm_code)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (plat_id, alarm_code) DO NOTHING
                """
                # 注意：这里我们选择 ON CONFLICT DO NOTHING，或者可以更新名称
                # 如果需要更新名称：
                # ON CONFLICT (plat_id, alarm_code) DO UPDATE SET alarm_name = EXCLUDED.alarm_name

                for a_name, a_code in alarm_types.items():
                    cursor.execute(insert_alarm_sql, (plat_id, a_name, str(a_code)))

            conn.commit()
            logger.info("✅ 数据库配置更新成功")

    except Exception as e:
        conn.rollback()
        logger.error(f"保存配置到数据库失败: {e}")
    finally:
        if conn:
            conn.close()

def get_existing_platform_ids():
    """获取数据库中已存在的平台ID集合"""
    conn = get_db_connection()
    if not conn:
        return set()

    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT plat_id FROM platforms")
            result = cursor.fetchall()
            return {row[0] for row in result}
    except Exception as e:
        logger.error(f"获取已存在平台列表失败: {e}")
        return set()
    finally:
        if conn:
            conn.close()


def ensure_smart_search_columns():
    """检查并扩展 platforms 表，新增智搜所需字段"""
    conn = get_db_connection()
    if not conn:
        return False

    columns_to_add = [
        ("base_url", "TEXT DEFAULT ''"),
        ("username", "TEXT DEFAULT ''"),
        ("password", "TEXT DEFAULT ''"),
        ("login_url", "TEXT DEFAULT '/api/oauth/anyTenant/login'"),
        ("state", "TEXT DEFAULT '1'"),
    ]

    try:
        with conn.cursor() as cursor:
            for col_name, col_def in columns_to_add:
                try:
                    cursor.execute(f"ALTER TABLE platforms ADD COLUMN {col_name} {col_def}")
                except psycopg2.errors.DuplicateColumn:
                    conn.rollback()
            conn.commit()
            logger.info("✅ platforms 表字段扩展完成")
            return True
    except Exception as e:
        conn.rollback()
        logger.error(f"扩展 platforms 表失败: {e}")
        return False
    finally:
        if conn:
            conn.close()


def sync_remote_platforms(remote_items):
    """
    将远程接口返回的平台数据同步到数据库。
    remote_items: list of dict，每个 dict 含 id, name, baseUrl, loginUrl, state 等字段。
    所有平台的真实登录凭证统一为 superAdmin/HzxfAdmin0321~
    """
    conn = get_db_connection()
    if not conn:
        return 0

    upsert_sql = """
        INSERT INTO platforms (plat_id, platform_name, base_url, username, password, login_url, state)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (plat_id) DO UPDATE SET
            platform_name = EXCLUDED.platform_name,
            base_url = EXCLUDED.base_url,
            username = EXCLUDED.username,
            password = EXCLUDED.password,
            login_url = EXCLUDED.login_url,
            state = EXCLUDED.state
    """

    count = 0
    try:
        with conn.cursor() as cursor:
            for item in remote_items:
                plat_id = str(item.get("id", "") or "")
                name = item.get("name", "") or ""
                base_url = (item.get("baseUrl", "") or "").rstrip("/")
                login_url = item.get("loginUrl", "") or "/api/oauth/anyTenant/login"
                state = str(item.get("state", "1") or "1")

                if not plat_id or not name:
                    continue

                # 逐条插入，用 savepoint 隔离冲突
                try:
                    cursor.execute("SAVEPOINT sp_sync")
                    cursor.execute(upsert_sql, (
                        plat_id, name, base_url,
                        "superAdmin", "HzxfAdmin0321~",
                        login_url, state
                    ))
                    cursor.execute("RELEASE SAVEPOINT sp_sync")
                    count += 1
                except Exception as row_err:
                    cursor.execute("ROLLBACK TO SAVEPOINT sp_sync")
                    logger.warning(f"跳过平台 [{name}]: {row_err}")

        conn.commit()
        logger.info(f"✅ 同步 {count} 个平台到数据库")
        return count
    except Exception as e:
        conn.rollback()
        logger.error(f"同步平台数据失败: {e}")
        return 0
    finally:
        if conn:
            conn.close()


def get_smart_search_platforms(only_active=True):
    """
    获取智搜平台列表（含 base_url, username, password 等字段）
    only_active=True 时只返回 state='1' 的启用平台
    """
    conn = get_db_connection()
    if not conn:
        return []

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            sql = """
                SELECT plat_id, platform_name, base_url, username, password, login_url, state
                FROM platforms
                WHERE base_url IS NOT NULL AND base_url != ''
            """
            if only_active:
                sql += " AND state = '1'"
            sql += " ORDER BY platform_name"

            cursor.execute(sql)
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"获取智搜平台列表失败: {e}")
        return []
    finally:
        if conn:
            conn.close()


def ensure_ai_image_tables():
    """创建 AI 生图相关表"""
    conn = get_db_connection()
    if not conn:
        return False

    create_tasks_sql = """
        CREATE TABLE IF NOT EXISTS ai_image_tasks (
            task_id TEXT PRIMARY KEY,
            task_type TEXT NOT NULL,
            prompt TEXT NOT NULL DEFAULT '',
            task_status TEXT NOT NULL DEFAULT 'pending',
            output_count INTEGER NOT NULL DEFAULT 1,
            repeat_count INTEGER NOT NULL DEFAULT 1,
            batch_size INTEGER NOT NULL DEFAULT 10,
            total_items INTEGER NOT NULL DEFAULT 0,
            completed_items INTEGER NOT NULL DEFAULT 0,
            success_items INTEGER NOT NULL DEFAULT 0,
            failed_items INTEGER NOT NULL DEFAULT 0,
            root_dir TEXT NOT NULL DEFAULT '',
            params_json TEXT NOT NULL DEFAULT '{}',
            request_summary_json TEXT NOT NULL DEFAULT '{}',
            latest_error TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMP NOT NULL
        )
    """

    create_items_sql = """
        CREATE TABLE IF NOT EXISTS ai_image_task_items (
            item_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES ai_image_tasks(task_id) ON DELETE CASCADE,
            batch_index INTEGER NOT NULL DEFAULT 1,
            asset_count INTEGER NOT NULL DEFAULT 0,
            item_status TEXT NOT NULL DEFAULT 'pending',
            volc_task_status TEXT NOT NULL DEFAULT '',
            volc_task_id TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            asset_list_json TEXT NOT NULL DEFAULT '[]',
            request_payload_json TEXT NOT NULL DEFAULT '{}',
            submit_response_json TEXT NOT NULL DEFAULT '{}',
            result_response_json TEXT NOT NULL DEFAULT '{}',
            result_urls_json TEXT NOT NULL DEFAULT '[]',
            result_files_json TEXT NOT NULL DEFAULT '[]',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """

    create_index_sql = """
        CREATE INDEX IF NOT EXISTS idx_ai_image_tasks_created_at
        ON ai_image_tasks(created_at DESC)
    """

    try:
        with conn.cursor() as cursor:
            cursor.execute(create_tasks_sql)
            cursor.execute(create_items_sql)
            cursor.execute(create_index_sql)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"创建 AI 生图表失败: {e}")
        return False
    finally:
        conn.close()


def create_ai_image_task(task: dict):
    conn = get_db_connection()
    if not conn:
        return False

    sql = """
        INSERT INTO ai_image_tasks (
            task_id, task_type, prompt, task_status, output_count, repeat_count, batch_size,
            total_items, completed_items, success_items, failed_items, root_dir,
            params_json, request_summary_json, latest_error, created_at, updated_at, expires_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
        )
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (
                task["task_id"],
                task["task_type"],
                task.get("prompt", ""),
                task.get("task_status", "pending"),
                task.get("output_count", 1),
                task.get("repeat_count", 1),
                task.get("batch_size", 10),
                task.get("total_items", 0),
                task.get("completed_items", 0),
                task.get("success_items", 0),
                task.get("failed_items", 0),
                task.get("root_dir", ""),
                json.dumps(task.get("params_json", {}), ensure_ascii=False),
                json.dumps(task.get("request_summary_json", {}), ensure_ascii=False),
                task.get("latest_error", ""),
                task["created_at"],
                task["updated_at"],
                task["expires_at"],
            ))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"创建 AI 生图任务失败: {e}")
        return False
    finally:
        conn.close()


def create_ai_image_task_items(items: list):
    if not items:
        return True

    conn = get_db_connection()
    if not conn:
        return False

    sql = """
        INSERT INTO ai_image_task_items (
            item_id, task_id, batch_index, asset_count, item_status, volc_task_status, volc_task_id,
            error_message, asset_list_json, request_payload_json, submit_response_json,
            result_response_json, result_urls_json, result_files_json, created_at, updated_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
    """

    try:
        with conn.cursor() as cursor:
            for item in items:
                cursor.execute(sql, (
                    item["item_id"],
                    item["task_id"],
                    item.get("batch_index", 1),
                    item.get("asset_count", 0),
                    item.get("item_status", "pending"),
                    item.get("volc_task_status", ""),
                    item.get("volc_task_id", ""),
                    item.get("error_message", ""),
                    json.dumps(item.get("asset_list_json", []), ensure_ascii=False),
                    json.dumps(item.get("request_payload_json", {}), ensure_ascii=False),
                    json.dumps(item.get("submit_response_json", {}), ensure_ascii=False),
                    json.dumps(item.get("result_response_json", {}), ensure_ascii=False),
                    json.dumps(item.get("result_urls_json", []), ensure_ascii=False),
                    json.dumps(item.get("result_files_json", []), ensure_ascii=False),
                    item["created_at"],
                    item["updated_at"],
                ))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"创建 AI 生图子任务失败: {e}")
        return False
    finally:
        conn.close()


def update_ai_image_task(task_id: str, fields: dict):
    if not fields:
        return True

    conn = get_db_connection()
    if not conn:
        return False

    allowed_json_fields = {"params_json", "request_summary_json"}
    set_clauses = []
    values = []

    for key, value in fields.items():
        set_clauses.append(f"{key} = %s")
        if key in allowed_json_fields:
            values.append(json.dumps(value, ensure_ascii=False))
        else:
            values.append(value)

    set_clauses.append("updated_at = NOW()")
    values.append(task_id)

    sql = f"UPDATE ai_image_tasks SET {', '.join(set_clauses)} WHERE task_id = %s"
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, values)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"更新 AI 生图主任务失败: {e}")
        return False
    finally:
        conn.close()


def update_ai_image_task_item(item_id: str, fields: dict):
    if not fields:
        return True

    conn = get_db_connection()
    if not conn:
        return False

    allowed_json_fields = {
        "asset_list_json",
        "request_payload_json",
        "submit_response_json",
        "result_response_json",
        "result_urls_json",
        "result_files_json",
    }
    set_clauses = []
    values = []

    for key, value in fields.items():
        set_clauses.append(f"{key} = %s")
        if key in allowed_json_fields:
            values.append(json.dumps(value, ensure_ascii=False))
        else:
            values.append(value)

    set_clauses.append("updated_at = NOW()")
    values.append(item_id)
    sql = f"UPDATE ai_image_task_items SET {', '.join(set_clauses)} WHERE item_id = %s"

    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, values)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"更新 AI 生图子任务失败: {e}")
        return False
    finally:
        conn.close()


def get_ai_image_history(page=1, page_size=20, prompt="", status="", start_date="", end_date=""):
    conn = get_db_connection()
    if not conn:
        return {"records": [], "total": 0, "pages": 0, "current": page}

    where = ["1=1"]
    params = []

    if prompt:
        where.append("prompt ILIKE %s")
        params.append(f"%{prompt}%")
    if status:
        where.append("task_status = %s")
        params.append(status)
    if start_date:
        where.append("created_at >= %s")
        params.append(start_date)
    if end_date:
        where.append("created_at <= %s")
        params.append(end_date)

    offset = max(page - 1, 0) * page_size
    where_sql = " AND ".join(where)

    count_sql = f"SELECT COUNT(*) FROM ai_image_tasks WHERE {where_sql}"
    data_sql = f"""
        SELECT *
        FROM ai_image_tasks
        WHERE {where_sql}
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(count_sql, params)
            total = cursor.fetchone()["count"]
            cursor.execute(data_sql, params + [page_size, offset])
            records = cursor.fetchall()

        for row in records:
            row["params_json"] = json.loads(row.get("params_json") or "{}")
            row["request_summary_json"] = json.loads(row.get("request_summary_json") or "{}")

        pages = (total + page_size - 1) // page_size if page_size else 0
        return {"records": records, "total": total, "pages": pages, "current": page}
    except Exception as e:
        logger.error(f"查询 AI 生图历史失败: {e}")
        return {"records": [], "total": 0, "pages": 0, "current": page}
    finally:
        conn.close()


def get_ai_image_task_detail(task_id: str):
    conn = get_db_connection()
    if not conn:
        return None

    task_sql = "SELECT * FROM ai_image_tasks WHERE task_id = %s"
    item_sql = """
        SELECT *
        FROM ai_image_task_items
        WHERE task_id = %s
        ORDER BY batch_index ASC, created_at ASC
    """

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(task_sql, (task_id,))
            task = cursor.fetchone()
            if not task:
                return None
            cursor.execute(item_sql, (task_id,))
            items = cursor.fetchall()

        task["params_json"] = json.loads(task.get("params_json") or "{}")
        task["request_summary_json"] = json.loads(task.get("request_summary_json") or "{}")
        for item in items:
            item["asset_list_json"] = json.loads(item.get("asset_list_json") or "[]")
            item["request_payload_json"] = json.loads(item.get("request_payload_json") or "{}")
            item["submit_response_json"] = json.loads(item.get("submit_response_json") or "{}")
            item["result_response_json"] = json.loads(item.get("result_response_json") or "{}")
            item["result_urls_json"] = json.loads(item.get("result_urls_json") or "[]")
            item["result_files_json"] = json.loads(item.get("result_files_json") or "[]")
        task["items"] = items
        return task
    except Exception as e:
        logger.error(f"查询 AI 生图任务详情失败: {e}")
        return None
    finally:
        conn.close()


def get_expired_ai_image_tasks():
    conn = get_db_connection()
    if not conn:
        return []

    sql = """
        SELECT task_id, root_dir
        FROM ai_image_tasks
        WHERE expires_at < NOW()
    """
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql)
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"查询过期 AI 生图任务失败: {e}")
        return []
    finally:
        conn.close()


def delete_ai_image_tasks(task_ids: list):
    if not task_ids:
        return True

    conn = get_db_connection()
    if not conn:
        return False

    sql = "DELETE FROM ai_image_tasks WHERE task_id = ANY(%s)"
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (task_ids,))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"删除 AI 生图任务失败: {e}")
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 台账监听下载
# ---------------------------------------------------------------------------

def ensure_ledger_watch_tables():
    """建立监听下载所需的两张表（幂等）"""
    conn = get_db_connection()
    if not conn:
        return False
    ddl = """
    CREATE TABLE IF NOT EXISTS ledger_watch_jobs (
        job_id      TEXT PRIMARY KEY,
        name        TEXT NOT NULL DEFAULT '',
        source_types TEXT NOT NULL DEFAULT 'alarm,reject',
        platform_ids TEXT NOT NULL DEFAULT '[]',
        alarm_type_filter TEXT NOT NULL DEFAULT '[]',
        small_model_mode TEXT NOT NULL DEFAULT 'crop',
        poll_interval_seconds INTEGER NOT NULL DEFAULT 60,
        time_offset_seconds INTEGER NOT NULL DEFAULT 20,
        enabled     BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS ledger_watch_watermarks (
        job_id      TEXT NOT NULL,
        platform_id TEXT NOT NULL,
        source_type TEXT NOT NULL,
        last_seen_time TEXT NOT NULL DEFAULT '',
        last_seen_id   TEXT NOT NULL DEFAULT '',
        updated_at  TIMESTAMP NOT NULL DEFAULT NOW(),
        PRIMARY KEY (job_id, platform_id, source_type)
    );
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(ddl)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"建立监听下载表失败: {e}")
        return False
    finally:
        conn.close()


def upsert_watch_job(job: dict) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    sql = """
        INSERT INTO ledger_watch_jobs
            (job_id, name, source_types, platform_ids, alarm_type_filter,
             small_model_mode, poll_interval_seconds, time_offset_seconds, enabled, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (job_id) DO UPDATE SET
            name = EXCLUDED.name,
            source_types = EXCLUDED.source_types,
            platform_ids = EXCLUDED.platform_ids,
            alarm_type_filter = EXCLUDED.alarm_type_filter,
            small_model_mode = EXCLUDED.small_model_mode,
            poll_interval_seconds = EXCLUDED.poll_interval_seconds,
            time_offset_seconds = EXCLUDED.time_offset_seconds,
            enabled = EXCLUDED.enabled,
            updated_at = NOW()
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (
                job["job_id"],
                job.get("name", ""),
                job.get("source_types", "alarm,reject"),
                json.dumps(job.get("platform_ids", []), ensure_ascii=False),
                json.dumps(job.get("alarm_type_filter", []), ensure_ascii=False),
                job.get("small_model_mode", "crop"),
                job.get("poll_interval_seconds", 60),
                job.get("time_offset_seconds", 20),
                job.get("enabled", True),
            ))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"保存监听任务失败: {e}")
        return False
    finally:
        conn.close()


def get_watch_jobs(only_enabled=False) -> list:
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            sql = "SELECT * FROM ledger_watch_jobs"
            if only_enabled:
                sql += " WHERE enabled = TRUE"
            sql += " ORDER BY created_at DESC"
            cursor.execute(sql)
            rows = cursor.fetchall()
        result = []
        for row in rows:
            r = dict(row)
            r["platform_ids"] = json.loads(r.get("platform_ids") or "[]")
            r["alarm_type_filter"] = json.loads(r.get("alarm_type_filter") or "[]")
            result.append(r)
        return result
    except Exception as e:
        logger.error(f"查询监听任务失败: {e}")
        return []
    finally:
        conn.close()


def delete_watch_job(job_id: str) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM ledger_watch_jobs WHERE job_id=%s", (job_id,))
            cursor.execute("DELETE FROM ledger_watch_watermarks WHERE job_id=%s", (job_id,))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"删除监听任务失败: {e}")
        return False
    finally:
        conn.close()


def get_watch_watermark(job_id: str, platform_id: str, source_type: str) -> dict:
    conn = get_db_connection()
    if not conn:
        return {}
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT last_seen_time, last_seen_id FROM ledger_watch_watermarks "
                "WHERE job_id=%s AND platform_id=%s AND source_type=%s",
                (job_id, platform_id, source_type),
            )
            row = cursor.fetchone()
        return dict(row) if row else {}
    except Exception as e:
        logger.error(f"查询水位线失败: {e}")
        return {}
    finally:
        conn.close()


def set_watch_watermark(job_id: str, platform_id: str, source_type: str,
                        last_seen_time: str, last_seen_id: str) -> None:
    conn = get_db_connection()
    if not conn:
        return
    sql = """
        INSERT INTO ledger_watch_watermarks
            (job_id, platform_id, source_type, last_seen_time, last_seen_id, updated_at)
        VALUES (%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (job_id, platform_id, source_type) DO UPDATE SET
            last_seen_time = EXCLUDED.last_seen_time,
            last_seen_id   = EXCLUDED.last_seen_id,
            updated_at     = NOW()
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (job_id, platform_id, source_type, last_seen_time, last_seen_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"更新水位线失败: {e}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Devices table
# ---------------------------------------------------------------------------

def ensure_devices_table() -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    sql = """
        CREATE TABLE IF NOT EXISTS devices (
            device_id      TEXT PRIMARY KEY,
            device_name    TEXT,
            alarm_type_names TEXT[],
            base_url       TEXT,
            plat_id        TEXT,
            platform_name  TEXT,
            updated_at     TIMESTAMP DEFAULT NOW()
        )
    """
    idx_sql = """
        CREATE INDEX IF NOT EXISTS idx_devices_plat_id ON devices(plat_id)
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            cursor.execute(idx_sql)
        conn.commit()
        logger.info("✅ devices 表就绪")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"创建 devices 表失败: {e}")
        return False
    finally:
        conn.close()


def upsert_devices(devices: list) -> int:
    """批量 upsert 设备信息，返回写入行数。"""
    if not devices:
        return 0
    conn = get_db_connection()
    if not conn:
        return 0
    sql = """
        INSERT INTO devices (device_id, device_name, alarm_type_names, base_url, plat_id, platform_name, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (device_id) DO UPDATE SET
            device_name      = EXCLUDED.device_name,
            alarm_type_names = EXCLUDED.alarm_type_names,
            base_url         = EXCLUDED.base_url,
            plat_id          = EXCLUDED.plat_id,
            platform_name    = EXCLUDED.platform_name,
            updated_at       = NOW()
    """
    count = 0
    try:
        with conn.cursor() as cursor:
            for d in devices:
                alarm_names = d.get("alarm_type_names") or []
                cursor.execute(sql, (
                    d["device_id"],
                    d.get("device_name", ""),
                    alarm_names,
                    d.get("base_url", ""),
                    d.get("plat_id", ""),
                    d.get("platform_name", ""),
                ))
                count += 1
        conn.commit()
        return count
    except Exception as e:
        conn.rollback()
        logger.error(f"upsert_devices 失败: {e}")
        return 0
    finally:
        conn.close()


def get_devices_by_alarm_type_name(alarm_type_name: str, base_url: str = "") -> list:
    """返回绑定了指定告警类型的设备列表（可选按 base_url 过滤平台）。"""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            if base_url:
                cursor.execute(
                    "SELECT * FROM devices WHERE %s = ANY(alarm_type_names) AND base_url = %s",
                    (alarm_type_name, base_url)
                )
            else:
                cursor.execute(
                    "SELECT * FROM devices WHERE %s = ANY(alarm_type_names)",
                    (alarm_type_name,)
                )
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"get_devices_by_alarm_type_name 失败: {e}")
        return []
    finally:
        conn.close()
