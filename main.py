# main.py
import os
import logging
from flask import Flask, request, abort
import telebot
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Logging để debug trên Render Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Lấy token từ biến môi trường (bắt buộc set trên Render)
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("Biến môi trường TELEGRAM_TOKEN chưa được thiết lập!")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot tra cứu J&T Express VN đang hoạt động trên Render!"

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    else:
        abort(403)

@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.reply_to(message,
        "Chào bạn 👋\n"
        "Tôi là bot tra cứu tình trạng đơn hàng J&T Express Việt Nam.\n\n"
        "Cách dùng:\n"
        "/check <mã vận đơn> <4 số cuối số điện thoại>\n"
        "Ví dụ: /check 861396533622 6719\n\n"
        "Lưu ý: Dữ liệu lấy trực tiếp từ trang chính thức jtexpress.vn"
    )

@bot.message_handler(commands=['help'])
def cmd_help(message):
    bot.reply_to(message,
        "Lệnh hỗ trợ:\n"
        "/start - Bắt đầu\n"
        "/help - Hiển thị hướng dẫn\n"
        "/check <mã> <4 số cuối SDT> - Tra cứu đơn hàng"
    )

@bot.message_handler(commands=['check'])
def cmd_check(message):
    try:
        args = message.text.split()
        if len(args) != 3:
            bot.reply_to(message, "Sai cú pháp!\nDùng: /check <mã vận đơn> <4 số cuối SĐT>\nVí dụ: /check 861396533622 6719")
            return

        billcode = args[1].strip()
        cellphone = args[2].strip()

        if len(cellphone) != 4 or not cellphone.isdigit():
            bot.reply_to(message, "4 số cuối số điện thoại phải là 4 chữ số!")
            return

        url = f"https://jtexpress.vn/vi/tracking?type=track&billcode={billcode}&cellphone={cellphone}"
        logger.info(f"Tra cứu: {billcode} - {cellphone} từ user {message.from_user.id}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            page = browser.new_page()

            # Load trang với timeout dài hơn một chút
            page.goto(url, timeout=45000, wait_until="networkidle")

            # Chờ phần timeline xuất hiện
            try:
                page.wait_for_selector('.result-vandon-item', timeout=30000)
            except PlaywrightTimeoutError:
                bot.reply_to(message, "Không tìm thấy thông tin đơn hàng.\n"
                                      "Kiểm tra lại mã vận đơn và 4 số cuối SĐT, hoặc thử lại sau.")
                browser.close()
                return

            items = page.query_selector_all('.result-vandon-item')
            if not items:
                bot.reply_to(message, "Không có trạng thái nào được tìm thấy cho đơn hàng này.")
                browser.close()
                return

            statuses = []
            for item in items:
                time_spans = item.query_selector_all('span.text-[14px].SFProDisplayBold')
                if len(time_spans) < 2:
                    continue

                hour = time_spans[0].inner_text().strip()
                date = time_spans[1].inner_text().strip()

                desc_div = item.query_selector('div:last-child')
                desc = desc_div.inner_text().strip() if desc_div else "Không có mô tả"

                statuses.append(f"{date} {hour}: {desc}")

            # Tạo nội dung reply
            reply_lines = [f"📦 Đơn hàng: {billcode}"]
            reply_lines.extend(statuses)  # đã là thứ tự mới nhất → cũ nhất

            text = "\n".join(reply_lines)

            if len(text) > 3800:
                text = text[:3750] + "\n\n... (dữ liệu dài, xem đầy đủ tại: " + url + ")"

            if not statuses:
                text += "\n\nHiện tại chưa có cập nhật trạng thái."

            bot.reply_to(message, text)

            browser.close()

    except Exception as e:
        logger.error(f"Lỗi khi xử lý /check: {str(e)}", exc_info=True)
        bot.reply_to(message, "Đã xảy ra lỗi khi tra cứu.\nVui lòng thử lại sau hoặc kiểm tra mã vận đơn / số điện thoại.")

if __name__ == '__main__':
    # Xóa webhook cũ nếu có (an toàn khi redeploy)
    try:
        bot.remove_webhook()
    except:
        pass

    # Tự động lấy domain từ Render
    domain = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
    if not domain:
        service_name = os.environ.get('RENDER_SERVICE_NAME', 'jt-bot')
        domain = f"{service_name}.onrender.com"

    webhook_url = f"https://{domain}/{TOKEN}"
    logger.info(f"Đặt webhook tại: {webhook_url}")

    bot.set_webhook(webhook_url)

    # Trên Render không chạy app.run(), gunicorn sẽ xử lý
    # Chỉ giữ để test local nếu cần
    # port = int(os.environ.get('PORT', 5000))
    # app.run(host='0.0.0.0', port=port)
