"""内存任务表 + 后台线程 —— 处理慢调用。

支持 exclusive_key：同一类外部资源任务（例如 OpenCLI 浏览器）同一时间只跑一个，
避免用户连续点击后堆叠多组抓取/发布请求。
"""
import threading
import traceback
import uuid

TASKS: dict[str, dict] = {}
_LOCK = threading.Lock()
_ACTIVE_EXCLUSIVE: dict[str, str] = {}  # exclusive_key -> task_id


def submit(fn, *args, exclusive_key: str | None = None, **kwargs) -> str:
    """提交后台任务。exclusive_key 相同的任务不会并行/排队堆叠。"""
    task_id = uuid.uuid4().hex[:12]
    with _LOCK:
        if exclusive_key and exclusive_key in _ACTIVE_EXCLUSIVE:
            running_id = _ACTIVE_EXCLUSIVE[exclusive_key]
            TASKS[task_id] = {
                "status": "error",
                "result": None,
                "error": f"已有 {exclusive_key} 任务运行中（{running_id}），本次未重复提交",
            }
            return task_id
        TASKS[task_id] = {"status": "pending", "result": None, "error": None}
        if exclusive_key:
            _ACTIVE_EXCLUSIVE[exclusive_key] = task_id

    def _run():
        try:
            with _LOCK:
                if task_id in TASKS:
                    TASKS[task_id]["status"] = "running"
            result = fn(*args, **kwargs)
            with _LOCK:
                TASKS[task_id] = {"status": "done", "result": result, "error": None}
        except Exception as e:  # noqa: BLE001
            with _LOCK:
                TASKS[task_id] = {
                    "status": "error", "result": None,
                    "error": f"{e}",
                    "trace": traceback.format_exc()[-800:],
                }
        finally:
            if exclusive_key:
                with _LOCK:
                    if _ACTIVE_EXCLUSIVE.get(exclusive_key) == task_id:
                        _ACTIVE_EXCLUSIVE.pop(exclusive_key, None)

    threading.Thread(target=_run, daemon=True).start()
    return task_id


def get(task_id: str) -> dict | None:
    with _LOCK:
        t = TASKS.get(task_id)
        return dict(t) if t else None
