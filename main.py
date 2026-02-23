import os
import logging
from flask import Flask, request, abort
import telebot
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN chưa set!")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

@app.route('/')
def health():
    return "Bot tra cứu J&T Express VN đang chạy!"

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    abort(403)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "Chào bạn! Bot tra cứu đơn J&T Express VN.\n"
                          "Lệnh: /check <mã vận đơn> <4 số cuối SĐT>\n"
                          "Ví dụ: /check 861396533622 6719")

@bot.message_handler(commands=['help'])
def help_cmd(message):
    bot.reply_to(message, "Cách dùng: /check mãvậnđơn 4sốcuốiSDT\n"
                          "Ví dụ: /check JT123456789VN 1234")

@bot.message_handler(commands=['check'])
def check(message):
    try:
        parts = message.text.split()
        if len(parts) != 3:
            bot.reply_to(message, "Sai cú pháp! Dùng: /check <mã vận đơn> <4 số cuối SĐT>\nVí dụ: /check 861396533622 6719")
            return

        billcode = parts[1].strip()
        cellphone = parts[2].strip()
        if len(cellphone) != 4 or not cellphone.isdigit():
            bot.reply_to(message, "4 số cuối SĐT phải là 4 chữ số!")
            return

        url = f"https://jtexpress.vn/vi/tracking?type=track&billcode={billcode}&cellphone={cellphone}"
        logger.info(f"Check: {billcode} - {cellphone}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            page = browser.new_page()
            page.goto(url, timeout=40000, wait_until="networkidle")

            # Chờ timeline load (tối đa 30s)
            try:
                page.wait_for_selector('.result-vandon-item', timeout=30000)
            except PlaywrightTimeoutError:
                bot.reply_to(message, "Không tìm thấy thông tin đơn hàng hoặc trang load chậm.\n"
                                      "Kiểm tra mã + SĐT, hoặc thử lại sau.")
                browser.close()
                return

            # Parse tất cả items
            items = page.query_selector_all('.result-vandon-item')
            if not items:
                bot.reply_to(message, "Không có trạng thái nào được tìm thấy.")
                browser.close()
                return

            statuses = []
            for item in items:
                time_spans = item.query_selector_all('span.text-[14px].SFProDisplayBold')
                if len(time_spans) < 2:
                    continue
                hour = time_spans[0].inner_text().strip()
                date = time_spans[1].inner_text().strip()

                # Mô tả: div cuối cùng
                desc_div = item.query_selector('div:last-child')
                desc = desc_div.inner_text().strip() if desc_div else "Không có mô tả"

                statuses.append(f"{date} {hour}: {desc}")

            # Từ mới nhất → cũ nhất (trang J&T thường newest first)
            reply_lines = [f"📦 Đơn: {billcode}"]
            reply_lines.extend(statuses)

            text = "\n".join(reply_lines)
            if len(text) > 3800:
                text = text[:3750] + "\n... (dữ liệu dài, xem đầy đủ tại jtexpress.vn)"

            bot.reply_to(message, text or "Không có trạng thái cập nhật.")

            browser.close()

    except Exception as e:
        logger.error(f"Lỗi: {str(e)}")
        bot.reply_to(message, f"Lỗi xảy ra: {str(e)}\nThử lại hoặc kiểm tra mã/SĐT.")

if __name__ == '__main__':
    bot.remove_webhook()
    domain = os.environ.get('RENDER_EXTERNAL_HOSTNAME') or f"{os.environ.get('RENDER_SERVICE_NAME', 'jt-bot')}.onrender.com"
    webhook_url = f"https://{domain}/{TOKEN}"
    logger.info(f"Set webhook: {webhook_url}")
    bot.set_webhook(webhook_url)

    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
