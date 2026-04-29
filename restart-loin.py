import csv
import itertools
import logging
import os
import json
import random
import re
import shutil
import subprocess
import time
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, Future
from pathlib import Path
from typing import List, Set, Dict, Optional, Tuple

import httpx
import undetected_chromedriver as uc
from dotenv import load_dotenv
from openai import OpenAI
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

load_dotenv()

BASE_DIR = Path("/home/kongla/Documents/GitHub/Real-estate-Scraping")
OUTPUT_PATH = Path("/home/kongla/Documents/GitHub/Real-Estate Listing Aggregator System/facebook-scraping/output.csv")
PROFILE_PATH = BASE_DIR / "chrome_profile"

MAX_STAGNANT = 10
SCROLL_SIZE = 3200
START_GROUP_IDX = 1

MODEL_NAME = "typhoon-v2.5-30b-a3b-instruct"
MAX_WORKERS = 18
LLM_TIMEOUT = 60.0
LLM_CONCURRENCY = 12
MAX_INFLIGHT_JOBS = 30

_timeout_config = httpx.Timeout(LLM_TIMEOUT)
_httpx_client = httpx.Client(timeout=_timeout_config, limits=httpx.Limits(max_connections=MAX_WORKERS, max_keepalive_connections=5))

_clients = [
    OpenAI(api_key=os.getenv("TYPHOON_API_KEY"), base_url="https://api.opentyphoon.ai/v1", max_retries=0, http_client=_httpx_client),
    OpenAI(api_key=os.getenv("TYPHOON_API_KEY2"), base_url="https://api.opentyphoon.ai/v1", max_retries=0, http_client=_httpx_client),
    OpenAI(api_key=os.getenv("TYPHOON_API_KEY3"), base_url="https://api.opentyphoon.ai/v1", max_retries=0, http_client=_httpx_client),
]
_client_cycle = itertools.cycle(_clients)
_client_lock = threading.Lock()
_llm_semaphore = threading.Semaphore(LLM_CONCURRENCY)
_pending_lock = threading.Lock()
_pending_futures: Set[Future] = set()

def _get_client() -> OpenAI:
    with _client_lock:
        return next(_client_cycle)

def _register_future(fut: Future) -> None:
    with _pending_lock:
        _pending_futures.add(fut)

def _cleanup_done_futures() -> None:
    with _pending_lock:
        done = {f for f in _pending_futures if f.done()}
        _pending_futures.difference_update(done)

def _wait_for_backpressure() -> None:
    while True:
        _cleanup_done_futures()
        with _pending_lock:
            pending = len(_pending_futures)
        if pending < MAX_INFLIGHT_JOBS:
            return
        done, _ = wait(list(_pending_futures), return_when=FIRST_COMPLETED)
        with _pending_lock:
            _pending_futures.difference_update(done)

GROUP_URLS: List[str] = [
    "https://www.facebook.com/groups/302468990428489/",
    "https://www.facebook.com/groups/322977734828852/",
    "https://www.facebook.com/groups/812156038944325/",
    "https://www.facebook.com/groups/1472146056424210/",
    "https://www.facebook.com/groups/426467944402414/",
    "https://www.facebook.com/groups/homerentcm/",
    "https://www.facebook.com/groups/509895225859790/",
    "https://www.facebook.com/groups/1299302030158649/",
    "https://www.facebook.com/groups/530492663958652/",
    "https://www.facebook.com/groups/596670275854317/",
    "https://www.facebook.com/groups/cnxre/",
    "https://www.facebook.com/groups/1885263611797363/",
    "https://www.facebook.com/groups/1897854047114064/",
    "https://www.facebook.com/groups/303531690102574/",
    "https://www.facebook.com/groups/1881982752029163/",
    "https://www.facebook.com/groups/568026117396809/",
    "https://www.facebook.com/groups/1928645537294336/",
    "https://www.facebook.com/groups/298821664628156/",
    "https://www.facebook.com/groups/903971886395138/",
    "https://www.facebook.com/groups/142702946428033/",
    "https://www.facebook.com/groups/250469775470436/",
    "https://www.facebook.com/groups/1873094122912006/",
    "https://www.facebook.com/groups/2083392538672247/",
    "https://www.facebook.com/groups/864193946960728/",
    "https://www.facebook.com/groups/baanchiangmai/",
    "https://www.facebook.com/groups/694087674785430/",
    "https://www.facebook.com/groups/411301775702951/",
    "https://www.facebook.com/groups/169718747164928/",
    "https://www.facebook.com/groups/1475061816108017/",
    "https://www.facebook.com/groups/1582425938465943/",
    "https://www.facebook.com/groups/sale.rent.poolvillachiangmai/",
    "https://www.facebook.com/groups/251125079442673/",
    "https://www.facebook.com/groups/1034329704984830/",
    "https://www.facebook.com/groups/203683550205761/",
    "https://www.facebook.com/groups/1450131905304596/",
    "https://www.facebook.com/groups/959493160788393/",
    "https://www.facebook.com/groups/2897034136980512/",
    "https://www.facebook.com/groups/korn.property/",
    "https://www.facebook.com/groups/1456428424593312/",
    "https://www.facebook.com/groups/152080739566471/",
    "https://www.facebook.com/groups/Land.House.C.M.2014/",
    "https://www.facebook.com/groups/2336780789695894/",
    "https://www.facebook.com/groups/236116797208244/",
    "https://www.facebook.com/groups/landhomechiangmai/",
]

OUTPUT_HEADERS = ["วันที่โพส", "website", "ประเภท", "สถานะ", "ชื่อโครงการ", "ขนาด", "ราคา", "เขต", "Link", "เบอร์โทรศัพท์", "Line", "คำอธิบาย"]

SYSTEM_PROMPT = """คุณคือ AI Data Engine ระดับ Senior ที่เชี่ยวชาญด้าน Real Estate Analytics 
ทำหน้าที่สกัดข้อมูลและจำแนกประเภทโพสต์จากข้อความอสังหาริมทรัพย์
กฎเหล็ก: ตอบกลับเป็น JSON Structure เท่านั้น ห้ามมีข้อความอื่นปน บรรทัดใหม่ใน Description ต้องใช้ \\n

{
  "is_real_estate": true/false,
  "is_owner": true/false,
  "owner_confidence": 0.0,
  "classification_type": "OWNER/AGENT/UNKNOWN",
  "evidence_phrases": [],
  "risk_flags": [],
  "post_date_text": "ดึงข้อความเวลา",
  "extracted":{
    "property_type": "บ้านเดี่ยว/คอนโด/ที่ดิน ฯลฯ",
    "rental_sale_status": "ขาย/เช่า/ขายและเช่า",
    "project_name": "ชื่อโครงการ (ถ้าไม่มีให้ null)",
    "district": "เขต/พื้นที่",
    "size_text": "ขนาดพื้นที่",
    "price_text": "ราคาเต็ม",
    "price_value_thb": null,
    "phone": "เบอร์โทรศัพท์",
    "line": "ID Line",
    "description": "รายละเอียดทั้งหมด (ใช้ \\n แทนการขึ้นบรรทัดใหม่)"
  }
}

GATE 1: AGENT HARD-FILTER (Line OA @, โครงสร้าง 2 ภาษาเป๊ะ, รหัสทรัพย์, Admin/ฝ่ายขาย/Sale)
GATE 2: OWNER VERIFIED (Owner Post, เจ้าของขายเอง, ขายขาดทุน, เหตุผลส่วนตัวเช่นย้ายงาน)
GATE 3: HEURISTIC (โพสต์สั้นมากแต่เป็น Financial Loss ให้มองเป็น Owner)
"""

MONTH_MAP = {
    "มกราคม": 1, "ม.ค.": 1, "กุมภาพันธ์": 2, "ก.พ.": 2, "มีนาคม": 3, "มี.ค.": 3,
    "เมษายน": 4, "เม.ย.": 4, "พฤษภาคม": 5, "พ.ค.": 5, "มิถุนายน": 6, "มิ.ย.": 6,
    "กรกฎาคม": 7, "ก.ค.": 7, "สิงหาคม": 8, "ส.ค.": 8, "กันยายน": 9, "ก.ย.": 9,
    "ตุลาคม": 10, "ต.ค.": 10, "พฤศจิกายน": 11, "พ.ย.": 11, "ธันวาคม": 12, "ธ.ค.": 12,
}

_INVISIBLE_CHARS_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u00a0\u2060\u180e\u2028\u2029\u00ad]")
_DATE_PATTERN_DAYS = re.compile(r"(\d+)\s*วัน")
_DATE_PATTERN_DMY = re.compile(r"(\d{1,2})[\s\.\-/]+([ก-๙a-zA-Z]+)(?:[\s\.\-/]+(\d{2,4}))?")
csv_lock = threading.Lock()

def is_post_older_than_24h(date_text: str) -> bool:
    if not date_text or date_text == "N/A":
        return False
    val = _INVISIBLE_CHARS_RE.sub("", date_text).strip().lower()
    if any(k in val for k in ("วันนี้", "นาที", "ชั่วโมง", "ชม.")):
        return False
    if _DATE_PATTERN_DAYS.search(val):
        return int(_DATE_PATTERN_DAYS.search(val).group(1)) >= 1
    return bool(_DATE_PATTERN_DMY.search(val) or "เมื่อวาน" in val)

def get_chrome_version(chrome_exec: str) -> int:
    try:
        res = subprocess.run([chrome_exec, "--version"], capture_output=True, text=True, check=False)
        match = re.search(r"(\d+)\.", res.stdout) if res.stdout else None
        return int(match.group(1)) if match else 0
    except:
        return 0

def cleanup_chrome_profile() -> None:
    try:
        if not PROFILE_PATH.exists():
            return
        
        default_dir = PROFILE_PATH / "Default"
        if not default_dir.exists():
            return
        
        cache_dirs = ["Cache", "Cache.new", "Code Cache", "GPUCache"]
        for cache_name in cache_dirs:
            cache_path = default_dir / cache_name
            if cache_path.exists():
                shutil.rmtree(cache_path, ignore_errors=True)
        
        cookies_file = default_dir / "Cookies"
        cookies_journal = default_dir / "Cookies-journal"
        for f in [cookies_file, cookies_journal]:
            if f.exists():
                try:
                    f.unlink()
                except:
                    pass
        
        logger.info("Chrome profile cleaned: cache/cookies removed")
    except Exception as e:
        logger.warning(f"Profile cleanup partial failure: {e}")

def check_login_status(driver: uc.Chrome) -> bool:
    try:
        driver.get("https://www.facebook.com/")
        time.sleep(5)
        
        url_lower = driver.current_url.lower()
        is_login_page = any(x in url_lower for x in ["login", "account", "checkpoint"])
        
        try:
            login_form = driver.find_element(By.CSS_SELECTOR, "[data-testid='login_form']")
            is_login_form_visible = login_form.is_displayed()
        except:
            is_login_form_visible = False
        
        if is_login_page or is_login_form_visible:
            logger.warning("Login required. Waiting 90 seconds for user authentication...")
            time.sleep(90)
            driver.get("https://www.facebook.com/")
            time.sleep(3)
            
            url_after = driver.current_url.lower()
            return not any(x in url_after for x in ["login", "checkpoint"])
        
        return True
    except Exception as e:
        logger.warning(f"Login check failed: {e}")
        return True

def create_driver() -> uc.Chrome:
    PROFILE_PATH.mkdir(parents=True, exist_ok=True)
    cleanup_chrome_profile()
    
    chrome_exec = shutil.which("google-chrome") or shutil.which("chromium-browser") or shutil.which("chrome")
    
    if not chrome_exec:
        raise RuntimeError("Chrome/Chromium not found in PATH")
    
    opts = uc.ChromeOptions()
    opts.add_argument(f"--user-data-dir={PROFILE_PATH}")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-sync")
    opts.add_argument("--disable-default-apps")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-background-networking")
    opts.add_argument("--disable-client-side-phishing-detection")
    opts.add_argument("--disable-backgroundtimer-throttling")
    opts.add_argument("--enable-features=NetworkService,NetworkServiceInProcess")
    opts.add_argument("--disable-features=TranslateUI")
    opts.add_argument("--window-position=0,0")
    opts.add_argument("--window-size=1920,1080")
    opts.page_load_strategy = "normal"
    
    try:
        driver = uc.Chrome(
            options=opts,
            version_main=get_chrome_version(chrome_exec),
            browser_executable_path=chrome_exec,
            use_subprocess=True,
            verbose=True
        )
        driver.set_page_load_timeout(45)
        driver.implicitly_wait(10)
        logger.info("Chrome driver initialized with visible window")
        return driver
    except Exception as e:
        logger.error(f"Failed to create Chrome driver: {e}")
        raise

def apply_new_post_filter(driver: uc.Chrome) -> None:
    try:
        driver.execute_script("""
            (function() {
                const filterBtn = Array.from(document.querySelectorAll('div[role="button"]'))
                    .find(e => e.innerText && (e.innerText.includes('เรียงลำดับ') || e.innerText.includes('จัดเรียง')));
                if (!filterBtn) return;
                filterBtn.click();
                setTimeout(() => {
                    const options = Array.from(document.querySelectorAll('div[role="menuitemradio"]'));
                    const target = options.find(e => e.innerText && (e.innerText.includes('โพสต์ใหม่') || e.innerText.includes('ล่าสุด')));
                    if (target) target.click();
                }, 1500);
            })();
        """)
        time.sleep(4)
    except Exception as e:
        logger.warning(f"Filter application failed: {e}")

def expand_see_more(driver: uc.Chrome) -> None:
    try:
        driver.execute_script(r"""
            const INVIS = new Set([0x200B,0x200C,0x200D,0xFEFF,0x00A0,0x2060,0x180E,0x2028,0x2029,0x00AD]);
            function clean(s) {
                let r = '';
                for (let i = 0; i < s.length; i++) if (!INVIS.has(s.charCodeAt(i))) r += s[i];
                return r.trim().toLowerCase();
            }
            const targets = new Set(['ดูเพิ่มเติม', 'see more']);
            Array.from(document.querySelectorAll('div[role="button"], span[role="button"]')).forEach(el => {
                const txt = clean(el.innerText || el.textContent || '');
                if (!targets.has(txt)) return;
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return;
                const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
                const opts = {bubbles: true, cancelable: true, view: window, clientX: cx, clientY: cy, screenX: window.screenX + cx, screenY: window.screenY + cy};
                ['pointerover','mouseover','pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
                    try { el.dispatchEvent(new MouseEvent(evt, opts)); } catch(_) {}
                });
            });
        """)
        time.sleep(1.5)
    except Exception as e:
        logger.warning(f"See more expansion failed: {e}")

def extract_posts(driver: uc.Chrome) -> Tuple[List[Dict[str, str]], bool]:
    try:
        result = driver.execute_script(r"""
            const INVIS = new Set([0x200B,0x200C,0x200D,0xFEFF,0x00A0,0x2060,0x180E,0x2028,0x2029,0x00AD]);
            function clean(s) {
                let r = '';
                for (let i = 0; i < s.length; i++) if (!INVIS.has(s.charCodeAt(i))) r += s[i];
                return r.trim().toLowerCase();
            }
            const results = [];
            let hasOld = false;
            document.querySelectorAll("div[role='article']").forEach(a => {
                const links = Array.from(a.querySelectorAll("a[href]")).filter(l => l.href.includes('/posts/') || l.href.includes('/permalink/'));
                if (links.length === 0) return;
                const url = links[0].href.split('?')[0];
                const msgNode = a.querySelector("div[data-ad-comet-preview='message']") || a.querySelector("div[data-ad-preview='message']");
                if (!msgNode) return;
                let date = "N/A";
                const dateLinks = a.querySelectorAll("a[role='link'][href*='/posts/'], a[role='link'][href*='/permalink/']");
                for (let l of dateLinks) {
                    const aria = (l.getAttribute("aria-label") || "").trim();
                    const txt = (l.textContent || "").trim();
                    if (aria && aria.length > 0 && aria.length < 30) { date = aria; break; }
                    else if (txt && txt.length > 0 && txt.length < 30 && /\d/.test(txt)) { date = txt; break; }
                }
                const dateStr = clean(date);
                if (dateStr && !/วันนี้|นาที|ชั่วโมง/.test(dateStr)) {
                    const isOld = /\d+\s*วัน/.test(dateStr) || dateStr.includes('เมื่อวาน') || /\d{1,2}[\s.\/-]+\w+/.test(dateStr);
                    if (isOld) hasOld = true;
                }
                results.push({"Post_URL": url, "Full_Content": msgNode.innerText.trim(), "Date": date});
            });
            return {results, hasOld};
        """)
        return result['results'], result['hasOld']
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return [], False

def call_llm_service(payload: str) -> Optional[Dict]:
    for attempt in range(3):
        if not _llm_semaphore.acquire(timeout=LLM_TIMEOUT):
            await_time = 2.0 ** attempt
            logger.warning(f"LLM semaphore timeout, waiting {await_time}s")
            time.sleep(await_time)
            continue
        try:
            client = _get_client()
            response = client.chat.completions.create(
                model=MODEL_NAME,
                temperature=0.0,
                max_tokens=10000,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": payload}
                ],
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.warning(f"LLM attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
        finally:
            _llm_semaphore.release()
    return None

def process_post_worker(raw_item: Dict) -> None:
    try:
        payload = f"Date: {raw_item.get('Date', 'N/A')}\n\nContent: {raw_item.get('Full_Content', '')}"
        ai_response = call_llm_service(payload)
        if not ai_response or not ai_response.get("is_real_estate") or not ai_response.get("is_owner"):
            return
        
        ext = ai_response.get("extracted", {})
        row = {
            "วันที่โพส": raw_item.get("Date", "-"),
            "website": "facebook",
            "ประเภท": ext.get("property_type", "-"),
            "สถานะ": ext.get("rental_sale_status", "-"),
            "ชื่อโครงการ": ext.get("project_name", "-"),
            "ขนาด": ext.get("size_text", "-"),
            "ราคา": ext.get("price_text", "-"),
            "เขต": ext.get("district", "-"),
            "Link": raw_item.get("Post_URL", "-"),
            "เบอร์โทรศัพท์": ext.get("phone", "-"),
            "Line": ext.get("line", "-"),
            "คำอธิบาย": ext.get("description", "-"),
        }
        with csv_lock:
            with open(OUTPUT_PATH, "a", encoding="utf-8-sig", newline="") as f:
                csv.DictWriter(f, fieldnames=OUTPUT_HEADERS).writerow(row)
    except Exception as e:
        logger.error(f"Worker error: {e}")

def process_group(driver: uc.Chrome, url: str, seen_urls: Set[str], executor: ThreadPoolExecutor, group_idx: int, total: int) -> None:
    try:
        logger.info(f"[{group_idx}/{total}] Loading: {url}")
        driver.get(url)
        time.sleep(5)
        
        apply_new_post_filter(driver)
        saved, stagnant = 0, 0
        
        for iteration in range(500):
            _wait_for_backpressure()
            expand_see_more(driver)
            posts, has_old = extract_posts(driver)
            
            if not posts:
                stagnant += 1
            else:
                unseen = [p for p in posts if p["Post_URL"] not in seen_urls]
                valid = [p for p in unseen if not is_post_older_than_24h(p["Date"])]
                if valid:
                    stagnant = 0
                    for item in valid:
                        seen_urls.add(item["Post_URL"])
                        _register_future(executor.submit(process_post_worker, item))
                        saved += 1
                    logger.info(f"[{group_idx}/{total}] Dispatched {len(valid)} posts (Total: {saved})")
                else:
                    stagnant += 1
            
            if has_old or stagnant >= MAX_STAGNANT:
                logger.info(f"[{group_idx}/{total}] Stopped (Old: {has_old}, Stagnant: {stagnant})")
                break
            
            driver.execute_script(f"window.scrollBy(0, {SCROLL_SIZE + random.randint(-500, 500)});")
            time.sleep(1.0)
    except Exception as e:
        logger.error(f"[{group_idx}/{total}] Error: {e}", exc_info=True)

def main() -> None:
    if not OUTPUT_PATH.exists():
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=OUTPUT_HEADERS).writeheader()
    
    seen_urls: Set[str] = set()
    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("Link"):
                    seen_urls.add(row["Link"])
    except Exception as e:
        logger.warning(f"Could not load existing URLs: {e}")
    
    driver = None
    try:
        driver = create_driver()
        
        if not check_login_status(driver):
            logger.error("Failed to verify login status")
            return
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for idx, group_url in enumerate(GROUP_URLS[START_GROUP_IDX - 1:], start=START_GROUP_IDX):
                process_group(driver, group_url, seen_urls, executor, idx, len(GROUP_URLS))
            _wait_for_backpressure()
            logger.info("All groups processed successfully")
    except Exception as e:
        logger.error(f"Main execution failed: {e}", exc_info=True)
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

if __name__ == "__main__":
    main()
