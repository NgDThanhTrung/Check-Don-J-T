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

# Logging để debug trên Render
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Token từ biến môi trường Render
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN chưa được set trong Environment Variables!")

bot = telebot.TeleBot(TOKEN, threaded=False)  # threaded=False rất quan trọng cho Render free tier

app = Flask(__name__)

# Webhook URL dùng domain Render cung cấp
RENDER_HOST = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if not RENDER_HOST:
    raise ValueError("RENDER_EXTERNAL_HOSTNAME không tồn tại - kiểm tra lại trên Render")

WEBHOOK_URL = f"https://{RENDER_HOST}/{TOKEN}"

def init_driver():
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = uc.Chrome(options=options, use_subprocess=True)
    return driver

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message,
                 "Xin chào! Bot tra cứu tình trạng đơn hàng J&T Express.\n\n"
                 "Lệnh:\n"
                 "/check <mã vận đơn> <4 số cuối SĐT>\n"
                 "Ví dụ: /check 861396533622 6719")

@bot.message_handler(commands=['check'])
def check_tracking(message):
    try:
        parts = message.text.strip().split()
        if len(parts) != 3:
            bot.reply_to(message, "Sai cú pháp!\nDùng: /check <mã đơn> <4 số cuối SĐT>")
            return

        _, billcode, cellphone = parts

        if len(cellphone) != 4 or not cellphone.isdigit():
            bot.reply_to(message, "4 số cuối SĐT phải là 4 chữ số!")
            return

        url = f"https://jtexpress.vn/vi/tracking?type=track&billcode={billcode}&cellphone={cellphone}"

        bot.reply_to(message, f"Đang tra cứu đơn {billcode}... ⏳ (có thể mất 10-40 giây)")

        driver = init_driver()
        try:
            driver.get(url)

            # Chờ phần result-vandon-item xuất hiện → đảm bảo JS load xong
            WebDriverWait(driver, 40).until(
                EC.presence_of_element_located((By.CLASS_NAME, "result-vandon-item"))
            )
            time.sleep(2)  # buffer an toàn

            # Tìm tab-content (nếu không có thì fallback lấy body)
            try:
                tab_content = driver.find_element(By.CLASS_NAME, "tab-content")
            except:
                tab_content = driver.find_element(By.TAG_NAME, "body")
                logger.warning("Không tìm thấy .tab-content, fallback dùng body")

            # Lấy tất cả các item trạng thái
            items = tab_content.find_elements(By.CLASS_NAME, "result-vandon-item")

            if not items:
                bot.reply_to(message, "Không tìm thấy thông tin trạng thái đơn hàng.\nCó thể mã đơn / số ĐT sai hoặc trang đang lỗi.")
                return

            status_lines = []
            for item in items:
                try:
                    # Lấy thời gian và ngày (thường là 2 span SFProDisplayBold)
                    time_elements = item.find_elements(By.CSS_SELECTOR, "span.text-[14px].SFProDisplayBold")
                    time_part = time_elements[0].text.strip() if time_elements else ""
                    date_part = time_elements[1].text.strip() if len(time_elements) > 1 else ""

                    # Lấy phần mô tả trạng thái (div cuối cùng trong item)
                    description_divs = item.find_elements(By.TAG_NAME, "div")
                    description = description_divs[-1].text.strip() if description_divs else "Không có mô tả"

                    line = f"{date_part} {time_part}: {description}"
                    status_lines.append(line.strip())

                except Exception as e:
                    logger.debug(f"Lỗi parse 1 item: {e}")
                    continue

            if not status_lines:
                bot.reply_to(message, "Không trích xuất được trạng thái chi tiết.")
                return

            # Ghép kết quả
            reply = (
                f"📦 **Tình trạng đơn hàng {billcode}**\n"
                f"   SĐT: ****{cellphone}\n\n"
                + "\n".join(status_lines) + "\n\n"
                f"(Nguồn: J&T Express - cập nhật lúc {time.strftime('%H:%M %d/%m/%Y')})"
            )

            if len(reply) > 3800:
                reply = reply[:3750] + "\n... (quá dài, xem đầy đủ trên website J&T)"

            bot.reply_to(message, reply)

        finally:
            driver.quit()

    except Exception as e:
        logger.error(f"Lỗi tổng thể khi check: {e}", exc_info=True)
        bot.reply_to(message, f"Lỗi xảy ra: {str(e)[:150]}\nThử lại sau vài phút hoặc kiểm tra mã đơn.")

# Webhook route
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
    return "Bot tra cứu J&T Express (Selenium) đang hoạt động!"

if __name__ == "__main__":
    try:
        bot.remove_webhook()
        bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Webhook đã set thành công: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Lỗi set webhook: {e}")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
