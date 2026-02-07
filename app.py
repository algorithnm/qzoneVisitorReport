import requests
import json
import openpyxl
import time
import re
import os
import datetime
import logging
import sys
from requests.utils import quote
import weeklyReport
import time

def load_config(path="config.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()

# ================= 配置区域 =================
TARGET_UIN = CONFIG["visitor"]["UIN"]  # 目标QQ
UIN_NAME = CONFIG["visitor"]["nickname"]
COOKIE_PATH = fr"./COOKIE/cookies-{TARGET_UIN}.json"
DB_FILE = f"./qzone_visitor_db_{UIN_NAME}.json"
EXCEL_FILE = f"./qzone_访客记录_总表_{UIN_NAME}.xlsx"
INTERVAL = CONFIG["visitor"]["interval"]  # 刷新间隔(秒)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0"
# ===========================================

# 启动 Web 服务（不阻塞）
weeklyReport.run_background()

# ================= 日志配置 =================
# 配置日志输出格式：时间 - 级别 - 消息
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout)  # 输出到控制台
        # 如果需要同时保存日志到文件，取消下面这行的注释:
        # logging.FileHandler("qzone_monitor.log", encoding='utf-8')
    ]
)
logger = logging.getLogger("QzoneMonitor")
# ===========================================

def get_g_tk(skey):
    """计算 g_tk (bkn)"""
    hash_val = 5381
    for c in skey:
        hash_val = (hash_val << 5) + hash_val + ord(c)
    return hash_val & 2147483647

def refresh_cookie():
    """连接本地QQ客户端自动更新Cookie"""
    logger.info("正在刷新 Cookie (依赖本地QQ)...")
    s = requests.Session()
    s.headers.update({'User-Agent': UA})
    uin = TARGET_UIN

    try:
        # 1. 获取 pt_local_token
        url1 = "https://xui.ptlogin2.qq.com/cgi-bin/xlogin?s_url=https%3A%2F%2Fhuifu.qq.com%2Findex.html&style=20&appid=715021417&proxy_url=https%3A%2F%2Fhuifu.qq.com%2Fproxy.html"
        tk = s.get(url1, timeout=5).cookies.get('pt_local_token')
        if not tk: raise Exception("无法获取 pt_local_token")

        # 2. 获取 clientkey
        url2 = f"https://localhost.ptlogin2.qq.com:4301/pt_get_st?clientuin={uin}&callback=ptui_getst_CB&r=0.7284667321181328&pt_local_tk={tk}"
        try:
            r2 = s.get(url2, headers={'Referer': 'https://ssl.xui.ptlogin2.qq.com/', 'Cookie': f'pt_local_token={tk}'}, timeout=5)
        except: raise Exception("连接本地QQ失败，请检查QQ是否运行")
        
        if r2.status_code == 400: raise Exception("本地接口返回400")
        
        k_idx = re.search(r'keyindex:\s*(\d+)', r2.text)
        ck = r2.cookies.get('clientkey')
        if not k_idx or not ck: raise Exception("无法提取 keyindex 或 clientkey")

        # 3. 获取跳转链接
        u1 = quote(f"https://qzs.qzone.qq.com/qzone/v5/loginsucc.html?para=izone&specifyurl=http%3A%2F%2Fuser.qzone.qq.com%2F{uin}%2Finfocenter")
        url3 = f"https://ssl.ptlogin2.qq.com/jump?clientuin={uin}&keyindex={k_idx.group(1)}&pt_aid=549000912&daid=5&u1={u1}&pt_local_tk={tk}&pt_3rd_aid=0&ptopt=1&style=40"
        
        r3 = s.get(url3, headers={'Cookie': f'pt_local_token={tk};clientuin={uin};clientkey={ck};'}, timeout=5)
        # --- 正则修复 ---
        # 兼容两种格式: 
        # 1. ptui_qzone_login('0', '0', 'http...') 
        # 2. ptui_qlogin_CB('0', 'http...', '')
        pturl = re.search(r"'0',\s*(?:'0',\s*)?'(http.*?)'", r3.text)
        if not pturl: raise Exception("无法解析跳转URL")

        # 4. 获取最终Cookie
        r4 = requests.get(pturl.group(1), headers={'User-Agent': UA}, allow_redirects=False, timeout=10)
        cookies = r4.cookies.get_dict()
        
        if 'p_skey' not in cookies: raise Exception("最终Cookie缺失 p_skey")

        cookies['g_tk'] = get_g_tk(cookies['p_skey'])
        
        os.makedirs(os.path.dirname(COOKIE_PATH), exist_ok=True)
        with open(COOKIE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cookies, f, indent=4, ensure_ascii=False)
            
        logger.info("Cookie 更新成功")
        return True

    except Exception as e:
        logger.error(f"Cookie 更新失败: {e}")
        return False

def get_headers():
    """读取本地配置"""
    try:
        with open(COOKIE_PATH, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return {
            'Cookie': ';'.join([f"{k}={v}" for k, v in d.items()]),
            'User-Agent': UA
        }, d.get('g_tk')
    except: return None, None

def append_to_json(new_records):
    """只把新记录追加到 JSON 数组后面"""
    if not new_records:
        return

    # 如果文件不存在，直接创建
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_records, f, ensure_ascii=False, indent=4)
        return

    # JSON 不能真正 append，只能：读 -> 改 -> 写
    with open(DB_FILE, 'r+', encoding='utf-8') as f:
        try:
            data = json.load(f)
            if not isinstance(data, list):
                data = []
        except json.JSONDecodeError:
            data = []

        data.extend(new_records)

        f.seek(0)
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.truncate()


def save_data(records):
    """保存数据到 Excel 和 JSON"""
    # Excel
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "访客记录"
        ws.append(["时间戳(time)", "访问时间", "QQ号(uin)", "昵称(name)", "src", "platform_src", "service_src", "hide_from", "is_hide_visit", "yellow", "supervip", "shuoshuo_id"])
        
        for r in records:
            ws.append([
                r['time'], r['time_str'], r['uin'], r['name'], r['src'], r['platform_src'], 
                r['service_src'], r['hide_from'], r['is_hide_visit'], r['yellow'], r['supervip'], r['shuoshuo_id']
            ])
            
        dims = {'A': 13, 'B': 20, 'C': 20, 'D': 15, 'L': 30}
        for c, w in dims.items(): ws.column_dimensions[c].width = w
        wb.save(EXCEL_FILE)
    except PermissionError: 
        logger.warning("Excel 被占用，本次未写入，仅更新JSON")

    # JSON
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=4)

def parse_visitor(item):
    """格式化单条数据"""
    ssid = item['shuoshuoes'][0]['id'] if item.get('shuoshuoes') else ""
    return {
        "time": item.get('time'), "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item.get('time') or 0)),
        "uin": item.get('uin'), "name": item.get('name'),
        "src": item.get('src'), "platform_src": item.get('platform_src'),
        "service_src": item.get('service_src'), "hide_from": item.get('hide_from'),
        "is_hide_visit": item.get('is_hide_visit'), "yellow": item.get('yellow'),
        "supervip": item.get('supervip'), "shuoshuo_id": ssid
    }

def run_task(retry=False):
    """执行监控任务"""
    headers, tk = get_headers()
    if not headers:
        if refresh_cookie(): return run_task(True)
        return

    url = f"https://h5.qzone.qq.com/proxy/domain/g.qzone.qq.com/cgi-bin/friendshow/cgi_get_visitor_more?uin={TARGET_UIN}&mask=7&page=1&fupdate=1&g_tk={tk}"
    
    try:
        res = requests.get(url, headers=headers, timeout=10).text.strip()
    except Exception as e:
        logger.error(f"请求超时/错误: {e}")
        return

    # 检查状态
    match = re.search(r'_Callback\((.*)\);?', res, re.DOTALL)
    if not match:
        logger.warning("API 返回非 JSON 格式 -> 尝试刷新 Cookie")
        if not retry and refresh_cookie(): run_task(True)
        return

    try:
        data = json.loads(match.group(1))
        if data.get('code') != 0:
            logger.warning(f"API 错误 (code={data.get('code')}) -> 尝试刷新 Cookie")
            if not retry and refresh_cookie(): run_task(True)
            return
    except: return

    # 处理数据
    new_items = []
    for item in data.get('data', {}).get('items', []):
        new_items.append(parse_visitor(item))
        for sub in item.get('uins', []): new_items.append(parse_visitor(sub))

    # 合并去重
    local = []
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f: local = json.load(f)
        except: pass

    exist_keys = {(r['uin'], r['time']) for r in local}
    added = 0
    added_records = []

    for r in new_items:
        key = (r['uin'], r['time'])
        if key not in exist_keys:
            local.append(r)
            added_records.append(r)
            exist_keys.add(key)
            added += 1


    if added > 0:
        logger.info(f"发现 {added} 条新记录，已追加保存。")

    # 1. 追加 JSON
    append_to_json(added_records)

    # 2. Excel 仍然用全量（否则很难保证顺序 & 去重）
    local.sort(key=lambda x: x.get('time') or 0, reverse=True)
    save_data(local)


def main():
    logger.info(f"监控启动 | QQ: {TARGET_UIN} | 频率: {INTERVAL}s")
    if not os.path.exists(COOKIE_PATH): refresh_cookie()
    
    while True:
        try:
            run_task()
            time.sleep(INTERVAL)
        except KeyboardInterrupt:
            logger.info("停止运行")
            break
        except Exception as e:
            logger.critical(f"未知错误: {e}", exc_info=True)
            time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
