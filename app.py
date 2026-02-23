import os
import time
import threading
from flask import Flask
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import telebot

app = Flask(__name__)

# Route đơn giản để giữ server alive trên Render
@app.route('/')
def home():
    return 'OK'

# Khởi tạo Telegram Bot từ env vars
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Hàm tạo WebDriver với cấu hình cho Render
def get_driver():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

# Hàm scrape trạng thái mới nhất
def scrape_status():
    driver = get_driver()
    url = 'https://jtexpress.vn/vi/tracking?type=track&billcode=861396533622&cellphone=6719'
    driver.get(url)
    
    # Chờ và xử lý popup xác thực (giả định selector dựa trên trang J&T điển hình)
    try:
        wait = WebDriverWait(driver, 15)
        # Chờ input field cho 4 số cuối SĐT (placeholder thường là "Nhập 4 số cuối số điện thoại")
        input_elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[placeholder*="Nhập 4 số cuối"]')))
        input_elem.send_keys('6719')
        
        # Chờ và click button submit (giả định class 'btn-confirm' hoặc tương tự)
        submit_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button.btn-confirm, button[type="submit"], button[onclick*="verify"]')))
        submit_btn.click()
    except Exception as e:
        print(f"Error handling popup: {e}")  # Log lỗi nếu không có popup hoặc selector sai
    
    # Chờ tracking list load (giả định ID hoặc class 'tracking-list' hoặc 'track-detail')
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '#tracking-detail ul, .track-list ul')))
        
        # Lấy list items (giả định ul li, với class 'date-time' cho thời gian và 'content' cho nội dung)
        items = driver.find_elements(By.CSS_SELECTOR, '#tracking-detail ul li, .track-list ul li')
        if items:
            latest_item = items[0]  # Item mới nhất thường ở đầu
            time_text = latest_item.find_element(By.CSS_SELECTOR, '.date-time, .time, span.time').text.strip()
            content_text = latest_item.find_element(By.CSS_SELECTOR, '.content, .desc, span.status').text.strip()
            driver.quit()
            return time_text, content_text
    except Exception as e:
        print(f"Error scraping tracking list: {e}")
    
    driver.quit()
    return None, None

# Hàm gửi thông báo qua Telegram
def send_update(time_str, content):
    message = f"Cập nhật hành trình mới: {content} - Lúc: {time_str}"
    bot.send_message(CHAT_ID, message)

# Background task để giám sát
def monitor():
    while True:
        time.sleep(900)  # 15 phút
        new_time, new_content = scrape_status()
        if new_time and new_content:
            current_status = f"{new_time}|{new_content}"
            try:
                with open('last_status.txt', 'r') as f:
                    old_status = f.read().strip()
            except FileNotFoundError:
                old_status = ''
            
            if current_status != old_status:
                send_update(new_time, new_content)
                with open('last_status.txt', 'w') as f:
                    f.write(current_status)

# Khởi động background thread
thread = threading.Thread(target=monitor, daemon=True)
thread.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
