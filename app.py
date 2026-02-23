import os
import time
import threading
import json
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

# Environment variables
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')  # chat ID mặc định cho background updates
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# File lưu trạng thái cũ (hỗ trợ nhiều mã vận đơn)
STATUS_FILE = 'tracking_status.json'

# Load trạng thái cũ
def load_status():
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, 'r') as f:
            return json.load(f)
    return {}

# Save trạng thái
def save_status(status_dict):
    with open(STATUS_FILE, 'w') as f:
        json.dump(status_dict, f, ensure_ascii=False, indent=2)

# Tạo WebDriver
def get_driver():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

# Hàm scrape trạng thái cho một mã vận đơn
def scrape_jt_status(billcode, last4_phone):
    driver = get_driver()
    url = f"https://jtexpress.vn/vi/tracking?type=track&billcode={billcode}&cellphone={last4_phone}"
    driver.get(url)
    
    wait = WebDriverWait(driver, 20)
    
    # Xử lý popup xác thực (nếu có)
    try:
        # Chờ input xuất hiện (placeholder thường chứa "4 số cuối" hoặc "Xác thực")
        phone_input = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, 'input[placeholder*="4 số cuối"], input[type="text"][maxlength="4"], input[name="cellphone"], .modal input')
        ))
        phone_input.clear()
        phone_input.send_keys(last4_phone)
        
        # Tìm và click nút xác thực (text "Xác thực", "Tra cứu", class btn, v.v.)
        confirm_btn = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'button:contains("Xác thực"), button:contains("Tra cứu"), button.btn-primary, button[type="submit"], .modal button')
        ))
        confirm_btn.click()
        
        # Chờ popup đóng hoặc trang load kết quả
        time.sleep(3)  # chờ JS xử lý
    except Exception as e:
        print(f"Popup handling error or no popup: {e}")
    
    # Lấy trạng thái mới nhất từ danh sách hành trình
    try:
        # Chờ danh sách hành trình load
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.tracking-list, #tracking-detail, ul.track-list, .status-list')))
        
        # Lấy items (thường ul > li, item đầu là mới nhất)
        items = driver.find_elements(By.CSS_SELECTOR, '.tracking-list li, #tracking-detail li, ul li.track-item')
        if items:
            latest = items[0]
            # Thời gian: thường class time/date hoặc span đầu
            time_elem = latest.find_element(By.CSS_SELECTOR, '.time, .date, span.time, .track-time')
            time_str = time_elem.text.strip()
            
            # Nội dung trạng thái
            content_elem = latest.find_element(By.CSS_SELECTOR, '.status, .desc, .content, .track-desc, p, div.status-text')
            content_str = content_elem.text.strip()
            
            driver.quit()
            return time_str, content_str, True  # True = thành công
    except Exception as e:
        print(f"Scrape error: {e}")
    
    driver.quit()
    return None, None, False

# Gửi thông báo
def send_message(chat_id, text):
    try:
        bot.send_message(chat_id, text)
    except Exception as e:
        print(f"Telegram send error: {e}")

# Background monitor (cho mã mặc định)
DEFAULT_BILLCODE = "861396533622"
DEFAULT_LAST4 = "6719"

def background_monitor():
    status_data = load_status()
    while True:
        time.sleep(900)  # 15 phút
        time_str, content, success = scrape_jt_status(DEFAULT_BILLCODE, DEFAULT_LAST4)
        if success:
            key = f"{DEFAULT_BILLCODE}_{DEFAULT_LAST4}"
            old = status_data.get(key, "")
            new = f"{time_str}|{content}"
            if new != old:
                msg = f"[AUTO] Cập nhật mới cho {DEFAULT_BILLCODE}: {content} - Lúc: {time_str}"
                send_message(CHAT_ID, msg)
                status_data[key] = new
                save_status(status_data)

# Telegram commands
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "Bot giám sát J&T Express sẵn sàng!\nDùng lệnh: /check <mã_vận_đơn> <4_số_cuối_SĐT>\nVí dụ: /check 861396533622 6719")

@bot.message_handler(commands=['check'])
def check(message):
    try:
        args = message.text.split()[1:]
        if len(args) != 2:
            bot.reply_to(message, "Sai cú pháp! Ví dụ: /check 861396533622 6719")
            return
        
        billcode, last4 = args[0].strip(), args[1].strip()
        if not billcode.isdigit() or len(last4) != 4 or not last4.isdigit():
            bot.reply_to(message, "Mã vận đơn phải là số, 4 số cuối SĐT phải là 4 chữ số.")
            return
        
        bot.reply_to(message, f"Đang tra cứu vận đơn {billcode}...")
        
        time_str, content, success = scrape_jt_status(billcode, last4)
        if success:
            msg = f"Trạng thái mới nhất:\n{content}\nLúc: {time_str}"
            # Optional: lưu trạng thái nếu muốn theo dõi lâu dài
            status_data = load_status()
            key = f"{billcode}_{last4}"
            status_data[key] = f"{time_str}|{content}"
            save_status(status_data)
        else:
            msg = "Không lấy được dữ liệu. Có thể mã đơn sai, hoặc trang thay đổi. Kiểm tra lại!"
        
        bot.reply_to(message, msg)
    except Exception as e:
        bot.reply_to(message, f"Lỗi: {str(e)}")

# Khởi động polling Telegram trong thread riêng (vì Flask chạy blocking)
def start_telegram_polling():
    bot.infinity_polling(skip_pending=True)

# Flask route giữ alive
@app.route('/')
def home():
    return 'J&T Tracker is running!'

if __name__ == '__main__':
    # Khởi động background monitor
    monitor_thread = threading.Thread(target=background_monitor, daemon=True)
    monitor_thread.start()
    
    # Khởi động Telegram polling
    tg_thread = threading.Thread(target=start_telegram_polling, daemon=True)
    tg_thread.start()
    
    # Chạy Flask
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
