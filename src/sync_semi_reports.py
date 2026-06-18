#!/usr/bin/env python3
"""sync_semi_reports.py — 芯片半导体产业链研报批量同步到 WeKnora"""
import os, sys, json, uuid, hashlib, re, time, argparse, subprocess
from pathlib import Path
import urllib.request
try: import requests
except: os.system("pip install requests -q"); import requests

WURL = os.environ.get("WEKNORA_URL", "http://localhost:18880")
WEMAIL = "admin@weknora.com"; WPASS = "Admin1234"
REPORT_API = "https://reportapi.eastmoney.com/report/list"
PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{}_1.pdf"
UA = "Mozilla/5.0"
OUTDIR = "/tmp/semi_reports"
KB_ID = "44f0265c-2a1a-4ae6-b5ec-a55d955998ff"  # 芯片半导体

SEMI_STOCKS = {
    "603986": ("兆易创新", "存储芯片"), "300223": ("北京君正", "存储芯片"),
    "688008": ("澜起科技", "存储芯片"), "688525": ("佰维存储", "存储芯片"),
    "688110": ("东芯股份", "存储芯片"),
    "300661": ("圣邦股份", "模拟芯片"), "688536": ("思瑞浦", "模拟芯片"),
    "688798": ("艾为电子", "模拟芯片"), "688381": ("帝奥微", "模拟芯片"),
    "603290": ("斯达半导", "功率半导体"), "600460": ("士兰微", "功率半导体"),
    "688187": ("时代电气", "功率半导体"), "605111": ("新洁能", "功率半导体"),
    "688261": ("东微半导", "功率半导体"),
    "688018": ("乐鑫科技", "SoC/MCU"), "688099": ("晶晨股份", "SoC/MCU"),
    "300458": ("全志科技", "SoC/MCU"), "688595": ("芯海科技", "SoC/MCU"),
    "603501": ("韦尔股份", "CIS图像传感"), "688728": ("格科微", "CIS图像传感"),
    "603005": ("晶方科技", "CIS图像传感"),
    "300782": ("卓胜微", "射频芯片"), "688153": ("唯捷创芯", "射频芯片"),
    "688270": ("臻镭科技", "射频芯片"),
    "600584": ("长电科技", "封测"), "002156": ("通富微电", "封测"),
    "002185": ("华天科技", "封测"),
    "002371": ("北方华创", "半导体设备"), "688012": ("中微公司", "半导体设备"),
    "688072": ("拓荆科技", "半导体设备"), "688037": ("芯源微", "半导体设备"),
    "300604": ("长川科技", "半导体设备"),
    "688126": ("沪硅产业", "半导体材料"), "605358": ("立昂微", "半导体材料"),
    "300054": ("鼎龙股份", "半导体材料"), "002409": ("雅克科技", "半导体材料"),
    "688981": ("中芯国际", "晶圆制造"), "688347": ("华虹公司", "晶圆制造"),
    "002049": ("紫光国微", "FPGA/逻辑"), "688385": ("复旦微电", "FPGA/逻辑"),
    "688041": ("海光信息", "FPGA/逻辑"), "688256": ("寒武纪", "FPGA/逻辑"),
    "301269": ("华大九天", "EDA/IP"), "688206": ("概伦电子", "EDA/IP"),
}

def log(m): print(m, flush=True)

def login():
    r = requests.post(f"{WURL}/api/v1/auth/login",
        json={"email": WEMAIL, "password": WPASS}, timeout=10)
    return r.json()["token"]

def search(code, max_pg=3):
    s = requests.Session(); s.headers.update({"User-Agent": UA})
    all_rows = []
    for pg in range(1, max_pg+1):
        try:
            r = s.get(REPORT_API, params={
                "pageSize":"100","pageNo":str(pg),"code":code,
                "beginTime":"2024-01-01","endTime":"2026-05-31",
                "qType":"0","rcode":""
            }, timeout=30)
            rows = r.json().get("data") or []
            if not rows: break
            all_rows.extend(rows)
            tp = r.json().get("TotalPage",1) or 1
            if pg >= min(tp, max_pg): break
            time.sleep(0.15)
        except: break
    return all_rows

def download(info_code, title, date_str, org, d):
    safe = re.sub(r'[\\/:*?"<>|]', "_", title)[:60]
    fname = f"{date_str}_{org}_{safe}.pdf" if date_str else f"{info_code}.pdf"
    fp = Path(d) / fname
    if fp.exists() and fp.stat().st_size > 1024: return str(fp)
    for _ in range(3):
        try:
            req = urllib.request.Request(PDF_TPL.format(info_code),
                headers={"User-Agent": UA})
            data = urllib.request.urlopen(req, timeout=120).read()
            if len(data) >= 1024:
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_bytes(data); return str(fp)
        except: time.sleep(2)
    return None

def get_hashes():
    try:
        r = subprocess.run(["docker","exec","weknora-postgres","psql","-U","weknora","-d","weknora","-Atc",
            f"SELECT file_hash FROM knowledges WHERE knowledge_base_id='{KB_ID}' AND deleted_at IS NULL AND file_hash IS NOT NULL"],
            capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return set(h.strip() for h in r.stdout.strip().split("\n") if h.strip())
    except: pass
    return set()

def upload(files_info, existing_hashes):
    db_cmd = ["docker","exec","weknora-postgres","psql","-U","weknora","-d","weknora","-c"]
    done = skipped = failed = 0
    for fi in files_info:
        fp = Path(fi["path"])
        if not fp.exists(): failed += 1; continue
        fsize = fp.stat().st_size
        fhash = hashlib.md5(fp.read_bytes()).hexdigest()
        fname = fp.name
        if fhash in existing_hashes: skipped += 1; continue

        kid = str(uuid.uuid4())
        title = fi["title"].replace("'","''")
        desc = f"{fi.get('org','')} | {fi.get('stock','')}({fi.get('code','')}) | {fi.get('date','')}"
        meta = json.dumps(fi.get("meta",{}), ensure_ascii=False).replace("'","''")

        cp = subprocess.run(["docker","cp",str(fp),f"weknora-app:/data/files/{fname}"],
            capture_output=True, text=True, timeout=30)
        if cp.returncode != 0:
            log(f"    ⚠️ cp失败: {fname[:30]}")
            failed += 1; continue

        sql = f"""INSERT INTO knowledges (id,tenant_id,knowledge_base_id,type,title,description,
source,parse_status,enable_status,file_name,file_type,file_size,file_path,file_hash,storage_size,metadata,tag_id,channel)
VALUES ('{kid}',1,'{KB_ID}','file','{title}','{desc}','local_upload','unprocessed','enabled',
'{fname}','pdf',{fsize},'/data/files/{fname}','{fhash}',{fsize},'{meta}'::jsonb,'{fi.get('tag_id','')}','upload');"""

        r = subprocess.run(db_cmd + [sql], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            done += 1; existing_hashes.add(fhash)
            if done % 30 == 0: log(f"    ...已上传 {done} 篇")
        else:
            err = r.stderr[:100].lower()
            if "duplicate" in err or "unique" in err: skipped += 1; existing_hashes.add(fhash)
            else: log(f"    ❌ DB: {r.stderr[:80]}"); failed += 1
    return done, skipped, failed

def run():
    log("="*60)
    log(f"🔍 芯片半导体研报批量同步 ({Path(__file__).stem})")
    log("="*60)

    all_reports = []
    for code, (name, tag) in SEMI_STOCKS.items():
        try:
            rows = search(code)
            log(f"  📡 {code} {name}: {len(rows)} 篇 [{tag}]")
            for row in rows:
                all_reports.append({
                    "code": code, "stock": name, "tag": tag,
                    "title": row.get("title",""), "date": str(row.get("publishDate",""))[:10],
                    "org": row.get("orgSName",""), "info_code": row.get("infoCode",""),
                    "rating": row.get("emRatingName",""),
                })
        except Exception as e:
            log(f"  ❌ {code}: {e}")
        time.sleep(0.2)

    log(f"\n📊 总计: {len(all_reports)} 篇")
    if not all_reports: return

    outdir = Path(OUTDIR); outdir.mkdir(parents=True, exist_ok=True)
    log(f"\n📥 下载 PDF → {OUTDIR}")
    downloaded = []
    for r in all_reports:
        if not r["info_code"]: continue
        fp = download(r["info_code"], r["title"], r["date"], r["org"], OUTDIR)
        if fp: r["path"] = fp; downloaded.append(r)

    total_mb = sum(Path(r['path']).stat().st_size for r in downloaded)/1024/1024
    log(f"\n📊 下载: {len(downloaded)}/{len(all_reports)} ({total_mb:.0f}MB)")

    if not downloaded: return

    log(f"\n📤 上传 WeKnora...")
    try:
        token = login()
        # Get tag map
        r = requests.get(f"{WURL}/api/v1/knowledge-bases/{KB_ID}/tags",
            headers={"Authorization": f"Bearer {token}"}, timeout=10)
        tag_map = {t["name"]: t["id"] for t in r.json()["data"]["data"]}
        log(f"   标签: {list(tag_map.keys())}")

        existing = get_hashes()
        log(f"   已有 {len(existing)} 个hash")

        # Build upload list with tag_ids
        upload_list = []
        for rpt in downloaded:
            upload_list.append({
                "path": rpt["path"], "title": rpt["title"],
                "stock": rpt["stock"], "code": rpt["code"],
                "org": rpt["org"], "date": rpt["date"],
                "tag_id": tag_map.get(rpt["tag"], ""),
                "meta": {"source": "eastmoney", "org": rpt["org"],
                    "stock_code": rpt["code"], "stock_name": rpt["stock"],
                    "publish_date": rpt["date"], "rating": rpt["rating"]},
            })

        done, skipped, failed = upload(upload_list, existing)

        log(f"\n{'='*60}")
        log(f"📊 完成报告")
        log(f"   搜索: {len(all_reports)} 篇")
        log(f"   下载: {len(downloaded)} 篇 ({total_mb:.0f}MB)")
        log(f"   上传: {done} 篇")
        log(f"   跳过: {skipped} 篇")
        log(f"   失败: {failed} 篇")
        log("="*60)

        tag_dist = {}; stk_dist = {}
        for rpt in downloaded:
            tag_dist[rpt["tag"]] = tag_dist.get(rpt["tag"], 0) + 1
            stk_dist[rpt["stock"]] = stk_dist.get(rpt["stock"], 0) + 1
        log("\n上传分类:")
        for t, c in sorted(tag_dist.items(), key=lambda x: -x[1]):
            log(f"  🏷 {t}: {c} 篇")
        log("\n热门标的上传:")
        for s, c in sorted(stk_dist.items(), key=lambda x: -x[1])[:10]:
            log(f"  📈 {s}: {c} 篇")

    except Exception as e:
        log(f"❌ 上传失败: {e}")
        import traceback; log(traceback.format_exc())

def show_stats():
    try:
        token = login()
        r = requests.get(f"{WURL}/api/v1/knowledge-bases/{KB_ID}/tags",
            headers={"Authorization": f"Bearer {token}"}, timeout=10)
        tags = r.json()["data"]["data"]

        cnt, sz = subprocess.run(
            ["docker","exec","weknora-postgres","psql","-U","weknora","-d","weknora","-Atc",
             f"SELECT count(*),coalesce(sum(file_size),0) FROM knowledges WHERE knowledge_base_id='{KB_ID}' AND deleted_at IS NULL"],
            capture_output=True, text=True, timeout=5).stdout.strip().split("|")

        log(f"\n📚 芯片半导体 — {int(cnt)} 篇, {int(sz)/1024/1024:.0f} MB")
        for t in tags:
            log(f"    #{t['name']}: {t.get('knowledge_count',0)} 篇")
    except Exception as e:
        log(f"❌ 失败: {e}")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true", help="全量同步")
    p.add_argument("--stats", action="store_true", help="统计")
    args = p.parse_args()
    if args.stats: show_stats()
    else: run()
