""" 
定时任务调度模块
"""

from apscheduler.schedulers.background import BackgroundScheduler

_scheduler = None


def init_scheduler(job_func, hour: int = 8, minute: int = 0):
    """初始化每日定时采集任务"""
    global _scheduler

    if _scheduler is not None:
        _scheduler.shutdown(wait=False)

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        job_func,
        trigger="cron",
        hour=hour,
        minute=minute,
        id="daily_collect",
        replace_existing=True,
    )
    _scheduler.start()
    print(f"[调度器] 已启动，每日 {hour:02d}:{minute:02d} 自动采集")


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        print("[调度器] 已停止")
