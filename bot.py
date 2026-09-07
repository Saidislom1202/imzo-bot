import os
import re
import sqlite3
import logging
import tempfile
from datetime import datetime, date, timedelta, time as dt_time
from zoneinfo import ZoneInfo
from threading import Thread
from flask import Flask
from openpyxl import Workbook
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise SystemExit(
        "❌ BOT_TOKEN environment o'zgaruvchisi topilmadi!\n"
        "Mahalliyda ishga tushirish uchun: setx BOT_TOKEN \"<tokeningiz>\" (PowerShell'ni qayta oching)\n"
        "Render'da: Dashboard → Environment → BOT_TOKEN qo'shing."
    )

# Yagona Admin ID (faqat ruxsatlarni boshqaradi)
ADMIN_IDS = [1168625514]

# Standart rollar oldindan belgilangan foydalanuvchilar
DEFAULT_CREATOR_ID = 7013318890            # Zakaz shakillantiruvchi
DEFAULT_EXECUTOR_IDS = [467848004, 6756726326]  # Zakaz bajaruvchilari (ikkalasi ham tasdiqlashi shart)

# Zakazni yakunlash uchun necha nafar bajaruvchi tasdiqlashi kerak
REQUIRED_CONFIRMATIONS = 2

ROLE_NAMES = {
    'creator': "📝 Zakaz yaratuvchi",
    'executor': "✅ Zakaz bajaruvchi",
    'observer': "👁 Kuzatuvchi",
}

GET_ID, GET_SHOWROOM, GET_DEADLINE = range(3)
REG_INFO = 10

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
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT,
            status TEXT DEFAULT 'pending',
            role TEXT
        )
    """)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT")
    except sqlite3.OperationalError:
        pass  # ustun allaqachon mavjud
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            showroom TEXT,
            deadline TEXT,
            deadline_date TEXT,
            status TEXT DEFAULT 'pending',
            completed_by TEXT,
            created_at TEXT,
            completed_at TEXT
        )
    """)
    for col_def in ("deadline_date TEXT", "created_at TEXT", "completed_at TEXT"):
        try:
            cursor.execute(f"ALTER TABLE orders ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass  # ustun allaqachon mavjud
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_confirmations (
            order_id TEXT,
            user_id INTEGER,
            user_name TEXT,
            chat_id INTEGER,
            message_id INTEGER,
            confirmed_at TEXT,
            PRIMARY KEY (order_id, user_id)
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

def approve_user(user_id, role):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, status, role) VALUES (?, 'approved', ?)
        ON CONFLICT(user_id) DO UPDATE SET status='approved', role=excluded.role
    """, (user_id, role))
    conn.commit()
    conn.close()

def seed_default_users():
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    defaults = [(DEFAULT_CREATOR_ID, "Zakaz yaratuvchi (standart)", "creator")]
    for idx, uid in enumerate(DEFAULT_EXECUTOR_IDS, start=1):
        defaults.append((uid, f"Zakaz bajaruvchi {idx} (standart)", "executor"))
    for uid, name, role in defaults:
        cursor.execute("""
            INSERT INTO users (user_id, full_name, status, role)
            VALUES (?, ?, 'approved', ?)
            ON CONFLICT(user_id) DO NOTHING
        """, (uid, name, role))
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

def get_user_role(user_id):
    if user_id in ADMIN_IDS:
        return 'admin'
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE user_id = ? AND status = 'approved'", (user_id,))
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

def parse_deadline(text):
    """'3 kun' / '3' -> shu kundan N kun keyin. '15.09.2026' kabi sana -> aniq sana."""
    text = text.strip()
    m = re.match(r'^(\d+)\s*(kun|kunlik)?$', text, re.IGNORECASE)
    if m:
        return date.today() + timedelta(days=int(m.group(1)))
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None

def save_order(order_id, showroom, deadline_text, deadline_date):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO orders (order_id, showroom, deadline, deadline_date, created_at) VALUES (?, ?, ?, ?, ?)",
        (order_id, showroom, deadline_text, deadline_date.isoformat(), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()

def get_order(order_id):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT order_id, showroom, deadline, deadline_date, status FROM orders WHERE order_id = ?",
        (order_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return row

def add_confirmation(order_id, user_id, user_name, chat_id, message_id):
    """Buyurtmani bajaruvchi tomonidan tasdiqlanishini qayd etadi.
    Qaytaradi: (bu yangi tasdiq bo'ldimi, shu buyurtma bo'yicha barcha tasdiqlar ro'yxati)."""
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO order_confirmations (order_id, user_id, user_name, chat_id, message_id, confirmed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (order_id, user_id, user_name, chat_id, message_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    is_new = cursor.rowcount > 0
    conn.commit()
    cursor.execute(
        "SELECT user_id, user_name, chat_id, message_id FROM order_confirmations WHERE order_id = ? ORDER BY confirmed_at",
        (order_id,)
    )
    confirmations = cursor.fetchall()
    conn.close()
    return is_new, confirmations

def format_order_base(order_id, showroom, deadline_text, deadline_date):
    return (
        f"📦 **YANGI BUYURTMA!**\n\n"
        f"🆔 **Zakaz ID:** `{order_id}`\n"
        f"🏢 **Shourum:** {showroom}\n"
        f"⏳ **Muddat:** {deadline_text} ({deadline_date.strftime('%d.%m.%Y')})"
    )

def complete_order(order_id, user_name):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE orders SET status = 'completed', completed_by = ?, completed_at = ? WHERE order_id = ? AND status = 'pending'",
        (user_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), order_id)
    )
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def cancel_order(order_id):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ? AND status = 'pending'", (order_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def generate_monthly_report(year, month):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    month_str = f"{year:04d}-{month:02d}"
    cursor.execute("""
        SELECT order_id, showroom, deadline, status, completed_by, created_at
        FROM orders
        WHERE substr(created_at, 1, 7) = ?
        ORDER BY created_at
    """, (month_str,))
    rows = cursor.fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Hisobot"
    ws.append(["Zakaz ID", "Shourum", "Muddat", "Holati", "Bajaruvchi", "Yaratilgan sana"])
    status_names = {"pending": "Kutilmoqda", "completed": "Bajarildi", "cancelled": "Bekor qilindi"}
    for order_id, showroom, deadline, status, completed_by, created_at in rows:
        ws.append([order_id, showroom, deadline, status_names.get(status, status), completed_by or "-", created_at or "-"])

    for col_cells in ws.columns:
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col_cells) + 2
        ws.column_dimensions[col_cells[0].column_letter].width = min(width, 40)

    file_path = os.path.join(tempfile.gettempdir(), f"hisobot_{month_str}.xlsx")
    wb.save(file_path)
    return file_path, len(rows)

def generate_statistics():
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("SELECT status, COUNT(*) FROM orders GROUP BY status")
    status_counts = dict(cursor.fetchall())

    cursor.execute("""
        SELECT completed_by,
               COUNT(*) AS soni,
               SUM(CASE WHEN substr(completed_at, 1, 10) > deadline_date THEN 1 ELSE 0 END) AS kechikkan
        FROM orders
        WHERE status = 'completed' AND completed_by IS NOT NULL
        GROUP BY completed_by
        ORDER BY soni DESC
    """)
    per_executor = cursor.fetchall()
    conn.close()

    total = sum(status_counts.values())
    completed = status_counts.get('completed', 0)
    pending = status_counts.get('pending', 0)
    cancelled = status_counts.get('cancelled', 0)

    text = (
        f"📊 **UMUMIY STATISTIKA**\n\n"
        f"📦 Jami zakazlar: {total}\n"
        f"✅ Bajarilgan: {completed}\n"
        f"⏳ Kutilmoqda: {pending}\n"
        f"🚫 Bekor qilingan: {cancelled}\n"
    )

    if per_executor:
        text += "\n👷 **Bajaruvchilar bo'yicha:**\n"
        for name, soni, kechikkan in per_executor:
            text += f"• {name}: {soni} ta bajargan"
            if kechikkan:
                text += f" ({kechikkan} tasi kechikib)"
            text += "\n"

    return text

# === MUDDAT NAZORATI (avtomatik kunlik eslatma) ===
async def notify_all_approved(context: ContextTypes.DEFAULT_TYPE, text: str):
    for uid in get_approved_users():
        try:
            await context.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
        except Exception:
            pass

async def daily_digest(context: ContextTypes.DEFAULT_TYPE):
    """Har kuni ertalab 9:00da hali bajarilmagan (pending) barcha zakazlar haqida eslatma yuboradi."""
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT order_id, showroom, deadline_date FROM orders
        WHERE status = 'pending' AND deadline_date IS NOT NULL
        ORDER BY deadline_date
    """)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return

    today = date.today()
    lines = []
    for order_id, showroom, deadline_date_str in rows:
        days_left = (date.fromisoformat(deadline_date_str) - today).days
        if days_left > 0:
            lines.append(f"🆔 `{order_id}` ({showroom}) — muddatga {days_left} kun qoldi")
        elif days_left == 0:
            lines.append(f"🆔 `{order_id}` ({showroom}) — muddat BUGUN tugaydi!")
        else:
            lines.append(f"🆔 `{order_id}` ({showroom}) — ⚠️ muddat {-days_left} kun oldin o'tgan!")

    text = "☀️ **Xayrli tong! Kutilayotgan buyurtmalar:**\n\n" + "\n".join(lines)
    await notify_all_approved(context, text)

# === BOT HANDLERLARI ===
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
            InlineKeyboardButton("📝 Yaratuvchi", callback_data=f"role_creator_{user.id}"),
            InlineKeyboardButton("✅ Bajaruvchi", callback_data=f"role_executor_{user.id}")
        ],
        [
            InlineKeyboardButton("👁 Kuzatuvchi", callback_data=f"role_observer_{user.id}"),
            InlineKeyboardButton("🚫 Spam / Rad etish", callback_data=f"deny_{user.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

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
    role = get_user_role(update.effective_user.id)
    if role not in ('admin', 'creator'):
        await update.message.reply_text("❌ Faqat Zakaz yaratuvchi buyurtma kirita oladi!")
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
    deadline_text = update.message.text.strip()

    deadline_date = parse_deadline(deadline_text)
    if deadline_date is None:
        await update.message.reply_text(
            "❌ Muddatni tushunmadim. Quyidagi formatlardan birida yozing:\n"
            "• Necha kundan keyin: masalan `3 kun` yoki shunchaki `3`\n"
            "• Aniq sana: masalan `15.09.2026`",
            parse_mode="Markdown"
        )
        return GET_DEADLINE

    try:
        save_order(order_id, showroom, deadline_text, deadline_date)

        keyboard = [[InlineKeyboardButton("✅ Bajarildi deb belgilash", callback_data=f"done_{order_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        msg_text = format_order_base(order_id, showroom, deadline_text, deadline_date)

        creator_id = update.effective_user.id
        for u_id in get_approved_users():
            if u_id == creator_id:
                continue
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

async def hisobot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Bu buyruq faqat Admin uchun!")
        return

    today = date.today()
    file_path, count = generate_monthly_report(today.year, today.month)

    if count == 0:
        await update.message.reply_text(f"📊 {today.month}.{today.year} oyida hali buyurtmalar yo'q.")
        os.remove(file_path)
        return

    with open(file_path, "rb") as f:
        await update.message.reply_document(
            document=f,
            filename=os.path.basename(file_path),
            caption=f"📊 {today.month}.{today.year} oyi uchun hisobot ({count} ta buyurtma)"
        )
    os.remove(file_path)

async def bekor_qilish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = get_user_role(update.effective_user.id)
    if role not in ('admin', 'creator'):
        await update.message.reply_text("❌ Faqat Zakaz yaratuvchi yoki Admin buyurtmani bekor qila oladi!")
        return

    if not context.args:
        await update.message.reply_text(
            "Foydalanish: `/bekor <Zakaz_ID>`\nMasalan: `/bekor 1245`", parse_mode="Markdown"
        )
        return

    order_id = context.args[0]
    if cancel_order(order_id):
        await update.message.reply_text(f"✅ Zakaz `{order_id}` bekor qilindi.", parse_mode="Markdown")
        await notify_all_approved(context, f"🚫 **Zakaz bekor qilindi!**\n\n🆔 `{order_id}`")
    else:
        await update.message.reply_text("❌ Bunday ID'li faol (hali bajarilmagan) buyurtma topilmadi.")

async def statistika(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Bu buyruq faqat Admin uchun!")
        return
    await update.message.reply_text(generate_statistics(), parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    data = query.data

    # Ruxsat/rol berish faqat Adminga tegishli
    if (data.startswith("role_") or data.startswith("deny_")) and user.id not in ADMIN_IDS:
        await query.answer("⛔ Sizda bu amalni bajarishga ruxsat yo'q!", show_alert=True)
        return

    if data.startswith("role_"):
        await query.answer()
        _, role, target_id_str = data.split("_")
        target_id = int(target_id_str)
        approve_user(target_id, role)
        role_label = ROLE_NAMES.get(role, role)
        await query.edit_message_text(text=query.message.text + f"\n\n✅ **Ruxsat berildi!** Rol: {role_label}")
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"🎉 Tabriklaymiz! Sizga botdan foydalanish uchun ruxsat berildi.\nRolingiz: {role_label}"
            )
        except Exception:
            pass

    elif data.startswith("deny_"):
        await query.answer()
        target_id = int(data.split("deny_")[1])
        update_user_status(target_id, 'rejected')
        await query.edit_message_text(text=query.message.text + "\n\n🚫 **Rad etildi.**")

    elif data.startswith("done_"):
        role = get_user_role(user.id)
        if role not in ('admin', 'executor'):
            await query.answer("⛔ Faqat Zakaz bajaruvchi buyurtmani bajarilgan deb belgilay oladi!", show_alert=True)
            return

        order_id = data.split("done_")[1]
        order = get_order(order_id)
        if order is None or order[4] != 'pending':
            await query.answer("❌ Bu buyurtma allaqachon bajarilgan yoki topilmadi!", show_alert=True)
            return
        _, showroom, deadline_text, deadline_date_str, _ = order
        deadline_date = date.fromisoformat(deadline_date_str)
        base_text = format_order_base(order_id, showroom, deadline_text, deadline_date)

        # Foydalanuvchining Ismi va Username'ini shakllantirish
        username_str = f"@{user.username}" if user.username else "Username yo'q"
        full_user_name = f"{user.full_name} ({username_str})"

        is_new, confirmations = add_confirmation(
            order_id, user.id, full_user_name, query.message.chat_id, query.message.message_id
        )

        if not is_new:
            remaining = max(REQUIRED_CONFIRMATIONS - len(confirmations), 0)
            await query.answer(
                f"ℹ️ Siz bu buyurtmani allaqachon tasdiqlagansiz. Yakunlanishi uchun yana {remaining} ta bajaruvchi tasdiqlashi kerak.",
                show_alert=True
            )
            return

        if len(confirmations) < REQUIRED_CONFIRMATIONS:
            remaining = REQUIRED_CONFIRMATIONS - len(confirmations)
            await query.answer("✅ Tasdiqlandi! Yakunlanishi uchun yana bitta bajaruvchi tasdiqlashi kerak.", show_alert=True)
            confirmed_lines = "\n".join(f"🔸 {c_name}" for _, c_name, _, _ in confirmations)
            updated_text = (
                f"{base_text}\n\n"
                f"**Tasdiqlangan ({len(confirmations)}/{REQUIRED_CONFIRMATIONS}):**\n{confirmed_lines}\n\n"
                f"⏳ Yakunlanishi uchun yana {remaining} ta bajaruvchi tasdiqlashi kerak."
            )
            keyboard = [[InlineKeyboardButton("✅ Bajarildi deb belgilash", callback_data=f"done_{order_id}")]]
            await query.edit_message_text(text=updated_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        # Talab qilingan barcha bajaruvchilar tasdiqladi — buyurtma to'liq yakunlandi
        names_only = [c_name for _, c_name, _, _ in confirmations]
        completed_by_text = " va ".join(names_only)
        complete_order(order_id, completed_by_text)
        await query.answer("✅ Buyurtma to'liq yakunlandi!")

        final_text = (
            f"{base_text}\n\n"
            f"✅ **BAJARILDI!**\n"
            f"👥 **Bajaruvchilar:** {completed_by_text}"
        )

        # Tasdiqlagan har bir bajaruvchining o'zidagi xabarini yakuniy holatga yangilaymiz
        for c_user_id, c_name, c_chat_id, c_message_id in confirmations:
            try:
                await context.bot.edit_message_text(
                    chat_id=c_chat_id, message_id=c_message_id, text=final_text, parse_mode="Markdown"
                )
            except Exception:
                pass

        # Yakunlanganini hammaga (jumladan kuzatuvchilarga) e'lon qilamiz
        broadcast_text = (
            f"🎉 **BUYURTMA TO'LIQ YAKUNLANDI!**\n\n"
            f"🆔 **Zakaz ID:** `{order_id}`\n"
            f"🏢 **Shourum:** {showroom}\n"
            f"👥 **Bajaruvchilar:** {completed_by_text}"
        )
        confirmed_ids = {c[0] for c in confirmations}
        for u_id in get_approved_users():
            if u_id in confirmed_ids:
                continue
            try:
                await context.bot.send_message(chat_id=u_id, text=broadcast_text, parse_mode="Markdown")
            except Exception:
                pass

# === BOTNI ISHGA TUSHIRISH ===
async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Ro'yxatdan o'tish / Botni ishga tushirish"),
        BotCommand("zakaz", "Yangi buyurtma kiritish (Yaratuvchi)"),
        BotCommand("bekor", "Buyurtmani bekor qilish (Yaratuvchi/Admin)"),
        BotCommand("hisobot", "Oylik hisobotni Excel'da yuklab olish (Admin)"),
        BotCommand("statistika", "Umumiy statistikani ko'rish (Admin)"),
        BotCommand("cancel", "Joriy amalni bekor qilish"),
    ])

if __name__ == "__main__":
    init_db()
    seed_default_users()

    # Veb-serverni fonda ishga tushirish
    keep_alive()

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    if app.job_queue is not None:
        app.job_queue.run_daily(daily_digest, time=dt_time(hour=9, minute=0, tzinfo=ZoneInfo("Asia/Tashkent")))
    else:
        logging.warning("JobQueue mavjud emas — 'pip install \"python-telegram-bot[job-queue]\"' o'rnating, aks holda kunlik eslatma ishlamaydi.")

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
    app.add_handler(CommandHandler('hisobot', hisobot))
    app.add_handler(CommandHandler('bekor', bekor_qilish))
    app.add_handler(CommandHandler('statistika', statistika))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Bot muvaffaqiyatli ishga tushdi...")
    app.run_polling()