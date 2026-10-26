# -*- coding: utf-8 -*-
"""按平台和本轮时间窗口读取预警；结果仅在任务内存中使用。"""
import json
from integrations.smarteye.configuration import get_env
from datetime import datetime

import pymysql


def query_alarm_candidates(platform: dict, start_time: str, end_time: str,
                           alarm_codes: list) -> list:
    if datetime.fromisoformat(start_time) >= datetime.fromisoformat(end_time):
        raise ValueError("预警查询开始时间必须早于结束时间")
    if not alarm_codes:
        return []

    configs = json.loads(get_env("LEDGER_ALARM_DATABASES", "{}"))
    plat_id = str(platform.get("plat_id", ""))
    config = configs.get(plat_id)
    if not config:
        raise ValueError(f"未配置平台 {platform.get('platform_name', plat_id)} ({plat_id}) 的预警数据库")

    connection = pymysql.connect(
        **config, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10, read_timeout=60, write_timeout=10,
    )
    try:
        with connection.cursor() as cursor:
            placeholders = ", ".join(["%s"] * len(alarm_codes))
            cursor.execute(
                "SELECT id, device_id, alarm_time, alarm_type_id "
                "FROM aisf_alarm_analysis "
                "WHERE alarm_time >= %s AND alarm_time < %s "
                f"AND alarm_type_id IN ({placeholders}) AND device_id <> '' "
                "ORDER BY alarm_time, id",
                (start_time, end_time, *alarm_codes),
            )
            return list(cursor.fetchall())
    finally:
        connection.close()
