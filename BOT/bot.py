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

ADMIN_ID = 1168625514      # Admin ID
CREATOR_ID = 6171433145    # Buyurtma yaratuvchi ID
EXECUTOR_ID = 1477633344    # Bajaruvchi ID (Haqiqiy ID yozing)

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
            status TEXT DEFAULT 'pending'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS allowed_users (
            user_id INTEGER PRIMARY KEY
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

def add_allowed_user(user_id):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO allowed_users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def get_all_allowed_users():
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM allowed_users")
    rows = cursor.fetchall()
    conn.close()
    
    users = {int(ADMIN_ID), int(CREATOR_ID), int(EXECUTOR_ID)}
    for row in rows:
        try:
            users.add(int(row[0]))
        except Exception:
            pass
    return list(users)

# === BOT HANDLERLARI ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    allowed_list = get_all_allowed_users()

    if user_id in allowed_list:
        if user_id == int(ADMIN_ID):
            await update.message.reply_text("Assalomu alaykum Bosh Admin! Tizim nazoratingiz ostida.")
        elif user_id == int(CREATOR_ID):
            await update.message.reply_text("Assalomu alaykum! Siz buyurtma yaratuvchisiz.\n\nYangi buyurtma kiritish uchun /zakaz buyrug'ini yuboring.")
        elif user_id == int(EXECUTOR_ID):
            await update.message.reply_text("Assalomu alaykum! Siz buyurtmalarni bajaruvchi xodimsiz. Buyurtmalar kelishini kuting.")
        else:
            await update.message.reply_text("Assalomu alaykum! Siz tizimdasiz (kuzatuvchi rejimida).")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton("✅ Ruxsat berish", callback_data=f"allow_{user_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    username_str = f"@{user.username}" if user.username else "Username yo'q"
    req_text = (
        f"🔔 YANGI FOYDALANUVCHI RUXSAT SO'RAMOQDA!\n\n"
        f"👤 Ismi: {user.full_name}\n"
        f"🌐 Username: {username_str}\n"
        f"🆔 Telegram ID: `{user_id}`"
    )

    try:
        await context.bot.send_message(chat_id=int(ADMIN_ID), text=req_text, reply_markup=reply_markup, parse_mode="Markdown")
        await update.message.reply_text("⏳ So'rovingiz Adminga yuborildi. Ruxsat berilishini kuting...")
    except Exception as e:
        logging.error(f"Start handler error: {e}")
        await update.message.reply_text("❌ So'rov yuborishda xatolik yuz berdi.")

    return ConversationHandler.END

async def start_zakaz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != int(CREATOR_ID):
        await update.message.reply_text("❌ Faqat maxsus buyurtma yaratuvchi xodim `/zakaz` bera oladi!")
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
    try:
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

        all_users = get_all_allowed_users()
        for u_id in all_users:
            try:
                await context.bot.send_message(chat_id=u_id, text=msg_text, reply_markup=reply_markup)
            except Exception as e:
                logging.error(f"User {u_id} ga xabar yuborishda xatolik: {e}")

        await update.message.reply_text("✅ Buyurtma saqlandi va barcha a'zolarga yuborildi!")
    except Exception as e:
        logging.error(f"get_deadline error: {e}")
        await update.message.reply_text("❌ Buyurtmani saqlashda xatolik yuz berdi.")
        
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Jarayon bekor qilindi.")
    return ConversationHandler.END

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    user_id = user.id
    data = query.data

    if data.startswith("allow_"):
        if user_id != int(ADMIN_ID):
            await query.answer("🚫 Faqat Admin yangi foydalanuvchiga ruxsat bera oladi!", show_alert=True)
            return

        target_user_id = int(data.split("allow_")[1])
        add_allowed_user(target_user_id)

        await query.edit_message_text(f"{query.message.text}\n\n✅ ADMIN TARAFIDAN RUXSAT BERILDI!")
        
        try:
            await context.bot.send_message(
                chat_id=target_user_id, 
                text="🎉 Sizga tizimdan kuzatuvchi sifatida foydalanishga ruxsat berildi! Endi kelgan buyurtmalarni ko'rib borishingiz mumkin."
            )
        except Exception:
            pass
        return

    if data.startswith("done_"):
        if user_id != int(EXECUTOR_ID):
            await query.answer("🚫 Siz faqat kuzatuvchisiz! Buyurtmani yakunlash huquqi faqat mas'ul bajaruvchida bor.", show_alert=True)
            return

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
            
            await query.edit_message_text(text=updated_text, reply_markup=None)

            admin_msg = (
                f"🔔 BUYURTMA BAJARILDI!\n\n"
                f"🆔 Zakaz ID: {order_id}\n"
                f"👤 Bajaruvchi xodim: {user.full_name}\n"
                f"🌐 Username: {username_str}"
            )
            
            for notify_id in [int(ADMIN_ID), int(CREATOR_ID)]:
                try:
                    await context.bot.send_message(chat_id=notify_id, text=admin_msg)
                except Exception:
                    pass
        else:
            await query.answer("❌ Bu buyurtma allaqachon bajarilgan!", show_alert=True)

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
