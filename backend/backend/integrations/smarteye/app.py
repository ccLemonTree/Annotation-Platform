from integrations.smarteye.runtime import cached_image, response_bytes
from integrations.smarteye.runtime import DurableTasks, tracked_run, save_image_content, actor, get_runtime
from integrations.smarteye.configuration import download_root
# -*- coding: utf-8 -*-
import os
from pathlib import Path
import uuid
import time
import atexit
import logging
import zipfile
import shutil
import concurrent.futures
from integrations.smarteye.runtime import ContextExecutor as ThreadPoolExecutor
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
import requests
import random
import ssl
from integrations.smarteye.api.utils import get_common_headers
# 导入爬虫工具模块
from integrations.smarteye.api.alarm_data_crawler import get_alarm_image_data
from integrations.smarteye.api.alarm_type_crawler import AlarmTypeFetcher
from integrations.smarteye.api.reject_image_crawler import get_reject_image_data
from integrations.smarteye.api.capture_image_crawler import get_capture_image_data
from integrations.smarteye.api.platform_data_checker import get_platforms_with_alarm_data
from integrations.smarteye.api.config import API_BASE_URL
from urllib3 import poolmanager
from requests.adapters import HTTPAdapter
from integrations.smarteye.api.login_utils import get_token_by_login
from datetime import datetime, timedelta
from integrations.smarteye.api.utils import logger
from integrations.smarteye.api.platform_enum import get_combined_platform_mapping
from integrations.smarteye.api.smart_search import smart_search_tasks, run_smart_search_task, login_platform
from integrations.smarteye.api.db_utils import ensure_smart_search_columns, sync_remote_platforms, get_smart_search_platforms
from integrations.smarteye.api.ai_image import (
    AI_IMAGE_DOWNLOAD_ROOT,
    build_asset_public_url,
    build_reuse_assets,
    cleanup_expired_ai_image_tasks,
    copy_existing_assets,
    create_ai_image_task_payload,
    get_history as get_ai_image_history_data,
    get_runtime_task as get_ai_image_runtime_task,
    get_task_detail as get_ai_image_task_detail,
    import_url_assets,
    persist_task as persist_ai_image_task,
    run_ai_image_task,
    save_uploaded_files,
    start_cleanup_loop,
)
from integrations.smarteye.api.ledger_image_downloader import (
    ledger_image_tasks, run_ledger_image_download_task,
    get_all_schedulers, configure_scheduler, cancel_task,
    start_scheduler, stop_scheduler, remove_scheduler,
    fetch_and_save_devices,
)

# 加载环境变量

# 配置静态文件目录 (指向前端构建目录)
# 假设前端构建输出在 download_img-main/dist
FRONTEND_DIST_DIR = os.path.join(os.getcwd(), 'download_img-main', 'dist')
app = Flask(__name__, static_folder=os.path.join(FRONTEND_DIST_DIR, 'assets'), template_folder=FRONTEND_DIST_DIR)
# 确保所有 JSON 响应中中文不被转义为 \uXXXX
app.json.ensure_ascii = False
# 安全配置
DOWNLOAD_PATH = str(download_root())
MAX_TASK_WORKERS = 3  # 最大并发任务数
MAX_DOWNLOAD_WORKERS = 10  # 每个任务的最大下载并发数 (优化：从 2 提升至 10)
DOWNLOAD_DELAY = 0.05  # 下载间隔基础值 (优化：降低基础延迟)
# 确保下载目录存在


# 使用更安全的线程池配置
task_executor = ThreadPoolExecutor(max_workers=MAX_TASK_WORKERS)
tasks = DurableTasks("detection")

# Token 内存缓存
TOKEN_CACHE = {
    "token": "",
    "tenantId": "",
    "expire_time": ""
}


# --- 核心：自动获取/刷新 Token 逻辑 ---

def get_automated_token():
    """获取有效 Token 的闭环逻辑"""
    global TOKEN_CACHE

    # 1. 检查本地缓存是否过期（预留5分钟缓冲）
    if TOKEN_CACHE["token"] and TOKEN_CACHE["expire_time"]:
        expire_dt = datetime.strptime(TOKEN_CACHE["expire_time"], "%Y-%m-%d %H:%M:%S")
        if expire_dt > (datetime.now() + timedelta(minutes=5)):
            return TOKEN_CACHE["token"], TOKEN_CACHE["tenantId"]

    # 2. 如果无效，触发大模型识别登录
    logger.info(f"Token 失效或不存在，正在尝试登录 IP: {API_BASE_URL}...")
    result = get_token_by_login()

    if result.get("success"):
        TOKEN_CACHE = {
            "token": result["token"],
            "tenantId": result["tenantId"],
            "expire_time": result["expire_time"]
        }
        logger.info("✅ 自动化登录成功，Token 已更新")
        return TOKEN_CACHE["token"], TOKEN_CACHE["tenantId"]
    else:
        logger.error(f"❌ 自动化登录失败: {result.get('msg')}")
        return None, None


# 自定义SSL适配器，支持弱加密算法
class WeakSSLAdapter(HTTPAdapter):
    """适配弱加密SSL证书的适配器"""

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        # 创建宽松的SSL上下文
        ctx = ssl.create_default_context()
        # 放宽加密算法限制，支持弱密钥
        ctx.set_ciphers('DEFAULT:@SECLEVEL=1')
        # 关闭证书验证
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        self.poolmanager = poolmanager.PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ctx,
            **kwargs
        )


def download_file(url, save_path, session=None, collection_name=None):
    """下载单个文件（带超时和重试，支持弱加密SSL证书）"""
    if cached_image(save_path):
        return True
    max_retries = 3

    # 优化：优先使用传入的 session 以复用连接池
    local_session = session if session else requests.Session()
    if not session:
        local_session.mount('https://', WeakSSLAdapter())
        requests.packages.urllib3.disable_warnings(
            requests.packages.urllib3.exceptions.InsecureRequestWarning
        )

    for attempt in range(max_retries):
        try:
            # 优化：根据配置值减少或移除随机延时
            if DOWNLOAD_DELAY > 0:
                time.sleep(random.uniform(0.01, DOWNLOAD_DELAY))

            response = local_session.get(
                url,
                stream=True,
                timeout=15,
                verify=False,  # 关闭证书验证
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'
                }
            )
            response.raise_for_status()

            # 优化：分块写入，增大缓存 size 到 8KB
            if not save_image_content(save_path, response_bytes(response), collection_name=collection_name):
                if not session:local_session.close()
                return False

            logger.info(f"成功下载: {url}")
            return True

        except Exception as e:
            logger.warning(f"下载失败（尝试 {attempt + 1}/{max_retries}）: {url} - {e}")
            if attempt == max_retries - 1:
                if not session:
                    local_session.close()
                return False
            time.sleep(1)  # 重试前等待

    if not session:
        local_session.close()
    return False


def run_download_task(task_id, params):
    """安全的后台下载任务（支持多平台）"""
    tasks[task_id]['status'] = 'running'
    tasks[task_id]['progress'] = 0
    tasks[task_id]['message'] = '正在获取数据...'
    source_type = params.get('sourceType', 'alarm')

    # 解析平台列表
    plat_names = params.get('platNames', [])
    if not plat_names:
        plat_name = params.get('platName', '')
        plat_names = [plat_name] if plat_name else []

    query_name = '_'.join(plat_names[:3]) if plat_names else 'unknown'
    query_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in query_name)
    if len(plat_names) > 3:
        query_name += f'_等{len(plat_names)}个平台'

    try:
        # 获取自动化 Token
        auth_token, _ = get_automated_token()

        # 兼容处理：将 frontend 传来的 targetType 映射为爬虫需要的 parameter
        if params.get('targetType'):
            params['alarm_type_name'] = params.get('targetType')

        # 1. 获取全量数据（多平台循环）
        image_list = []
        query_failures = 0
        for idx, pn in enumerate(plat_names):
            tasks[task_id]['message'] = f'正在查询平台 [{pn}] ({idx+1}/{len(plat_names)})...'
            single_params = dict(params)
            single_params['platName'] = pn

            result_data = {}
            if source_type == 'alarm':
                result_data = get_alarm_image_data(single_params, auth_token, get_all_records=True)
            elif source_type == 'reject':
                result_data = get_reject_image_data(single_params, auth_token, get_all_records=True)
            elif source_type == 'capture':
                result_data = get_capture_image_data(single_params, auth_token, get_all_records=True)

            if "error" in result_data:
                query_failures += 1
                logger.warning(f"平台 [{pn}] 下载数据获取失败: {result_data['error']}")
                continue

            plat_name = result_data.get("platName", pn)
            type_url_map = result_data.get("type_url_map", {})
            for type_name, urls in type_url_map.items():
                for url in urls:
                    image_list.append({
                        'url': url,
                        'platform': plat_name,
                        'type': type_name
                    })

        total_images = len(image_list)
        tasks[task_id]['total'] = total_images
        tasks[task_id]['message'] = f'找到 {total_images} 张图片。开始下载...'

        if total_images == 0:
            tasks[task_id]['status'] = 'failed' if query_failures == len(plat_names) else 'completed'
            tasks[task_id]['progress'] = 100
            tasks[task_id]['message'] = '未找到图片。'
            return

        # 创建任务目录
        task_dir = os.path.join(DOWNLOAD_PATH, f"{query_name}_{source_type}_{task_id}")
        os.makedirs(task_dir, exist_ok=True)

        # 使用独立的线程池进行下载，控制并发
        downloaded_count = 0
        download_tasks = []

        # 优化：在任务级别创建 Session 并挂载适配器，实现连接池复用
        with requests.Session() as task_session:
            task_session.mount('https://', WeakSSLAdapter())
            requests.packages.urllib3.disable_warnings(
                requests.packages.urllib3.exceptions.InsecureRequestWarning
            )

            with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as download_executor:
                for img in image_list:
                    url = img['url']
                    type_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in img['type'])
                    type_dir = os.path.join(task_dir, type_name)
                    os.makedirs(type_dir, exist_ok=True)

                    from urllib.parse import urlparse, unquote
                    filename = Path(unquote(urlparse(url).path)).name.replace('\\', '_')
                    if not filename or len(filename) > 255:
                        filename = f"{uuid.uuid4()}.jpg"

                    save_path = os.path.join(type_dir, filename)

                    # 优化：传递 session
                    future = download_executor.submit(download_file, url, save_path, task_session, img.get('platform'))
                    download_tasks.append(future)

                # 监控下载进度
                for i, future in enumerate(concurrent.futures.as_completed(download_tasks)):
                    if future.result():
                        downloaded_count += 1

                    # 更新进度
                    progress = int((i + 1) / total_images * 100)
                    tasks[task_id].update({
                        'current': downloaded_count,
                        'progress': progress,
                        'message': f'已下载 {downloaded_count}/{i + 1}/{total_images} 张图片'
                    })

        # 3. 打包为 ZIP 供浏览器下载
        tasks[task_id]['message'] = f'下载完成({downloaded_count}/{total_images})，正在打包 ZIP...'
        zip_filename = f"{query_name}_{source_type}_{task_id}.zip"
        zip_path = os.path.join(DOWNLOAD_PATH, zip_filename)
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(task_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, task_dir)
                        zf.write(file_path, arcname)
            logger.info(f"ZIP 打包完成: {zip_path}")
        except Exception as ze:
            logger.error(f"ZIP 打包失败: {ze}")

        tasks[task_id]['progress'] = 100
        tasks[task_id]['zip_path'] = zip_path
        tasks[task_id]['zip_filename'] = zip_filename
        filtered=get_runtime().get(task_id).get('filtered_count',0)
        tasks[task_id]['message'] = f'下载完成！成功下载 {downloaded_count}/{total_images} 张图片，按分辨率规则跳过 {filtered} 张，已打包为 ZIP'
        tasks[task_id]['status'] = 'completed' if downloaded_count or filtered else 'failed'

    except Exception as e:
        logger.error(f"任务 {task_id} 失败: {e}", exc_info=True)
        tasks[task_id]['status'] = 'failed'
        tasks[task_id]['message'] = f"任务失败: {str(e)}"


@app.route('/platforms', methods=['GET', 'POST'])
def get_search_results():
    """获取报警记录列表 (分页，支持多平台)"""

    # 1. 自动获取有效的 Token
    auth_token, tenant_id = get_automated_token()

    if not auth_token:
        return jsonify({
            "code": 1,
            "message": "系统自动登录失败，请检查账号配置或验证码识别服务"
        }), 500

    # 2. 收集查询参数
    query_params = {}
    page_index = 1
    page_size = 20

    # 优先尝试从 JSON Body 获取 (POST 请求)
    json_data = request.get_json(silent=True)
    if json_data:
        query_params.update({
            'startTime': json_data.get('startTime', ''),
            'endTime': json_data.get('endTime', ''),
            'deviceId': json_data.get('deviceId', ''),
            'platName': json_data.get('platName', ''),
            'sourceType': json_data.get('sourceType', 'alarm'),
            'targetType': json_data.get('targetType', '')
        })
        if json_data.get('targetType'):
             query_params['alarm_type_name'] = json_data.get('targetType')

        page_index = int(json_data.get('current', 1))
        page_size = int(json_data.get('size', 20))

        # 多平台支持：优先使用 platNames 数组
        plat_names = json_data.get('platNames', [])
    else:
        # GET params
        query_params.update({
            'startTime': request.args.get('startTime', ''),
            'endTime': request.args.get('endTime', ''),
            'deviceId': request.args.get('deviceId', ''),
            'platName': request.args.get('platName', ''),
            'sourceType': request.args.get('sourceType', 'alarm')
        })
        if request.args.get('targetType'):
             query_params['alarm_type_name'] = request.args.get('targetType')
        plat_names = []

    # 兼容处理：如果没有 platNames 但有 platName，将其转为列表
    if not plat_names:
        if isinstance(query_params['platName'], list):
            plat_names = query_params['platName']
        elif query_params['platName']:
            plat_names = [query_params['platName']]

    if not plat_names:
         return jsonify({"code": 1, "message": "请选择平台"}), 400

    # 3. 查询数据（支持多平台）
    try:
        source_type = query_params['sourceType']
        all_records = []
        total_count = 0
        total_pages = 0

        for pn in plat_names:
            single_params = dict(query_params)
            single_params['platName'] = pn

            if source_type == 'alarm':
                result = get_alarm_image_data(
                    single_params, auth_token,
                    page_index=page_index, page_size=page_size,
                    get_all_records=False
                )
            elif source_type == 'reject':
                result = get_reject_image_data(
                    single_params, auth_token,
                    page_index=page_index, page_size=page_size,
                    get_all_records=False
                )
            elif source_type == 'capture':
                result = get_capture_image_data(
                    single_params, auth_token,
                    page_index=page_index, page_size=page_size,
                    get_all_records=False
                )
            else:
                return jsonify({"code": 1, "message": f"不支持的数据类型: {source_type}"}), 400

            if "error" in result:
                logger.warning(f"平台 [{pn}] 查询失败: {result['error']}")
                continue

            all_records.extend(result.get("records", []))
            total_count += int(result.get("total", 0))
            pages = int(result.get("pages", 0))
            if pages > total_pages:
                total_pages = pages

        merged_result = {
            "records": all_records,
            "total": total_count,
            "pages": total_pages,
            "current": page_index,
        }
        return jsonify({"code": 0, "data": merged_result})

    except Exception as e:
        logger.error(f"获取数据失败: {e}", exc_info=True)
        if "40005" in str(e) or "身份错误" in str(e):
            global TOKEN_CACHE
            TOKEN_CACHE["token"] = ""
        return jsonify({"code": 1, "message": f"获取数据失败: {str(e)}"}), 500


@app.route('/download', methods=['POST'])
def start_download():
    """启动下载任务"""
    data = request.json
    if not data:
        return jsonify({"code": 1, "message": "未提供数据"}), 400
    if data.get('sourceType', 'alarm') not in ('alarm', 'capture', 'reject'):
        return jsonify(code=400, message='不支持的数据类型'), 400

    # 基本参数检查
    plat_names = data.get('platNames', [])
    plat_name = data.get('platName', '')
    if not plat_names and not plat_name:
         return jsonify({"code": 1, "message": "缺少参数: platName 或 platNames"}), 400

    # 默认 source_type
    if 'sourceType' not in data:
        data['sourceType'] = 'alarm'

    # 检查任务队列状态
    active_tasks = sum(1 for task in tasks.values() if task['status'] == 'running')
    if active_tasks >= MAX_TASK_WORKERS:
        return jsonify({"code": 1, "message": "当前任务数已达上限，请稍后再试"}), 429

    # 创建任务
    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        "id": task_id,
        "status": "pending",
        "progress": 0,
        "current": 0,
        "total": 0,
        "message": "任务已创建，等待执行",
        "created_at": time.time(),
        "params": data
    }

    # 提交任务到线程池
    task_executor.submit(tracked_run, run_download_task, task_id, data)

    return jsonify({"code": 0, "data": {"task_id": task_id}, "message": "任务已加入队列"})


@app.route('/progress/<task_id>', methods=['GET'])
def get_progress(task_id):
    """获取任务进度"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"code": 1, "message": "任务不存在"}), 404

    return jsonify({"code": 0, "data": task})


@app.route('/download-zip/<task_id>', methods=['GET'])
def download_zip(task_id):
    """浏览器下载已打包的 ZIP 文件"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"code": 1, "message": "任务不存在"}), 404

    zip_path = task.get('zip_path')
    zip_filename = task.get('zip_filename', f'{task_id}.zip')

    if not zip_path or not os.path.exists(zip_path):
        return jsonify({"code": 1, "message": "ZIP 文件不存在或尚未打包完成"}), 404

    return send_file(
        zip_path,
        as_attachment=True,
        download_name=zip_filename,
        mimetype='application/zip'
    )


@app.route('/alarmType', methods=['GET'])
def push_alarm_type():
    """更新预警类型"""
    auth_token, tenant_id = get_automated_token()

    if not auth_token:
        return jsonify({
            "code": 1,
            "message": "系统自动登录失败，请检查账号配置或验证码识别服务"
        }), 500
    fetcher = AlarmTypeFetcher(auth_token)
    res = fetcher.update_all_and_refresh()
    if not res:
        return jsonify({"code": 1, "message": '更新预警类型失败'}), 404
    return jsonify({"code": 0, "data": res})


@app.route('/platform-list', methods=['GET'])
def get_simple_platform_list():
    """获取所有平台列表（仅名称和ID），不请求外部接口"""
    try:
        mapping = get_combined_platform_mapping()
        platform_list = []
        for name, info in mapping.items():
            platform_list.append({
                "label": name,
                "value": info.get("platId"),
                "alarmTypes": info.get("alarmTypes", {}) # 返回告警类型字典 {name: id}
            })
        return jsonify({"code": 0, "data": platform_list})
    except Exception as e:
        logger.error(f"获取平台列表失败: {e}")
        return jsonify({"code": 1, "message": "获取平台列表失败"}), 500


# ==================== 智搜数据下载 API ====================

# 应用启动时确保数据库表结构就绪
from integrations.smarteye.api.db_utils import ensure_devices_table, ensure_ai_image_tables
from integrations.smarteye.api.ledger_watch_downloader import restore_watch_jobs


@app.route('/smart-search/platforms', methods=['GET'])
def get_smart_search_platform_list():
    """获取智搜平台列表（已启用的）"""
    try:
        platforms = get_smart_search_platforms(only_active=True)
        result = []
        for p in platforms:
            result.append({
                "plat_id": p["plat_id"],
                "name": p["platform_name"],
                "base_url": p["base_url"],
                "state": p["state"],
            })
        return jsonify({"code": 0, "data": result})
    except Exception as e:
        logger.error(f"获取智搜平台列表失败: {e}")
        return jsonify({"code": 1, "message": str(e)}), 500


@app.route('/smart-search/sync-platforms', methods=['POST'])
def sync_smart_search_platforms():
    """从远程接口拉取平台数据并同步到数据库"""
    try:
        auth_token, tenant_id = get_automated_token()
        if not auth_token:
            return jsonify({
                "code": 1,
                "message": "系统自动登录失败，请检查账号配置或验证码识别服务"
            }), 500

        remote_url = f"{API_BASE_URL}/api/defPlatform/page"
        headers = get_common_headers(auth_token)
        if tenant_id is not None:
            headers["tenantid"] = str(tenant_id)
        rsp = requests.post(
            remote_url,
            json={"current": 1, "size": '1000', "model": {}},
            headers=headers,
            timeout=15,
        )
        rsp.raise_for_status()
        rsp_data = rsp.json()

        records = []
        if rsp_data.get("code") == 0:
            data = rsp_data.get("data", {})
            records = data.get("records", [])
        elif isinstance(rsp_data.get("data"), list):
            records = rsp_data["data"]

        if not records:
            return jsonify({"code": 1, "message": "远程接口未返回平台数据"}), 400

        count = sync_remote_platforms(records)
        return jsonify({"code": 0, "message": f"同步成功，共 {count} 个平台", "count": count})

    except Exception as e:
        logger.error(f"同步平台数据失败: {e}")
        return jsonify({"code": 1, "message": f"同步失败: {str(e)}"}), 500


@app.route('/smart-search/start', methods=['POST'])
def start_smart_search():
    """启动多平台智搜任务"""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"code": 1, "message": "请提供搜索参数"}), 400

    platform_ids = data.get("platform_ids", [])
    search_params = {
        "text": data.get("text", ""),
        "limit": data.get("limit", 15),
        "startTime": data.get("startTime", ""),
        "endTime": data.get("endTime", ""),
    }

    if not platform_ids:
        return jsonify({"code": 1, "message": "请选择至少一个平台"}), 400
    if not search_params["text"]:
        return jsonify({"code": 1, "message": "请输入搜索内容"}), 400

    # 从数据库获取选中的平台信息
    all_platforms = get_smart_search_platforms(only_active=False)
    selected = [p for p in all_platforms if p["plat_id"] in platform_ids]

    if not selected:
        return jsonify({"code": 1, "message": "未找到选中的平台"}), 400

    # 创建任务
    task_id = str(uuid.uuid4())
    smart_search_tasks[task_id] = {
        "id": task_id,
        "status": "pending",
        "progress": 0,
        "message": "任务已创建",
        "platform_results": [],
        "created_at": time.time(),
    }

    # 后台执行
    task_executor.submit(tracked_run, run_smart_search_task, task_id, selected, search_params)

    return jsonify({"code": 0, "data": {"task_id": task_id}, "message": "搜索任务已启动"})


@app.route('/smart-search/progress/<task_id>', methods=['GET'])
def get_smart_search_progress(task_id):
    """查询智搜任务进度"""
    task = smart_search_tasks.get(task_id)
    if not task:
        return jsonify({"code": 1, "message": "任务不存在"}), 404
    return jsonify({"code": 0, "data": task})


@app.route('/smart-search/download-zip/<task_id>', methods=['GET'])
def download_smart_search_zip(task_id):
    """下载智搜已打包的 ZIP 文件"""
    task = smart_search_tasks.get(task_id)
    if not task:
        return jsonify({"code": 1, "message": "任务不存在"}), 404

    zip_path = task.get("zip_path")
    zip_filename = task.get("zip_filename", f"{task_id}.zip")

    if not zip_path or not os.path.exists(zip_path):
        return jsonify({"code": 1, "message": "ZIP 文件不存在或尚未完成"}), 404

    return send_file(
        zip_path,
        as_attachment=True,
        download_name=zip_filename,
        mimetype="application/zip",
    )


@app.route('/ledger/device/log', methods=['POST'])
def get_ledger_device_log():
    """代理获取设备台账图片，自动根据平台名称找到 base_url 并附加 Token"""
    try:
        data = request.json
        if not data:
            return jsonify({"code": 1, "msg": "请求参数为空"}), 400

        plat_name = data.get('platName')
        payload = data.get('payload', {})

        if not plat_name:
            return jsonify({"code": 1, "msg": "缺少平台名称 platName"}), 400

        # 获取自动化 Token
        auth_token, _ = get_automated_token()
        if not auth_token:
            return jsonify({"code": 1, "msg": "系统自动登录失败，无法获取 Token"}), 500

        # 获取平台配置以查找 base_url
        platforms = get_smart_search_platforms(only_active=False)
        base_url = ""
        for p in platforms:
            if p["platform_name"] == plat_name:
                base_url = p["base_url"]
                break

        if not base_url:
            return jsonify({"code": 1, "msg": f"未找到平台 [{plat_name}] 的 base_url 配置，请确认该平台是否在智搜配置中同步"}), 400

        # 对目标平台单独登录，获取该平台自己的 token
        plat_info = next((p for p in platforms if p["platform_name"] == plat_name), None)
        login_result = login_platform(plat_info)
        if not login_result.get("success"):
            return jsonify({"code": 1, "msg": f"目标平台登录失败: {login_result.get('msg')}"}), 500

        plat_token = login_result["token"]
        plat_tenant_id = login_result.get("tenantId", "")

        target_url = f"{base_url.rstrip('/')}/api/schedule/capture/stat/v1/log"

        from urllib.parse import urlparse as _urlparse
        _parsed = _urlparse(base_url)
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-encoding": "gzip, deflate",
            "accept-language": "zh-CN,zh;q=0.9",
            "content-type": "application/json;charset=UTF-8",
            "gray_version": "zuihou",
            "host": _parsed.netloc,
            "origin": base_url.rstrip('/'),
            "referer": f"{base_url.rstrip('/')}/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
            "applicationid": "1",
            "authorization": "bGFtcF93ZWJfcHJvOmxhbXBfd2ViX3Byb19zZWNyZXQ=",
            "tenantid": plat_tenant_id,
            "token": plat_token,
            "repeatalarm": "true",  # 台账查询必需字段
        }

        logger.info(f"正在查询台账数据: {target_url} - {payload}")
        rsp = requests.post(target_url, headers=headers, json=payload, timeout=20)
        rsp.raise_for_status()

        return jsonify(rsp.json())

    except requests.exceptions.RequestException as e:
        logger.error(f"代理请求台账数据失败: {e}")
        return jsonify({"code": 1, "msg": f"请求目标平台失败: {e}"}), 500
    except Exception as e:
        logger.error(f"处理台账数据请求异常: {e}", exc_info=True)
        return jsonify({"code": 1, "msg": str(e)}), 500


# ==================== 台账图片自动下载 API ====================

@app.route('/ledger-image/update-devices', methods=['POST'])
def update_ledger_devices():
    """从平台拉取设备列表并存到数据库"""
    data = request.get_json(silent=True) or {}
    platform_ids = data.get('platform_ids')  # 不传则更新所有启用平台

    all_platforms = get_smart_search_platforms(only_active=True)
    if platform_ids:
        all_platforms = [p for p in all_platforms if p["plat_id"] in platform_ids]

    if not all_platforms:
        return jsonify({"code": 1, "message": "没有找到启用的平台"}), 400

    try:
        summary = fetch_and_save_devices(all_platforms)
        return jsonify({"code": 0, "data": summary,
                        "message": f"设备更新完成，共写入 {summary['total']} 台"})
    except Exception as e:
        logger.error(f"更新设备失败: {e}", exc_info=True)
        return jsonify({"code": 1, "message": f"更新失败: {str(e)}"}), 500


@app.route('/ledger-image/start', methods=['POST'])
def start_ledger_image_download():
    """手动触发台账图片下载任务"""
    data = request.get_json(silent=True) or {}
    platform_ids = data.get('platform_ids')
    alarm_type_filter = data.get('alarm_type_filter')
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    small_model_mode = data.get('small_model_mode', 'crop')

    active_count = sum(1 for t in ledger_image_tasks.values() if t.get('status') == 'running')
    if active_count >= MAX_TASK_WORKERS:
        return jsonify({"code": 1, "message": "当前任务数已达上限"}), 429

    task_id = str(uuid.uuid4())
    ledger_image_tasks[task_id] = {
        "id": task_id, "status": "pending", "progress": 0,
        "message": "任务已创建", "created_at": time.time(),
        "platform_results": [],
    }
    task_executor.submit(tracked_run, run_ledger_image_download_task, task_id,
                         platform_ids, alarm_type_filter, start_time, end_time,
                         small_model_mode)
    return jsonify({"code": 0, "data": {"task_id": task_id}})


@app.route('/ledger-image/progress/<task_id>', methods=['GET'])
def get_ledger_image_progress(task_id):
    """查询台账图片下载任务进度"""
    task = ledger_image_tasks.get(task_id)
    if not task:
        return jsonify({"code": 1, "message": "任务不存在"}), 404
    return jsonify({"code": 0, "data": task})


@app.route('/ledger-image/stop/<task_id>', methods=['POST'])
def stop_ledger_image_task(task_id):
    """停止正在运行的台账图片下载任务"""
    ok = cancel_task(task_id)
    if not ok:
        return jsonify({"code": 1, "message": "任务不存在或已完成"}), 404
    return jsonify({"code": 0, "message": "任务已标记为取消"})


@app.route('/ledger-image/download/<task_id>', methods=['GET'])
def download_ledger_image_zip(task_id):
    """下载台账图片 ZIP 包"""
    task = ledger_image_tasks.get(task_id)
    if not task:
        return jsonify({"code": 1, "message": "任务不存在"}), 404
    zip_path = task.get("zip_path")
    if not zip_path or not os.path.exists(zip_path):
        return jsonify({"code": 1, "message": "ZIP 文件不存在或尚未完成"}), 404
    return send_file(zip_path, as_attachment=True,
                     download_name=task.get("zip_filename", f"{task_id}.zip"),
                     mimetype="application/zip")


@app.route('/ledger-image/scheduler/status', methods=['GET'])
def get_ledger_scheduler_status():
    """获取所有调度器状态 + 所有任务"""
    return jsonify({
        "code": 0,
        "data": {
            "schedulers": get_all_schedulers(),
            "all_tasks": [
                {
                    "task_id": tid,
                    "status": t.get("status", "unknown"),
                    "progress": t.get("progress", 0),
                    "message": t.get("message", ""),
                    "created_at": t.get("created_at"),
                    "zip_filename": t.get("zip_filename"),
                    "zip_path": t.get("zip_path"),
                    "platform_results": t.get("platform_results", []),
                    "stage": t.get("stage"),
                    "download_total": t.get("download_total", 0),
                    "download_completed": t.get("download_completed", 0),
                    "download_pending": t.get("download_pending", 0),
                    "download_success": t.get("download_success", 0),
                    "download_failed": t.get("download_failed", 0),
                }
                for tid, t in ledger_image_tasks.items()
            ],
        }
    })


@app.route('/ledger-image/scheduler/configure', methods=['POST'])
def configure_ledger_scheduler():
    """创建或更新调度器配置，可同时启停"""
    data = request.get_json(silent=True) or {}
    sched_id = data.get('sched_id')  # None = create new
    name = data.get('name', '')
    enabled = data.get('enabled', False)
    interval_minutes = data.get('interval_minutes')
    task_duration_hours = data.get('task_duration_hours')
    platform_ids = data.get('platform_ids')
    alarm_type_filter = data.get('alarm_type_filter')
    small_model_mode = data.get('small_model_mode')

    result = configure_scheduler(
        sched_id=sched_id, name=name, enabled=enabled,
        interval_minutes=interval_minutes,
        task_duration_hours=task_duration_hours,
        platform_ids=platform_ids,
        alarm_type_filter=alarm_type_filter,
        small_model_mode=small_model_mode,
    )
    return jsonify({"code": 0, "data": result})


@app.route('/ledger-image/scheduler/start', methods=['POST'])
def start_ledger_scheduler_route():
    """启动指定调度器"""
    data = request.get_json(silent=True) or {}
    sched_id = data.get('sched_id')
    if not sched_id:
        return jsonify({"code": 1, "message": "缺少 sched_id"}), 400
    ok = start_scheduler(sched_id)
    return jsonify({"code": 0 if ok else 1, "message": "已启动" if ok else "调度器不存在"})


@app.route('/ledger-image/scheduler/stop', methods=['POST'])
def stop_ledger_scheduler_route():
    """停止指定调度器"""
    data = request.get_json(silent=True) or {}
    sched_id = data.get('sched_id')
    if not sched_id:
        return jsonify({"code": 1, "message": "缺少 sched_id"}), 400
    ok = stop_scheduler(sched_id)
    return jsonify({"code": 0 if ok else 1, "message": "已停止" if ok else "调度器不存在"})


@app.route('/ledger-image/scheduler/<sched_id>', methods=['DELETE'])
def delete_ledger_scheduler(sched_id):
    """删除指定调度器"""
    ok = remove_scheduler(sched_id)
    return jsonify({"code": 0 if ok else 1, "message": "已删除" if ok else "调度器不存在"})


# ==================== 台账监听下载 API ====================

@app.route('/ledger-watch/jobs', methods=['GET'])
def list_watch_jobs():
    from integrations.smarteye.api.ledger_watch_downloader import get_all_watch_states
    return jsonify({"code": 0, "data": get_all_watch_states()})


@app.route('/ledger-watch/jobs', methods=['POST'])
def create_watch_job():
    """创建监听任务（不启动）"""
    from integrations.smarteye.api.ledger_watch_downloader import start_watch_job
    from integrations.smarteye.api.db_utils import upsert_watch_job, ensure_ledger_watch_tables
    ensure_ledger_watch_tables()
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id") or str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "name": data.get("name", "监听任务"),
        "source_types": "alarm",
        "platform_ids": data.get("platform_ids", []),
        "alarm_type_filter": data.get("alarm_type_filter", []),
        "small_model_mode": data.get("small_model_mode", "crop"),
        "poll_interval_seconds": int(data.get("poll_interval_seconds", 60)),
        "time_offset_seconds": 15,
        "enabled": bool(data.get("enabled", True)),
    }
    ok = upsert_watch_job(job)
    if not ok:
        return jsonify({"code": 1, "message": "保存失败"}), 500
    if job["enabled"]:
        start_watch_job(job_id)
    return jsonify({"code": 0, "data": {"job_id": job_id}})


@app.route('/ledger-watch/jobs/<job_id>', methods=['PUT'])
def update_watch_job(job_id):
    from integrations.smarteye.api.ledger_watch_downloader import start_watch_job, stop_watch_job
    from integrations.smarteye.api.db_utils import upsert_watch_job, get_watch_jobs
    data = request.get_json(silent=True) or {}
    jobs = get_watch_jobs()
    existing = next((j for j in jobs if j["job_id"] == job_id), None)
    if not existing:
        return jsonify({"code": 1, "message": "任务不存在"}), 404
    existing.update({k: v for k, v in data.items() if k != "job_id"})
    upsert_watch_job(existing)
    if existing.get("enabled"):
        stop_watch_job(job_id)
        start_watch_job(job_id)
    else:
        stop_watch_job(job_id)
    return jsonify({"code": 0})


@app.route('/ledger-watch/jobs/<job_id>', methods=['DELETE'])
def delete_watch_job_route(job_id):
    from integrations.smarteye.api.ledger_watch_downloader import stop_watch_job
    from integrations.smarteye.api.db_utils import delete_watch_job
    stop_watch_job(job_id)
    delete_watch_job(job_id)
    return jsonify({"code": 0})


@app.route('/ledger-watch/jobs/<job_id>/start', methods=['POST'])
def start_watch_job_route(job_id):
    from integrations.smarteye.api.ledger_watch_downloader import start_watch_job
    from integrations.smarteye.api.db_utils import upsert_watch_job, get_watch_jobs
    jobs = get_watch_jobs()
    job = next((j for j in jobs if j["job_id"] == job_id), None)
    if not job:
        return jsonify({"code": 1, "message": "任务不存在"}), 404
    job["enabled"] = True
    upsert_watch_job(job)
    start_watch_job(job_id)
    return jsonify({"code": 0})


@app.route('/ledger-watch/jobs/<job_id>/stop', methods=['POST'])
def stop_watch_job_route(job_id):
    from integrations.smarteye.api.ledger_watch_downloader import stop_watch_job
    from integrations.smarteye.api.db_utils import upsert_watch_job, get_watch_jobs
    jobs = get_watch_jobs()
    job = next((j for j in jobs if j["job_id"] == job_id), None)
    if job:
        job["enabled"] = False
        upsert_watch_job(job)
    stop_watch_job(job_id)
    return jsonify({"code": 0})


@app.route('/download-images', methods=['POST'])
def download_images_zip():
    """接收 URL 列表，下载后打包为 ZIP 返回给浏览器"""
    data = request.get_json(silent=True)
    if not data or not data.get('urls'):
        return jsonify({"code": 1, "msg": "缺少 urls 参数"}), 400

    urls = data['urls']
    if not isinstance(urls, list) or not all(isinstance(url, str) and url for url in urls):
        return jsonify(code=400, message='urls必须为非空URL字符串列表'), 400
    collection_name=data.get('collection_name')
    if collection_name is not None and (not isinstance(collection_name,str) or not collection_name.strip() or len(collection_name.strip())>200):
        return jsonify(code=400,message='图片集名称无效'),400
    zip_name = data.get('filename', 'images') + '.zip'

    tmp_dir = os.path.join(DOWNLOAD_PATH, f"tmp_{uuid.uuid4()}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        with requests.Session() as sess:
            sess.mount('https://', WeakSSLAdapter())
            requests.packages.urllib3.disable_warnings(
                requests.packages.urllib3.exceptions.InsecureRequestWarning
            )
            with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS) as ex:
                def _dl(item):
                    index, url = item
                    fname = url.split('/')[-1].split('?')[0] or f"{uuid.uuid4()}.jpg"
                    if len(fname) > 200:
                        fname = f"{uuid.uuid4()}.jpg"
                    return download_file(url, os.path.join(tmp_dir, f'{index}_{fname}'), sess, collection_name) if collection_name is not None else download_file(url, os.path.join(tmp_dir, f'{index}_{fname}'), sess)

                results = list(ex.map(_dl, enumerate(urls)))
                if not all(results):
                    return jsonify(code=502, message=f'部分图片下载失败：成功{sum(results)}/{len(urls)}张，请重试'), 502

        zip_path = os.path.join(DOWNLOAD_PATH, f"tmp_{uuid.uuid4()}.zip")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in os.listdir(tmp_dir):
                zf.write(os.path.join(tmp_dir, f), f)

        return send_file(zip_path, as_attachment=True, download_name=zip_name, mimetype='application/zip')

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ==================== AI 生图 API ====================

@app.route('/ai-image/upload', methods=['POST'])
def ai_image_upload():
    """上传本地图片到 AI 生图素材池"""
    cleanup_expired_ai_image_tasks()
    session_id = actor.get()['session_id']
    public_base_url = request.form.get('public_base_url', '').rstrip('/')
    files = request.files.getlist('files')
    if not files:
        return jsonify({"code": 1, "message": "未上传图片文件"}), 400

    assets = save_uploaded_files(session_id, files, public_base_url)
    return jsonify({"code": 0, "data": {"session_id": session_id, "assets": assets}})


@app.route('/ai-image/upload-folder', methods=['POST'])
def ai_image_upload_folder():
    """上传文件夹内图片到 AI 生图素材池"""
    return ai_image_upload()


@app.route('/ai-image/import-urls', methods=['POST'])
def ai_image_import_urls():
    """通过 URL 导入图片素材"""
    cleanup_expired_ai_image_tasks()
    data = request.get_json(silent=True) or {}
    urls = data.get('urls', [])
    session_id = actor.get()['session_id']
    public_base_url = (data.get('public_base_url') or '').rstrip('/')
    if not urls:
        return jsonify({"code": 1, "message": "缺少 urls"}), 400

    assets = import_url_assets(session_id, urls, public_base_url)
    return jsonify({"code": 0, "data": {"session_id": session_id, "assets": assets}})


@app.route('/ai-image/import-existing', methods=['POST'])
def ai_image_import_existing():
    """从现有页面图片结果导入到 AI 生图素材池"""
    cleanup_expired_ai_image_tasks()
    data = request.get_json(silent=True) or {}
    assets = data.get('assets', [])
    session_id = actor.get()['session_id']
    public_base_url = (data.get('public_base_url') or '').rstrip('/')
    if not assets:
        return jsonify({"code": 1, "message": "缺少 assets"}), 400

    imported = copy_existing_assets(session_id, assets, public_base_url)
    return jsonify({"code": 0, "data": {"session_id": session_id, "assets": imported}})


@app.route('/ai-image/start', methods=['POST'])
def start_ai_image_task():
    """启动 AI 生图任务"""
    cleanup_expired_ai_image_tasks()
    data = request.get_json(silent=True) or {}

    try:
        task = create_ai_image_task_payload(data)
        get_runtime().create(task["task_id"], "ai", {"status": "pending", "request": data}, actor.get())
        persist_ai_image_task(task)
        task_executor.submit(tracked_run, run_ai_image_task, task["task_id"])
        return jsonify({"code": 0, "data": {"task_id": task["task_id"]}, "message": "任务已启动"})
    except Exception as e:
        logger.error(f"启动 AI 生图任务失败: {e}", exc_info=True)
        return jsonify({"code": 1, "message": str(e)}), 400


@app.route('/ai-image/progress/<task_id>', methods=['GET'])
def get_ai_image_progress(task_id):
    """查询 AI 生图任务进度"""
    task = get_ai_image_runtime_task(task_id)
    if not task:
        return jsonify({"code": 1, "message": "任务不存在"}), 404
    return jsonify({"code": 0, "data": task})


@app.route('/ai-image/history', methods=['GET'])
def get_ai_image_history():
    """查询 AI 生图历史"""
    cleanup_expired_ai_image_tasks()
    page = int(request.args.get('current', 1))
    size = int(request.args.get('size', 20))
    prompt = request.args.get('prompt', '')
    status = request.args.get('status', '')
    start_date = request.args.get('startDate', '')
    end_date = request.args.get('endDate', '')
    result = get_ai_image_history_data(page, size, prompt, status, start_date, end_date)
    return jsonify({"code": 0, "data": result})


@app.route('/ai-image/history/<task_id>', methods=['GET'])
def get_ai_image_history_detail(task_id):
    """查询单个 AI 生图任务详情"""
    cleanup_expired_ai_image_tasks()
    detail = get_ai_image_task_detail(task_id)
    if not detail:
        return jsonify({"code": 1, "message": "任务不存在"}), 404
    return jsonify({"code": 0, "data": detail})


@app.route('/ai-image/reuse/<task_id>', methods=['GET'])
@app.route('/ai-image/reuse/<task_id>/<item_id>', methods=['GET'])
def reuse_ai_image_assets(task_id, item_id=None):
    """将历史结果图复用为参考图素材"""
    cleanup_expired_ai_image_tasks()
    assets = build_reuse_assets(task_id, item_id or "")
    return jsonify({"code": 0, "data": {"assets": assets}})


@app.route('/ai-image/assets/<task_id>/<path:relative_name>', methods=['GET'])
def serve_ai_image_asset(task_id, relative_name):
    """提供 AI 生图输入/输出素材访问"""
    root_dir = AI_IMAGE_DOWNLOAD_ROOT / task_id
    file_path = (root_dir / relative_name).resolve()
    if not file_path.is_relative_to(root_dir.resolve()):
        return jsonify({"code": 1, "message": "非法路径"}), 400
    if not file_path.exists():
        return jsonify({"code": 1, "message": "文件不存在"}), 404
    return send_file(str(file_path))


# ==================== 工具任务 API ====================

@app.route('/tool-tasks/create', methods=['POST'])
def create_tool_task_route():
    """创建批量下载工具任务"""
    from integrations.smarteye.api.tool_tasks import create_tool_task
    try:
        data = request.get_json(silent=True) or {}
        platform_name = data.get('platform_name')
        device_id = data.get('device_id', '')
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        max_pages = int(data.get('max_pages', 0))

        if not platform_name or not start_time or not end_time:
            return jsonify({"code": 1, "msg": "缺少必要参数"}), 400

        task_id = create_tool_task(platform_name, device_id, start_time, end_time, max_pages)
        return jsonify({"code": 0, "data": {"task_id": task_id}})
    except Exception as e:
        logger.error(f"创建工具任务失败: {e}", exc_info=True)
        return jsonify({"code": 1, "msg": str(e)}), 500


@app.route('/tool-tasks/<task_id>', methods=['GET'])
def get_tool_task_status_route(task_id):
    """查询工具任务状态"""
    from integrations.smarteye.api.tool_tasks import get_tool_task_status
    task = get_tool_task_status(task_id)
    if not task:
        return jsonify({"code": 1, "msg": "任务不存在"}), 404
    return jsonify({"code": 0, "data": task})


@app.route('/tool-tasks', methods=['GET'])
def list_tool_tasks_route():
    """列出所有工具任务"""
    from integrations.smarteye.api.tool_tasks import list_tool_tasks
    tasks = list_tool_tasks()
    return jsonify({"code": 0, "data": tasks})


@app.route('/tool-tasks/<task_id>/download', methods=['GET'])
def download_tool_task_result(task_id):
    """下载工具任务结果 ZIP"""
    from integrations.smarteye.api.tool_tasks import get_tool_task_status
    task = get_tool_task_status(task_id)
    if not task:
        return jsonify({"code": 1, "msg": "任务不存在"}), 404

    zip_path = task.get('zip_path')
    if not zip_path or not os.path.exists(zip_path):
        return jsonify({"code": 1, "msg": "结果文件不存在"}), 404

    return send_file(zip_path, as_attachment=True,
                    download_name=f"tool_task_{task_id}.zip",
                    mimetype='application/zip')


