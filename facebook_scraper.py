import csv
import logging
import os
import json
import random
import re
import shutil
import subprocess
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Set, Dict

import pandas as pd
import undetected_chromedriver as uc
from dotenv import load_dotenv
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

load_dotenv()

BASE_DIR = Path("/home/kongla/Documents/GitHub/Real-Estate Listing Aggregator System/facebook-scraping")
OUTPUT_PATH = BASE_DIR / "facebook_output.xlsx"
PROFILE_PATH = BASE_DIR / "chrome_profile"

TARGET_POSTS = 120
MAX_STAGNANT = 10
SCROLL_SIZE = 3000

MODEL_NAME = "typhoon-v2.5-30b-a3b-instruct"
MAX_WORKERS = 10
LLM_TIMEOUT = 60.0

client = OpenAI(api_key=os.getenv("TYPHOON_API_KEY"), base_url="https://api.opentyphoon.ai/v1", timeout=LLM_TIMEOUT)

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
    "https://www.facebook.com/groups/142702946428033/members",
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
    "https://www.facebook.com/groups/landhomechiangmai/"
]

OUTPUT_HEADERS = ["ประเภท", "ราคา", "ทำเล", "ขนาด", "Link"]

SYSTEM_PROMPT = """คุณคือ AI วิเคราะห์อสังหาริมทรัพย์ระดับ Expert รองรับการวิเคราะห์ได้ทั้ง English, Thai และ Chinese
หน้าที่ของคุณคือดึงข้อมูล (Data Extraction) และจำแนกประเภทผู้โพสต์ (Classification) จากข้อความที่ให้มา
ตอบกลับเป็น JSON Structure เท่านั้น ห้ามมี Text อื่นปน

{
  "is_real_estate": true/false,
  "is_owner": true/false,
  "owner_confidence": 0.0,
  "evidence_phrases": [],
  "risk_flags": [],
  "post_date_text": "ดึงข้อความเวลาที่พบในข้อมูลตามที่ส่งมา",
  "extracted":{
    "property_type":"",
    "rental_sale_status":"",
    "project_name":"",
    "district":"",
    "size_text":"",
    "price_text":"",
    "price_value_thb":null,
    "phone":"",
    "line":"",
    "description":"ดึงรายละเอียดข้อความทั้งหมดมา ห้ามตัดทิ้ง ห้าม Truncate เด็ดขาด"
  }
}

=== OWNER vs AGENT OPTIMIZED CLASSIFICATION (FAST-FAIL PIPELINE) ===

กลไกการตัดสิน (Short-Circuit Evaluation):
ประเมินตามลำดับ GATE 1 -> GATE 2 -> GATE 3
*** กฎเหล็ก: หากตรงกับ GATE 1 (Agent) ให้ตั้งค่า `is_owner: false`, `owner_confidence: 0.0`, และระบุ `risk_flags` ให้ชัดเจน แต่ "บังคับดึงข้อมูลใน Object `extracted` มาทั้งหมดห้ามทิ้งเด็ดขาด" เผื่อใช้ตรวจสอบย้อนหลังใน Log ***

GATE 1: AGENT HARD-FILTER (High Risk -> ถือเป็น Agent)
หากพบเงื่อนไขใดเงื่อนไขหนึ่งต่อไปนี้ ถือเป็น Agent/Sales แน่นอน (is_owner: false):
- [Line Official Account]: การระบุ LINE ID ที่มีเครื่องหมาย "@" นำหน้า เช่น "LINE : @homecareproperty", "@Dgrandhouse" ถือเป็น Agent 100%
- [Pattern/Repeated Contact]: มีการระบุรหัสทรัพย์สิน (Property ID), รูปแบบการโพสต์เป็น Template ซ้ำๆ, หรือใช้ Hashtag จำนวนมากผิดปกติ
- [Sales Closing]: "ปิดเกม", "Units สุดท้าย", "โค้งสุดท้าย", "จองด่วน", "Hot Item", "Rare Item", "เปิดรับลงทะเบียน", "รอบ VVIP"
- [Broker Services]: "บริการด้านอสังหา", "ดันสินเชื่อทุกเคส", "ฟรีค่าโอน", "ดูแลจนถึงวันโอน", "รับฝากขาย/เช่า", "Co-broker ยินดี" (ยกเว้นเจ้าของบอกรับเอเจ้นท์ ให้ดู Gate 2)
- [Corporate Marketing]: "มรดกแห่งชีวิต", "นิยามใหม่แห่งการพักผ่อน", "ยกระดับการใช้ชีวิต", "Ultra Luxury" 
- [Agent Naming/Routing]: "ทักหา[ชื่อ]", "ติดต่อคุณ...", "แอดไลน์หาทีมงาน", "แอดมิน"

GATE 2: OWNER VERIFIED (High Confidence -> เจ้าของ 100%)
หากผ่าน Gate 1 มาได้ และพบสัญญาณเหล่านี้ (is_owner: true, Confidence 0.9-1.0):
- [Direct Claim]: "เจ้าของขายเอง", "เจ้าของปล่อยเอง", "Owner Post", "เจ้าของ ยินดีรับเอเจ้นท์"
- [Ownership Proof]: "บ้านสร้างเอง", "เจ้าของไม่เคยเข้าอยู่", "ขายเพราะย้ายงาน", "อยู่เองสะอาดมาก", "ซื้อไว้นานไม่ได้อยู่", "ลดกว่าที่ซื้อมา"
- [Personal Tone]: ใช้สรรพนาม "ผม/ฉัน/พี่", "ตัดใจปล่อยด่วน", "ของจริงสวยกว่ารูป", "แถมเครื่องใช้ไฟฟ้าตามภาพ", ติดต่อ Line แบบ Personal ID (ไม่มี @)
"""

MONTH_MAP = {
    "มกราคม": 1, "ม.ค.": 1, "กุมภาพันธ์": 2, "ก.พ.": 2, "มีนาคม": 3, "มี.ค.": 3,
    "เมษายน": 4, "เม.ย.": 4, "พฤษภาคม": 5, "พ.ค.": 5, "มิถุนายน": 6, "มิ.ย.": 6,
    "กรกฎาคม": 7, "ก.ค.": 7, "สิงหาคม": 8, "ส.ค.": 8, "กันยายน": 9, "ก.ย.": 9,
    "ตุลาคม": 10, "ต.ค.": 10, "พฤศจิกายน": 11, "พ.ย.": 11, "ธันวาคม": 12, "ธ.ค.": 12,
}

csv_lock = threading.Lock()

def get_chrome_version(chrome_exec: str) -> int:
    try:
        res = subprocess.run([chrome_exec, "--version"], capture_output=True, text=True, check=False)
        return int(re.search(r"(\d+)\.", res.stdout).group(1)) if res.stdout else 0
    except Exception:
        return 0

def create_driver() -> uc.Chrome:
    PROFILE_PATH.mkdir(parents=True, exist_ok=True)
    chrome_exec = shutil.which("google-chrome") or shutil.which("chromium-browser")
    opts = uc.ChromeOptions()
    opts.add_argument(f"--user-data-dir={PROFILE_PATH}")
    opts.add_argument("--disable-notifications")
    opts.page_load_strategy = "eager"
    return uc.Chrome(options=opts, version_main=get_chrome_version(chrome_exec), browser_executable_path=chrome_exec)

def humanized_scroll(driver: uc.Chrome) -> None:
    driver.execute_script(f"window.scrollBy(0, {SCROLL_SIZE + random.randint(-500, 500)});")
    time.sleep(random.uniform(1, 2.0))

def apply_new_post_filter(driver: uc.Chrome):
    try:
        driver.execute_script("""
            const filterBtn = Array.from(document.querySelectorAll('div[role="button"]'))
                .find(e => e.innerText && (e.innerText.includes('เรียงลำดับฟีดในกลุ่มตาม') || e.innerText.includes('จัดเรียงตาม')));
            if (filterBtn) {
                filterBtn.click();
                setTimeout(() => {
                    const options = Array.from(document.querySelectorAll('div[role="menuitemradio"]'));
                    const target = options.find(e => e.innerText && (e.innerText.includes('โพสต์ใหม่') || e.innerText.includes('รายการสินค้าใหม่')));
                    if (target) target.click();
                }, 1500);
            }
        """)
        time.sleep(3.5)
    except Exception as e:
        logger.error(f"Filter error: {e}")

def expand_all_see_more(driver: uc.Chrome):
    try:
        driver.execute_script("""
            Array.from(document.querySelectorAll('div[role="button"]')).forEach(b => {
                const txt = (b.textContent || "").trim();
                if (txt === 'ดูเพิ่มเติม' || txt === 'See more') {
                    b.click();
                }
            });
        """)
        time.sleep(1)
    except Exception:
        pass

def batch_extract_dom(driver: uc.Chrome) -> List[Dict[str, str]]:
    return driver.execute_script("""
        const results = [];
        document.querySelectorAll("div[role='article']").forEach(a => {
            const linkNodes = Array.from(a.querySelectorAll("a[href]"))
                .filter(l => l.href.includes('/posts/') || l.href.includes('/permalink/'));
            if (linkNodes.length === 0) return;

            const linkNode = linkNodes[0];
            const url = linkNode.href.split('?')[0];
            const msgNode = a.querySelector("div[data-ad-comet-preview='message']") || a.querySelector("div[data-ad-preview='message']");
            if (!msgNode) return;

            const content = msgNode.innerText.trim();
            let date = "N/A";

            for (let l of linkNodes) {
                const aria = (l.getAttribute("aria-label") || "").trim();
                const text = (l.textContent || "").trim();
                if (aria && aria.length > 0 && aria.length < 30) {
                    date = aria;
                    break;
                } else if (text && text.length > 0 && text.length < 30) {
                    date = text;
                    break;
                }
            }

            results.push({"Post_URL": url, "Full_Content": content, "Date": date});
        });
        return results;
    """)

def call_llm_service(payload: str) -> dict | None:
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                temperature=0.0,
                max_tokens=15000,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": payload},
                ],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception:
            time.sleep(2 ** attempt)
    return None

def parse_date(date_text: str) -> str:
    now = datetime.now()
    if not date_text or date_text == "N/A": return "-"
    val = date_text.strip().lower()

    if any(k in val for k in ("วันนี้", "นาที", "ชั่วโมง", "ชม.")):
        return now.strftime("%d/%m/%Y")

    if "เมื่อวาน" in val:
        return (now - timedelta(days=1)).strftime("%d/%m/%Y")

    m_days = re.search(r"(\d+)\s*วัน", val)
    if m_days:
        return (now - timedelta(days=int(m_days.group(1)))).strftime("%d/%m/%Y")

    m_date = re.search(r"(\d{1,2})[\s\.\/-]*(\d{1,2})?\s*(\w+)\s*(\d{2,4})?", val)
    if m_date:
        day = int(m_date.group(1))
        month = MONTH_MAP.get(m_date.group(3), 0)
        year = int(m_date.group(4)) if m_date.group(4) else now.year
        return f"{day:02}/{month:02}/{year}"

    return "-"

# Additional logic for saving output to Excel will be added here