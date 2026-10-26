from integrations.smarteye.configuration import get_env
# -*- coding: utf-8 -*-
"""
@File    : config.py
@Time    : 2025/11/30
@Description : 配置文件，存储API地址、请求头和认证信息
"""
import os

# API 基础地址
API_HOST = "36.139.165.206:8890"
API_BASE_URL = f"http://{API_HOST}"
CAPTCHA_MODEL_API = "http://39.185.65.85:8690/bigv1/chat/completions"
CAPTCHA_MODEL_NAME = "Qwen3-VL-8B-Instruct"

# 各个接口的URL
URLS = {
    "reject": f"{API_BASE_URL}/api/approval/dis/record",
    "alarm_stat": f"{API_BASE_URL}/api/stat/platform/alarmType",
    "alarm_list": f"{API_BASE_URL}/api/approval/approved/list",
    "capture": f"{API_BASE_URL}/api/capture/stat/log",
    "platform": f"{API_BASE_URL}/api/stat/platform",
    # "platform_alarmType": f"{API_BASE_URL}/stat/platform/alarmType"
}

# 默认请求头配置
DEFAULT_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-encoding": "gzip, deflate",
    "accept-language": "zh-CN,zh;q=0.9",
    "content-type": "application/json;charset=UTF-8",
    "gray_version": "zuihou",
    "host": API_HOST,
    "origin": API_BASE_URL,
    "proxy-connection": "keep-alive",
    "referer": f"{API_BASE_URL}/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
}

# 认证信息 (优先从环境变量获取，否则使用默认值)
AUTH_CREDENTIALS = {
    "applicationid": get_env("API_APPLICATION_ID", "1"),
    "authorization": get_env("API_AUTHORIZATION", "bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ="),
    "tenantid": get_env("API_TENANT_ID", "223311382831955968")
}
