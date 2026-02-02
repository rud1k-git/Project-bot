import telebot
import sqlite3
import threading
import time
from datetime import datetime, date
import pytz

TOKEN = "ваш токен"
bot = telebot.TeleBot(TOKEN)

TZ = pytz.timezone("Europe/Moscow")

# --- БАЗА ДАННЫХ ---
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    text TEXT,
    remind_time TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS birthdays (
    chat_id INTEGER,
    name TEXT,
    birth_date TEXT
)
""")

conn.commit()

# --- КНОПКИ ---
def main_keyboard():
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Добавить напоминание", "📋 Список напоминаний")
    kb.add("❌ Удалить напоминание")
    kb.add("🎂 Добавить день рождения", "🎉 Сколько дней до ДР")
    return kb

# --- START ---
@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(
        message.chat.id,
        "⏰ Бот-напоминалка (МСК)\n"
        "Время вводится по московскому времени!",
        reply_markup=main_keyboard()
    )

# --- СОСТОЯНИЯ ---
user_state = {}

# --- ДОБАВИТЬ НАПОМИНАНИЕ ---
@bot.message_handler(func=lambda m: m.text == "➕ Добавить напоминание")
def ask_reminder(message):
    user_state[message.chat.id] = "add_reminder"
    bot.send_message(
        message.chat.id,
        "Введите:\nГГГГ-ММ-ДД ЧЧ:ММ текст\n\nПример:\n2026-02-10 18:30 Сделать дз"
    )

# --- СПИСОК ---
@bot.message_handler(func=lambda m: m.text == "📋 Список напоминаний")
def list_reminders(message):
    cursor.execute(
        "SELECT id, text, remind_time FROM reminders WHERE chat_id=?",
        (message.chat.id,)
    )
    rows = cursor.fetchall()

    if not rows:
        bot.send_message(message.chat.id, "📭 Напоминаний нет")
        return

    text = "📋 Напоминания:\n\n"
    for r in rows:
        t = datetime.fromisoformat(r[2]).strftime("%Y-%m-%d %H:%M")
        text += f"{r[0]}. ⏰ {t} — {r[1]}\n"

    bot.send_message(message.chat.id, text)

# --- УДАЛИТЬ ---
@bot.message_handler(func=lambda m: m.text == "❌ Удалить напоминание")
def ask_delete(message):
    user_state[message.chat.id] = "delete"
    bot.send_message(message.chat.id, "Введите ID напоминания")

# --- ДЕНЬ РОЖДЕНИЯ ---
@bot.message_handler(func=lambda m: m.text == "🎂 Добавить день рождения")
def ask_birthday(message):
    user_state[message.chat.id] = "birthday"
    bot.send_message(
        message.chat.id,
        "Введите:\nИмя ГГГГ-ММ-ДД\n\nПример:\nМама 1980-05-12"
    )

@bot.message_handler(func=lambda m: m.text == "🎉 Сколько дней до ДР")
def days_to_birthday(message):
    cursor.execute(
        "SELECT name, birth_date FROM birthdays WHERE chat_id=?",
        (message.chat.id,)
    )
    rows = cursor.fetchall()

    if not rows:
        bot.send_message(message.chat.id, "🎂 Дней рождения нет")
        return

    today = datetime.now(TZ).date()
    text = "🎉 До дней рождения:\n\n"

    for name, bd in rows:
        bdate = datetime.strptime(bd, "%Y-%m-%d").date()
        next_bd = bdate.replace(year=today.year)
        if next_bd < today:
            next_bd = next_bd.replace(year=today.year + 1)
        days = (next_bd - today).days
        text += f"{name} — через {days} дн.\n"

    bot.send_message(message.chat.id, text)

# --- ОБРАБОТКА ВВОДА ---
@bot.message_handler(func=lambda m: m.chat.id in user_state)
def handle_input(message):
    state = user_state.pop(message.chat.id)

    try:
        if state == "add_reminder":
            parts = message.text.split(maxsplit=2)
            dt = datetime.strptime(
                parts[0] + " " + parts[1],
                "%Y-%m-%d %H:%M"
            )
            dt = TZ.localize(dt)

            cursor.execute(
                "INSERT INTO reminders VALUES (NULL, ?, ?, ?)",
                (message.chat.id, parts[2], dt.isoformat())
            )
            conn.commit()
            bot.send_message(message.chat.id, "✅ Напоминание добавлено")

        elif state == "delete":
            cursor.execute(
                "DELETE FROM reminders WHERE id=? AND chat_id=?",
                (int(message.text), message.chat.id)
            )
            conn.commit()
            bot.send_message(message.chat.id, "🗑 Удалено")

        elif state == "birthday":
            name, d = message.text.split()
            cursor.execute(
                "INSERT INTO birthdays VALUES (?, ?, ?)",
                (message.chat.id, name, d)
            )
            conn.commit()
            bot.send_message(message.chat.id, "🎂 День рождения сохранён")

    except:
        bot.send_message(message.chat.id, "❌ Ошибка ввода")

# --- ПРОВЕРКА НАПОМИНАНИЙ ---
def checker():
    while True:
        now = datetime.now(TZ).isoformat(timespec="minutes")
        cursor.execute(
            "SELECT id, chat_id, text FROM reminders WHERE remind_time <= ?",
            (now,)
        )
        for r in cursor.fetchall():
            bot.send_message(r[1], f"⏰ Напоминание:\n{r[2]}")
            cursor.execute("DELETE FROM reminders WHERE id=?", (r[0],))
            conn.commit()
        time.sleep(30)

threading.Thread(target=checker, daemon=True).start()

bot.polling()

