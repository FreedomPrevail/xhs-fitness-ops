#!/usr/bin/env python3
"""运营看板 Flask 后端 —— 一体化小红书健身号运营工具。

放在 ROOT 层,借位路径 import 现有脚本。慢调用(搜索/写稿/采集/发布)走
后台线程+轮询;快调用(数据/推荐/封面/合规/分析)同步返回。

启动: python app.py  →  http://127.0.0.1:5000
"""
import os
import sys
from pathlib import Path

# 强制 UTF-8(Windows 控制台 + 子进程输出含中文/emoji)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402

from dashboard import services, tasks                            # noqa: E402

app = Flask(__name__, static_folder=None)
STATIC = ROOT / "dashboard" / "static"
COVERS = ROOT / "content" / "covers"
POST_PAGES = ROOT / "content" / "post_pages"


# ---------- 静态页 ----------
@app.get("/")
def index():
    response = send_from_directory(STATIC, "index.html")
    # The dashboard is a single HTML file and changes frequently.  On Windows it
    # is otherwise very easy to keep seeing an older Golden UI after restarting.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-XHS-UI-Version"] = "manual-connected-infographic-v4.2"
    return response


@app.get("/covers/<path:name>")
def cover_file(name):
    """封面图预览。"""
    return send_from_directory(COVERS, name)


@app.get("/post-pages/<post_id>/<path:name>")
def post_page_file(post_id, name):
    return send_from_directory(POST_PAGES / post_id, name)


# ---------- 快接口(同步)----------
@app.get("/api/dashboard")
def api_dashboard():
    return jsonify(services.dashboard_data())


@app.get("/api/recommend")
def api_recommend():
    return jsonify(services.recommend_snapshot())


@app.post("/api/analyze")
def api_analyze():
    return jsonify(services.run_analyze())


@app.post("/api/cover")
def api_cover():
    d = request.get_json(force=True)
    return jsonify(services.make_cover_img(
        d.get("cover_text", ""), d.get("category", "_default"), d.get("stem", "cover"),
        title=d.get("title", ""), topic_id=d.get("topic_id", ""), model_config=d.get("model_config") or {},
        golden_usage_id=d.get("golden_usage_id", "")))


@app.post("/api/carousel")
def api_carousel():
    """Generate a Canva-ready carousel; image creation is user-triggered only."""
    d = request.get_json(force=True)
    model_config = dict(d.get("model_config") or {})
    image_options = dict(model_config.get("image_generation") or {})
    # This dedicated endpoint represents an explicit generate action. Direct CLI
    # use remains opt-in through --generate-images.
    image_options["enabled"] = bool(d.get("generate_images", True))
    if "max_image_assets" in d:
        image_options["max_assets"] = int(d["max_image_assets"])
    if "cover_direction_index" in d:
        model_config["cover_direction_index"] = int(d["cover_direction_index"])
    model_config["image_generation"] = image_options
    tid = tasks.submit(services.generate_visual_carousel, d.get("topic_id", ""),
                       d.get("content") or {}, model_config, exclusive_key="CodexImagegen")
    return jsonify({"task_id": tid})


@app.post("/api/carousel/select-cover")
def api_carousel_select_cover():
    """Save the operator's cover choice and replace page 1 everywhere."""
    d=request.get_json(force=True)
    return jsonify(services.select_carousel_cover(d.get("post_id",""),d.get("index",0)))


@app.get("/api/canva/status")
def api_canva_status():
    return jsonify(services.canva_status())


@app.post("/api/canva/login")
def api_canva_login():
    """Explicitly open Canva OAuth; never runs during dashboard startup."""
    tid = tasks.submit(services.canva_login, exclusive_key="CanvaOAuth")
    return jsonify({"task_id": tid})


@app.post("/api/canva/import")
def api_canva_import():
    """Explicitly import one generated layered PPTX into the user's Canva."""
    d = request.get_json(force=True)
    tid = tasks.submit(services.canva_import, d.get("post_id", ""), exclusive_key="CanvaImport")
    return jsonify({"task_id": tid})


@app.post("/api/compliance")
def api_compliance():
    d = request.get_json(force=True)
    return jsonify(services.check(d.get("title", ""), d.get("body", ""),d.get("cover_text","")))


@app.get("/api/operation/status")
def api_operation_status():
    return jsonify(services.operation_status())


@app.post("/api/topic/daily-decision")
def api_topic_daily_decision():
    d=request.get_json(force=True) if request.data else {}
    tid=tasks.submit(services.daily_topic_decision,d.get("model_config") or {})
    return jsonify({"task_id":tid})


@app.post("/api/daily/run")
def api_daily_run():
    d=request.get_json(force=True) if request.data else {}
    tid=tasks.submit(services.run_personal_daily,d,exclusive_key="CodexImagegen")
    return jsonify({"task_id":tid})


# ---------- 慢接口(后台)----------
@app.post("/api/search")
def api_search():
    d = request.get_json(force=True)
    tid = tasks.submit(services.search_hot, d.get("keyword", ""), int(d.get("limit", 15)), exclusive_key="OpenCLI")
    return jsonify({"task_id": tid})


@app.post("/api/collect")
def api_collect():
    tid = tasks.submit(services.run_collect, exclusive_key="OpenCLI")
    return jsonify({"task_id": tid})


@app.post("/api/account/data-center/collect")
def api_account_data_center_collect():
    """只读用户指定的小红书创作者账号数据中心。"""
    tid = tasks.submit(services.collect_creator_data_center, exclusive_key="OpenCLI")
    return jsonify({"task_id": tid})


@app.post("/api/generate")
def api_generate():
    d = request.get_json(force=True)
    tid = tasks.submit(services.generate_draft, d.get("topic_id", ""),
                       d.get("refs", []), d.get("model_config"), d.get("topic_brief"))
    return jsonify({"task_id": tid})


@app.get("/api/models")
def api_models():
    """探测本地可用 LLM + 返回配置选项给前端。"""
    return jsonify(services.probe_models())


@app.get("/api/samples")
def api_samples():
    """健身样本库 top 帖 + 爆火因子分析(快)。"""
    return jsonify(services.samples_analysis())


@app.post("/api/pipeline")
def api_pipeline():
    """人机协同选题：先采集外部样本并暂停，再由人工选Golden后续跑。"""
    d = request.get_json(force=True) if request.data else {}
    phase=str(d.get("phase") or "continue_after_human")
    if phase not in {"collect_external","continue_after_human"}:
        return jsonify({"ok":False,"error":"未知pipeline phase"}),400
    tid = tasks.submit(services.run_pipeline, bool(d.get("discover", True)),
                       d.get("model_config") or {}, phase, exclusive_key="OpenCLI")
    return jsonify({"task_id": tid})


@app.get("/api/xhs/verification/status")
def api_xhs_verification_status():
    """只读检查保留的前台认证会话，不自动点击或绕过验证。"""
    return jsonify(services.xhs_verification_status())


@app.post("/api/publish")
def api_publish():
    d = request.get_json(force=True)
    tid = tasks.submit(
        services.publish, d.get("title", ""), d.get("body", ""),
        d.get("topics", []), d.get("images", ""), d.get("category", ""),
        d.get("topic_id", ""), bool(d.get("draft", True)), d.get("golden_usage_id", ""), d.get("cover_text", ""), exclusive_key="OpenCLI")
    return jsonify({"task_id": tid})


@app.get("/api/task/<tid>")
def api_task(tid):
    t = tasks.get(tid)
    if not t:
        return jsonify({"status": "error", "error": "未知任务"}), 404
    return jsonify(t)



# ---------- Golden Sample / Pattern ----------
@app.get("/api/golden")
def api_golden():
    return jsonify(services.golden_overview())

@app.post("/api/golden/discover")
def api_golden_discover():
    d = request.get_json(force=True) if request.data else {}
    return jsonify(services.golden_discover(int(d.get("n", 10))))

@app.post("/api/golden/human")
def api_golden_human():
    d = request.get_json(force=True)
    return jsonify(services.golden_human_select(d.get("note_ref", ""), d.get("reason", "人工判断值得学习"), d.get("content_type", "unknown")))

@app.post("/api/golden/analyze")
def api_golden_analyze():
    d = request.get_json(force=True) if request.data else {}
    tid = tasks.submit(services.golden_analyze, d.get("model_config") or {}, int(d.get("limit", 20)), exclusive_key="OpenCLI")
    return jsonify({"task_id": tid})

@app.post("/api/golden/patterns/rebuild")
def api_golden_rebuild():
    return jsonify(services.golden_rebuild_patterns())

@app.post("/api/golden/revalidate")
def api_golden_revalidate():
    d = request.get_json(force=True) if request.data else {}
    tid = tasks.submit(services.golden_revalidate, int(d.get("limit", 10)), exclusive_key="OpenCLI")
    return jsonify({"task_id": tid})

@app.post("/api/golden/media")
def api_golden_media():
    d=request.get_json(force=True)
    return jsonify(services.golden_classify_media(d.get("candidate_id",""),d.get("content_type","unknown")))

@app.post("/api/golden/feedback")
def api_golden_feedback():
    d = request.get_json(force=True)
    return jsonify(services.golden_feedback(d.get("usage_id", ""), int(d.get("views", 0)), int(d.get("likes", 0)), int(d.get("favorites", 0)), int(d.get("comments", 0)),
                                            shares=int(d.get("shares",0)),rise_fans=int(d.get("rise_fans",0)),
                                            avg_view_time_seconds=d.get("avg_view_time_seconds"),ctr=d.get("ctr"),
                                            completion_rate=d.get("completion_rate"),top_source=d.get("top_source","")))


@app.get("/api/account/intelligence")
def api_account_intelligence():
    return jsonify(services.account_intelligence_overview())


@app.post("/api/account/audience")
def api_account_audience():
    d=request.get_json(force=True) if request.data else {}
    return jsonify(services.save_account_audience(d))


@app.post("/api/orchestrate")
def api_orchestrate():
    d = request.get_json(force=True) if request.data else {}
    tid = tasks.submit(services.orchestrate, d.get("topic_id", ""), d.get("refs", []), d.get("model_config") or {})
    return jsonify({"task_id": tid})

@app.post("/api/review")
def api_review():
    d = request.get_json(force=True)
    return jsonify(services.review_generated(d.get("topic_id", ""), d.get("content") or {}))

@app.post("/api/restart")
def api_restart():
    import subprocess
    # 用 subprocess 启动新进程 + 退出当前进程(os.execv 在 Windows 不可靠)
    def restart():
        import time
        time.sleep(0.5)
        kwargs = {"cwd": str(ROOT)}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen([sys.executable] + sys.argv, **kwargs)
        os._exit(0)
    import threading
    threading.Thread(target=restart, daemon=True).start()
    return jsonify({"ok": True, "message": "Flask 正在重启…"})


if __name__ == "__main__":
    print("运营看板启动中 → http://127.0.0.1:5000")
    app.run("127.0.0.1", 5000, threaded=True, debug=False)
