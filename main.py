import os
import logging
import time
from flask import Flask, request, abort
import telebot
from telebot import types
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Logging để xem lỗi trên Render
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Token từ Environment Variables
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN chưa được set!")

bot = telebot.TeleBot(TOKEN, threaded=False)  # threaded=False cho Render free

app = Flask(__name__)

# Webhook dùng domain Render cung cấp
RENDER_HOST = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if not RENDER_HOST:
    raise ValueError("RENDER_EXTERNAL_HOSTNAME không tồn tại!")

WEBHOOK_URL = f"https://{RENDER_HOST}/{TOKEN}"

def init_driver():
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

    try:
        driver = uc.Chrome(
            options=options,
            use_subprocess=True,
            version_main=128,                   # Chỉ định version Chrome ổn định (có thể thử 120, 121, 128)
            browser_executable_path=None,       # Tự tải Chromium nếu cần
            driver_executable_path=None         # Tự tải chromedriver
        )
        logger.info("undetected_chromedriver khởi tạo thành công")
        return driver
    except Exception as e:
        logger.error(f"Lỗi khởi tạo driver: {e}")
        raise

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message,
                 "Xin chào! Bot tra cứu tình trạng đơn J&T Express.\n\n"
                 "Lệnh: /check <mã vận đơn> <4 số cuối SĐT>\n"
                 "Ví dụ: /check 861396533622 6719")

@bot.message_handler(commands=['check'])
def check_tracking(message):
    try:
        parts = message.text.strip().split()
        if len(parts) != 3:
            bot.reply_to(message, "Sai cú pháp! Dùng: /check <mã đơn> <4 số cuối SĐT>")
            return

        _, billcode, cellphone = parts

        if len(cellphone) != 4 or not cellphone.isdigit():
            bot.reply_to(message, "4 số cuối SĐT phải là 4 chữ số!")
            return

        url = f"https://jtexpress.vn/vi/tracking?type=track&billcode={billcode}&cellphone={cellphone}"

        bot.reply_to(message, f"Đang tra cứu đơn {billcode}... ⏳ (có thể mất 15-50 giây)")

        driver = init_driver()
        try:
            driver.get(url)

            # Chờ phần kết quả tracking load
            WebDriverWait(driver, 45).until(
                EC.presence_of_element_located((By.CLASS_NAME, "result-vandon-item"))
            )
            time.sleep(3)  # chờ render thêm

            # Tìm tab-content hoặc fallback body
            try:
                container = driver.find_element(By.CLASS_NAME, "tab-content")
            except:
                container = driver.find_element(By.TAG_NAME, "body")
                logger.warning("Không tìm thấy tab-content → dùng body")

            # Lấy tất cả item trạng thái
            items = container.find_elements(By.CLASS_NAME, "result-vandon-item")

            if not items:
                bot.reply_to(message, "Không tìm thấy thông tin trạng thái.\nKiểm tra mã đơn / SĐT hoặc trang lỗi.")
                return

            status_lines = []
            for item in items:
                try:
                    # Thời gian + ngày
                    time_spans = item.find_elements(By.CSS_SELECTOR, "span.text-[14px].SFProDisplayBold")
                    time_str = time_spans[0].text.strip() if time_spans else ""
                    date_str = time_spans[1].text.strip() if len(time_spans) > 1 else ""

                    # Mô tả trạng thái (div cuối)
                    desc_div = item.find_elements(By.TAG_NAME, "div")[-1]
                    desc = desc_div.text.strip() if desc_div else "Không có mô tả"

                    line = f"{date_str} {time_str}: {desc}"
                    if line.strip():
                        status_lines.append(line)
                except:
                    continue

            if not status_lines:
                bot.reply_to(message, "Không trích xuất được chi tiết trạng thái.")
                return

            reply = (
                f"📦 **Tình trạng đơn {billcode}**\n"
                f"   SĐT: ****{cellphone}\n\n"
                + "\n".join(status_lines) + "\n\n"
                f"(Cập nhật từ J&T Express - {time.strftime('%d/%m/%Y %H:%M')})"
            )

            if len(reply) > 3800:
                reply = reply[:3750] + "\n... (xem đầy đủ trên web)"

            bot.reply_to(message, reply)

        finally:
            driver.quit()

    except Exception as e:
        logger.error(f"Lỗi tổng: {e}", exc_info=True)
        bot.reply_to(message, f"Lỗi xảy ra: {str(e)[:150]}\nThử lại sau vài phút hoặc kiểm tra mã đơn.")

# Webhook
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    abort(403)

@app.route('/')
def index():
    return "Bot J&T Tracking đang chạy!"

if __name__ == "__main__":
    try:
        bot.remove_webhook()
        bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Webhook set: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Webhook lỗi: {e}")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
