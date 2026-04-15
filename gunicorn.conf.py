"""Gunicorn 生产环境配置"""
import os
import multiprocessing

# 监听地址
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# 工作进程数 — 因为有后台采集线程 + APScheduler，用 1 个 worker 避免重复调度
# 如需多 worker，需将调度器改为外部进程（如 cron）
workers = 1

# 线程数 — 支持并发 Web 请求
threads = 4

# 超时（秒）— 采集任务可能较慢
timeout = 300
graceful_timeout = 30

# 日志
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 预加载应用（加快启动、共享内存）
preload_app = True
