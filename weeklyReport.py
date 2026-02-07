import json
import datetime
import time
from collections import defaultdict, Counter, deque
import os
import sys
import threading
from functools import wraps
from flask import render_template, request, redirect, url_for
from flask import Flask, Response, jsonify, request, abort, session

def load_config(path="config.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()

# ================= 配置 =================
DB_FILE = CONFIG["db_file"]
LOG_FILE = CONFIG["log_file"]

QOS_LIMIT = CONFIG["qos"]["limit"]
QOS_WINDOW = CONFIG["qos"]["window"]

PORT = CONFIG["server"]["port"]
REFRESH_INTERVAL = CONFIG["server"]["refresh_interval"]

ADMIN_TOKEN = CONFIG["admin"]["token"]
ADMIN_IPS = set(CONFIG["admin"]["ips"])

SECRET_KEY = CONFIG["admin"]["secret_key"]
# =======================================

#周报缓存
REPORT_CACHE = None
REPORT_TS = 0

#重启函数
def restart_self():
    time.sleep(1)  # 给 HTTP 响应留时间
    python = sys.executable
    os.execv(python, [python] + sys.argv)

#管理员登录

def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            # API 请求 → 直接 401
            if request.path.startswith("/admin/api"):
                abort(401)
            # 页面请求 → 跳登录页
            return redirect("/admin/login")
        return view_func(*args, **kwargs)
    return wrapper

def build_time_series(records, start_ts, end_ts, bucket_seconds):
    """
    通用时间序列生成器
    """
    if end_ts <= start_ts:
        return [], []

    total_buckets = int((end_ts - start_ts) // bucket_seconds)
    values = [0] * total_buckets
    labels = []

    start = datetime.datetime.fromtimestamp(start_ts)

    for i in range(total_buckets):
        t = start + datetime.timedelta(seconds=i * bucket_seconds)

        # label 自适应显示
        if bucket_seconds >= 3600:
            labels.append(t.strftime("%m-%d %H:%M"))
        else:
            labels.append(t.strftime("%H:%M"))

    for r in records:
        ts = r.get("time")
        if not ts:
            continue

        idx = (ts - start_ts) // bucket_seconds
        if 0 <= idx < total_buckets:
            values[int(idx)] += 1

    return labels, values

#时间工具
def week_start_6am(ref=None):
    now = ref or datetime.datetime.now()
    monday = now - datetime.timedelta(days=now.weekday())
    return monday.replace(hour=6, minute=0, second=0, microsecond=0)


#数据加载
def load_data():
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


#连续 168 小时
def build_168h_series(records, start_ts):
    values = [0] * 168
    labels = []
    start = datetime.datetime.fromtimestamp(start_ts)

    for i in range(168):
        labels.append((start + datetime.timedelta(hours=i)).strftime("%a %H"))

    for r in records:
        t = r.get("time")
        if not t:
            continue
        idx = (t - start_ts) // 3600
        if 0 <= idx < 168:
            values[int(idx)] += 1

    return labels, values


#每条说说的小时序列
def build_shuoshuo_series(records, start_ts):
    result = defaultdict(lambda: [0] * 168)

    for r in records:
        sid = r.get("shuoshuo_id")
        t = r.get("time")
        if not sid or not t:
            continue

        idx = (t - start_ts) // 3600
        if 0 <= idx < 168:
            result[sid][int(idx)] += 1

    return result

def generate_weekly_report_full(week_offset: int = 0):
    all_data = load_data()

    start = week_start_6am() + datetime.timedelta(weeks=week_offset)
    end = start + datetime.timedelta(days=7)
    start_ts = int(start.timestamp())

    week_data = [
        r for r in all_data
        if start_ts <= r.get("time", 0) < int(end.timestamp())
    ]

    week_uins = {r["uin"] for r in week_data if "uin" in r}
    old_uins = {
        r["uin"]
        for r in all_data
        if r.get("time", 0) < start_ts and "uin" in r
    }

    labels, total_series = build_168h_series(week_data, start_ts)
    shuoshuo_series = build_shuoshuo_series(week_data, start_ts)

    shuoshuo_total = {
        sid: sum(series)
        for sid, series in shuoshuo_series.items()
    }

    filtered_sorted_shuoshuo = {
        sid: {
            "total": shuoshuo_total[sid],
            "series": shuoshuo_series[sid],
        }
        for sid in sorted(
            shuoshuo_total,
            key=lambda x: shuoshuo_total[x],
            reverse=True
        )
        if shuoshuo_total[sid] >= 30
    }

    return {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "week": f"{start.year}-W{start.isocalendar()[1]}",
        "summary": {
            "total_visits": len(week_data),
            "unique_visitors": len(week_uins),
            "new_visitors": len(week_uins - old_uins),
        },
        "hourly_168": {
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "labels": labels,
            "values": total_series,
        },
        "shuoshuo": filtered_sorted_shuoshuo,
    }

#周报生成
def generate_weekly_report(
    week_offset: int = 0,
    start_ts: int | None = None,
    end_ts: int | None = None,
    bucket_seconds: int = 3600
):
    all_data = load_data()

    # ===== 时间范围 =====
    if start_ts is None or end_ts is None:
        start = week_start_6am() + datetime.timedelta(weeks=week_offset)
        end = start + datetime.timedelta(days=7)
        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())

    # ===== 数据筛选 =====
    data = [
        r for r in all_data
        if start_ts <= r.get("time", 0) < end_ts
    ]

    # ===== 用户统计 =====
    uins = {r["uin"] for r in data if "uin" in r}
    old_uins = {
        r["uin"] for r in all_data
        if r.get("time", 0) < start_ts and "uin" in r
    }

    # ===== 时间曲线 =====
    labels, values = build_time_series(
        data,
        start_ts,
        end_ts,
        bucket_seconds
    )

    return {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "time_range": {
            "start": datetime.datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M"),
            "end": datetime.datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M"),
            "bucket_seconds": bucket_seconds
        },
        "summary": {
            "total_visits": len(data),
            "unique_visitors": len(uins),
            "new_visitors": len(uins - old_uins),
        },
        "series": {
            "labels": labels,
            "values": values
        }
    }



#刷新页面

def get_report_cached():
    global REPORT_CACHE, REPORT_TS
    now = time.time()

    if REPORT_CACHE is None or now - REPORT_TS >= REFRESH_INTERVAL:
        print("♻️ 刷新【当前周】访客周报数据")
        REPORT_CACHE = generate_weekly_report(0)
        REPORT_TS = now

    return REPORT_CACHE

#本周数据

def get_week_data():
    all_data = load_data()
    start = week_start_6am()
    end = start + datetime.timedelta(days=7)
    start_ts = int(start.timestamp())

    return [
        r for r in all_data
        if start_ts <= r.get("time", 0) < end.timestamp()
    ]

#本周top10

def get_week_top10_users():
    week_data = get_week_data()

    counter = Counter()
    name_map = {}

    for r in week_data:
        uin = r.get("uin")
        if not uin:
            continue

        counter[uin] += 1

        # 直接使用原始 JSON 里的 name
        if r.get("name"):
            name_map[uin] = r["name"]

    result = []
    for uin, cnt in counter.most_common(10):
        result.append({
            "uin": uin,
            "name": name_map.get(uin, "未知"),
            "visits": cnt
        })

    return result


#全量独立用户

def get_total_unique_users():
    all_data = load_data()
    return len({r["uin"] for r in all_data if "uin" in r})

#查询uin

def query_uin_records(uin, limit=200):
    uin = str(uin)
    all_data = load_data()

    records = []
    for r in all_data:
        if str(r.get("uin")) != uin:
            continue

        item = dict(r)

        ts = r.get("time", 0)

        # ✅ 优先使用原始 time_str
        if r.get("time_str"):
            item["time_human"] = r["time_str"]
        elif ts:
            item["time_human"] = datetime.datetime.fromtimestamp(
                ts
            ).strftime("%Y-%m-%d %H:%M:%S")
        else:
            item["time_human"] = "-"

        records.append(item)

    records.sort(key=lambda x: x.get("time", 0), reverse=True)
    return records[:limit]

#前序周报
def get_week_report(week_offset: int):
    """
    week_offset = 0   当前周
    week_offset = -1  上一周
    """
    # 你原来的数据生成逻辑
    report = generate_weekly_report(week_offset)

    report["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return report

# ================= Flask =================
app = Flask(__name__)

app.secret_key = SECRET_KEY

@app.route("/")
def index():
    week_offset = int(request.args.get("week", 0))
    if week_offset > 0:
        week_offset = 0

    report = generate_weekly_report_full(week_offset)

    return render_template(
        "index.html",
        report=report,
        week_offset=week_offset
    )


@app.route("/api/report")
def api_report():
    return jsonify(get_report_cached())


# ---------- HTML ----------
def render_html(report):
    options = ""
    for sid, v in sorted(
        report["shuoshuo"].items(),
        key=lambda x: -x[1]["total"]
    ):
        options += f'<option value="{sid}">{sid}（{v["total"]} 次）</option>'

    return render_template("index.html")

# ================= QoS 限流器 =================

# ip -> deque[timestamps]
IP_BUCKET = defaultdict(deque)

def qos_check(ip):
    now = time.time()
    bucket = IP_BUCKET[ip]

    # 清理过期请求
    while bucket and now - bucket[0] > QOS_WINDOW:
        bucket.popleft()

    if len(bucket) >= QOS_LIMIT:
        return False

    bucket.append(now)
    return True


# ================= IP 获取 =================

def get_client_ip():
    # 支持反向代理
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


# ================= 访问日志 =================

def write_access_log(record):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


ADMIN_IPS = {"127.0.0.1", "192.168.2.64"}

@app.before_request
def before_request():
    ip = get_client_ip()

    # 管理员接口不限流
    if request.path.startswith("/admin"):
        pass
    else:
        if not qos_check(ip):
            abort(429, description="想刷我接口吗😅")

    # ---- 记录日志 ----
    record = {
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": int(time.time()),
        "ip": ip,
        "port": request.environ.get("REMOTE_PORT"),
        "method": request.method,
        "path": request.path
    }
    write_access_log(record)


@app.route("/admin/api/top10")
@admin_required
def admin_week_top10():
    return jsonify({
        "week": get_report_cached()["week"],
        "top10": get_week_top10_users()
    })

@app.route("/admin/api/unique_total")
@admin_required
def admin_unique_users():
    return jsonify({
        "unique_users_total": get_total_unique_users()
    })

@app.route("/admin/api/uin/<uin>")
@admin_required
def admin_query_uin(uin):
    limit = int(request.args.get("limit", 200))
    return jsonify({
        "uin": uin,
        "count": len(query_uin_records(uin, limit)),
        "records": query_uin_records(uin, limit)
    })

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        token = request.form.get("token", "")
        if token == ADMIN_TOKEN:
            session["is_admin"] = True
            return redirect("/admin")
        return "Token 错误", 403

    return render_template("admin_login.html")

@app.route("/admin")
@admin_required
def admin_dashboard():
    return render_template("admin_dashboard.html")


@app.route("/admin/api/restart", methods=["POST"])
@admin_required
def admin_restart():
    threading.Thread(target=restart_self).start()
    return jsonify({
        "status": "ok",
        "message": "服务正在重启"
    })

@app.route("/api/report/custom")
def api_report_custom():
    """
    /api/report/custom?
        start=1706000000
        &end=1706600000
        &scale=3600
    """
    try:
        start_ts = int(request.args["start"])
        end_ts = int(request.args["end"])
        scale = int(request.args.get("scale", 3600))
    except Exception:
        abort(400, "参数错误")

    report = generate_weekly_report(
        start_ts=start_ts,
        end_ts=end_ts,
        bucket_seconds=scale
    )
    return jsonify(report)

def run_background():
    """
    在后台线程启动 Flask，不阻塞调用方
    """
    host = CONFIG["server"].get("host", "0.0.0.0")

    t = threading.Thread(
        target=app.run,
        kwargs={
            "host": host,
            "port": PORT,
            "debug": False,
            "use_reloader": False,  # ⚠️ 必须关
        },
        daemon=True
    )
    t.start()
    return t

if __name__ == "__main__":
    host = CONFIG["server"].get("host", "0.0.0.0")
    app.run(host=host, port=PORT, debug=False)

