"""
性能监控中间件
记录请求响应时间、状态码等指标
"""
import time
import logging
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class PerformanceMonitorMiddleware(BaseHTTPMiddleware):
    """性能监控中间件"""

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.slow_request_threshold = 1.0  # 1秒

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """处理请求并记录性能指标"""
        # 记录开始时间
        start_time = time.time()

        # 处理请求
        try:
            response = await call_next(request)
        except Exception as e:
            # 记录异常
            process_time = time.time() - start_time
            logger.error(
                f"Request failed: {request.method} {request.url.path} - "
                f"Error: {str(e)} - Time: {process_time:.3f}s"
            )
            raise

        # 计算处理时间
        process_time = time.time() - start_time

        # 添加响应头
        response.headers["X-Process-Time"] = str(round(process_time, 3))

        # 记录慢请求
        if process_time > self.slow_request_threshold:
            logger.warning(
                f"Slow request: {request.method} {request.url.path} - "
                f"Time: {process_time:.3f}s - Status: {response.status_code}"
            )

        # 记录所有请求（DEBUG级别）
        logger.debug(
            f"{request.method} {request.url.path} - "
            f"Status: {response.status_code} - "
            f"Time: {process_time:.3f}s"
        )

        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """请求日志中间件"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """记录请求详情"""
        # 获取客户端IP
        client_ip = request.client.host if request.client else "unknown"

        # 记录请求开始
        logger.info(
            f"Request started: {request.method} {request.url.path} - "
            f"Client: {client_ip}"
        )

        # 处理请求
        response = await call_next(request)

        # 记录请求完成
        logger.info(
            f"Request completed: {request.method} {request.url.path} - "
            f"Status: {response.status_code} - Client: {client_ip}"
        )

        return response


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """错误处理中间件"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """捕获并记录所有未处理的异常"""
        try:
            response = await call_next(request)
            return response
        except Exception as e:
            logger.exception(
                f"Unhandled exception in {request.method} {request.url.path}: {str(e)}"
            )
            # 重新抛出异常，让FastAPI的异常处理器处理
            raise
