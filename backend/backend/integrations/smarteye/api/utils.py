# -*- coding: utf-8 -*-
"""
@File    : utils.py
@Time    : 2025/11/30
@Description : 通用工具函数
"""
import re
import json
import requests
import logging
import sys
import os
from pathlib import Path
from typing import Dict, Any, Optional
from integrations.smarteye.api.config import DEFAULT_HEADERS, AUTH_CREDENTIALS

_LOG_FILE = Path(__file__).parent.parent / "app.log"

def setup_logger(name: str = "auto_img_downloader"):
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        logger.propagate = False
    return logger

logger = setup_logger()

def is_response_success(response_data: Dict[str, Any]) -> bool:
    """
    判断API响应是否成功
    兼容 code 为 0 (int) 或 "0" (str) 的情况
    """
    if not response_data:
        return False
    
    code = response_data.get("code")
    return str(code) == '0'

def get_common_headers(auth_token: str) -> Dict[str, str]:
    """
    获取通用的请求头
    
    Args:
        auth_token: 认证Token
        
    Returns:
        合并了默认头、认证信息和Token的请求头字典
    """
    headers = DEFAULT_HEADERS.copy()
    headers.update(AUTH_CREDENTIALS)
    headers["token"] = auth_token
    return headers

def parse_loose_json(text: str) -> Dict[str, Any]:
    """
    解析可能不规范的JSON字符串（修复未加引号的key）
    
    Args:
        text: JSON字符串
        
    Returns:
        解析后的字典，如果解析失败返回空字典或抛出异常
    """
    if not text:
        return {}
        
    try:
        # 尝试直接解析
        return json.loads(text)
    except json.JSONDecodeError:
        # 修复JSON格式中code字段值缺少引号的问题
        # 这是一个比较脆弱的修复，但为了兼容现有逻辑保留
        try:
            fixed_text = re.sub(
                r'(?<="code":)([^,"}{]+)',
                lambda m: f'"{m.group(0).strip()}"',
                text
            )
            return json.loads(fixed_text)
        except Exception as e:
            logger.error(f"JSON修复并解析失败: {e}")
            # 如果修复失败，返回空字典或者抛出异常，视情况而定
            # 这里为了保持兼容性，返回空字典，但记录日志
            return {}

def safe_post_request(url: str, json_data: Dict[str, Any], headers: Dict[str, str], timeout: int = 30) -> Optional[Dict[str, Any]]:
    """
    发送安全的POST请求，处理异常和JSON解析
    
    Args:
        url: 请求URL
        json_data: 请求体数据
        headers: 请求头
        timeout: 超时时间
        
    Returns:
        解析后的响应数据字典，如果请求失败返回None
    """
    try:
        response = requests.post(
            url=url,
            headers=headers,
            json=json_data,
            timeout=timeout,
            verify=False  # 注意：这里仍然关闭了验证，因为服务端证书可能有问题
        )
        response.raise_for_status()
        
        response_text = response.text.strip()
        return parse_loose_json(response_text)
        
    except requests.exceptions.RequestException as e:
        logger.error(f"请求错误: {url} - {e}")
        return None
    except Exception as e:
        logger.error(f"未知错误: {url} - {e}")
        return None
