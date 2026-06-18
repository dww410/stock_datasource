#!/usr/bin/env python3
"""
sync_ad_reports.py — 批量搜索东财自动驾驶产业链研报 → 下载 PDF → 上传 WeKnora
支持全页扫描、断点续传、标签分类、重复检测。

用法:
  # 全量搜索+下载+上传（所有自动驾驶标的）
  python3 src/sync_ad_reports.py --all

  # 只搜索不下载（预览）
  python3 src/sync_ad_reports.py --search-only

  # 从缓存目录上传已有 PDF
  python3 src/sync_ad_reports.py --upload --dir /tmp/ad_reports

  # 查看 WeKnora 统计
  python3 src/sync_ad_reports.py --stats

  # 增量：只处理指定代码
  python3 src/sync_ad_reports.py --codes 002920,601689
"""
import os, sys, json, uuid, hashlib, re, time, argparse
from datetime import datetime, date
from pathlib import Path
import urllib.request, urllib.error
import subprocess

try:
    import requests
except ImportError:
    os.system("pip install requests -q")
    import requests

# ─── Config ──────────────────────────────────────────────
WURL = os.environ.get("WEKNORA_URL", "http://localhost:18880")
WEMAIL = os.environ.get("WEKNORA_EMAIL", "admin@weknora.com")
WPASS = os.environ.get("WEKNORA_PASSWORD", "Admin1234")
REPORT_API = "https://reportapi.eastmoney.com/report/list"
PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{}_1.pdf"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
OUTDIR = "/tmp/ad_reports"
BEGIN_TIME = "2024-01-01"
END_TIME = "2026-05-31"
MAX_PAGES = 10  # 每只股票最多查10页（1000篇，实际无意义）

# ─── 股票 → 标签映射 ────────────────────────────────────
# 按自动驾驶细分领域分类
AD_STOCKS = {
    # ── 智驾方案(ADAS系统集成/域控) ──
    "002920": ("德赛西威", "智驾方案"),
    "688326": ("经纬恒润", "智驾方案"),
    "300496": ("中科创达", "智驾方案"),
    # ── 线控底盘(制动/转向/悬挂) ──
    "601689": ("拓普集团", "线控底盘"),
    "603596": ("伯特利", "线控底盘"),
    "002284": ("亚太股份", "线控底盘"),
    "002048": ("宁波华翔", "线控底盘"),
    # ── 感知传感(LiDAR/摄像头/雷达/传感器) ──
    "603297": ("永新光学", "感知传感"),
    "300552": ("万集科技", "感知传感"),
    "002036": ("联创电子", "感知传感"),
    "603197": ("保隆科技", "感知传感"),
    # ── 高精定位(地图/GNSS) ──
    "002405": ("四维图新", "高精定位"),
    "300627": ("华测导航", "高精定位"),
    "300177": ("中海达", "高精定位"),
    # ── 智能座舱(HUD/座舱电子) ──
    "002906": ("华阳集团", "智能座舱"),
    "600699": ("均胜电子", "智能座舱"),
    "002813": ("路畅科技", "智能座舱"),
    "002766": ("索菱股份", "智能座舱"),
    "300928": ("华安鑫创", "智能座舱"),
    "301221": ("光庭信息", "智能座舱"),
    # ── 车路云(V2X/车路协同) ──
    "300098": ("高新兴", "车路云"),
    "300807": ("天迈科技", "车路云"),
    "300212": ("易华录", "车路云"),
    "300020": ("银江技术", "车路云"),
    # ── 车载芯片(CIS/半导体) ──
    "603501": ("韦尔股份", "车载芯片"),
    "300661": ("圣邦股份", "车载芯片"),
    "688052": ("纳芯微", "车载芯片"),
    "603005": ("晶方科技", "车载芯片"),
    "600745": ("闻泰科技", "车载芯片"),
    # ── 通用(涉及多个领域) ──
    "300825": ("阿尔特", "自动驾驶"),
}

# ─── 新增子标签定义 ────────────────────────────────────
NEW_TAGS = {
    "智驾方案":   {"color": "#4A90D9", "desc": "ADAS/智驾域控/系统集成"},
    "感知传感":   {"color": "#7B68EE", "desc": "激光雷达/摄像头/雷达/传感器"},
    "高精定位":   {"color": "#2E8B57", "desc": "高精地图/GNSS定位"},
    "智能座舱":   {"color": "#D2691E", "desc": "HUD/座舱电子/车载娱乐"},
    "车载芯片":   {"color": "#8B4513", "desc": "车载CIS/半导体/传感器芯片"},
}

def log(m):
    print(m, flush=True)

# ─── WeKnora Auth ────────────────────────────────────────
def login():
    r = requests.post(f"{WURL}/api/v1/auth/login",
        json={"email": WEMAIL, "password": WPASS}, timeout=10)
    r.raise_for_status()
    return r.json()["token"]

def api_get(path, token, params=None):
    r = requests.get(f"{WURL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def api_post(path, token, data=None):
    r = requests.post(f"{WURL}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=data or {}, timeout=15)
    r.raise_for_status()
    return r.json()

# ─── 知识库 & 标签管理 ─────────────────────────────────
def ensure_kb(token, name="研报"):
    data = api_get("/api/v1/knowledge-bases", token)
    for kb in data.get("data", []):
        if kb["name"] == name:
            log(f"  📚 知识库: {name} ({kb['id'][:8]}...)")
            return kb["id"]
    d = api_post("/api/v1/knowledge-bases", token,
        {"name": name, "description": "行业、概念研究报告", "type": "document"})
    return d["data"]["id"]

def ensure_tags(token, kb_id):
    """获取或创建所有需要的标签，返回 {name: id} 映射"""
    data = api_get(f"/api/v1/knowledge-bases/{kb_id}/tags", token)
    existing = {t["name"]: t["id"] for t in data.get("data", {}).get("data", [])}
    log(f"  已有标签: {list(existing.keys())}")

    # 确保基础标签存在
    base_tags = ["自动驾驶", "智能驾驶", "线控底盘", "激光雷达", "车路云"]
    for n in base_tags:
        if n not in existing:
            c = NEW_TAGS.get(n, {}).get("color", "#FF6B35")
            d = api_post(f"/api/v1/knowledge-bases/{kb_id}/tags", token,
                {"name": n, "color": c})
            existing[n] = d["data"]["id"]
            log(f"  🏷 创建标签: {n}")

    # 创建新增子标签
    for n, cfg in NEW_TAGS.items():
        if n not in existing:
            d = api_post(f"/api/v1/knowledge-bases/{kb_id}/tags", token,
                {"name": n, "color": cfg["color"]})
            existing[n] = d["data"]["id"]
            log(f"  🏷 创建子标签: {n} ({cfg['desc']})")
        else:
            log(f"  ✅ 标签已存在: {n}")

    return existing

# ─── 东财研报全页搜索 ────────────────────────────────
def search_reports_all_pages(code, max_pages=10):
    """搜索指定股票的所有研报（多页）"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://data.eastmoney.com/"})
    all_rows = []
    total_pages = 1

    for page in range(1, max_pages + 1):
        params = {
            "pageSize": "100", "industryCode": "*", "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": BEGIN_TIME, "endTime": END_TIME,
            "pageNo": str(page), "fields": "", "qType": "0",
            "orgCode": "", "code": code, "rcode": "",
            "p": str(page), "pageNum": str(page), "pageNumber": str(page),
        }
        try:
            r = s.get(REPORT_API, params=params, timeout=30)
            d = r.json()
            rows = d.get("data") or []
            if not rows:
                break
            all_rows.extend(rows)
            tp = d.get("TotalPage", 1) or 1
            if page == 1:
                total_pages = min(tp, max_pages)
            if page >= total_pages:
                break
            time.sleep(0.3)
        except Exception as e:
            log(f"    ⚠️ 第{page}页: {e}")
            break

    return all_rows

def download_pdf(info_code, title, date_str, org, target_dir):
    """下载单篇研报PDF，已存在则跳过"""
    url = PDF_TPL.format(info_code)
    safe = re.sub(r'[\\/:*?"<>|]', "_", title)[:60]
    fname = f"{date_str}_{org}_{safe}.pdf" if date_str else f"{info_code}.pdf"
    fpath = Path(target_dir) / fname
    if fpath.exists() and fpath.stat().st_size > 1024:
        return str(fpath)  # 已存在直接返回

    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://data.eastmoney.com/"})
            resp = urllib.request.urlopen(req, timeout=120)
            data = resp.read()
            if len(data) >= 1024:
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_bytes(data)
                return str(fpath)
            elif attempt < 2:
                time.sleep(2)
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                log(f"    ⚠️ 下载失败({attempt+1}次): {info_code} {e}")
    return None

# ─── 已上传文件查询（去重）────────────────────────────
def get_existing_hashes():
    """查询 WeKnora 中已上传文件的 hash 集合"""
    try:
        r = subprocess.run(
            ["docker", "exec", "weknora-postgres", "psql", "-U", "weknora",
             "-d", "weknora", "-Atc",
             "SELECT file_hash FROM knowledges WHERE deleted_at IS NULL AND file_hash IS NOT NULL"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return set(h.strip() for h in r.stdout.strip().split("\n") if h.strip())
    except Exception as e:
        log(f"  ⚠️ 查询已上传hash失败: {e}")
    return set()

# ─── 上传到 WeKnora ─────────────────────────────────────
def upload_pdfs(token, kb_id, tag_map, file_infos, existing_hashes=None):
    """批量上传PDF到WeKnora（docker cp + psql），跳过已存在的"""
    if existing_hashes is None:
        existing_hashes = get_existing_hashes()

    db_cmd = ["docker", "exec", "weknora-postgres",
              "psql", "-U", "weknora", "-d", "weknora", "-c"]
    done, skipped, failed = 0, 0, 0

    for fi in file_infos:
        fp = Path(fi["path"])
        if not fp.exists():
            failed += 1
            continue

        fsize = fp.stat().st_size
        fhash = hashlib.md5(fp.read_bytes()).hexdigest()
        fname = fp.name

        # 去重
        if fhash in existing_hashes:
            skipped += 1
            continue

        kid = str(uuid.uuid4())
        tag_id_val = tag_map.get(fi.get("tag", ""), "")
        title = fi["title"].replace("'", "''").replace("\\", "\\\\")
        desc = f"{fi.get('org','')} | {fi.get('stock','')}({fi.get('code','')}) | {fi.get('date','')}"
        meta = json.dumps({
            "source": "eastmoney",
            "org": fi.get("org", ""),
            "stock_code": fi.get("code", ""),
            "stock_name": fi.get("stock", ""),
            "publish_date": fi.get("date", ""),
            "rating": fi.get("rating", ""),
        }, ensure_ascii=False).replace("'", "''")

        # docker cp (用subprocess避免shell特殊字符问题)
        cp = subprocess.run(
            ["docker", "cp", str(fp), f"weknora-app:/data/files/{fname}"],
            capture_output=True, text=True, timeout=30)
        if cp.returncode != 0:
            log(f"    ⚠️ cp失败: {fname}: {cp.stderr[:80]}")
            failed += 1
            continue

        sql = f"""INSERT INTO knowledges (id, tenant_id, knowledge_base_id, type, title,
            description, source, parse_status, enable_status, file_name, file_type,
            file_size, file_path, file_hash, storage_size, metadata, tag_id, channel)
            VALUES ('{kid}', 1, '{kb_id}', 'file', '{title}', '{desc}',
            'local_upload', 'unprocessed', 'enabled', '{fname}', 'pdf', {fsize},
            '/data/files/{fname}', '{fhash}', {fsize}, '{meta}'::jsonb,
            '{tag_id_val}', 'upload');"""

        r = subprocess.run(db_cmd + [sql], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            done += 1
            existing_hashes.add(fhash)
            if done % 20 == 0:
                log(f"  ...已上传 {done} 篇")
        else:
            err = r.stderr[:100]
            if "duplicate" in err.lower() or "unique" in err.lower() or "already exists" in err.lower():
                skipped += 1
                existing_hashes.add(fhash)
            else:
                log(f"  ❌ DB错误: {err}")
                failed += 1

    return done, skipped, failed

# ─── 主流水线 ──────────────────────────────────────────
def run_pipeline(codes=None, search_only=False, outdir=OUTDIR):
    log("=" * 60)
    log(f"🔍 自动驾驶产业链研报批量同步 ({datetime.now():%Y-%m-%d %H:%M})")
    log(f"   标的数: {len(codes or AD_STOCKS)} | 时段: {BEGIN_TIME}~{END_TIME}")
    log("=" * 60)

    # 1. 搜索所有研报
    total_found = 0
    all_reports = []
    for code in (codes or list(AD_STOCKS.keys())):
        name = AD_STOCKS[code][0]
        tag = AD_STOCKS[code][1]
        try:
            rows = search_reports_all_pages(code, MAX_PAGES)
            log(f"  📡 {code} {name}: {len(rows)} 篇 (标签: {tag})")
            for row in rows:
                all_reports.append({
                    "code": code,
                    "stock": name,
                    "tag": tag,
                    "title": row.get("title", ""),
                    "date": str(row.get("publishDate", ""))[:10],
                    "org": row.get("orgSName", ""),
                    "info_code": row.get("infoCode", ""),
                    "rating": row.get("emRatingName", ""),
                    "predict_eps_this": row.get("predictThisYearEps", ""),
                    "predict_eps_next": row.get("predictNextYearEps", ""),
                })
            total_found += len(rows)
        except Exception as e:
            log(f"  ❌ {code} {name}: {e}")
        time.sleep(0.3)

    log(f"\n📊 总计搜索: {total_found} 篇")
    if search_only:
        # 统计分布
        tag_dist = {}
        for r in all_reports:
            t = r["tag"]
            tag_dist[t] = tag_dist.get(t, 0) + 1
        log("\n标签分布:")
        for t, c in sorted(tag_dist.items(), key=lambda x: -x[1]):
            log(f"  {t}: {c} 篇")
        return all_reports

    if not all_reports:
        log("❌ 无研报可下载")
        return

    # 2. 下载 PDF
    outdir_p = Path(outdir)
    outdir_p.mkdir(parents=True, exist_ok=True)
    log(f"\n📥 下载 PDF → {outdir}")
    downloaded = []
    for r in all_reports:
        if not r["info_code"]:
            continue
        fp = download_pdf(r["info_code"], r["title"], r["date"], r["org"], outdir)
        if fp:
            r["path"] = fp
            downloaded.append(r)

    log(f"\n📊 下载: {len(downloaded)}/{total_found} 篇 ({sum(Path(r['path']).stat().st_size for r in downloaded)/1024/1024:.1f}MB)")

    if not downloaded:
        log("❌ 无PDF可上传")
        return

    # 3. 上传 WeKnora
    log(f"\n📤 上传 WeKnora...")
    try:
        token = login()
        kb_id = ensure_kb(token)
        tag_map = ensure_tags(token, kb_id)
        existing = get_existing_hashes()
        log(f"   已有 {len(existing)} 个文件hash（用于去重）")

        done, skipped, failed = upload_pdfs(token, kb_id, tag_map, downloaded, existing)

        log("\n" + "=" * 60)
        log(f"📊 完成报告")
        log(f"   搜索: {total_found} 篇")
        log(f"   下载: {len(downloaded)} 篇 ({sum(Path(r['path']).stat().st_size for r in downloaded)/1024/1024:.1f}MB)")
        log(f"   上传: {done} 篇")
        log(f"   跳过(已存在): {skipped} 篇")
        log(f"   失败: {failed} 篇")
        log("=" * 60)

        # 标签分布
        tag_dist = {}
        for r in downloaded:
            t = r["tag"]
            tag_dist[t] = tag_dist.get(t, 0) + 1
        log("\n上传分类:")
        for t, c in sorted(tag_dist.items(), key=lambda x: -x[1]):
            log(f"  🏷 {t}: {c} 篇")

    except Exception as e:
        log(f"❌ 上传阶段失败: {e}")
        import traceback
        log(traceback.format_exc())

    return downloaded

# ─── 统计 ────────────────────────────────────────────────
def show_stats(detailed=False):
    try:
        token = login()
        data = api_get("/api/v1/knowledge-bases", token)
        for kb in data.get("data", []):
            kbid = kb["id"]
            kbname = kb["name"]

            tdata = api_get(f"/api/v1/knowledge-bases/{kbid}/tags", token)
            tags = tdata.get("data", {}).get("data", [])

            cnt, sz = subprocess.run(
                ["docker", "exec", "weknora-postgres", "psql", "-U", "weknora",
                 "-d", "weknora", "-Atc",
                 f"SELECT count(*),coalesce(sum(file_size),0) FROM knowledges WHERE knowledge_base_id='{kbid}' AND deleted_at IS NULL"],
                capture_output=True, text=True, timeout=5).stdout.strip().split("|")
            cnt = int(cnt)
            sz = int(sz)

            log(f"\n📚 {kbname} — {cnt} 篇，{sz/1024/1024:.1f} MB")
            if tags:
                log(f"   标签分布:")
                for t in tags:
                    tc = t.get("knowledge_count", 0)
                    log(f"     #{t['name']}: {tc} 篇")
            if detailed and kbname == "研报":
                # 按解析状态分
                r = subprocess.run(
                    ["docker", "exec", "weknora-postgres", "psql", "-U", "weknora",
                     "-d", "weknora", "-Atc",
                     f"SELECT parse_status, count(*) FROM knowledges WHERE knowledge_base_id='{kbid}' AND deleted_at IS NULL GROUP BY parse_status ORDER BY count(*) DESC"],
                    capture_output=True, text=True, timeout=5)
                log(f"\n   解析状态:")
                for line in r.stdout.strip().split("\n"):
                    if line.strip():
                        st, n = line.split("|")
                        log(f"     {st}: {n}")
    except Exception as e:
        log(f"❌ 查询失败: {e}")

# ─── CLI ─────────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser(description="自动驾驶研报批量同步到WeKnora")
    p.add_argument("--all", action="store_true", help="全量搜索+下载+上传")
    p.add_argument("--search-only", action="store_true", help="仅搜索预览")
    p.add_argument("--codes", default="", help="股票代码，逗号分隔")
    p.add_argument("--upload", action="store_true", help="上传已有PDF目录")
    p.add_argument("--dir", default=OUTDIR, help="PDF目录")
    p.add_argument("--limit", type=int, default=10, help="每只股票最大页数")
    p.add_argument("--stats", action="store_true", help="统计")
    p.add_argument("--background", action="store_true", help="后台运行(nohup)")
    args = p.parse_args()

    if args.stats:
        show_stats(detailed=True)
    elif args.search_only:
        run_pipeline(search_only=True)
    elif args.upload:
        token = login()
        kb_id = ensure_kb(token)
        tag_map = ensure_tags(token, kb_id)
        existing = get_existing_hashes()
        files = []
        for f in sorted(Path(args.dir).glob("*.pdf")):
            # 尝试从文件名解析信息
            parts = f.stem.split("_", 2)
            files.append({
                "path": str(f),
                "title": f.stem,
                "tag": "自动驾驶",
                "code": "",
                "stock": "",
                "org": parts[1] if len(parts) > 1 else "",
                "date": parts[0] if len(parts) > 0 else "",
                "rating": "",
            })
        log(f"准备上传 {len(files)} 篇")
        done, skipped, failed = upload_pdfs(token, kb_id, tag_map, files, existing)
        log(f"上传: {done} 篇, 跳过: {skipped}, 失败: {failed}")
    elif args.all or args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()] if args.codes else list(AD_STOCKS.keys())
        MAX_PAGES = args.limit
        run_pipeline(codes)
    else:
        p.print_help()
