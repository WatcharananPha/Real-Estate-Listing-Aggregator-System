import time
import os
import csv
from pathlib import Path
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from dotenv import load_dotenv

load_dotenv()
FACEBOOK_EMAIL = os.getenv('FACEBOOK_EMAIL')
FACEBOOK_PASSWORD = os.getenv('FACEBOOK_PASSWORD')

INPUT_LINKS_CSV = Path("CSV_file/Facebook_post_urls.csv")
OUTPUT_DETAILS_CSV = Path("scraped_full_content.csv")
PROFILE_PATH = Path(os.getcwd()) / 'fb_chrome_profile'

WAIT = 30

def click_cookie(driver):
    sels = [
        'div[aria-label*="cookie"] span[dir="auto"]',
        'div[aria-label*="Cookie"] span[dir="auto"]',
        'button[data-cookiebanner="accept_button"]',
        'div[role="dialog"] button'
    ]
    for s in sels:
        try:
            btns = driver.find_elements(By.CSS_SELECTOR, s)
            for btn in btns:
                if "allow" in btn.text.lower() or "accept" in btn.text.lower() or "ยอมรับ" in btn.text:
                    btn.click()
                    return
        except:
            pass

def login(driver):
    driver.get("https://www.facebook.com/?locale=en_US")
    time.sleep(3)
    click_cookie(driver)
    if "login" in driver.current_url.lower():
        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "email"))).send_keys(FACEBOOK_EMAIL)
            driver.find_element(By.ID, "pass").send_keys(FACEBOOK_PASSWORD)
            driver.find_element(By.NAME, "login").click()
            time.sleep(5)
        except:
            pass
    try:
        WebDriverWait(driver, WAIT).until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="main"]')))
    except:
        pass

def expand_see_more(driver):
    driver.execute_script("""
        var buttons = document.querySelectorAll('div[role="button"]');
        for(var i=0; i<buttons.length; i++){
            var t = buttons[i].innerText;
            if(t && (t.includes('See more') || t.includes('ดูเพิ่มเติม'))){
                buttons[i].click();
            }
        }
    """)
    time.sleep(1)

def extract_content_precise(driver):
    script = """
        function getText() {
            var main = document.querySelector("div[role='main']");
            if (!main) return "";
            var msgDiv = main.querySelector("div[data-ad-preview='message']");
            if (msgDiv) return msgDiv.innerText;
            var actions = Array.from(main.querySelectorAll("div[role='button']")).find(el => 
                el.innerText.includes("Like") || el.innerText.includes("ถูกใจ") || 
                el.innerText.includes("Comment") || el.innerText.includes("แสดงความคิดเห็น")
            );
            var contentCandidate = "";
            var bestLength = 0;
            var textDivs = main.querySelectorAll("div[dir='auto'], span[dir='auto']");
            for(var i=0; i<textDivs.length; i++) {
                var el = textDivs[i];
                if (actions && (el.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_PRECEDING) === 0) {
                    continue;
                }
                var txt = el.innerText.trim();
                if(txt.length > 0 && 
                   !txt.includes("Like") && 
                   !txt.includes("Comment") && 
                   !txt.includes("Share") &&
                   !txt.match(/^\d+ (Comments|Shares)$/) &&
                   !txt.match(/^(All|Most relevant)$/) 
                ) {
                    if (txt.length > bestLength) {
                        bestLength = txt.length;
                        contentCandidate = txt;
                    }
                }
            }
            return contentCandidate;
        }
        return getText();
    """
    return driver.execute_script(script)

def extract_date_with_hover(driver):
    try:
        script_find = """
            var all = document.querySelectorAll('a[role="link"]');
            for(var i=0; i<all.length; i++){
                var h = all[i].getAttribute('href');
                var l = all[i].getAttribute('aria-label');
                if(h && (h.includes('/posts/') || h.includes('/permalink/') || h.includes('multi_permalinks'))){
                    if(l) return all[i];
                }
            }
            return null;
        """
        el = driver.execute_script(script_find)
        if el:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
            time.sleep(0.5)
            ActionChains(driver).move_to_element(el).perform()
            time.sleep(0.5)
            val = el.get_attribute("aria-label")
            if not val: val = el.text
            return val
    except:
        pass
    return "N/A"

def get_post_data(driver, url):
    for attempt in range(2):
        try:
            driver.get(url)
            WebDriverWait(driver, WAIT).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='main']")))
            time.sleep(3)
            expand_see_more(driver)
            content = extract_content_precise(driver)
            date_str = extract_date_with_hover(driver)
            if content:
                content = content.replace("ดูน้อยลง", "").replace("See less", "").strip()
            if (content and len(content) > 10) or attempt == 1:
                return content, date_str
        except Exception as e:
            print(f"Error on {url}: {e}")
            time.sleep(2)
    return "N/A", "N/A"

if not FACEBOOK_EMAIL or not FACEBOOK_PASSWORD:
    raise SystemExit("Missing Env Vars")

opts = uc.ChromeOptions()
opts.add_argument(f'--user-data-dir={PROFILE_PATH.as_posix()}')
opts.add_argument('--disable-notifications')
opts.add_argument('--lang=en-US')
opts.page_load_strategy = "eager"

driver = uc.Chrome(options=opts, version_main=144)
driver.set_page_load_timeout(60)

try:
    login(driver)
    if not INPUT_LINKS_CSV.exists():
        print("CSV file not found.")
        exit()
    with open(INPUT_LINKS_CSV, 'r', encoding='utf-8-sig') as infile, \
         open(OUTPUT_DETAILS_CSV, 'w', newline='', encoding='utf-8-sig') as outfile:
        reader = csv.DictReader(infile)
        writer = csv.writer(outfile)
        writer.writerow(['Post_URL', 'Full_Post_Content', 'Date'])
        count = 0
        for row in reader:
            if count >= 5:
                break
            url = (row.get('PostURL') or '').strip()
            if not url:
                continue
            print(f"[{count+1}] Scraping: {url}")
            txt, dt = get_post_data(driver, url)
            if not txt:
                txt = "N/A"
            if not dt:
                dt = "N/A"
            writer.writerow([url, txt, dt])
            print(f"    -> Text Len: {len(txt)} | Date: {dt}")
            print("-" * 50)
            count += 1
            time.sleep(2)
finally:
    driver.quit()
