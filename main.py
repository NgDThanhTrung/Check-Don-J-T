import os
import logging
import time
from flask import Flask, request, abort
import telebot
from telebot import types
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

# Logging để dễ debug trên Render
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN chưa được thiết lập!")

bot = telebot.TeleBot(TOKEN, threaded=False)

app = Flask(__name__)

RENDER_HOST = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if not RENDER_HOST:
    raise ValueError("RENDER_EXTERNAL_HOSTNAME không tồn tại!")

WEBHOOK_URL = f"https://{RENDER_HOST}/{TOKEN}"

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

        bot.reply_to(message, f"Đang tra cứu đơn {billcode}... ⏳ (thường 10–40 giây)")

        status_lines = None

        # Thử 2 lần nếu fail lần đầu (tăng độ ổn định trên Render)
        for attempt in range(2):
            try:
                with sync_playwright() as p:
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
                    page.goto(url, wait_until='networkidle', timeout=60000)

                    # Chờ selector chính
                    page.wait_for_selector('.result-vandon-item', timeout=50000)

                    time.sleep(2)  # chờ render hoàn tất

                    items = page.query_selector_all('.result-vandon-item')

                    if items:
                        status_lines = []
                        for item in items:
                            try:
                                time_spans = item.query_selector_all('span.text-[14px].SFProDisplayBold')
                                time_str = time_spans[0].inner_text().strip() if time_spans else ''
                                date_str = time_spans[1].inner_text().strip() if len(time_spans) > 1 else ''

                                desc_elements = item.query_selector_all('div')
                                desc = desc_elements[-1].inner_text().strip() if desc_elements else ''

                                line = f"{date_str} {time_str}: {desc}".strip()
                                if line:
                                    status_lines.append(line)
                            except:
                                continue

                    browser.close()

                    if status_lines:
                        break  # thành công → thoát loop retry

            except (PlaywrightTimeoutError, PlaywrightError) as e:
                logger.warning(f"Thử {attempt+1} thất bại: {e}")
                if attempt == 1:
                    raise  # lần thứ 2 vẫn fail → raise lỗi

        if not status_lines:
            bot.reply_to(message, "Không tìm thấy thông tin trạng thái đơn hàng.\nCó thể mã đơn / số ĐT sai hoặc trang tạm thời lỗi.")
            return

        reply = (
            f"📦 **Tình trạng đơn hàng {billcode}**\n"
            f"   SĐT: ****{cellphone}\n\n"
            + "\n".join(status_lines) + "\n\n"
            f"(Nguồn: J&T Express - {time.strftime('%d/%m/%Y %H:%M')})"
        )

        if len(reply) > 3800:
            reply = reply[:3750] + "\n... (quá dài, xem đầy đủ trên website)"

        bot.reply_to(message, reply)

    except Exception as e:
        logger.error(f"Lỗi tổng quát: {e}", exc_info=True)
        bot.reply_to(message, f"Lỗi xảy ra: {str(e)[:150]}\nThử lại sau vài phút hoặc kiểm tra mã đơn.")

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
        logger.info(f"Webhook đã set thành công: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Lỗi set webhook: {e}")

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
