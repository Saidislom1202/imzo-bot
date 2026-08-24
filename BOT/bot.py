import os
import sqlite3
import logging
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    ContextTypes, 
    ConversationHandler, 
    filters
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# === SOZLAMALAR ===
TOKEN = "8591659134:AAFGyN4WstAJ77vICb6wS4y9zkUXDoV_aVw"

# ID'larni o'zingizniki bilan almashtiring:
CREATOR_ADMIN_ID = 1168625514  # Buyurtma yaratuvchi (Admin)
EXECUTOR_ID = 1477633344        # Buyurtmani bajaruvchi (Tugatuvchi xodim)

# Tizimdagi barcha foydalanuvchilar ID'lari (Admin, Bajaruvchi va Kuzatuvchilar)
ALLOWED_USERS = [
    1168625514,  # Buyurtma yaratuvchi Admin
    987654321,   # Bajaruvchi xodim
    123456789,   # Kuzatuvchi 1
    111222333,   # Kuzatuvchi 2
    444555666,   # Kuzatuvchi 3
    777888999,   # Kuzatuvchi 4
    000111222    # Kuzatuvchi 5
]

GET_ID, GET_SHOWROOM, GET_DEADLINE = range(3)

# === RENDER UCHUN FLASK WEBSERVER ===
web_app = Flask('')

@web_app.route('/')
@web_app.route('/health')
def home():
    return "OK", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# === DATABASE FUNKSIYALARI ===
def init_db():
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            showroom TEXT,
            deadline TEXT,
            status TEXT DEFAULT 'pending',
            completed_by TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_order(order_id, showroom, deadline):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO orders (order_id, showroom, deadline) VALUES (?, ?, ?)", (str(order_id), showroom, deadline))
    conn.commit()
    conn.close()

# === BOT HANDLERLARI ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("🚫 Sizga ushbu botdan foydalanish uchun ruxsat berilmagan!")
        return ConversationHandler.END

    if user_id == CREATOR_ADMIN_ID:
        await update.message.reply_text("Assalomu alaykum Admin!\n\nBuyurtma yaratish uchun /zakaz buyrug'ini yuboring.")
    elif user_id == EXECUTOR_ID:
        await update.message.reply_text("Assalomu alaykum! Siz buyurtmalarni bajaruvchi xodimsiz. Buyurtmalar kelishini kuting.")
    else:
        await update.message.reply_text("Assalomu alaykum! Siz tizimdasiz (kuzatuvchi rejimida).")

    return ConversationHandler.END

async def start_zakaz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != CREATOR_ADMIN_ID:
        await update.message.reply_text("❌ Faqat maxsus Admin buyurtma yarata oladi!")
        return ConversationHandler.END

    await update.message.reply_text("📝 Zakaz ID'sini kiriting:\n(Masalan: 1245)")
    return GET_ID

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['order_id'] = update.message.text.strip()
    await update.message.reply_text("🏢 Shourum nomini kiriting:\n(Masalan: Chilonzor Showroom)")
    return GET_SHOWROOM

async def get_showroom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['showroom'] = update.message.text.strip()
    await update.message.reply_text("⏳ Muddatni kiriting:\n(Masalan: 2 kun)")
    return GET_DEADLINE

async def get_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order_id = context.user_data['order_id']
    showroom = context.user_data['showroom']
    deadline = update.message.text.strip()

    save_order(order_id, showroom, deadline)

    keyboard = [[InlineKeyboardButton("✅ Bajarildi deb belgilash", callback_data=f"done_{order_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    msg_text = (
        f"📦 YANGI BUYURTMA!\n\n"
        f"🆔 Zakaz ID: {order_id}\n"
        f"🏢 Shourum: {showroom}\n"
        f"⏳ Muddat: {deadline}"
    )

    # Barcha 7 kishiga xabar yuboriladi
    for u_id in ALLOWED_USERS:
        try:
            await context.bot.send_message(chat_id=u_id, text=msg_text, reply_markup=reply_markup)
        except Exception:
            pass

    await update.message.reply_text("✅ Buyurtma saqlandi va barcha a'zolarga yuborildi!")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Jarayon bekor qilindi.")
    return ConversationHandler.END

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    # Faqat belgilangan EXECUTOR tugmani bosa oladi
    if user.id != EXECUTOR_ID:
        await query.answer("🚫 Sizda bu buyurtmani bajarildi deb belgilash ruxsati yo'q!", show_alert=True)
        return

    data = query.data

    if data.startswith("done_"):
        order_id = data.split("done_")[1]
        username_str = f"@{user.username}" if user.username else "Username yo'q"
        full_user_name = f"{user.full_name} ({username_str})"

        original_text = query.message.text
        if "✅ BAJARILDI!" not in original_text:
            updated_text = (
                f"{original_text}\n\n"
                f"✅ BAJARILDI!\n"
                f"👤 Bajaruvchi: {full_user_name}"
            )
            # Tugmani olib tashlab, kartochkani yangilaymiz
            await query.edit_message_text(text=updated_text, reply_markup=None)

            admin_msg = (
                f"🔔 BUYURTMA BAJARILDI!\n\n"
                f"🆔 Zakaz ID: {order_id}\n"
                f"👤 Bajaruvchi xodim: {user.full_name}\n"
                f"🌐 Username: {username_str}"
            )
            try:
                await context.bot.send_message(chat_id=CREATOR_ADMIN_ID, text=admin_msg)
            except Exception:
                pass
        else:
            await query.answer("❌ Bu buyurtma allaqachon bajarilgan!", show_alert=True)

# === BOTNI ISHGA TUSHIRISH ===
if __name__ == "__main__":
    init_db()
    keep_alive()

    app = ApplicationBuilder().token(TOKEN).build()

    zakaz_handler = ConversationHandler(
        entry_points=[CommandHandler('zakaz', start_zakaz)],
        states={
            GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_id)],
            GET_SHOWROOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_showroom)],
            GET_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deadline)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(CommandHandler('start', start))
    app.add_handler(zakaz_handler)
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Bot muvaffaqiyatli ishga tushdi...")
    app.run_polling()
