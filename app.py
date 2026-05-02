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
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

BASE_DIR = Path("/home/kongla/Documents/GitHub/Real-estate-Scraping")
OUTPUT_PATH = Path(
    "/home/kongla/Documents/GitHub/Real-Estate Listing Aggregator System/facebook-scraping/Output.csv"
)
PROFILE_PATH = BASE_DIR / "chrome_profile"

MAX_STAGNANT = 10
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
    "860696615",
    "897004546",
    "935068042",
    "936959144",
    "923391919",
    "819612163",
    "819638788",
    "928512744",
    "655653642",
    "637803645",
    "930391151",
    "924964978",
    "815311101",
    "891927904",
    "658516959",
    "639964993",
    "810291600",
    "659722284",
    "926165642",
    "988494095",
    "869131588",
    "659549746",
    "943197737",
    "622614596",
    "990096164",
    "952659690",
    "646533516",
    "834705654",
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
  "extracted": {
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
  }
}

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


def _normalize_line_id(s: str) -> str:
    if not s:
        return ""
    return _INVISIBLE_CHARS_RE.sub("", s).strip()


def _normalize_phone(s: str) -> str:
    if not s:
        return ""
    digits = _NON_DIGIT_RE.sub("", s)
    return _LEADING_ZERO_RE.sub("", digits)


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
    m_min = _MINUTES_RE.search(tmp)
    if m_min:
        return False
    m_hr = _HOURS_RE.search(tmp)
    if m_hr:
        try:
            return int(m_hr.group(1)) >= 24
        except Exception:
            return True
    if "วันนี้" in tmp:
        return False
    if "เมื่อวาน" in tmp:
        return True
    m_days = _DAYS_RE.search(tmp)
    if m_days:
        try:
            return int(m_days.group(1)) >= 1
        except Exception:
            return True
    if _DATE_RE.search(tmp):
        parsed = parse_date(tmp)
        if parsed == "-":
            return True
        try:
            dt = datetime.strptime(parsed, "%d/%m/%Y")
            return (now - dt) > timedelta(hours=24)
        except Exception:
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


def create_driver():
    import undetected_chromedriver as uc

    PROFILE_PATH.mkdir(parents=True, exist_ok=True)
    chrome_exec = shutil.which("google-chrome") or shutil.which("chromium-browser")
    opts = uc.ChromeOptions()
    opts.add_argument(f"--user-data-dir={PROFILE_PATH}")
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.media_stream": 2,
        "profile.managed_default_content_settings.plugins": 2,
        "profile.managed_default_content_settings.stylesheets": 2,
        "profile.managed_default_content_settings.fonts": 2,
    }
    opts.add_experimental_option("prefs", prefs)
    opts.add_argument("--blink-settings=imagesEnabled=false")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-features=IsolateOrigins,site-per-process")
    opts.page_load_strategy = "eager"
    return uc.Chrome(
        options=opts,
        version_main=get_chrome_version(chrome_exec),
        browser_executable_path=chrome_exec,
    )


def expand_all_see_more(driver) -> int:
    clicked = driver.execute_script(r"""
        const TARGET_SUBSTRINGS = ['ดูเพิ่มเติม','see more'];
        const candidates = document.querySelectorAll('div[role="button"], span[role="button"], a[role="link"], a');
        let clicked = 0;
        for (let el of candidates) {
            if (el.href) {
                const h = el.href.toLowerCase();
                if (h.includes('/reel/') || h.includes('/reels/') || h.includes('/video/') || h.includes('/videos/') || h.includes('/watch/')) {
                    continue;
                }
            }
            let text = (el.innerText || el.textContent || '').replace(/[\u200B-\u200D\uFEFF\u00A0\u2060\u180E\u2028\u2029\u00AD]/g, '').trim().toLowerCase();
            if (!text) continue;
            for (let s of TARGET_SUBSTRINGS) {
                if (text.indexOf(s) !== -1) {
                    el.scrollIntoView({behavior: 'instant', block: 'center'});
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    try {
                        el.click();
                        clicked++;
                    } catch(_) {}
                    break;
                }
            }
        }
        return clicked;
    """)
    if clicked > 0:
        time.sleep(1.0)
    return clicked or 0


def atomic_fb_extract(driver) -> Tuple[List[Dict[str, str]], bool]:
    result = driver.execute_script(r"""
        const results = [];
        const articles = document.querySelectorAll("div[role='article']");
        const isForbidden = (href) => {
            if (!href) return false;
            const h = href.toLowerCase();
            return h.includes('/reel/') || h.includes('/reels/') || h.includes('/video/') || h.includes('/videos/') || h.includes('/watch/');
        };
        for (let a of articles) {
            try {
                let url = "";
                let date = "N/A";
                const timeRE = /(นาที|ชั่วโมง|ชม\.|วัน|เมื่อวาน|วันนี้)/i;
                const timeLinks = Array.from(a.querySelectorAll("a[href]")).filter(l => {
                    if (isForbidden(l.href)) return false;
                    const aria = (l.getAttribute("aria-label") || "").trim();
                    const txt = (l.textContent || "").trim();
                    return (timeRE.test(aria) && aria.length < 25) || (timeRE.test(txt) && txt.length < 25);
                });
                if (timeLinks.length > 0) {
                    url = timeLinks[0].href.split('?')[0];
                    const aria = (timeLinks[0].getAttribute("aria-label") || "").trim();
                    const txt = (timeLinks[0].textContent || "").trim();
                    date = (aria && timeRE.test(aria)) ? aria : txt;
                } else {
                    const fallbackLinks = Array.from(a.querySelectorAll("a[href]")).filter(l => {
                        if (isForbidden(l.href)) return false;
                        return l.href.includes('/groups/') || l.href.includes('/posts/') || l.href.includes('/permalink/');
                    });
                    if (fallbackLinks.length === 0) continue;
                    url = fallbackLinks[0].href.split('?')[0];
                }
                if (isForbidden(url)) continue;
                let msgNode = a.querySelector("div[data-ad-comet-preview='message']") || a.querySelector("div[data-ad-preview='message']");
                let content = "";
                if (msgNode && (msgNode.innerText || "").trim().length > 0) {
                    content = msgNode.innerText.trim();
                } else {
                    const candidates = Array.from(a.querySelectorAll('div[dir="auto"], span[dir="auto"], p'));
                    let best = "";
                    for (let c of candidates) {
                        try {
                            let txt = (c.innerText || c.textContent || "").trim();
                            if (txt.length > best.length && txt.length > 10) { best = txt; }
                        } catch(e) {}
                    }
                    content = best.trim();
                }
                if (!content) continue;
                results.push({"Post_URL": url, "Full_Content": content, "Date": date});
            } catch(e) {}
        }
        return {results: results, hasOldPost: false};
    """)
    return result["results"], result["hasOldPost"]


def _wait_for_articles(driver, timeout: float = 25.0) -> bool:
    """Poll until at least one article element appears or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        count = driver.execute_script(
            "return document.querySelectorAll(\"div[role='article']\").length;"
        )
        if count and count > 0:
            return True
        time.sleep(1.5)
    return False


def humanized_scroll(driver) -> None:
    driver.execute_script("window.scrollBy(0, 4000);")
    time.sleep(random.uniform(0.5, 1.5))


def apply_new_post_filter(driver) -> None:
    try:
        driver.execute_script(r"""
            const btnCandidates = Array.from(document.querySelectorAll('div[role="button"], span[dir="auto"]'));
            const filterBtn = btnCandidates.find(e => {
                const t = (e.innerText || e.textContent || '').replace(/[\u200B-\u200D\uFEFF\u00A0\u2060\u180E\u2028\u2029\u00AD]/g,'').trim();
                return /เรียงลำดับ|จัดเรียง|เกี่ยวข้อง|เรียงตาม/.test(t) && !/ความคิดเห็น/.test(t);
            });
            if (filterBtn) {
                filterBtn.scrollIntoView({behavior: 'instant', block: 'center'});
                filterBtn.click();
            }
        """)
        time.sleep(2.0)
        driver.execute_script(r"""
            const menuItems = Array.from(document.querySelectorAll('div[role="menuitemradio"]'));
            const target = menuItems.find(e => {
                const text = (e.innerText || e.textContent || '').replace(/[\u200B-\u200D\uFEFF\u00A0\u2060\u180E\u2028\u2029\u00AD]/g,'').trim();
                return text.includes('แสดงโพสต์ล่าสุดก่อน') || text.includes('โพสต์ใหม่');
            });
            if (target) {
                const isChecked = target.getAttribute('aria-checked') === 'true';
                if (!isChecked) {
                    target.scrollIntoView({behavior: 'instant', block: 'center'});
                    const opts = { bubbles: true, cancelable: true, view: window };
                    target.dispatchEvent(new MouseEvent('mousedown', opts));
                    target.dispatchEvent(new MouseEvent('mouseup', opts));
                    target.dispatchEvent(new MouseEvent('click', opts));
                    target.click();
                    const innerSpan = target.querySelector('span[dir="auto"]');
                    if (innerSpan) { innerSpan.click(); }
                }
            }
        """)
        time.sleep(3.0)
    except Exception:
        pass


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


def worker_process_and_save(
    raw_item: Dict[str, str], log_sink: List[str], stats: Dict
) -> None:
    payload = f"Post Date: {raw_item.get('Date', 'N/A')}\n\nContent:\n{raw_item.get('Full_Content', '')}"
    ai_response = call_llm_service(payload, raw_item.get("Post_URL", ""))
    log_sink.append(
        f"[LLM] {raw_item.get('Post_URL', '')} → {json.dumps(ai_response, ensure_ascii=False)[:120]}..."
    )

    if not ai_response or not ai_response.get("is_real_estate"):
        return
    stats["is_real_estate"] = stats.get("is_real_estate", 0) + 1

    if not ai_response.get("is_owner"):
        return
    stats["is_owner"] = stats.get("is_owner", 0) + 1

    ext = ai_response.get("extracted", {})
    norm_line = _normalize_line_id(ext.get("line", ""))
    norm_phone = _normalize_phone(ext.get("phone", ""))

    if not norm_phone:
        candidate_phone = _PHONE_EXTRACT_RE.search(raw_item.get("Full_Content", ""))
        norm_phone = (
            _normalize_phone(candidate_phone.group(1)) if candidate_phone else ""
        )

    line_match = (
        any(k == norm_line or k in norm_line for k in KNOWN_LINE_IDS)
        if norm_line
        else False
    )
    phone_match = (
        any(p in norm_phone or norm_phone in p for p in KNOWN_PHONE_NUMBERS)
        if norm_phone
        else False
    )

    if line_match or phone_match:
        return

    final_data = transform_record(raw_item, ai_response)
    with csv_lock:
        with open(OUTPUT_PATH, "a", encoding="utf-8-sig", newline="") as f:
            csv.DictWriter(f, fieldnames=OUTPUT_HEADERS).writerow(final_data)

    stats["saved"] = stats.get("saved", 0) + 1
    log_sink.append(
        f"[SAVED] {final_data['ชื่อโครงการ']} | {final_data['เขต']} | {final_data['ราคา']}"
    )


def process_group(
    driver,
    url: str,
    seen_urls: Set[str],
    executor: ThreadPoolExecutor,
    group_idx: int,
    total_groups: int,
    log_sink: List[str],
    stats: Dict,
    stop_event: threading.Event,
) -> None:
    try:
        log_sink.append(f"[Group {group_idx}/{total_groups}] {url}")
        stats["current_group"] = group_idx
        stats["current_url"] = url
        driver.get(url)
        time.sleep(5)
        loaded = _wait_for_articles(driver, timeout=25.0)
        if not loaded:
            log_sink.append(
                f"[WARNING] Timeout waiting for articles — skipping group: {url}"
            )
            return
        apply_new_post_filter(driver)
        # รอให้หน้าโหลด content หลัง filter ด้วย
        _wait_for_articles(driver, timeout=15.0)
        time.sleep(5.0)
        saved_count, stagnant_count, consecutive_truncated = 0, 0, 0
        found_old_post = False

        for _ in range(500):
            if stop_event.is_set():
                log_sink.append("[STOP] Stop requested — halting.")
                return
            _wait_for_backpressure()
            expand_all_see_more(driver)
            extracted, _ = atomic_fb_extract(driver)

            if not extracted:
                stagnant_count += 1
                consecutive_truncated = 0
            else:
                unseen = [i for i in extracted if i["Post_URL"] not in seen_urls]
                valid_unseen = []
                old_count = 0
                for item in unseen:
                    if is_post_older_than_24h(item["Date"]):
                        old_count += 1
                        if old_count >= 1:
                            found_old_post = True
                            break
                    else:
                        valid_unseen.append(item)

                new_items = [
                    i for i in valid_unseen if not _is_truncated(i["Full_Content"])
                ]
                truncated_count = len(valid_unseen) - len(new_items)

                if new_items:
                    stagnant_count = 0
                    consecutive_truncated = 0
                    for item in new_items:
                        seen_urls.add(item["Post_URL"])
                        stats["collected"] = stats.get("collected", 0) + 1
                        log_sink.append(f"[COLLECTED] {item['Post_URL']}")
                        fut = executor.submit(
                            worker_process_and_save, item, log_sink, stats
                        )
                        _register_future(fut)
                        saved_count += 1
                elif truncated_count:
                    consecutive_truncated += 1
                    if consecutive_truncated >= 5:
                        stagnant_count += 1
                        consecutive_truncated = 0
                else:
                    stagnant_count += 1
                    consecutive_truncated = 0

            if found_old_post or stagnant_count >= MAX_STAGNANT:
                break
            humanized_scroll(driver)
    except Exception as e:
        stats["last_error"] = f"{url} => {str(e)}"
        log_sink.append(f"[ERROR] [{url}]: {e}")


def run_scraper(
    start_idx: int,
    extra_urls: List[str],
    log_sink: List[str],
    stats: Dict,
    stop_event: threading.Event,
) -> None:
    group_urls = DEFAULT_GROUP_URLS + [
        u for u in extra_urls if u not in DEFAULT_GROUP_URLS
    ]
    if not OUTPUT_PATH.exists():
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=OUTPUT_HEADERS).writeheader()

    seen_urls: Set[str] = set()
    with open(OUTPUT_PATH, "r", encoding="utf-8-sig") as f:
        seen_urls.update(row["Link"] for row in csv.DictReader(f) if row.get("Link"))

    stats["total_groups"] = len(group_urls)
    stats["start_idx"] = start_idx
    resume_slice = group_urls[start_idx - 1 :]
    driver = create_driver()

    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for idx, group_url in enumerate(resume_slice, start=start_idx):
                if stop_event.is_set():
                    break
                process_group(
                    driver,
                    group_url,
                    seen_urls,
                    executor,
                    idx,
                    len(group_urls),
                    log_sink,
                    stats,
                    stop_event,
                )
            _wait_for_backpressure()
    finally:
        driver.quit()
        stats["status"] = "stopped"
        log_sink.append("[COMPLETE] Scraper finished.")


def inject_constant_to_file(target: str, value: str):
    p = Path(__file__).resolve()
    content = p.read_text(encoding="utf-8")
    if target == "phone" and value not in content:
        content = re.sub(
            r"(KNOWN_PHONE_NUMBERS:\s*Tuple\[str,\s*\.\.\.\]\s*=\s*\()",
            f'\\1\n    "{value}",',
            content,
        )
    elif target == "line" and value not in content:
        if "KNOWN_LINE_IDS: Tuple[str, ...] = tuple()" in content:
            content = content.replace(
                "KNOWN_LINE_IDS: Tuple[str, ...] = tuple()",
                f'KNOWN_LINE_IDS: Tuple[str, ...] = (\n    "{value}",\n)',
            )
        else:
            content = re.sub(
                r"(KNOWN_LINE_IDS:\s*Tuple\[str,\s*\.\.\.\]\s*=\s*\()",
                f'\\1\n    "{value}",',
                content,
            )
    p.write_text(content, encoding="utf-8")


def main_ui():
    st.set_page_config(page_title="Real-Estate Scraper", layout="wide")

    st.markdown(
        """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans+Thai:wght@300;500;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'IBM Plex Sans Thai', 'IBM Plex Mono', monospace;
    }
    .stApp { background: #0d0f14; color: #c9d1d9; }
    .main-header {
        font-size: 1.6rem; font-weight: 700; color: #58a6ff;
        letter-spacing: 0.05em; border-bottom: 1px solid #21262d;
        padding-bottom: 0.5rem; margin-bottom: 1.5rem;
    }
    .stat-card {
        background: #161b22; border: 1px solid #30363d; border-radius: 8px;
        padding: 1rem 1.2rem; text-align: center;
    }
    .stat-label { font-size: 0.72rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.1em; }
    .stat-value { font-size: 2rem; font-weight: 700; color: #58a6ff; font-family: 'IBM Plex Mono'; }
    .funnel-card {
        background: #161b22; border: 1px solid #30363d; border-radius: 8px;
        padding: 15px; display: flex; justify-content: space-between; text-align: center; margin-bottom: 1rem;
    }
    .funnel-item { flex: 1; border-right: 1px solid #30363d; }
    .funnel-item:last-child { border-right: none; }
    
    @keyframes pulse-flow {
        0% { border-color: #30363d; box-shadow: 0 0 0 0 rgba(88, 166, 255, 0); }
        50% { border-color: #58a6ff; box-shadow: 0 0 10px 2px rgba(88, 166, 255, 0.4); color: #58a6ff; }
        100% { border-color: #30363d; box-shadow: 0 0 0 0 rgba(88, 166, 255, 0); }
    }
    .anim-container {
        display: flex; justify-content: space-around; align-items: center;
        background: #0d1117; padding: 15px; border-radius: 8px; border: 1px solid #21262d; margin-bottom: 1rem;
    }
    .anim-box {
        padding: 10px 20px; border-radius: 6px; border: 2px solid #30363d; 
        font-weight: 600; font-size: 0.9rem; background: #161b22;
    }
    .anim-running .anim-box { animation: pulse-flow 1.5s infinite; }
    .anim-arrow { color: #8b949e; font-size: 1.2rem; font-weight: bold; }
    
    .error-box {
        background: #4a0000; border: 1px solid #ff4444; color: #ffcccc; 
        padding: 10px; border-radius: 6px; margin-bottom: 1rem; font-family: 'IBM Plex Mono'; font-size: 0.85rem;
    }

    .log-box {
        background: #0d1117; border: 1px solid #21262d; border-radius: 6px;
        padding: 0.8rem 1rem; height: 250px; overflow-y: auto;
        font-family: 'IBM Plex Mono', monospace; font-size: 0.75rem;
        color: #8b949e; white-space: pre-wrap; word-break: break-all;
    }
    .status-running { color: #3fb950; font-weight: 600; }
    .status-idle { color: #8b949e; }
    .status-stopped { color: #f85149; font-weight: 600; }
    div[data-testid="stNumberInput"] label,
    div[data-testid="stTextArea"] label,
    div[data-testid="stSelectbox"] label { color: #8b949e; font-size: 0.82rem; }
    .stButton > button {
        background: #238636; color: #fff; border: none; border-radius: 6px;
        padding: 0.5rem 1.4rem; font-weight: 600; font-size: 0.9rem;
        transition: background 0.2s;
    }
    .stButton > button:hover { background: #2ea043; }
    div[data-testid="column"]:nth-child(2) .stButton > button { background: #b62324; }
    div[data-testid="column"]:nth-child(2) .stButton > button:hover { background: #da3633; }
    </style>
    """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="main-header">Real Estate Scraper — Monitor</div>',
        unsafe_allow_html=True,
    )

    if "scraper_thread" not in st.session_state:
        st.session_state.scraper_thread = None
    if "stop_event" not in st.session_state:
        st.session_state.stop_event = threading.Event()
    if "log_sink" not in st.session_state:
        st.session_state.log_sink = []
    if "stats" not in st.session_state:
        st.session_state.stats = {
            "status": "idle",
            "collected": 0,
            "is_real_estate": 0,
            "is_owner": 0,
            "saved": 0,
            "current_group": 0,
            "total_groups": len(DEFAULT_GROUP_URLS),
            "last_error": None,
        }
    if "extra_urls_store" not in st.session_state:
        st.session_state.extra_urls_store = []
    if "confirm_reset" not in st.session_state:
        st.session_state.confirm_reset = False

    is_running = (
        st.session_state.scraper_thread is not None
        and st.session_state.scraper_thread.is_alive()
    )

    col_left, col_right = st.columns([1, 2], gap="large")

    with col_left:
        st.markdown("#### Configuration")

        start_idx = st.number_input(
            "เริ่มต้นที่ Group ลำดับที่",
            min_value=1,
            max_value=len(DEFAULT_GROUP_URLS) + 200,
            value=1,
            step=1,
            disabled=is_running,
            help=f"ปัจจุบันมี {len(DEFAULT_GROUP_URLS)} groups ใน default list",
        )

        st.markdown("#### Add Additional Group URLs")
        new_url_input = st.text_input(
            "วาง Facebook Group URL แล้วกด Add",
            placeholder="https://www.facebook.com/groups/...",
            disabled=is_running,
            key="new_url_field",
        )
        add_col, clear_col = st.columns(2)
        with add_col:
            if st.button("Add URL", disabled=is_running):
                url = new_url_input.strip()
                if (
                    url.startswith("https://www.facebook.com")
                    and url not in st.session_state.extra_urls_store
                ):
                    st.session_state.extra_urls_store.append(url)
                    st.success(f"Added: {url}")
                elif url in st.session_state.extra_urls_store:
                    st.warning("URL นี้มีอยู่แล้ว")
                else:
                    st.error("กรุณาใส่ URL Facebook ที่ถูกต้อง")
        with clear_col:
            if st.button("Clear Extra", disabled=is_running):
                st.session_state.extra_urls_store = []

        if st.session_state.extra_urls_store:
            st.markdown(f"**Extra URLs ({len(st.session_state.extra_urls_store)}):**")
            for i, u in enumerate(st.session_state.extra_urls_store):
                c1, c2 = st.columns([5, 1])
                c1.caption(u)
                if c2.button("Delete", key=f"del_{i}", disabled=is_running):
                    st.session_state.extra_urls_store.pop(i)
                    st.rerun()

        st.markdown("---")
        st.markdown("#### Add Agent/Blocklist Permanently")
        agent_col1, agent_col2 = st.columns(2)
        with agent_col1:
            new_phone = st.text_input("เบอร์โทร Agent", placeholder="09xxxxxxx")
        with agent_col2:
            new_line = st.text_input("Line ID Agent", placeholder="@agentline")

        if st.button("Save to Source Code", type="secondary"):
            global KNOWN_PHONE_NUMBERS, KNOWN_LINE_IDS
            if new_phone and new_phone not in KNOWN_PHONE_NUMBERS:
                KNOWN_PHONE_NUMBERS += (new_phone,)
                inject_constant_to_file("phone", new_phone)
                st.success(f"Added Phone: {new_phone}")
            if new_line and new_line not in KNOWN_LINE_IDS:
                KNOWN_LINE_IDS += (new_line,)
                inject_constant_to_file("line", new_line)
                st.success(f"Added Line ID: {new_line}")

        st.markdown("---")
        total_urls = len(DEFAULT_GROUP_URLS) + len(st.session_state.extra_urls_store)
        st.caption(
            f"Total groups queued: **{total_urls}** | Starting from index **{start_idx}** → **{total_urls - start_idx + 1}** groups to process"
        )

        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if st.button("Start Scraping", disabled=is_running):
                st.session_state.stop_event = threading.Event()
                st.session_state.log_sink = []
                st.session_state.stats = {
                    "status": "running",
                    "collected": 0,
                    "is_real_estate": 0,
                    "is_owner": 0,
                    "saved": 0,
                    "current_group": 0,
                    "total_groups": total_urls,
                    "last_error": None,
                }
                t = threading.Thread(
                    target=run_scraper,
                    args=(
                        int(start_idx),
                        list(st.session_state.extra_urls_store),
                        st.session_state.log_sink,
                        st.session_state.stats,
                        st.session_state.stop_event,
                    ),
                    daemon=True,
                )
                st.session_state.scraper_thread = t
                t.start()
                st.rerun()

        with btn_col2:
            if st.button("Stop", disabled=not is_running):
                st.session_state.stop_event.set()
                st.session_state.stats["status"] = "stopping..."
                st.rerun()

    with col_right:
        stats = st.session_state.stats
        status = stats.get("status", "idle")
        status_class = (
            "status-running"
            if status == "running"
            else ("status-stopped" if "stop" in status else "status-idle")
        )
        anim_class = "anim-running" if status == "running" else ""

        st.markdown(
            f"**Status:** <span class='{status_class}'>{status.upper()}</span>",
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
        <div class="anim-container {anim_class}">
            <div class="anim-box">Facebook DOM</div>
            <div class="anim-arrow">→</div>
            <div class="anim-box">LLM Extract</div>
            <div class="anim-arrow">→</div>
            <div class="anim-box">Blocklist Filter</div>
            <div class="anim-arrow">→</div>
            <div class="anim-box">CSV Saved</div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
        <div class="funnel-card">
            <div class="funnel-item"><div class="stat-label">Raw Posts</div><div class="stat-value">{stats.get("collected", 0)}</div></div>
            <div class="funnel-item"><div class="stat-label">Real Estate</div><div class="stat-value">{stats.get("is_real_estate", 0)}</div></div>
            <div class="funnel-item"><div class="stat-label">Is Owner</div><div class="stat-value">{stats.get("is_owner", 0)}</div></div>
            <div class="funnel-item"><div class="stat-label">Passed Filter (Saved)</div><div class="stat-value" style="color:#3fb950;">{stats.get("saved", 0)}</div></div>
        </div>
        """,
            unsafe_allow_html=True,
        )

        if stats.get("last_error"):
            st.markdown(
                f'<div class="error-box"><b>Scraping Error Halted At:</b><br>{stats["last_error"]}</div>',
                unsafe_allow_html=True,
            )

        m1, m2 = st.columns(2)
        with m1:
            st.markdown(
                f'<div class="stat-card"><div class="stat-label">Group Progress</div><div class="stat-value">{stats.get("current_group", 0)}/{stats.get("total_groups", 0)}</div></div>',
                unsafe_allow_html=True,
            )
        with m2:
            pending = len([f for f in _pending_futures if not f.done()])
            st.markdown(
                f'<div class="stat-card"><div class="stat-label">In-flight LLM Jobs</div><div class="stat-value">{pending}</div></div>',
                unsafe_allow_html=True,
            )

        if stats.get("current_url"):
            st.caption(f"Current: `{stats['current_url']}`")

        if stats.get("total_groups", 0) > 0 and stats.get("current_group", 0) > 0:
            pct = min(stats["current_group"] / stats["total_groups"], 1.0)
            st.progress(
                pct, text=f"Group {stats['current_group']} / {stats['total_groups']}"
            )

        st.markdown("#### Live Log")
        log_lines = st.session_state.log_sink[-100:]
        log_text = (
            "\n".join(reversed(log_lines))
            if log_lines
            else "Waiting for scraper to start..."
        )
        st.markdown(f'<div class="log-box">{log_text}</div>', unsafe_allow_html=True)

        if OUTPUT_PATH.exists():
            st.markdown("#### Data")
            try:
                import pandas as pd

                df = pd.read_csv(OUTPUT_PATH, encoding="utf-8-sig")
                total_rows = len(df)

                st.caption(f"แสดงข้อมูลทั้งหมด **{total_rows}** รายการ")

                with st.expander("Filters", expanded=False):
                    filter_cols = st.columns(3)
                    f_type = filter_cols[0].multiselect(
                        "ประเภท",
                        options=sorted(df["ประเภท"].dropna().unique().tolist()),
                        key="f_type",
                    )
                    f_status = filter_cols[1].multiselect(
                        "สถานะ",
                        options=sorted(df["สถานะ"].dropna().unique().tolist()),
                        key="f_status",
                    )
                    f_district = filter_cols[2].multiselect(
                        "เขต",
                        options=sorted(df["เขต"].dropna().unique().tolist()),
                        key="f_district",
                    )

                filtered_df = df.copy()
                if f_type:
                    filtered_df = filtered_df[filtered_df["ประเภท"].isin(f_type)]
                if f_status:
                    filtered_df = filtered_df[filtered_df["สถานะ"].isin(f_status)]
                if f_district:
                    filtered_df = filtered_df[filtered_df["เขต"].isin(f_district)]

                if len(filtered_df) != total_rows:
                    st.caption(f"แสดงผลหลังกรอง: **{len(filtered_df)}** รายการ")

                st.dataframe(
                    filtered_df,
                    use_container_width=True,
                    height=500,
                    column_config={
                        "Link": st.column_config.LinkColumn("Link"),
                    },
                )
                with open(OUTPUT_PATH, "rb") as f:
                    st.download_button(
                        "Download Output.csv",
                        f,
                        file_name="Output.csv",
                        mime="text/csv",
                    )

                st.markdown("---")
                if not st.session_state.confirm_reset:
                    if st.button(
                        "Reset Output.csv", disabled=is_running, type="secondary"
                    ):
                        st.session_state.confirm_reset = True
                        st.rerun()
                else:
                    st.warning(
                        "All records in Output.csv will be permanently deleted. This action cannot be undone."
                    )
                    _rc1, _rc2 = st.columns(2)
                    with _rc1:
                        if st.button("Confirm Reset", type="primary"):
                            with csv_lock:
                                with open(
                                    OUTPUT_PATH, "w", newline="", encoding="utf-8-sig"
                                ) as _f:
                                    csv.DictWriter(
                                        _f, fieldnames=OUTPUT_HEADERS
                                    ).writeheader()
                            st.session_state.confirm_reset = False
                            st.rerun()
                    with _rc2:
                        if st.button("Cancel"):
                            st.session_state.confirm_reset = False
                            st.rerun()
            except Exception as e:
                st.caption(f"Cannot load preview: {e}")

    if is_running:
        time.sleep(2)
        st.rerun()


if __name__ == "__main__":
    main_ui()
