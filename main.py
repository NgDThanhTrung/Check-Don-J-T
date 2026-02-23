import os
import logging
import time
from flask import Flask, request, abort
import telebot
from telebot import types
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Logging để debug trên Render logs
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Lấy token từ environment variables
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN chưa được set trong Environment Variables!")

bot = telebot.TeleBot(TOKEN, threaded=False)  # threaded=False rất quan trọng trên Render free

app = Flask(__name__)

# Lấy domain từ Render
RENDER_HOST = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if not RENDER_HOST:
    raise ValueError("RENDER_EXTERNAL_HOSTNAME không tồn tại - kiểm tra Render dashboard")

WEBHOOK_URL = f"https://{RENDER_HOST}/{TOKEN}"

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message,
                 "Xin chào! Đây là bot tra cứu tình trạng đơn hàng J&T Express.\n\n"
                 "Cách dùng:\n"
                 "/check <mã vận đơn> <4 số cuối số điện thoại>\n"
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
            bot.reply_to(message, "4 số cuối số điện thoại phải là 4 chữ số!")
            return

        url = f"https://jtexpress.vn/vi/tracking?type=track&billcode={billcode}&cellphone={cellphone}"

        bot.reply_to(message, f"Đang tra cứu đơn {billcode}... ⏳ (thường mất 10–40 giây)")

        with sync_playwright() as p:
            # Khởi động browser headless
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-setuid-sandbox',
                    '--window-size=1920,1080',
                ]
            )

            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                ignore_https_errors=True,
            )

            page = context.new_page()

            # Truy cập trang
            page.goto(url, wait_until='networkidle', timeout=60000)

            # Chờ phần kết quả tracking xuất hiện
            try:
                page.wait_for_selector('.result-vandon-item', timeout=45000)
            except PlaywrightTimeoutError:
                bot.reply_to(message, "Không tải được thông tin tracking (có thể mã đơn / SĐT sai hoặc trang lỗi).")
                browser.close()
                return

            time.sleep(2)  # buffer để render hoàn tất

            # Lấy tất cả các khối result-vandon-item
            items = page.query_selector_all('.result-vandon-item')

            if not items:
                bot.reply_to(message, "Không tìm thấy trạng thái đơn hàng.")
                browser.close()
                return

            status_lines = []

            for item in items:
                try:
                    # Lấy thời gian và ngày
                    time_spans = item.query_selector_all('span.text-[14px].SFProDisplayBold')
                    time_str = time_spans[0].inner_text().strip() if time_spans else ''
                    date_str = time_spans[1].inner_text().strip() if len(time_spans) > 1 else ''

                    # Lấy mô tả trạng thái (div cuối cùng trong item)
                    desc_elements = item.query_selector_all('div')
                    desc = desc_elements[-1].inner_text().strip() if desc_elements else 'Không có mô tả'

                    line = f"{date_str} {time_str}: {desc}"
                    if line.strip():
                        status_lines.append(line)

                except Exception as inner_e:
                    logger.debug(f"Lỗi parse 1 item: {inner_e}")
                    continue

            browser.close()

            if not status_lines:
                bot.reply_to(message, "Không trích xuất được chi tiết trạng thái.")
                return

            # Xây dựng phản hồi
            reply = (
                f"📦 **Tình trạng đơn hàng {billcode}**\n"
                f"   SĐT: ****{cellphone}\n\n"
                + "\n".join(status_lines) + "\n\n"
                f"(Nguồn: J&T Express - cập nhật {time.strftime('%d/%m/%Y %H:%M')})"
            )

            if len(reply) > 3800:
                reply = reply[:3750] + "\n... (quá dài, xem đầy đủ trên website J&T)"

            bot.reply_to(message, reply)

    except Exception as e:
        logger.error(f"Lỗi khi tra cứu: {e}", exc_info=True)
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
    return "Bot tra cứu J&T Express đang hoạt động trên Render!"

if __name__ == "__main__":
    try:
        bot.remove_webhook()
        bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Webhook đã được thiết lập: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Lỗi set webhook: {e}")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
