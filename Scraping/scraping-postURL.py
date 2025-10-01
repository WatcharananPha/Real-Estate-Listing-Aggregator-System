import time
import os
import csv
from dotenv import load_dotenv
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

load_dotenv()
FACEBOOK_EMAIL = os.getenv('FACEBOOK_EMAIL')
FACEBOOK_PASSWORD = os.getenv('FACEBOOK_PASSWORD')
PAGE_URL = 'https://m.facebook.com/groups/322977734828852'
OUTPUT_CSV_FILE = 'Facebook_group_post_urls_last5y.csv'
PROFILE_PATH = r'/root/chrome-profile'

def login_to_facebook(driver):
    driver.get("https://m.facebook.com")
    time.sleep(3)
    selectors = [
        "button[data-cookiebanner='accept_button_dialog']",
        "button[title='Allow all cookies']",
        "button[title='Accept All']",
        "button[aria-label='Allow all cookies']"
    ]
    for s in selectors:
        btns = driver.find_elements(By.CSS_SELECTOR, s)
        if btns and btns[0].is_displayed():
            btns[0].click()
            time.sleep(2)
            break
    email = driver.find_elements(By.ID, "m_login_email")
    pwd = driver.find_elements(By.ID, "m_login_password")
    if not email:
        email = driver.find_elements(By.ID, "email")
        pwd = driver.find_elements(By.ID, "pass")
    if email and pwd:
        email[0].clear()
        email[0].send_keys(FACEBOOK_EMAIL)
        pwd[0].send_keys(FACEBOOK_PASSWORD)
        pwd[0].send_keys(Keys.RETURN)
        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='main'], #MRoot, #viewport, #m_group_stories_container")))

def collect_group_posts_last_5y(driver, group_url):
    cutoff_epoch = int(time.time() - 5 * 365 * 24 * 60 * 60)
    driver.get(group_url)
    WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='main'], #m_group_stories_container, article")))
    time.sleep(3)
    group_id = group_url.rstrip('/').split('/')[-1]
    urls = set()
    prev_len = 0
    stagnant = 0
    reached_old = False
    loops = 0
    while True:
        data = driver.execute_script("""
            var gid = arguments[0];
            var anchors = Array.from(document.querySelectorAll("a[href*='/groups/']"));
            var out = [];
            for (var i=0;i<anchors.length;i++){
                var a = anchors[i];
                var href = a.getAttribute("href") || "";
                if(!href) continue;
                if(href.indexOf("/groups/"+gid+"/") === -1 && href.indexOf("story.php") === -1) continue;
                if(href.indexOf("/reel") !== -1 || href.indexOf("/reels/") !== -1 || href.indexOf("/videos/") !== -1 || href.indexOf("/watch/") !== -1 || href.indexOf("video_id=") !== -1) continue;
                if(href.indexOf("/permalink/") === -1 && href.indexOf("/posts/") === -1 && href.indexOf("story.php") === -1) continue;
                if(href.indexOf("http")!==0){ href = location.origin + href; }
                href = href.split("?")[0];
                var el = a.closest("article") || a.closest("div");
                var ut = null;
                if(el){
                    var t = el.querySelector("abbr[data-utime], time[data-utime]");
                    if(t){ ut = parseInt(t.getAttribute("data-utime")); }
                    if(!ut){
                        var t2 = el.querySelector("time[datetime], abbr[datetime]");
                        if(t2 && t2.getAttribute("datetime")){
                            var ms = Date.parse(t2.getAttribute("datetime"));
                            if(!isNaN(ms)) ut = Math.floor(ms/1000);
                        }
                    }
                }
                out.push([href, ut]);
            }
            return out;
        """, group_id)
        for href, ut in data:
            if ut is None or ut >= cutoff_epoch:
                urls.add(href)
        if any((ut is not None and ut < cutoff_epoch) for _, ut in data):
            reached_old = True
        see_more = driver.find_elements(By.XPATH, "//div[@role='button' and (contains(., 'See more') or contains(., 'See More'))]")
        if see_more:
            see_more[0].click()
            time.sleep(1.2)
        driver.execute_script("window.scrollBy(0, arguments[0]);", 1200)
        time.sleep(2.0)
        curr_len = len(urls)
        if curr_len == prev_len:
            stagnant += 1
        else:
            stagnant = 0
        prev_len = curr_len
        loops += 1
        if (reached_old and stagnant >= 2) or stagnant >= 6 or loops >= 3000:
            break
    return urls

def main():
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=en-US")
    options.add_argument(f"--user-data-dir={PROFILE_PATH}")
    options.page_load_strategy = "eager"
    driver = uc.Chrome(
        options=options,
        use_subprocess=True,
        driver_executable_path="/usr/local/bin/chromedriver",
        browser_executable_path="/usr/bin/google-chrome-stable"
    )
    driver.set_page_load_timeout(90)
    driver.set_script_timeout(90)
    driver.command_executor._client_config.timeout = 300
    login_to_facebook(driver)
    all_urls = collect_group_posts_last_5y(driver, PAGE_URL)
    driver.quit()
    if all_urls:
        with open(OUTPUT_CSV_FILE, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['PostURL'])
            for u in sorted(all_urls):
                w.writerow([u])

if __name__ == "__main__":
    main()
