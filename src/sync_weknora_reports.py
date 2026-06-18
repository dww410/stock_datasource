#!/usr/bin/env python3
"""
sync_weknora_reports.py — 搜索东财研报 → 下载 PDF → 上传到 WeKnora 知识库

用法:
  # 搜索+上传指定股票
  python3 src/sync_weknora_reports.py --search --codes 002920,601689,002405

  # 仅查看知识库统计
  python3 src/sync_weknora_reports.py --stats

  # 从本地目录上传已有PDF
  python3 src/sync_weknora_reports.py --upload --dir /tmp/pdfs

环境变量:
  WEKNORA_URL  (默认 http://localhost:18880)
  WEKNORA_EMAIL (默认 admin@weknora.com)
  WEKNORA_PASSWORD (默认 Admin1234)
"""
import os, sys, json, uuid, hashlib, re, time, argparse
from datetime import datetime
from pathlib import Path
import urllib.request, urllib.error
import subprocess

try:
    import requests
except ImportError:
    os.system("pip install requests -q")
    import requests

# ─── Config ───
WURL = os.environ.get("WEKNORA_URL", "http://localhost:18880")
WEMAIL = os.environ.get("WEKNORA_EMAIL", "admin@weknora.com")
WPASS = os.environ.get("WEKNORA_PASSWORD", "Admin1234")
REPORT_API = "https://reportapi.eastmoney.com/report/list"
PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{}_1.pdf"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

STOCK_MAP = {
    "002920": ("德赛西威", "智能驾驶"),
    "601689": ("拓普集团", "智能驾驶"),
    "002405": ("四维图新", "自动驾驶"),
    "600741": ("华域汽车", "智能驾驶"),
    "688017": ("绿的谐波", "智能驾驶"),
    "002463": ("沪电股份", "智能驾驶"),
    "300750": ("宁德时代", "自动驾驶"),
    "300124": ("汇川技术", "智能驾驶"),
}

def log(m):
    print(m, flush=True)

# ─── Auth ───
def login():
    r = requests.post(f"{WURL}/api/v1/auth/login",
        json={"email": WEMAIL, "password": WPASS}, timeout=10)
    r.raise_for_status()
    return r.json()["token"]

def api_get(path, token, params=None):
    r = requests.get(f"{WURL}{path}", headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def api_post(path, token, data=None):
    r = requests.post(f"{WURL}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=data or {}, timeout=10)
    r.raise_for_status()
    return r.json()

# ─── KB & Tags ───
def ensure_kb(token, name="研报", desc="行业、概念研究报告"):
    data = api_get("/api/v1/knowledge-bases", token)
    for kb in data.get("data", []):
        if kb["name"] == name:
            return kb["id"]
    d = api_post("/api/v1/knowledge-bases", token,
        {"name": name, "description": desc, "type": "document"})
    return d["data"]["id"]

def ensure_tags(token, kb_id, tag_names):
    data = api_get(f"/api/v1/knowledge-bases/{kb_id}/tags", token)
    existing = {t["name"]: t["id"] for t in data.get("data", {}).get("data", [])}
    for n in tag_names:
        if n not in existing:
            d = api_post(f"/api/v1/knowledge-bases/{kb_id}/tags", token,
                {"name": n, "color": "#FF6B35"})
            existing[n] = d["data"]["id"]
            log(f"  🏷 创建标签: {n}")
    return existing

# ─── EastMoney ───
def search_reports(codes=None, max_per_code=3, keyword=None):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://data.eastmoney.com/"})
    results = []
    for code in codes or []:
        params = {"pageSize": str(max_per_code), "beginTime": "2024-01-01",
            "endTime": "2026-05-31", "pageNo": "1", "qType": "0",
            "code": code, "rcode": ""}
        try:
            r = s.get(REPORT_API, params=params, timeout=30)
            rows = r.json().get("data") or []
            for row in rows:
                results.append({
                    "code": code, "title": row.get("title",""),
                    "date": str(row.get("publishDate",""))[:10],
                    "org": row.get("orgSName",""), "info_code": row.get("infoCode",""),
                    "rating": row.get("emRatingName",""),
                })
            log(f"  📡 {code}: {len(rows)} 篇")
        except Exception as e:
            log(f"  ⚠️ {code}: {e}")
        time.sleep(0.3)
    return results

def download_pdf(info_code, title, date, org, target_dir):
    url = PDF_TPL.format(info_code)
    safe = re.sub(r'[\\/:*?"<>|]', "_", title)[:60]
    fname = f"{date}_{org}_{safe}.pdf" if date else f"{info_code}.pdf"
    fpath = Path(target_dir) / fname
    if fpath.exists() and fpath.stat().st_size > 1024:
        return str(fpath)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Referer": "https://data.eastmoney.com/"})
        resp = urllib.request.urlopen(req, timeout=60)
        data = resp.read()
        if len(data) >= 1024:
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_bytes(data)
            return str(fpath)
    except Exception as e:
        log(f"  ⚠️ 下载失败: {e}")
    return None

# ─── Upload ───
def upload_pdfs(token, kb_id, tag_map, file_infos):
    """Upload PDFs via Docker exec → psql insert."""
    db_cmd = ["docker", "exec", "weknora-postgres",
              "psql", "-U", "weknora", "-d", "weknora", "-c"]
    done = 0
    for fi in file_infos:
        fp = Path(fi["path"])
        if not fp.exists():
            continue
        fsize = fp.stat().st_size
        fhash = hashlib.md5(fp.read_bytes()).hexdigest()
        fname = fp.name
        kid = str(uuid.uuid4())
        tag_id = tag_map.get(fi.get("tag", ""), "")
        title = fi["title"].replace("'", "''")
        desc = f"{fi.get('org','')} | {fi.get('stock','')}({fi.get('code','')}) | {fi.get('date','')}"
        meta = json.dumps({
            "source": "eastmoney", "org": fi.get("org",""),
            "stock_code": fi.get("code",""), "stock_name": fi.get("stock",""),
            "publish_date": fi.get("date",""),
        }, ensure_ascii=False)

        ret = os.system(f'docker cp "{fp}" weknora-app:/data/files/{fname}')
        if ret != 0:
            log(f"  ❌ 拷贝失败: {fname}")
            continue

        sql = f"""INSERT INTO knowledges (id, tenant_id, knowledge_base_id, type, title,
            description, source, parse_status, enable_status, file_name, file_type,
            file_size, file_path, file_hash, storage_size, metadata, tag_id, channel)
            VALUES ('{kid}', 1, '{kb_id}', 'file', '{title}', '{desc}',
            'local_upload', 'unprocessed', 'enabled', '{fname}', 'pdf', {fsize},
            '/data/files/{fname}', '{fhash}', {fsize}, '{meta}'::jsonb,
            '{tag_id}', 'upload');"""

        r = subprocess.run(db_cmd + [sql], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            done += 1
            log(f"  ✅ {fi['title'][:40]} ({fsize/1024:.0f}KB)")
        else:
            log(f"  ❌ DB: {r.stderr[:120]}")
    return done

# ─── Pipeline ───
def run_pipeline(codes, limit=3, outdir="/tmp/weknora_uploads"):
    log("=" * 55)
    log("🔍 搜索东财研报...")
    reports = search_reports(codes=codes, max_per_code=limit)
    log(f"总计: {len(reports)} 篇")
    if not reports:
        return

    log("\n📥 下载 PDF...")
    files = []
    for r in reports:
        cinfo = STOCK_MAP.get(r["code"], ("", "自动驾驶"))
        r["stock"] = cinfo[0]
        r["tag"] = cinfo[1]
        fp = download_pdf(r["info_code"], r["title"], r["date"], r["org"], outdir)
        if fp:
            r["path"] = fp
            files.append(r)
            log(f"  ✅ {r['date']} {r['org']:8s} | {r['title'][:50]}")
    log(f"下载: {len(files)}/{len(reports)}")

    if not files:
        return

    log("\n📤 上传 WeKnora...")
    try:
        token = login()
        kb_id = ensure_kb(token)
        tags_needed = set(f["tag"] for f in files if f.get("tag"))
        tags_needed |= {"自动驾驶", "智能驾驶", "线控底盘", "激光雷达", "车路云"}
        tag_map = ensure_tags(token, kb_id, tags_needed)
        n = upload_pdfs(token, kb_id, tag_map, files)
        log(f"\n📊 完成: 搜索 {len(reports)} → 下载 {len(files)} → 上传 {n}")
    except Exception as e:
        log(f"❌ 上传失败: {e}")

# ─── Stats ───
def show_stats():
    try:
        token = login()
        data = api_get("/api/v1/knowledge-bases", token)
        for kb in data.get("data", []):
            kbid = kb["id"]
            kbname = kb["name"]
            kbdesc = kb.get("description", "")
            tdata = api_get(f"/api/v1/knowledge-bases/{kbid}/tags", token)
            tags = tdata.get("data", {}).get("data", [])
            cnt, sz = subprocess.run(
                ["docker", "exec", "weknora-postgres", "psql", "-U", "weknora", "-d", "weknora", "-Atc",
                 f"SELECT count(*),coalesce(sum(file_size),0) FROM knowledges WHERE knowledge_base_id='{kbid}' AND deleted_at IS NULL"],
                capture_output=True, text=True, timeout=5).stdout.strip().split("|")
            log(f"\n📚 {kbname} — {cnt} 篇，{int(sz)/1024/1024:.1f} MB")
            log(f"   {kbdesc}")
            if tags:
                tag_strs = [f"#{t['name']}({t.get('knowledge_count',0)})" for t in tags]
                log(f"   标签: {' | '.join(tag_strs)}")
    except Exception as e:
        log(f"❌ 查询失败: {e}")

# ─── Main ───
if __name__ == "__main__":
    p = argparse.ArgumentParser(description="研报搜索+下载+WeKnora上传")
    p.add_argument("--search", action="store_true", help="搜索+下载+上传")
    p.add_argument("--codes", default="", help="股票代码，逗号分隔")
    p.add_argument("--limit", type=int, default=3, help="每只股篇数")
    p.add_argument("--upload", action="store_true", help="上传已有PDF")
    p.add_argument("--dir", default="/tmp/weknora_uploads", help="PDF目录")
    p.add_argument("--stats", action="store_true", help="统计")
    args = p.parse_args()

    if args.stats:
        show_stats()
    elif args.search:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()] if args.codes else list(STOCK_MAP.keys())
        run_pipeline(codes, args.limit, args.dir)
    elif args.upload:
        token = login()
        kb_id = ensure_kb(token)
        tag_map = ensure_tags(token, kb_id, ["自动驾驶", "智能驾驶", "线控底盘", "激光雷达", "车路云"])
        files = [{"path": str(f), "title": f.stem, "tag": "自动驾驶", "code": "", "stock": "", "org": "", "date": ""}
                 for f in Path(args.dir).glob("*.pdf")]
        n = upload_pdfs(token, kb_id, tag_map, files)
        log(f"上传: {n} 篇")
    else:
        p.print_help()
