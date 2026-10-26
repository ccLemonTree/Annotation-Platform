# -*- coding: utf-8 -*-
"""
@File    : alarm_type_fetcher.py
@Time    : 2025/11/29
@Author  : 陈冲
@Description : 通过专用统计接口获取预警类型alarmTypeId和alarmTypeName映射
"""

# -*- coding: utf-8 -*-
import os
import requests
import json
from typing import Dict, List, Any, Optional
from integrations.smarteye.api.platform_enum import refresh_local_cache
from integrations.smarteye.api.db_utils import save_platform_config_to_db, get_existing_platform_ids
from integrations.smarteye.api.config import URLS, DEFAULT_HEADERS, AUTH_CREDENTIALS
from integrations.smarteye.api.utils import logger


class AlarmTypeFetcher:
    """预警类型获取器（基于 config.py 全局配置，使用统一日志输出）"""

    def __init__(self, auth_token: str):
        self.auth_token = auth_token
        # 从 config 获取接口地址
        self.platform_api = URLS.get("platform")
        self.alarm_type_api = URLS.get("alarm_stat")
        # 构建初始请求头
        self.headers = self._get_headers()

    def _get_headers(self) -> Dict[str, str]:
        """构建完整的请求头：合并基础配置、认证信息和动态 Token"""
        headers = DEFAULT_HEADERS.copy()
        headers.update(AUTH_CREDENTIALS)
        # 注入用户登录获取的动态 token
        headers["token"] = self.auth_token
        return headers

    def get_all_platforms(self) -> Dict[str, str]:
        """从接口获取所有平台信息"""
        logger.info(f"正在从接口获取平台列表: {self.platform_api}")
        try:
            # 根据要求，发送空 {} 获取所有平台
            response = requests.post(self.platform_api, headers=self.headers, json={}, timeout=100)
            response.raise_for_status()
            res_json = response.json()

            mapping = {}
            if res_json.get("code") == 0 and isinstance(res_json.get("data"), list):
                for item in res_json["data"]:
                    # 优先取 platName，备选 name
                    name = item.get("platName") or item.get("name")
                    p_id = item.get("platId") or item.get("id")
                    if name and p_id:
                        mapping[name] = str(p_id)
                logger.info(f"✅ 成功获取 {len(mapping)} 个平台")
                return mapping
            else:
                logger.error(f"❌ 获取平台列表失败: {res_json.get('msg')}")
                return {}
        except Exception as e:
            logger.error(f"❌ 请求平台列表异常: {str(e)}")
            return {}

    def get_platform_alarm_types(self, plat_id: str) -> Dict[str, str]:
        """获取单个平台的预警类型映射"""
        payload = {"platId": plat_id, "startTime": "", "endTime": ""}
        try:
            response = requests.post(self.alarm_type_api, headers=self.headers, json=payload, timeout=100)
            res_data = response.json()

            types_map = {}
            if res_data.get("code") == 0 and isinstance(res_data.get("data"), list):
                for item in res_data["data"]:
                    t_id = item.get("alarmTypeId")
                    t_name = item.get("alarmTypeName") or f"未知类型_{t_id}"
                    if t_id is not None:
                        types_map[t_name] = str(t_id)
            return types_map
        except Exception as e:
            logger.error(f"获取平台 {plat_id} 的预警类型失败: {e}")
            return {}

    def get_combined_mapping_data(self) -> Dict[str, Any]:
        """构造整合后的数据结构（全量抓取所有平台的所有预警类型）"""
        platforms = self.get_all_platforms()
        
        combined_enum = {}
        logger.info(f"准备抓取 {len(platforms)} 个平台的预警类型...")

        import concurrent.futures
        from integrations.smarteye.runtime import ContextExecutor as ThreadPoolExecutor

        # 使用线程池并发加速，控制 max_workers=10 防止过载
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_plat = {
                executor.submit(self.get_platform_alarm_types, p_id): (name, p_id)
                for name, p_id in platforms.items()
            }
            
            for future in concurrent.futures.as_completed(future_to_plat):
                name, p_id = future_to_plat[future]
                try:
                    alarm_types = future.result()
                    # logger.info(f"  → 成功获取预警类型: {name} (共 {len(alarm_types)} 种)")
                    combined_enum[name] = {
                        "platId": p_id,
                        "alarmTypes": alarm_types
                    }
                except Exception as e:
                    logger.error(f"  → 抓取平台预警类型失败: {name} (ID: {p_id}) - {e}")
            
        return combined_enum

    def update_all_and_refresh(self):
        """核心方法：抓取 -> 写入数据库 -> 刷新内存缓存"""
        logger.info("开始全量更新预警类型映射...")

        # 1. 抓取新数据
        new_data = self.get_combined_mapping_data()
        
        # 获取原有完整数据
        from integrations.smarteye.api.db_utils import get_all_platforms_from_db
        all_data = get_all_platforms_from_db()

        if new_data:
            # 2. 将新数据持久化到数据库
            try:
                save_platform_config_to_db(new_data)
                # 持久化后，深度将新数据合并到全量数据中，避免覆盖已存在的 alarmTypes
                for p_name, p_info in new_data.items():
                    if p_name in all_data:
                        all_data[p_name]["platId"] = p_info.get("platId", all_data[p_name].get("platId"))
                        if "alarmTypes" not in all_data[p_name]:
                            all_data[p_name]["alarmTypes"] = {}
                        all_data[p_name]["alarmTypes"].update(p_info.get("alarmTypes", {}))
                    else:
                        all_data[p_name] = p_info
            except Exception as e:
                logger.error(f"❌ 写入数据库失败: {e}", exc_info=True)
        else:
            logger.info("未发现新平台，无需更新数据库。")

        if not all_data:
             logger.warning("🛑 获取本地/远程数据失败，操作终止。")
             return

        # 3. 刷新内存，必须使用完整的新旧合并数据
        refresh_local_cache(all_data)
        logger.info("🚀 内存缓存刷新完成！后续业务逻辑已实现同步。")
        return all_data


if __name__ == "__main__":
    TOKEN = os.environ["SMARTEYE_ACCESS_TOKEN"]

    fetcher = AlarmTypeFetcher(TOKEN)

    # 执行流程：获取平台 -> 获取类型 -> 生成 Python 枚举代码 -> 写入文件
    fetcher.update_all_and_refresh()
