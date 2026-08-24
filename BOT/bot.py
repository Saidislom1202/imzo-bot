import sqlite3
import logging
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

# Adminlar ID ro'yxati (shu yerga istalgancha admin ID sini qo'shishingiz mumkin)
ADMIN_IDS = [1168625514, 987654321]  # O'zingizning va boshqa adminlarning ID raqamlari

GET_ID, GET_SHOWROOM, GET_DEADLINE = range(3)
REG_INFO = 10

def init_db():
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            showroom TEXT,
            deadline TEXT,
            status TEXT DEFAULT 'pending',
            completed_by TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_user_request(user_id, name):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (user_id, full_name, status) VALUES (?, ?, 'pending')", (user_id, name))
    conn.commit()
    conn.close()

def update_user_status(user_id, status):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET status = ? WHERE user_id = ?", (status, user_id))
    conn.commit()
    conn.close()

def get_user_status(user_id):
    if user_id in ADMIN_IDS:
        return 'approved'
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def get_approved_users():
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE status = 'approved'")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    for admin_id in ADMIN_IDS:
        if admin_id not in users:
            users.append(admin_id)
    return list(set(users))

def save_order(order_id, showroom, deadline):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO orders (order_id, showroom, deadline) VALUES (?, ?, ?)", (order_id, showroom, deadline))
    conn.commit()
    conn.close()

def complete_order(order_id, user_name):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = 'completed', completed_by = ? WHERE order_id = ? AND status = 'pending'", (user_name, order_id))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    status = get_user_status(user.id)

    if status == 'approved':
        await update.message.reply_text("Assalomu alaykum! Siz tizimdasiz.\n\nBuyurtma kiritish uchun /zakaz buyrug'ini yuboring.")
        return ConversationHandler.END
    elif status == 'rejected':
        await update.message.reply_text("🚫 Sizga botdan foydalanish uchun ruxsat berilmagan.")
        return ConversationHandler.END

    await update.message.reply_text(
        "🔒 **Sizda tizimga kirish ruxsati yo'q!**\n\n"
        "Ruxsat olish uchun **Ism, Familiyangiz va Lavozimingizni** yozib yuboring:\n"
        "(Masalan: Ali Valiyev - Chilonzor filial sotuvchisi)"
    )
    return REG_INFO

async def receive_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    info_text = update.message.text.strip()
    add_user_request(user.id, info_text)

    await update.message.reply_text("✅ Ma'lumotlaringiz adminga yuborildi. Ruxsat berilishi bilan xabar beramiz!")

    keyboard = [
        [
            InlineKeyboardButton("✅ Ruxsat berish", callback_data=f"allow_{user.id}"),
            InlineKeyboardButton("🚫 Spam / Rad etish", callback_data=f"deny_{user.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Barcha adminlarga ruxsat so'rovini yuborish
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🔔 **Yangi ruxsat so'rovi!**\n\n👤 **Telegram:** {user.full_name}\n🆔 **ID:** `{user.id}`\n📝 **Ma'lumot:** {info_text}",
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        except Exception:
            pass

    return ConversationHandler.END

async def start_zakaz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Faqat Admin buyurtma kirita oladi!")
        return ConversationHandler.END

    await update.message.reply_text("📝 **Zakaz ID'sini kiriting:**\n(Masalan: 1245)")
    return GET_ID

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['order_id'] = update.message.text.strip()
    await update.message.reply_text("🏢 **Shourum nomini kiriting:**\n(Masalan: Chilonzor Showroom)")
    return GET_SHOWROOM

async def get_showroom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['showroom'] = update.message.text.strip()
    await update.message.reply_text("⏳ **Muddatni kiriting:**\n(Masalan: 2 kun)")
    return GET_DEADLINE

async def get_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order_id = context.user_data['order_id']
    showroom = context.user_data['showroom']
    deadline = update.message.text.strip()

    try:
        save_order(order_id, showroom, deadline)

        keyboard = [[InlineKeyboardButton("✅ Bajarildi deb belgilash", callback_data=f"done_{order_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        msg_text = (
            f"📦 **YANGI BUYURTMA!**\n\n"
            f"🆔 **Zakaz ID:** `{order_id}`\n"
            f"🏢 **Shourum:** {showroom}\n"
            f"⏳ **Muddat:** {deadline}"
        )

        for u_id in get_approved_users():
            try:
                await context.bot.send_message(chat_id=u_id, text=msg_text, parse_mode="Markdown", reply_markup=reply_markup)
            except Exception:
                pass

        await update.message.reply_text("✅ Buyurtma saqlandi va barcha xodimlarga yuborildi!")
    except sqlite3.IntegrityError:
        await update.message.reply_text("❌ Bu Zakaz ID allaqachon mavjud!")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Jarayon bekor qilindi.")
    return ConversationHandler.END

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    user_id = user.id
    
    user_identifier = f"@{user.username}" if user.username else user.full_name
    data = query.data

    await query.answer()

    if data.startswith("allow_"):
        target_id = int(data.split("allow_")[1])
        update_user_status(target_id, 'approved')
        await query.edit_message_text(text=query.message.text + "\n\n✅ **Ruxsat berildi!**")
        try:
            await context.bot.send_message(chat_id=target_id, text="🎉 Tabriklaymiz! Sizga botdan foydalanish uchun ruxsat berildi.")
        except Exception:
            pass

    elif data.startswith("deny_"):
        target_id = int(data.split("deny_")[1])
        update_user_status(target_id, 'rejected')
        await query.edit_message_text(text=query.message.text + "\n\n🚫 **Rad etildi.**")

    elif data.startswith("done_"):
        if get_user_status(user_id) != 'approved':
            await query.answer("❌ Sizda bu tugmani bosish ruxsati yo'q!", show_alert=True)
            return

        order_id = data.split("done_")[1]
        if complete_order(order_id, user_identifier):
            new_text = f"{query.message.text}\n\n✅ **BAJARILDI!**\n👤 **Bajaruvchi:** {user_identifier}"
            await query.edit_message_text(text=new_text, parse_mode="Markdown")

            for u_id in get_approved_users():
                if u_id != user_id:
                    try:
                        await context.bot.send_message(
                            chat_id=u_id, 
                            text=f"📢 **Zakaz ID: {order_id}** bajarildi!\n👤 **Bajaruvchi:** {user_identifier}",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass

if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    auth_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            REG_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_user_info)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    zakaz_handler = ConversationHandler(
        entry_points=[CommandHandler('zakaz', start_zakaz)],
        states={
            GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_id)],
            GET_SHOWROOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_showroom)],
            GET_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deadline)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(auth_handler)
    app.add_handler(zakaz_handler)
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Bot muvaffaqiyatli ishga tushdi...")
    app.run_polling()