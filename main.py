import os
import logging
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, abort
import telebot

# Logging để xem lỗi trên Render dashboard
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Lấy token từ biến môi trường (phải set trên Render)
TOKEN = os.environ.get('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN chưa được thiết lập trong Environment Variables!")

# Khởi tạo bot - threaded=False rất quan trọng trên Render free tier
bot = telebot.TeleBot(TOKEN, threaded=False)

app = Flask(__name__)

# Tự động lấy domain Render cung cấp hoặc dùng biến môi trường
RENDER_HOST = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if not RENDER_HOST:
    raise ValueError("Không tìm thấy RENDER_EXTERNAL_HOSTNAME - kiểm tra lại trên Render")

WEBHOOK_URL = f"https://{RENDER_HOST}/{TOKEN}"  # dùng token làm path để an toàn hơn

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message,
                 "Xin chào! Đây là bot tra cứu tình trạng đơn hàng J&T Express Việt Nam.\n\n"
                 "Cách dùng:\n"
                 "/check <mã vận đơn> <4 số cuối số điện thoại>\n"
                 "Ví dụ: /check JT123456789VN 6789")


@bot.message_handler(commands=['check'])
def handle_check(message):
    try:
        text_parts = message.text.strip().split()
        if len(text_parts) != 3:
            bot.reply_to(message, "Cú pháp sai!\nDùng: /check <mã đơn> <4 số cuối SĐT>\nVí dụ: /check JT123456789VN 6789")
            return

        _, billcode, cellphone = text_parts

        if len(cellphone) != 4 or not cellphone.isdigit():
            bot.reply_to(message, "4 số cuối số điện thoại phải là 4 chữ số!")
            return

        url = f"https://jtexpress.vn/vi/tracking?type=track&billcode={billcode}&cellphone={cellphone}"

        bot.reply_to(message, f"Đang tra cứu đơn {billcode}... ⏳")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # =======================
        # Phần lấy dữ liệu tracking - cần điều chỉnh nếu J&T thay đổi giao diện
        # =======================
        status_text = ""

        # Thử các class phổ biến trên trang J&T
        candidates = [
            soup.find('div', class_='tracking-detail'),
            soup.find('div', class_='status-detail'),
            soup.find('div', class_='timeline'),
            soup.find('div', class_='tracking-info'),
            soup.find('section', class_='tracking-section'),
        ]

        for candidate in candidates:
            if candidate:
                status_text = candidate.get_text(separator='\n', strip=True)
                break

        # Nếu không tìm thấy → fallback lấy text chính trong body
        if not status_text:
            body = soup.body
            if body:
                lines = []
                for line in body.get_text(separator='\n').split('\n'):
                    cleaned = line.strip()
                    if len(cleaned) > 10 and 'footer' not in cleaned.lower() and 'header' not in cleaned.lower():
                        lines.append(cleaned)
                status_text = '\n'.join(lines[:30])  # giới hạn để tránh quá dài

        if not status_text.strip():
            status_text = "Không tìm thấy thông tin tracking.\nCó thể mã đơn hoặc số điện thoại không đúng, hoặc trang web đang lỗi."

        # Giới hạn độ dài tin nhắn Telegram
        if len(status_text) > 3500:
            status_text = status_text[:3450] + "\n... (xem chi tiết trên website)"

        reply = (
            f"📦 **Đơn hàng: {billcode}**\n"
            f"📱 SĐT: ****{cellphone}\n\n"
            f"{status_text}"
        )

        bot.reply_to(message, reply, parse_mode='Markdown')

    except requests.exceptions.RequestException as e:
        logger.error(f"Lỗi request: {e}")
        bot.reply_to(message, "Không kết nối được với J&T Express. Thử lại sau vài phút nhé.")
    except Exception as e:
        logger.error(f"Lỗi không xác định: {e}", exc_info=True)
        bot.reply_to(message, f"Có lỗi xảy ra: {str(e)}\nVui lòng thử lại hoặc liên hệ admin.")


# Route webhook - Telegram sẽ gửi POST request đến đây
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    else:
        abort(403)


@app.route('/')
def index():
    return "Bot tra cứu J&T Express đang hoạt động trên Render!"


if __name__ == "__main__":
    # Xóa webhook cũ và set webhook mới khi khởi động
    try:
        bot.remove_webhook()
        bot.set_webhook(url=WEBHOOK_URL)
        logger.info(f"Webhook đã được thiết lập: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Lỗi khi set webhook: {e}")

    # Chạy Flask
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
