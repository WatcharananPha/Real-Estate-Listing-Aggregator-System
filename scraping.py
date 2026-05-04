import argparse
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
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Set, Dict, Optional, Tuple

import httpx
import undetected_chromedriver as uc
from dotenv import load_dotenv
from openai import OpenAI

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

load_dotenv()

BASE_DIR = Path("/home/kongla/Documents/GitHub/Real-estate-Scraping")
OUTPUT_PATH = Path(
    "/home/kongla/Documents/GitHub/Real-Estate Listing Aggregator System/facebook-scraping/output.csv"
)
PROFILE_PATH = BASE_DIR / "chrome_profile"

MAX_STAGNANT = 10
SCROLL_SIZE = 3000
START_GROUP_IDX = int(input("Enter START_GROUP_IDX (default 1): ") or 1)
MODEL_NAME = "typhoon-v2.5-30b-a3b-instruct"
MAX_WORKERS = 18
LLM_TIMEOUT = 60.0
LLM_CONCURRENCY = 25
MAX_INFLIGHT_JOBS = 50
LLM_PAYLOAD_TRIM = 3500
LLM_MAX_TOKENS = 5000

KNOWN_LINE_IDS = {
    "aor4546",
    "weer1973",
    "chingching5033",
    "sirinapha0900",
    "narin_2025",
    "artviolin",
    "gutzzjung",
}

KNOWN_PHONE_NUMBERS = {
    "0860696615",
    "0897004546",
    "0935068042",
    "0936959144",
    "0923391919",
    "0819612163",
    "0819638788",
    "0928512744",
    "0655653642",
    "0637803645",
    "0930391151",
    "0924964978",
    "0815311101",
    "0891927904",
    "0658516959",
    "0639964993",
    "0810291600",
    "0659722284",
    "0926165642",
    "0988494095",
    "0869131588",
    "0659549746",
    "0943197737",
    "0622614596",
    "0990096164",
    "0952659690",
    "0646533516",
    "0834705654",
}

MONTH_MAP: Dict[str, int] = {
    "มกราคม": 1,
    "ม.ค.": 1,
    "กุมภาพันธ์": 2,
    "ก.พ.": 2,
    "มีนาคม": 3,
    "มี.ค.": 3,
    "เมษายน": 4,
    "เม.ย.": 4,
    "พฤษภาคม": 5,
    "พ.ค.": 5,
    "มิถุนายน": 6,
    "มิ.ย.": 6,
    "กรกฎาคม": 7,
    "ก.ค.": 7,
    "สิงหาคม": 8,
    "ส.ค.": 8,
    "กันยายน": 9,
    "ก.ย.": 9,
    "ตุลาคม": 10,
    "ต.ค.": 10,
    "พฤศจิกายน": 11,
    "พ.ย.": 11,
    "ธันวาคม": 12,
    "ธ.ค.": 12,
}

_INVISIBLE_CHARS_RE = re.compile(
    r"[\u200b\u200c\u200d\ufeff\u00a0\u2060\u180e\u2028\u2029\u00ad]"
)
_TRUNCATION_SUFFIXES = (
    "... ดูเพิ่มเติม",
    "...ดูเพิ่มเติม",
    "... See more",
    "...See more",
)
_NON_DIGIT_RE = re.compile(r"\D")
_LEADING_ZERO_RE = re.compile(r"^0+")
_PHONE_EXTRACT_RE = re.compile(r"(\+?\d[\d\-\s]{6,}\d)")
_TIME_TEXT_RE = re.compile(r"\bเวลา\s*\d{1,2}[:\.]\d{2}\b")
_TIME_ONLY_RE = re.compile(r"\b\d{1,2}[:\.]\d{2}\b")
_MINUTES_RE = re.compile(r"(\d+)\s*นาที")
_HOURS_RE = re.compile(r"(\d+)\s*(?:ชั่วโมง|ชม\.)")
_DAYS_RE = re.compile(r"(\d+)\s*วัน")
_DATE_RE = re.compile(r"(\d{1,2})[\s\.\-/]+([ก-๙a-zA-Z]+)(?:[\s\.\-/]+(\d{2,4}))?")

_httpx_client = httpx.Client(
    timeout=LLM_TIMEOUT,
    limits=httpx.Limits(max_connections=MAX_WORKERS, max_keepalive_connections=5),
)

_clients: List[OpenAI] = [
    OpenAI(
        api_key=os.getenv(k),
        base_url="https://api.opentyphoon.ai/v1",
        timeout=LLM_TIMEOUT,
        http_client=_httpx_client,
        max_retries=0,
    )
    for k in ("TYPHOON_API_KEY", "TYPHOON_API_KEY2", "TYPHOON_API_KEY3")
    if os.getenv(k)
]
_client_cycle = itertools.cycle(_clients) if _clients else iter([])
_client_lock = threading.Lock()
_llm_semaphore = threading.Semaphore(LLM_CONCURRENCY)
_pending_lock = threading.Lock()
_pending_futures: Set[Future] = set()
csv_lock = threading.Lock()

DEFAULT_GROUP_URLS: List[str] = [
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

OUTPUT_HEADERS = [
    "วันที่โพส",
    "website",
    "ประเภท",
    "สถานะ",
    "ชื่อโครงการ",
    "ขนาด",
    "ราคา",
    "เขต",
    "Link",
    "เบอร์โทรศัพท์",
    "Line",
    "คำอธิบาย",
]

SYSTEM_PROMPT = f"""คุณคือ AI วิเคราะห์อสังหาริมทรัพย์ระดับ Expert รองรับการวิเคราะห์ได้ทั้ง English, Thai และ Chinese
หน้าที่ของคุณคือดึงข้อมูล (Data Extraction) และจำแนกประเภทผู้โพสต์ (Classification) จากข้อความที่ให้มา
ตอบกลับเป็น JSON Structure เท่านั้น ห้ามมี Text อื่นปน

{{
  "is_real_estate": true/false,
  "is_owner": true/false,
  "owner_confidence": 0.0,
  "evidence_phrases": [],
  "risk_flags": [],
  "post_date_text": "ดึงข้อความเวลาที่พบในข้อมูลตามที่ส่งมา",
  "extracted": {{
    "property_type": "",
    "rental_sale_status": "",
    "project_name": "",
    "district": "",
    "size_text": "",
    "price_text": "",
    "price_value_thb": null,
    "phone": "",
    "line": "",
    "description": "ดึงรายละเอียดข้อความทั้งหมดมา ห้ามตัดทิ้ง ห้าม Truncate เด็ดขาด"
  }}
}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GATE 0: AGENT BLOCKLIST HARD-REJECT  [ตรวจสอบก่อน Gate อื่นทุกอย่าง]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ผลลัพธ์: is_owner: false, owner_confidence: 0.0, risk_flags: ["BLOCKLIST_MATCH"]

นี่คือเงื่อนไขหลักสำหรับ filter Agent ออก — ถ้า phone หรือ LINE ID ที่พบในโพสต์ตรงกับรายการด้านล่างแม้แต่เบอร์เดียว ให้ return is_owner: false ทันที ไม่มีข้อยกเว้น
การ match phone: ตัดอักขระที่ไม่ใช่ตัวเลขออกก่อน แล้วเปรียบเทียบ
การ match LINE: strip whitespace + case-insensitive

BLOCKED_PHONES = {list(KNOWN_PHONE_NUMBERS)}
BLOCKED_LINES = {list(KNOWN_LINE_IDS)}

=== OWNER vs AGENT CLASSIFICATION — CONTEXT-AWARE PIPELINE ===

CORE PRINCIPLE: ห้ามตัดสินจาก Keyword เพียงตัวเดียว
ให้อ่านข้อความทั้งหมดก่อน แล้วถามตัวเองว่า
"ข้อความนี้เขียนโดยคนที่ 'เป็นเจ้าของทรัพย์นี้จริงๆ' หรือ 'คนที่ทำงานขายทรัพย์ให้คนอื่น'?"
สัญญาณแต่ละอย่างต้องอ่านในบริบท ไม่ใช่ match แล้วตัดสิน

ประเมินตามลำดับ GATE 1 → GATE 2 → GATE 3

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GATE 1: AGENT HARD-FILTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ผลลัพธ์: is_owner: false, owner_confidence: 0.0
*** กฎเหล็ก: แม้ตก Gate 1 ต้องดึงข้อมูลใน extracted ครบทุก field ห้ามทิ้งเด็ดขาด ***

ตรวจสอบตามลำดับ — หากพบข้อใดข้อหนึ่ง ถือเป็น Agent ทันที:

[G1-A] LINE Official Account
  • LINE ID มีเครื่องหมาย "@" นำหน้า เช่น @homecareproperty, @Dgrandhouse
  → Agent 100% ไม่มีข้อยกเว้น

[G1-B] Multi-language Template
  • โพสต์เนื้อหาเดิมซ้ำใน 2 ภาษาขึ้นไป (Thai + English + Chinese หรือ 2 ใน 3)
    โดยมีโครงสร้าง Section ชัดเจน ไม่ใช่แค่คำศัพท์ภาษาอังกฤษปนในประโยค
  → Agency สำหรับลูกค้าต่างชาติ

[G1-C] Property Code / Template Pattern
  • มีรหัสทรัพย์สิน เช่น "รหัส CM-1234", "Ref:", "Property Code:"
  • โครงสร้างโพสต์เป็น Template ซ้ำกันทุก Post (เห็นได้จาก Section Headers, Emoji เป็นระบบ)
  → สัญญาณ Back-office Agent

[G1-D] Sales Closing Language (ต้องเป็น Sales Push จริง ไม่ใช่บอกสถานะ)
  • "จองด่วน", "Hot Item", "Rare Item", "เปิดรับลงทะเบียน", "รอบ VVIP", "โค้งสุดท้าย"
  • "Units สุดท้าย" (บอกจำนวน Units เหลือ ≠ "หลังนี้หลังเดียว" ซึ่งอาจเป็นเจ้าของ)
  ⚠️ ข้อยกเว้น: "หลังสุดท้าย / ห้องสุดท้าย" ที่เป็นการบอกว่ามีแค่ 1 หลัง
    → ต้องดูบริบทรวม ถ้ามีสัญญาณ Agent อื่นประกอบ จึงถือเป็น Agent

[G1-E] Professional Broker Declarations
  • "บริการด้านอสังหา", "ดันสินเชื่อทุกเคส", "ดูแลจนถึงวันโอน"
  • "รับฝากขาย / รับฝากเช่า", "Co-broker ยินดี", "แอดไลน์หาทีมงาน", "ทีมงาน"
  ⚠️ ข้อยกเว้น: "ฟรีค่าโอน" และ "ค่าโอนคนละครึ่ง" ใช้ทั้งเจ้าของและ Agent
    → ห้ามนับเป็นสัญญาณ Agent เพียงลำพัง ต้องมีสัญญาณอื่นประกอบ

[G1-F] Agent Routing / Third-party Contact
  • "ทักหา [ชื่อ]", "ติดต่อคุณ...", "แอดมิน"
  ⚠️ ข้อยกเว้น: ชื่อส่วนตัวท้ายข้อความแบบเป็นกันเอง เช่น "0812345678 หนุ่ม",
    "สอบถามได้เลย แม่แอ๊ด", "ผมต้อมครับ" → ถือเป็น Personal Signature ไม่ใช่ Agent Routing
    กฎแยก: Agent Routing มักใช้ Formal หรือ 3rd-person ("ติดต่อคุณ...", "ทักหาทีม")
              Owner Signature ใช้ 1st-person หรือชื่อเล่นท้ายประโยค

[G1-G] Corporate / Developer Marketing Language
  • "มรดกแห่งชีวิต", "นิยามใหม่แห่งการพักผ่อน", "ยกระดับการใช้ชีวิต", "Ultra Luxury"
  • ภาษาโฆษณาระดับ Brand Copy ที่คนทั่วไปไม่พิมพ์เอง

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GATE 2: OWNER VERIFIED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ผลลัพธ์: is_owner: true, owner_confidence: 0.85–1.0

หากผ่าน Gate 1 มา และพบสัญญาณใดต่อไปนี้:

[G2-A] Explicit Owner Declaration
  • "เจ้าของขายเอง", "เจ้าของปล่อยเอง", "Owner Post", "ขายเอง ไม่ผ่านนายหน้า"
  • "เจ้าของยินดีรับ Agent / เจ้าของเปิดรับนายหน้า"
  → confidence: 0.95

[G2-B] Anti-Broker Statement
  • "#งดนายหน้า", "งดเอเจนต์", "ไม่รับนายหน้า", "ไม่ผ่านตัวแทน"
  → confidence: 0.90 (ชัดเจนว่าเจ้าของจัดการเอง)

[G2-C] Ownership Evidence (เล่าประสบการณ์ที่เจ้าของรู้แต่ Agent ไม่รู้)
  • "บ้านสร้างเอง", "อยู่เองมา X ปี", "ซื้อไว้นานไม่ได้ใช้", "ลดกว่าที่ซื้อมา"
  • "ย้ายงานต้องขาย", "เจ้าของไม่เคยเข้าอยู่", "ขายเพราะต้องการเงินด่วน"
  → confidence: 0.90

[G2-D] Owner-Only Financial Flexibility
  • "ผ่อนตรงกับเจ้าของได้", "ดาวน์ X% ยอดที่เหลือผ่อน 0% กับเจ้าของ"
  → Agent ไม่มีอำนาจเสนอแบบนี้ → confidence: 0.90

[G2-E] Personal Extras / First-Person Attachment
  • "แถมเครื่องใช้ไฟฟ้าตามภาพ", "แถมเฟอร์นิเจอร์ที่ซื้อมาเอง"
  • สรรพนาม "ผม/ดิฉัน/พี่" ใช้ตลอดข้อความอย่างเป็นธรรมชาติ ไม่ใช่แค่ท้ายข้อความ
  • "ของจริงสวยกว่ารูป", "อยู่สะอาดมากตลอด", "ตัดใจปล่อยเพราะ..."
  → confidence: 0.85

[G2-F] LINE Personal ID (เสริมสัญญาณอื่น)
  • LINE ID ที่ไม่มี "@" (เช่น ตัวเลข, ชื่อ, ตัวอักษร-ตัวเลข)
  → ไม่ใช่ Verified เพียงลำพัง แต่ใช้เพิ่ม confidence +0.05 เมื่อมีสัญญาณอื่นด้วย

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GATE 3: CONTEXTUAL SCORING (Ambiguous)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ใช้เมื่อ: ไม่มีสัญญาณชัดเจนจาก Gate 1 หรือ Gate 2
ประเมินจาก "ภาพรวมของข้อความ" ไม่ใช่นับจำนวน Keyword

[G3: ประเมิน 5 มิติ รวมกันก่อนสรุป]

มิติที่ 1 — รูปแบบการเขียน (Writing Style)
  Owner signal:   ข้อความดิบ ไม่มี Format ตายตัว ย่อหน้าไม่เป็นระเบียบ มีการพิมพ์ผิดบ้าง
  Agent signal:   มี Section Headers ชัด, Bullet Points เป็นระบบ, Emoji ใช้เป็น Icons ประจำ Section

มิติที่ 2 — เนื้อหาที่รู้ (Knowledge Content)
  Owner signal:   บอกเรื่องส่วนตัว เช่น ที่มาของทรัพย์ สภาพจริง ประสบการณ์อยู่อาศัย
  Agent signal:   ข้อมูลเชิงการตลาด เช่น ROI, Yield, ทำเลเหมาะลงทุน, เปรียบเทียบโครงการอื่น

มิติที่ 3 — ช่องทางติดต่อ (Contact Pattern)
  Owner signal:   เบอร์เดียว + ชื่อเล่น, Line Personal ID, นัดดูบ้านโดยตรง
  Agent signal:   หลายช่องทาง, Form, Link นัดชม, "ทีมงาน" รับเรื่อง

มิติที่ 4 — ภาษาที่ใช้ (Tone & Voice)
  Owner signal:   พูดถึงบ้าน/ที่ดินเหมือนเป็น "ของของตัวเอง" — "บ้านผม", "ที่ดินที่ซื้อมา"
  Agent signal:   ใช้ภาษากลางหรือ Professional เหมือนประกาศโฆษณา

มิติที่ 5 — Hashtag / SEO Pattern
  Owner signal:   Hashtag น้อย (0–5 อัน) หรือไม่มีเลย
  Agent signal:   Hashtag จำนวนมาก (10+ อัน) มีการใส่ SEO Keywords อย่างเป็นระบบ
  ⚠️ ข้อยกเว้น: เจ้าของบางรายใส่ Hashtag มากแต่มีสัญญาณ Gate 2 ชัดเจน
    → ให้น้ำหนัก Gate 2 มากกว่า Hashtag count

[G3: สรุป Confidence Score]
  4–5 มิติเป็น Owner signal  → is_owner: true,  confidence: 0.65–0.80
  3 มิติเป็น Owner signal    → is_owner: true,  confidence: 0.55–0.65
  3 มิติเป็น Agent signal    → is_owner: false, confidence: 0.25–0.40
  4–5 มิติเป็น Agent signal  → is_owner: false, confidence: 0.10–0.25

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TIEBREAKER: เมื่อมีสัญญาณ Owner และ Agent ปะปนกัน
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ให้ใช้กฎต่อไปนี้ตามลำดับ:

1. Gate 1 Hard-Filter เสมอ Override สัญญาณ Owner ทุกอย่าง (เว้น Exception ที่ระบุไว้)
2. Gate 2 สัญญาณ Owner Verified (G2-A, G2-B, G2-C, G2-D) Override Gate 3 เสมอ
3. สัญญาณจาก Gate 2 หลายข้อรวมกัน Override สัญญาณ Agent เดี่ยวๆ จาก Gate 3 ได้
   ยกตัวอย่าง: "เจ้าของขายเอง" + Hashtag เยอะ → ยังถือว่า Owner (confidence: 0.85)
4. เมื่อยังสรุปไม่ได้ → is_owner: false, confidence: 0.45, risk_flags: ["AMBIGUOUS_POSTER"]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RISK FLAGS REFERENCE (ใส่ใน risk_flags array)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"LINE_OFFICIAL_ACCOUNT"      → G1-A
"MULTILANG_TEMPLATE"         → G1-B
"PROPERTY_CODE_FOUND"        → G1-C
"SALES_CLOSING_LANGUAGE"     → G1-D
"BROKER_SERVICE_DECLARED"    → G1-E
"AGENT_ROUTING_CONTACT"      → G1-F
"CORPORATE_MARKETING_COPY"   → G1-G
"OWNER_SELF_DECLARED"        → G2-A (ใส่ใน evidence_phrases แทน risk_flags)
"ANTI_BROKER_STATEMENT"      → G2-B
"HIGH_HASHTAG_COUNT"         → Hashtag > 10 อัน (ข้อมูลเสริม ไม่ใช่ตัดสิน)
"AMBIGUOUS_POSTER"           → ไม่สามารถสรุปได้ชัดเจน
"NO_CONTACT_INFO"            → ไม่มีเบอร์/Line ในโพสต์
"""


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


def _is_truncated(content: str) -> bool:
    stripped = content.strip()
    return any(stripped.endswith(s) for s in _TRUNCATION_SUFFIXES)


def is_post_older_than_24h(date_text: str) -> bool:
    if not date_text or date_text == "N/A":
        return False
    now = datetime.now()
    val = _INVISIBLE_CHARS_RE.sub("", date_text).strip().lower()
    tmp = _TIME_TEXT_RE.sub("", val)
    tmp = _TIME_ONLY_RE.sub("", tmp).strip()
    if not tmp:
        return False
    if "<" in tmp or "น้อยกว่า" in tmp or "ต่ำกว่า" in tmp:
        return False
    m_min = _MINUTES_RE.search(tmp)
    if m_min:
        return False
    m_hr = _HOURS_RE.search(tmp)
    if m_hr:
        try:
            hours_val = int(m_hr.group(1))
            return hours_val >= 24
        except (ValueError, AttributeError, IndexError):
            return True
    if "วันนี้" in tmp:
        return False
    if "เมื่อวาน" in tmp:
        return True
    m_days = _DAYS_RE.search(tmp)
    if m_days:
        try:
            days_val = int(m_days.group(1))
            return days_val >= 1
        except (ValueError, AttributeError, IndexError):
            return True
    if _DATE_RE.search(tmp):
        parsed = parse_date(tmp)
        if parsed == "-":
            return True
        try:
            dt = datetime.strptime(parsed, "%d/%m/%Y")
            return (now - dt) > timedelta(hours=24)
        except (ValueError, TypeError):
            return True
    return False


def get_chrome_version(chrome_exec: Optional[str]) -> int:
    if not chrome_exec:
        return 0
    try:
        res = subprocess.run(
            [chrome_exec, "--version"], capture_output=True, text=True, check=False
        )
        match = re.search(r"(\d+)\.", res.stdout) if res.stdout else None
        return int(match.group(1)) if match else 0
    except Exception:
        return 0


def create_driver() -> uc.Chrome:
    PROFILE_PATH.mkdir(parents=True, exist_ok=True)
    chrome_exec = shutil.which("google-chrome") or shutil.which("chromium-browser")
    opts = uc.ChromeOptions()
    opts.add_argument(f"--user-data-dir={PROFILE_PATH}")
    opts.add_argument("--window-size=1920,1080")
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.media_stream": 2,
        "profile.managed_default_content_settings.plugins": 2,
    }
    opts.add_experimental_option("prefs", prefs)
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-features=IsolateOrigins,site-per-process")
    opts.add_argument("--blink-settings=imagesEnabled=false")
    opts.page_load_strategy = "eager"
    return uc.Chrome(
        options=opts,
        version_main=get_chrome_version(chrome_exec),
        browser_executable_path=chrome_exec,
    )


def atomic_fb_extract(driver: uc.Chrome) -> Tuple[List[Dict[str, str]], bool]:
    result = driver.execute_script(r"""
        const INVIS_CODES = new Set([0x200B,0x200C,0x200D,0xFEFF,0x00A0,0x2060,0x180E,0x2028,0x2029,0x00AD]);
        function cleanText(s) {
            let out = '';
            for (let i = 0; i < s.length; i++) {
                if (!INVIS_CODES.has(s.charCodeAt(i))) out += s[i];
            }
            return out.trim().toLowerCase();
        }
        const articles = document.querySelectorAll("div[role='article']");
        if (articles.length > 0) {
            const lastArticle = articles[articles.length - 1];
            lastArticle.scrollIntoView({behavior: 'instant', block: 'end'});
            window.scrollBy(0, 800);
        }
        const TARGET = new Set(['\u0e14\u0e39\u0e40\u0e1e\u0e34\u0e48\u0e21\u0e40\u0e15\u0e34\u0e21', 'see more']);
        const candidates = Array.from(document.querySelectorAll('div[role="button"], span[role="button"]'));
        for (const el of candidates) {
            const text = cleanText(el.innerText || el.textContent || '');
            if (!TARGET.has(text)) continue;
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const cx = rect.left + rect.width / 2;
            const cy = rect.top + rect.height / 2;
            const evtOpts = {bubbles: true, cancelable: true, view: window, clientX: cx, clientY: cy, screenX: window.screenX + cx, screenY: window.screenY + cy};
            for (const evtType of ['pointerover','mouseover','pointermove','mousemove','pointerdown','mousedown','pointerup','mouseup','click']) {
                try { el.dispatchEvent(new MouseEvent(evtType, evtOpts)); } catch(_) {}
            }
        }
        const results = [];
        let hasOldPost = false;
        document.querySelectorAll("div[role='article']").forEach(a => {
            const linkNodes = Array.from(a.querySelectorAll("a[href]")).filter(l => l.href.includes('/posts/') || l.href.includes('/permalink/'));
            if (linkNodes.length === 0) return;
            const url = linkNodes[0].href.split('?')[0];
            const msgNode = a.querySelector("div[data-ad-comet-preview='message']") || a.querySelector("div[data-ad-preview='message']");
            if (!msgNode) return;
            const content = msgNode.innerText.trim();
            let date = "N/A";
            for (let l of linkNodes) {
                const aria = (l.getAttribute("aria-label") || "").trim();
                const text = (l.textContent || "").trim();
                if (aria && aria.length > 0 && aria.length < 30) { date = aria; break; }
                else if (text && text.length > 0 && text.length < 30) { date = text; break; }
            }
            const dateStr = cleanText(date);
            if (dateStr && !dateStr.includes('\u0e27\u0e31\u0e19\u0e19\u0e35\u0e49') && !dateStr.includes('\u0e19\u0e32\u0e17\u0e35') && !dateStr.includes('\u0e0a\u0e31\u0e48\u0e27\u0e42\u0e21\u0e07')) {
                const hasDayCount = /\d+\s*\u0e27\u0e31\u0e19/.test(dateStr);
                const hasDateFormat = /\d{1,2}[\s.\/\-]+\w+/.test(dateStr);
                const isYesterday = dateStr.includes('\u0e40\u0e21\u0e37\u0e48\u0e2d\u0e27\u0e32\u0e19');
                if (hasDayCount || hasDateFormat || isYesterday) {
                    hasOldPost = true;
                }
            }
            results.push({"Post_URL": url, "Full_Content": content, "Date": date});
        });
        return {results, hasOldPost};
    """)
    return result["results"], result["hasOldPost"]


def humanized_scroll(driver: uc.Chrome) -> None:
    driver.execute_script(
        f"window.scrollBy(0, {SCROLL_SIZE + random.randint(-500, 500)});"
    )
    time.sleep(random.uniform(0.5, 1.0))


def apply_new_post_filter(driver: uc.Chrome) -> None:
    try:
        driver.execute_script("""
            const filterBtn = Array.from(document.querySelectorAll('div[role="button"]'))
                .find(e => e.innerText && (e.innerText.includes('\u0e40\u0e23\u0e35\u0e22\u0e07\u0e25\u0e33\u0e14\u0e31\u0e1a\u0e1f\u0e35\u0e14\u0e43\u0e19\u0e01\u0e25\u0e38\u0e48\u0e21\u0e15\u0e32\u0e21') || e.innerText.includes('\u0e08\u0e31\u0e14\u0e40\u0e23\u0e35\u0e22\u0e07\u0e15\u0e32\u0e21')));
            if (filterBtn) {
                filterBtn.click();
                setTimeout(() => {
                    const options = Array.from(document.querySelectorAll('div[role="menuitemradio"]'));
                    const target = options.find(e => e.innerText && (e.innerText.includes('\u0e42\u0e1e\u0e2a\u0e15\u0e4c\u0e43\u0e2b\u0e21\u0e48') || e.innerText.includes('\u0e23\u0e32\u0e22\u0e01\u0e32\u0e23\u0e2a\u0e34\u0e19\u0e04\u0e49\u0e32\u0e43\u0e2b\u0e21\u0e48')));
                    if (target) target.click();
                }, 1500);
            }
        """)
        time.sleep(3.5)
    except Exception as e:
        logger.error(f"Filter error: {e}")


def call_llm_service(payload: str, raw_url: str = "") -> Optional[Dict]:
    trimmed_payload = payload[:LLM_PAYLOAD_TRIM]
    for attempt in range(3):
        acquired = _llm_semaphore.acquire(timeout=LLM_TIMEOUT)
        if not acquired:
            time.sleep(1.0 * (attempt + 1))
            continue
        try:
            c = _get_client()
            response = c.chat.completions.create(
                model=MODEL_NAME,
                temperature=0.0,
                max_tokens=LLM_MAX_TOKENS,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": trimmed_payload},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            return json.loads(content) if content else None
        except Exception:
            time.sleep(2**attempt)
        finally:
            _llm_semaphore.release()
    return None


def parse_date(date_text: str) -> str:
    now = datetime.now()
    if not date_text or date_text == "N/A":
        return "-"
    val = _INVISIBLE_CHARS_RE.sub("", date_text).strip().lower()
    val = _TIME_TEXT_RE.sub("", val)
    val = _TIME_ONLY_RE.sub("", val).strip()
    m_min = _MINUTES_RE.search(val)
    if m_min:
        return (now - timedelta(minutes=int(m_min.group(1)))).strftime("%d/%m/%Y")
    m_hr = _HOURS_RE.search(val)
    if m_hr:
        return (now - timedelta(hours=int(m_hr.group(1)))).strftime("%d/%m/%Y")
    if "วันนี้" in val:
        return now.strftime("%d/%m/%Y")
    if "เมื่อวาน" in val:
        return (now - timedelta(days=1)).strftime("%d/%m/%Y")
    m_days = _DAYS_RE.search(val)
    if m_days:
        return (now - timedelta(days=int(m_days.group(1)))).strftime("%d/%m/%Y")
    m_date = _DATE_RE.search(val)
    if m_date:
        d, m_raw = int(m_date.group(1)), m_date.group(2)
        m = MONTH_MAP.get(m_raw)
        if m:
            y = now.year
            if m_date.group(3):
                y_raw = int(m_date.group(3))
                y = (
                    y_raw - 543
                    if y_raw > 2400
                    else (y_raw if y_raw > 100 else 2000 + y_raw)
                )
            return f"{d:02d}/{m:02d}/{y}"
    return "-"


def transform_record(raw_row: Dict[str, str], ai_data: Dict) -> Dict[str, str]:
    ext = ai_data.get("extracted", {})
    llm_date = _INVISIBLE_CHARS_RE.sub("", ai_data.get("post_date_text", "")).strip()
    dom_date = raw_row.get("Date", "").strip()
    actual_date = llm_date if llm_date and llm_date != "N/A" else dom_date
    return {
        "วันที่โพส": parse_date(actual_date),
        "website": "facebook",
        "ประเภท": ext.get("property_type", "-"),
        "สถานะ": ext.get("rental_sale_status", "-"),
        "ชื่อโครงการ": ext.get("project_name", "-"),
        "ขนาด": ext.get("size_text", "-"),
        "ราคา": str(ext.get("price_text", "-")),
        "เขต": ext.get("district", "-"),
        "Link": raw_row.get("Post_URL", "-"),
        "เบอร์โทรศัพท์": ext.get("phone", "-"),
        "Line": ext.get("line", "-"),
        "คำอธิบาย": ext.get("description", "-"),
    }


def worker_process_and_save(raw_item: Dict[str, str]) -> None:
    payload = f"Post Date: {raw_item.get('Date', 'N/A')}\n\nContent:\n{raw_item.get('Full_Content', '')}"
    ai_response = call_llm_service(payload, raw_item.get("Post_URL", ""))

    logger.info(
        f"[LLM] {raw_item.get('Post_URL', '')} → {json.dumps(ai_response, ensure_ascii=False)[:120]}..."
    )

    if not ai_response or not ai_response.get("is_real_estate"):
        return

    if not ai_response.get("is_owner"):
        return

    final_data = transform_record(raw_item, ai_response)
    with csv_lock:
        with open(OUTPUT_PATH, "a", encoding="utf-8-sig", newline="") as f:
            csv.DictWriter(f, fieldnames=OUTPUT_HEADERS).writerow(final_data)

    logger.info(
        f"[SAVED] {final_data['ชื่อโครงการ']} | {final_data['เขต']} | {final_data['ราคา']}"
    )


def process_group(
    driver: uc.Chrome,
    url: str,
    seen_urls: Set[str],
    executor: ThreadPoolExecutor,
    group_idx: int,
    total_groups: int,
) -> None:
    try:
        logger.info(f"[Group {group_idx}/{total_groups}] Start processing: {url}")
        driver.get(url)
        time.sleep(5)
        apply_new_post_filter(driver)

        saved_count, stagnant_count = 0, 0
        found_old_post = False

        for _ in range(300):
            _wait_for_backpressure()
            extracted, found_old = atomic_fb_extract(driver)
            found_old_post = found_old_post or found_old

            if not extracted:
                stagnant_count += 1
            else:
                unseen = [i for i in extracted if i["Post_URL"] not in seen_urls]
                valid_unseen = [
                    i for i in unseen if not is_post_older_than_24h(i["Date"])
                ]
                new_items = [
                    i for i in valid_unseen if not _is_truncated(i["Full_Content"])
                ]
                truncated_count = len(valid_unseen) - len(new_items)

                if truncated_count:
                    logger.info(
                        f"[Group {group_idx}/{total_groups}] Skipped {truncated_count} truncated post(s), will retry next iteration"
                    )

                if new_items:
                    stagnant_count = 0
                    for item in new_items:
                        seen_urls.add(item["Post_URL"])
                        fut = executor.submit(worker_process_and_save, item)
                        _register_future(fut)
                        saved_count += 1
                    logger.info(
                        f"[Group {group_idx}/{total_groups}] Collected {len(new_items)} new items (Total saved this group: {saved_count})"
                    )
                elif truncated_count:
                    stagnant_count = 0
                else:
                    stagnant_count += 1

            if found_old_post or stagnant_count >= MAX_STAGNANT:
                reason = (
                    "Found post older than 24h"
                    if found_old_post
                    else f"Stagnant: {stagnant_count}"
                )
                logger.info(
                    f"[Group {group_idx}/{total_groups}] Stop condition met. ({reason}, Saved: {saved_count})"
                )
                break

            humanized_scroll(driver)
    except Exception as e:
        logger.error(f"Error processing {url}: {e}")


def main() -> None:
    if not OUTPUT_PATH.exists():
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=OUTPUT_HEADERS).writeheader()

    seen_urls: Set[str] = set()
    with open(OUTPUT_PATH, "r", encoding="utf-8-sig") as f:
        seen_urls.update(row["Link"] for row in csv.DictReader(f) if row.get("Link"))

    total_groups = len(DEFAULT_GROUP_URLS)
    resume_slice = DEFAULT_GROUP_URLS[START_GROUP_IDX - 1 :]
    logger.info(
        f"Resuming from group {START_GROUP_IDX}/{total_groups}: {resume_slice[0]}"
    )

    driver = create_driver()
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for idx, group_url in enumerate(resume_slice, start=START_GROUP_IDX):
                process_group(driver, group_url, seen_urls, executor, idx, total_groups)
            _wait_for_backpressure()
    finally:
        driver.quit()


if __name__ == "__main__":
    main()