import os, re, json
import pandas as pd
from openai import OpenAI

TYPHOON_API_KEY = os.getenv("TYPHOON_API_KEY")

client = OpenAI(
    api_key=TYPHOON_API_KEY,
    base_url="https://api.opentyphoon.ai/v1"
)

SYSTEM_PROMPT = """คุณเป็นผู้เชี่ยวชาญถอดความหมายประกาศขายอสังหาในภาษาไทย
จงวิเคราะห์ว่าโพสต์นี้น่าจะเป็น 'เจ้าของขายเอง' หรือ 'นายหน้า/เอเจนท์'
ให้ตอบเป็น JSON เท่านั้น ตามสคีมานี้:
{
  "is_owner": true/false,
  "confidence": 0.0-1.0,
  "evidence_phrases": [string...],
  "risk_flags": [string...],
  "extracted": {
    "property_type": "บ้านเดี่ยว/ทาวน์โฮม/คอนโด/อื่นๆ?",
    "bedrooms": int|null,
    "bathrooms": int|null,
    "size_text": string|null,
    "location_text": string|null
  }
}
ตอบ JSON เท่านั้น ไม่ใส่คำอธิบายอื่น
"""

PROPERTY_PATTERNS = [
    r"บ้านเดี่ยว", r"บ้านแฝด", r"บ้าน", r"คอนโด", r"ทาวน์", r"ทาวน์โฮม",
    r"อาคารพาณิชย์", r"ตึกแถว", r"ที่ดิน", r"โกดัง", r"โรงงาน", r"อพาร์ตเมนต์",
    r"แมนชั่น", r"วิลลา", r"คฤหาสน์", r"โฮมออฟฟิศ", r"สำนักงาน", r"ออฟฟิศ",
    r"\bcondo\b", r"\bhouse\b", r"\btownhouse\b", r"\bland\b", r"\bwarehouse\b",
    r"\bapartment\b", r"\boffice\b", r"\bvilla\b", r"\bmansion\b", r"\bpenthouse\b"
]
prop_regex = [re.compile(p, flags=re.IGNORECASE) for p in PROPERTY_PATTERNS]

def normalize_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = re.sub(r"\s+", " ", s.replace("\u200b"," ")).strip()
    s = s.replace("ดูน้อยลง", "")
    return s

def is_real_estate(title: str, details: str, desc: str) -> bool:
    t = normalize_text((title or "") + " " + (details or "") + " " + (desc or ""))
    return any(r.search(t) for r in prop_regex)

def build_user_message(row):
    return f"URL: {row.get('URL','')}\nTITLE: {row.get('Title','')}\nPRICE: {row.get('Price','')}\nDETAILS: {row.get('Property_Details','')}\nDESCRIPTION:\n{row.get('Description','')}"

def call_typhoon_owner(json_input_text: str) -> dict:
    resp = client.chat.completions.create(
        model="typhoon-v2.5-30b-a3b-instruct",
        temperature=0.2,
        max_tokens=512,
        top_p=0.9,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json_input_text}
        ],
    )
    content = resp.choices[0].message.content.strip()
    content = re.sub(r"^```(?:json)?|```$", "", content).strip()
    data = json.loads(content)
    return data

def process_and_save(input_path: str, output_path: str, threshold: int = 80):
    df = pd.read_csv(input_path)
    rows = []
    for _, row in df.iterrows():
        title = normalize_text(row.get("Title",""))
        details = normalize_text(row.get("Property_Details",""))
        desc = normalize_text(row.get("Description",""))
        if not is_real_estate(title, details, desc):
            continue
        user_msg = build_user_message({"URL": row.get("URL",""), "Title": title, "Price": row.get("Price",""), "Property_Details": details, "Description": desc})
        ty = call_typhoon_owner(user_msg)
        conf = float(ty.get("confidence", 0.0))
        score = int(round(max(0.0, min(1.0, conf)) * 100))
        out = {
            "URL": row.get("URL",""),
            "Title": title,
            "Price": row.get("Price",""),
            "Property_Details": details,
            "Description": desc,
            "typhoon_is_owner": ty.get("is_owner", None),
            "typhoon_confidence": conf,
            "owner_score": score,
            "evidence_phrases": "|".join(ty.get("evidence_phrases", [])) if isinstance(ty.get("evidence_phrases", []), list) else "",
            "risk_flags": "|".join(ty.get("risk_flags", [])) if isinstance(ty.get("risk_flags", []), list) else "",
            "extracted_property_type": (ty.get("extracted") or {}).get("property_type", None),
            "extracted_bedrooms": (ty.get("extracted") or {}).get("bedrooms", None),
            "extracted_bathrooms": (ty.get("extracted") or {}).get("bathrooms", None),
            "extracted_size_text": (ty.get("extracted") or {}).get("size_text", None),
            "extracted_location_text": (ty.get("extracted") or {}).get("location_text", None)
        }
        if score >= threshold:
            rows.append(out)
    pd.DataFrame(rows).to_csv(output_path, index=False)

if __name__ == "__main__":
    input_path = r"C:\Users\kongl\Documents\GitHub\Real-Estate Listing Aggregator System\CSV_file\scraped_full_content.csv"
    output_path = r"C:\Users\kongl\Documents\GitHub\Real-Estate Listing Aggregator System\CSV_file\owner_scored.csv"
    process_and_save(input_path, output_path, threshold=80)