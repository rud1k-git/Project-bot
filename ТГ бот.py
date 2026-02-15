import telebot
import sqlite3
import threading
import time
from datetime import datetime, date, timedelta
import pytz
from telebot.types import (
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButton
)

# ============================================================
# НАСТРОЙКИ
# ============================================================

TOKEN = "токен"
ADMIN_ID = 152343  # <<< ВСТАВЬ СВОЙ TELEGRAM ID

bot = telebot.TeleBot(TOKEN)

TZ = pytz.timezone("Europe/Moscow")
DB_NAME = "bot.db"

# ============================================================
# БАЗА ДАННЫХ
# ============================================================

# Используем отдельные соединения для разных потоков
def get_db_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# Создаем таблицы при запуске
def init_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        text TEXT,
        remind_time TEXT,
        category TEXT DEFAULT 'Без категории',
        repeat_type TEXT DEFAULT 'none',
        notify_before INTEGER DEFAULT 0,
        done INTEGER DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS birthdays (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        name TEXT,
        birth_date TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS timers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        end_time TEXT,
        text TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        accepted INTEGER DEFAULT 0,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        registered_date TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bans (
        chat_id INTEGER PRIMARY KEY,
        until TEXT,
        reason TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admin_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER,
        action TEXT,
        target_id INTEGER,
        details TEXT,
        timestamp TEXT
    )
    """)

    conn.commit()
    
    # Проверяем существующие колонки в таблице users
    cursor.execute("PRAGMA table_info(users)")
    existing_columns = [column[1] for column in cursor.fetchall()]

    # Если таблица уже существует но без нужных колонок - добавляем их
    if 'username' not in existing_columns:
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")
        except:
            pass

    if 'first_name' not in existing_columns:
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
        except:
            pass

    if 'last_name' not in existing_columns:
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN last_name TEXT")
        except:
            pass

    if 'registered_date' not in existing_columns:
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN registered_date TEXT")
        except:
            pass

    conn.commit()
    conn.close()

# Инициализируем базу данных
init_database()

# ============================================================
# СОСТОЯНИЯ
# ============================================================

user_state = {}
temp_data = {}

# ============================================================
# ПРОВЕРКИ (исправлено - теперь каждое обращение создает новое соединение)
# ============================================================

def is_admin(chat_id):
    return chat_id == ADMIN_ID

def is_accepted(chat_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT accepted FROM users WHERE chat_id=?", (chat_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None and row[0] == 1

def set_accepted(chat_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users(chat_id, accepted)
        VALUES (?, 1)
        ON CONFLICT(chat_id) DO UPDATE SET accepted=1
    """, (chat_id,))
    conn.commit()
    conn.close()

def is_banned(chat_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT until FROM bans WHERE chat_id=?", (chat_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return False

    until = row[0]

    if until == "permanent":
        conn.close()
        return True

    try:
        until_dt = datetime.fromisoformat(until)
        if datetime.now(TZ) < until_dt:
            conn.close()
            return True
        else:
            cursor.execute("DELETE FROM bans WHERE chat_id=?", (chat_id,))
            conn.commit()
            conn.close()
            return False
    except:
        conn.close()
        return False

def log_admin_action(admin_id, action, target_id=None, details=""):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO admin_logs (admin_id, action, target_id, details, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (admin_id, action, target_id, details, datetime.now(TZ).isoformat()))
    conn.commit()
    conn.close()

# ============================================================
# ДЕКОРАТОР ДЛЯ ПРОВЕРКИ ДОСТУПА (исправлен)
# ============================================================

def check_access(func):
    def wrapper(message_or_call):
        try:
            if hasattr(message_or_call, 'chat'):
                chat_id = message_or_call.chat.id
            elif hasattr(message_or_call, 'message'):
                chat_id = message_or_call.message.chat.id
            else:
                return func(message_or_call)

            # Проверяем бан
            if is_banned(chat_id):
                if hasattr(message_or_call, 'chat'):
                    bot.send_message(chat_id, "🚫 Вы заблокированы")
                else:
                    bot.answer_callback_query(message_or_call.id, "🚫 Вы заблокированы", show_alert=True)
                return

            # Проверяем принятие соглашения (пропускаем /start и accept_agreement)
            if not is_accepted(chat_id):
                if hasattr(message_or_call, 'chat'):
                    if message_or_call.text != "/start":
                        bot.send_message(chat_id, "❗ Сначала примите соглашение через /start")
                        return
                elif hasattr(message_or_call, 'data'):
                    if message_or_call.data != "accept_agreement" and not message_or_call.data.startswith("year_") and not message_or_call.data.startswith("month_") and not message_or_call.data.startswith("day_"):
                        bot.answer_callback_query(message_or_call.id, "❗ Сначала примите соглашение", show_alert=True)
                        return

            # Вызываем функцию
            return func(message_or_call)
            
        except Exception as e:
            print(f"Error in check_access: {e}")
            # В случае ошибки всё равно пытаемся выполнить функцию
            try:
                return func(message_or_call)
            except:
                return
    return wrapper

# ============================================================
# КЛАВИАТУРЫ (исправлено - убрана кнопка "Планы на сегодня")
# ============================================================

def main_keyboard(chat_id):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    
    if is_admin(chat_id):
        # Для админа - расширенное меню
        kb.add("➕ Добавить напоминание", "📋 Список напоминаний")
        kb.add("🎂 Добавить день рождения", "🎉 Сколько дней до ДР")
        kb.add("⏱ Таймер", "❌ Удалить напоминание")
        kb.add("⚙️ Админ панель")
    else:
        # Для обычных пользователей
        kb.add("➕ Добавить напоминание", "📋 Список напоминаний")
        kb.add("🎂 Добавить день рождения", "🎉 Сколько дней до ДР")
        kb.add("⏱ Таймер", "❌ Удалить напоминание")
    
    return kb

def admin_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📊 Статистика", "👥 Список пользователей")
    kb.add("🔨 Заблокировать", "🔓 Разблокировать")
    kb.add("🚫 Список блокировок", "📜 Логи действий")
    kb.add("📢 Рассылка", "📋 Команды")
    kb.add("◀️ Назад в меню")
    return kb

# ============================================================
# СОГЛАШЕНИЕ
# ============================================================

def agreement_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Принимаю", callback_data="accept_agreement"))
    return kb

def remove_agreement_if_not_accepted(chat_id, message_id):
    time.sleep(60)
    if not is_accepted(chat_id):
        try:
            bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
            bot.send_message(chat_id, "⏳ Время истекло. Введите /start")
        except:
            pass

@bot.message_handler(commands=["start"])
def start(message):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Сохраняем информацию о пользователе
        cursor.execute("""
            INSERT INTO users(chat_id, username, first_name, last_name, registered_date, accepted)
            VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(chat_id) DO UPDATE SET 
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name
        """, (
            message.chat.id,
            message.from_user.username,
            message.from_user.first_name,
            message.from_user.last_name,
            datetime.now(TZ).isoformat()
        ))
        conn.commit()
        conn.close()

        text = (
            "📜 Пользовательское соглашение\n\n"
            "Бот хранит ваши данные в базе.\n"
            "Бот не несёт ответственности за пропущенные уведомления.\n\n"
            "Нажмите «Принимаю»."
        )

        msg = bot.send_message(message.chat.id, text, reply_markup=agreement_keyboard())

        threading.Thread(
            target=remove_agreement_if_not_accepted,
            args=(message.chat.id, msg.message_id),
            daemon=True
        ).start()
    except Exception as e:
        print(f"Error in start: {e}")
        bot.send_message(message.chat.id, "❌ Произошла ошибка. Попробуйте позже.")

# ============================================================
# ОБРАБОТЧИКИ CALLBACK
# ============================================================

@bot.callback_query_handler(func=lambda call: True)
@check_access
def callback_handler(call):
    try:
        chat_id = call.message.chat.id
        print(f"Callback received: {call.data} from {chat_id}")

        if call.data == "accept_agreement":
            set_accepted(chat_id)
            bot.edit_message_text(
                "✅ Соглашение принято!\n\nТеперь можно пользоваться ботом.",
                chat_id,
                call.message.message_id
            )
            bot.send_message(chat_id, "📌 Главное меню:", reply_markup=main_keyboard(chat_id))
            bot.answer_callback_query(call.id)
            return

        if call.data == "ignore":
            bot.answer_callback_query(call.id)
            return

        if call.data == "cancel":
            bot.edit_message_text(
                "❌ Действие отменено",
                chat_id,
                call.message.message_id
            )
            bot.answer_callback_query(call.id)
            return

        if call.data.startswith("year_"):
            choose_year(call)
        elif call.data.startswith("month_"):
            choose_month(call)
        elif call.data.startswith("day_"):
            choose_day(call)
        elif call.data.startswith("ban_duration_"):
            process_ban_duration(call)
        elif call.data.startswith("broadcast_"):
            process_broadcast_confirm(call)
        else:
            bot.answer_callback_query(call.id)
            
    except Exception as e:
        print(f"Error in callback_handler: {e}")
        try:
            bot.answer_callback_query(call.id, "❌ Произошла ошибка", show_alert=True)
        except:
            pass

# ============================================================
# КАЛЕНДАРЬ
# ============================================================

def year_keyboard():
    kb = InlineKeyboardMarkup()
    current_year = datetime.now(TZ).year
    for y in range(current_year, current_year + 5):
        kb.add(InlineKeyboardButton(str(y), callback_data=f"year_{y}"))
    kb.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    return kb

def month_keyboard(year):
    kb = InlineKeyboardMarkup()
    months = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]

    row = []
    for i, m in enumerate(months, start=1):
        row.append(InlineKeyboardButton(m, callback_data=f"month_{year}_{i}"))
        if len(row) == 3:
            kb.row(*row)
            row = []

    if row:
        kb.row(*row)

    kb.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    return kb

def day_keyboard(year, month):
    kb = InlineKeyboardMarkup(row_width=7)
    today = datetime.now(TZ).date()
    
    # Определяем количество дней в месяце
    if month == 12:
        days_in_month = (date(year + 1, 1, 1) - date(year, month, 1)).days
    else:
        days_in_month = (date(year, month + 1, 1) - date(year, month, 1)).days

    buttons = []
    for day in range(1, days_in_month + 1):
        current_date = date(year, month, day)
        
        if current_date < today:
            # Прошедшие дни - неактивные кнопки
            buttons.append(InlineKeyboardButton("❌", callback_data="ignore"))
        else:
            # Будущие дни - активные
            buttons.append(InlineKeyboardButton(str(day), callback_data=f"day_{year}_{month}_{day}"))
    
    # Добавляем кнопки в клавиатуру
    kb.add(*buttons)
    kb.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    
    return kb

def choose_year(call):
    try:
        year = int(call.data.split("_")[1])
        bot.edit_message_text(
            "📅 Выберите месяц:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=month_keyboard(year)
        )
        bot.answer_callback_query(call.id)
    except Exception as e:
        print(f"Error in choose_year: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка", show_alert=True)

def choose_month(call):
    try:
        parts = call.data.split("_")
        year = int(parts[1])
        month = int(parts[2])
        
        bot.edit_message_text(
            f"📅 Выберите день:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=day_keyboard(year, month)
        )
        bot.answer_callback_query(call.id)
    except Exception as e:
        print(f"Error in choose_month: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка", show_alert=True)

def choose_day(call):
    try:
        parts = call.data.split("_")
        year = int(parts[1])
        month = int(parts[2])
        day = int(parts[3])

        selected = date(year, month, day)
        today = datetime.now(TZ).date()

        if selected < today:
            bot.answer_callback_query(call.id, "❌ Нельзя выбрать прошедшую дату!", show_alert=True)
            return

        # Сохраняем дату в temp_data для следующего шага
        temp_data[f"selected_date_{call.message.chat.id}"] = selected
        
        bot.edit_message_text(
            f"✅ Вы выбрали дату: {selected.strftime('%d.%m.%Y')}\n\nТеперь введите текст напоминания:",
            call.message.chat.id,
            call.message.message_id
        )
        
        # Устанавливаем состояние для ожидания текста напоминания
        user_state[call.message.chat.id] = "waiting_reminder_text"
        
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        print(f"Error in choose_day: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при выборе даты", show_alert=True)

# ============================================================
# ОБРАБОТЧИК ТЕКСТА НАПОМИНАНИЯ
# ============================================================

@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "waiting_reminder_text")
@check_access
def process_reminder_text(message):
    try:
        selected_date = temp_data.pop(f"selected_date_{message.chat.id}", None)
        
        if not selected_date:
            bot.send_message(message.chat.id, "❌ Ошибка: дата не найдена. Начните заново.")
            user_state.pop(message.chat.id, None)
            return
        
        reminder_text = message.text
        
        # Сохраняем напоминание в базу данных
        conn = get_db_connection()
        cursor = conn.cursor()
        remind_time = datetime.combine(selected_date, datetime.min.time()).isoformat()
        cursor.execute("""
            INSERT INTO reminders (chat_id, text, remind_time, category, repeat_type, notify_before, done)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (message.chat.id, reminder_text, remind_time, "Без категории", "none", 0, 0))
        conn.commit()
        conn.close()
        
        bot.send_message(
            message.chat.id, 
            f"✅ Напоминание сохранено!\n\n"
            f"📅 Дата: {selected_date.strftime('%d.%m.%Y')}\n"
            f"📝 Текст: {reminder_text}"
        )
        
        user_state.pop(message.chat.id, None)
        
    except Exception as e:
        print(f"Error in process_reminder_text: {e}")
        bot.send_message(message.chat.id, "❌ Произошла ошибка. Попробуйте снова.")
        user_state.pop(message.chat.id, None)

# ============================================================
# АДМИН ПАНЕЛЬ
# ============================================================

@bot.message_handler(func=lambda m: m.text == "⚙️ Админ панель")
@check_access
def admin_panel(message):
    if not is_admin(message.chat.id):
        bot.send_message(message.chat.id, "🚫 Доступ запрещен")
        return
    
    bot.send_message(
        message.chat.id,
        "⚙️ Административная панель\n\n"
        "Выберите действие:",
        reply_markup=admin_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "◀️ Назад в меню")
@check_access
def back_to_menu(message):
    bot.send_message(
        message.chat.id,
        "📌 Главное меню:",
        reply_markup=main_keyboard(message.chat.id)
    )

# Статистика
@bot.message_handler(func=lambda m: m.text == "📊 Статистика")
@check_access
def show_statistics(message):
    if not is_admin(message.chat.id):
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE accepted = 1")
        accepted_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM bans")
        banned_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM reminders")
        total_reminders = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM birthdays")
        total_birthdays = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM timers")
        total_timers = cursor.fetchone()[0]
        
        conn.close()
        
        stats_text = (
            f"📊 **Статистика бота**\n\n"
            f"👥 **Пользователи:**\n"
            f"• Всего: {total_users}\n"
            f"• Активных: {accepted_users}\n"
            f"• Заблокировано: {banned_users}\n\n"
            f"📌 **Напоминания:** {total_reminders}\n"
            f"🎂 **Дни рождения:** {total_birthdays}\n"
            f"⏱ **Таймеры:** {total_timers}"
        )
        
        bot.send_message(message.chat.id, stats_text, parse_mode="Markdown")
    except Exception as e:
        print(f"Error in show_statistics: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при получении статистики")

# Список пользователей
@bot.message_handler(func=lambda m: m.text == "👥 Список пользователей")
@check_access
def list_users(message):
    if not is_admin(message.chat.id):
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_id, username, first_name, last_name, accepted, registered_date 
            FROM users ORDER BY registered_date DESC LIMIT 20
        """)
        users = cursor.fetchall()
        conn.close()
        
        if not users:
            bot.send_message(message.chat.id, "📭 Нет пользователей")
            return
        
        text = "👥 **Последние 20 пользователей:**\n\n"
        
        for user in users:
            user_id, username, first_name, last_name, accepted, reg_date = user
            
            reg_datetime = datetime.fromisoformat(reg_date) if reg_date else datetime.now(TZ)
            reg_str = reg_datetime.strftime("%d.%m.%Y %H:%M")
            
            name_parts = []
            if first_name:
                name_parts.append(first_name)
            if last_name:
                name_parts.append(last_name)
            full_name = " ".join(name_parts) if name_parts else "Нет имени"
            
            status = "✅" if accepted else "⏳"
            username_str = f"@{username}" if username else "нет username"
            
            text += (
                f"{status} **ID:** `{user_id}`\n"
                f"   • Имя: {full_name}\n"
                f"   • Username: {username_str}\n"
                f"   • Зарегистрирован: {reg_str}\n\n"
            )
        
        if len(text) > 4000:
            for i in range(0, len(text), 4000):
                bot.send_message(message.chat.id, text[i:i+4000], parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, text, parse_mode="Markdown")
    except Exception as e:
        print(f"Error in list_users: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при получении списка пользователей")

# Заблокировать
@bot.message_handler(func=lambda m: m.text == "🔨 Заблокировать")
@check_access
def ban_user_start(message):
    if not is_admin(message.chat.id):
        return
    
    bot.send_message(
        message.chat.id,
        "🔨 Введите ID пользователя для блокировки:"
    )
    user_state[message.chat.id] = "waiting_ban_id"

@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "waiting_ban_id")
@check_access
def process_ban_id(message):
    if not is_admin(message.chat.id):
        return
    
    try:
        user_id = int(message.text.strip())
        
        # Проверяем существование пользователя
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id FROM users WHERE chat_id = ?", (user_id,))
        user_exists = cursor.fetchone()
        conn.close()
        
        if not user_exists:
            bot.send_message(message.chat.id, f"❌ Пользователь {user_id} не найден в базе")
            user_state.pop(message.chat.id, None)
            return
        
        # Показываем опции блокировки
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("1 час", callback_data=f"ban_duration_1h_{user_id}"),
            InlineKeyboardButton("3 часа", callback_data=f"ban_duration_3h_{user_id}"),
            InlineKeyboardButton("12 часов", callback_data=f"ban_duration_12h_{user_id}")
        )
        kb.row(
            InlineKeyboardButton("1 день", callback_data=f"ban_duration_1d_{user_id}"),
            InlineKeyboardButton("7 дней", callback_data=f"ban_duration_7d_{user_id}"),
            InlineKeyboardButton("30 дней", callback_data=f"ban_duration_30d_{user_id}")
        )
        kb.row(
            InlineKeyboardButton("⛔️ Навсегда", callback_data=f"ban_duration_permanent_{user_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel")
        )
        
        bot.send_message(
            message.chat.id,
            f"Выберите срок блокировки для пользователя `{user_id}`:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        
        user_state.pop(message.chat.id, None)
        
    except ValueError:
        bot.send_message(message.chat.id, "❌ Некорректный ID")
        user_state.pop(message.chat.id, None)
    except Exception as e:
        print(f"Error in process_ban_id: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при обработке")
        user_state.pop(message.chat.id, None)

def process_ban_duration(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "🚫 Доступ запрещен", show_alert=True)
        return
    
    try:
        parts = call.data.split("_")
        duration_type = parts[2]
        user_id = int(parts[3])
        
        if duration_type == "permanent":
            until = "permanent"
            duration_text = "навсегда"
        else:
            number = int(duration_type[:-1])
            unit = duration_type[-1]
            
            if unit == "h":
                delta = timedelta(hours=number)
                duration_text = f"{number} час(ов)"
            elif unit == "d":
                delta = timedelta(days=number)
                duration_text = f"{number} день(дней)"
            else:
                bot.answer_callback_query(call.id, "❌ Неверный формат", show_alert=True)
                return
            
            until_dt = datetime.now(TZ) + delta
            until = until_dt.isoformat()
        
        # Сохраняем данные для следующего шага
        temp_data[f"ban_final_{call.message.chat.id}"] = {
            "user_id": user_id,
            "until": until,
            "duration_text": duration_text
        }
        
        bot.send_message(
            call.message.chat.id,
            f"📝 Введите причину блокировки пользователя `{user_id}`:",
            parse_mode="Markdown"
        )
        
        user_state[call.message.chat.id] = "waiting_ban_reason"
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        print(f"Error in process_ban_duration: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка", show_alert=True)

@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "waiting_ban_reason")
def process_ban_reason(message):
    if not is_admin(message.chat.id):
        return
    
    try:
        ban_data = temp_data.pop(f"ban_final_{message.chat.id}", None)
        if not ban_data:
            bot.send_message(message.chat.id, "❌ Ошибка: данные не найдены")
            user_state.pop(message.chat.id, None)
            return
        
        user_id = ban_data["user_id"]
        until = ban_data["until"]
        duration_text = ban_data["duration_text"]
        reason = message.text
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO bans(chat_id, until, reason)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET until=?, reason=?
        """, (user_id, until, reason, until, reason))
        conn.commit()
        conn.close()
        
        # Логируем действие
        log_admin_action(
            message.chat.id,
            "ban",
            user_id,
            f"Срок: {duration_text}, Причина: {reason}"
        )
        
        # Уведомляем пользователя
        try:
            ban_text = f"🚫 Вы заблокированы в боте"
            if until != "permanent":
                until_dt = datetime.fromisoformat(until)
                ban_text += f" до {until_dt.strftime('%d.%m.%Y %H:%M')}"
            else:
                ban_text += " навсегда"
            
            if reason:
                ban_text += f"\nПричина: {reason}"
            
            bot.send_message(user_id, ban_text)
        except:
            pass
        
        bot.send_message(
            message.chat.id,
            f"✅ Пользователь {user_id} заблокирован {duration_text}\nПричина: {reason}"
        )
        
        user_state.pop(message.chat.id, None)
        
    except Exception as e:
        print(f"Error in process_ban_reason: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при блокировке")
        user_state.pop(message.chat.id, None)

# Разблокировать
@bot.message_handler(func=lambda m: m.text == "🔓 Разблокировать")
@check_access
def unban_user_start(message):
    if not is_admin(message.chat.id):
        return
    
    bot.send_message(
        message.chat.id,
        "🔓 Введите ID пользователя для разблокировки:"
    )
    user_state[message.chat.id] = "waiting_unban_id"

@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "waiting_unban_id")
def process_unban(message):
    if not is_admin(message.chat.id):
        return
    
    try:
        user_id = int(message.text.strip())
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bans WHERE chat_id=?", (user_id,))
        conn.commit()
        deleted = cursor.rowcount
        conn.close()
        
        if deleted > 0:
            log_admin_action(message.chat.id, "unban", user_id)
            bot.send_message(message.chat.id, f"✅ Пользователь {user_id} разблокирован")
            
            # Уведомляем пользователя
            try:
                bot.send_message(user_id, "🔓 Вы разблокированы в боте")
            except:
                pass
        else:
            bot.send_message(message.chat.id, f"❌ Пользователь {user_id} не найден в списке заблокированных")
    
    except ValueError:
        bot.send_message(message.chat.id, "❌ Некорректный ID")
    except Exception as e:
        print(f"Error in process_unban: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при разблокировке")
    
    user_state.pop(message.chat.id, None)

# Список блокировок
@bot.message_handler(func=lambda m: m.text == "🚫 Список блокировок")
@check_access
def list_bans(message):
    if not is_admin(message.chat.id):
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT bans.chat_id, bans.until, bans.reason, users.username, users.first_name 
            FROM bans 
            LEFT JOIN users ON bans.chat_id = users.chat_id
        """)
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            bot.send_message(message.chat.id, "✅ Заблокированных пользователей нет")
            return
        
        text = "🚫 **Список заблокированных:**\n\n"
        
        for user_id, until, reason, username, first_name in rows:
            name = first_name if first_name else "Нет имени"
            username_str = f" (@{username})" if username else ""
            
            if until == "permanent":
                until_text = "НАВСЕГДА"
            else:
                until_dt = datetime.fromisoformat(until)
                until_text = until_dt.strftime('%d.%m.%Y %H:%M')
            
            reason_text = f"\n   • Причина: {reason}" if reason else ""
            
            text += (
                f"• **ID:** `{user_id}`{username_str}\n"
                f"  Имя: {name}\n"
                f"  До: {until_text}{reason_text}\n\n"
            )
        
        if len(text) > 4000:
            for i in range(0, len(text), 4000):
                bot.send_message(message.chat.id, text[i:i+4000], parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, text, parse_mode="Markdown")
            
    except Exception as e:
        print(f"Error in list_bans: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при получении списка блокировок")

# Логи действий
@bot.message_handler(func=lambda m: m.text == "📜 Логи действий")
@check_access
def show_logs(message):
    if not is_admin(message.chat.id):
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT admin_id, action, target_id, details, timestamp 
            FROM admin_logs 
            ORDER BY timestamp DESC LIMIT 20
        """)
        logs = cursor.fetchall()
        conn.close()
        
        if not logs:
            bot.send_message(message.chat.id, "📭 Логов пока нет")
            return
        
        text = "📜 **Последние 20 действий:**\n\n"
        
        for admin_id, action, target_id, details, timestamp in logs:
            ts = datetime.fromisoformat(timestamp).strftime("%d.%m.%Y %H:%M")
            
            action_emoji = {
                "ban": "🔨",
                "unban": "🔓",
                "broadcast": "📢",
                "warning": "⚠️"
            }.get(action, "📌")
            
            target_text = f" над `{target_id}`" if target_id else ""
            details_text = f"\n   • {details}" if details else ""
            
            text += f"{action_emoji} [{ts}] {action.upper()}{target_text}{details_text}\n\n"
        
        if len(text) > 4000:
            for i in range(0, len(text), 4000):
                bot.send_message(message.chat.id, text[i:i+4000], parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, text, parse_mode="Markdown")
            
    except Exception as e:
        print(f"Error in show_logs: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при получении логов")

# Рассылка
@bot.message_handler(func=lambda m: m.text == "📢 Рассылка")
@check_access
def broadcast_start(message):
    if not is_admin(message.chat.id):
        return
    
    bot.send_message(
        message.chat.id,
        "📢 Введите текст для рассылки всем пользователям:"
    )
    user_state[message.chat.id] = "waiting_broadcast"

@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "waiting_broadcast")
def process_broadcast(message):
    if not is_admin(message.chat.id):
        return
    
    try:
        broadcast_text = message.text
        
        # Кнопки подтверждения
        kb = InlineKeyboardMarkup()
        kb.row(
            InlineKeyboardButton("✅ Отправить", callback_data="broadcast_confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="broadcast_cancel")
        )
        
        bot.send_message(
            message.chat.id,
            f"📢 **Предпросмотр рассылки:**\n\n{broadcast_text}\n\nОтправить всем пользователям?",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        
        temp_data[f"broadcast_{message.chat.id}"] = broadcast_text
        user_state.pop(message.chat.id, None)
        
    except Exception as e:
        print(f"Error in process_broadcast: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка")
        user_state.pop(message.chat.id, None)

def process_broadcast_confirm(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "🚫 Доступ запрещен", show_alert=True)
        return
    
    try:
        if call.data == "broadcast_cancel":
            bot.edit_message_text(
                "❌ Рассылка отменена",
                call.message.chat.id,
                call.message.message_id
            )
            bot.answer_callback_query(call.id)
            return
        
        broadcast_text = temp_data.pop(f"broadcast_{call.message.chat.id}", None)
        if not broadcast_text:
            bot.answer_callback_query(call.id, "❌ Текст не найден", show_alert=True)
            return
        
        bot.edit_message_text(
            "📢 Начинаю рассылку...",
            call.message.chat.id,
            call.message.message_id
        )
        
        # Получаем всех принятых пользователей
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id FROM users WHERE accepted = 1")
        users = cursor.fetchall()
        conn.close()
        
        success = 0
        failed = 0
        
        for (user_id,) in users:
            try:
                bot.send_message(user_id, f"📢 **Рассылка:**\n\n{broadcast_text}", parse_mode="Markdown")
                success += 1
                time.sleep(0.05)
            except Exception as e:
                failed += 1
        
        # Логируем
        log_admin_action(
            call.message.chat.id,
            "broadcast",
            details=f"Отправлено: {success}, Ошибок: {failed}"
        )
        
        bot.send_message(
            call.message.chat.id,
            f"✅ Рассылка завершена!\n"
            f"📨 Отправлено: {success}\n"
            f"❌ Ошибок: {failed}"
        )
        
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        print(f"Error in process_broadcast_confirm: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка", show_alert=True)

# Команды
@bot.message_handler(func=lambda m: m.text == "📋 Команды")
@check_access
def show_admin_commands(message):
    if not is_admin(message.chat.id):
        return
    
    commands_text = (
        "📋 **Доступные команды:**\n\n"
        "**Основные:**\n"
        "/start - Запустить бота\n\n"
        "**Админ-команды:**\n"
        "/admin - Открыть админ панель\n"
        "/stats - Показать статистику\n"
        "/users - Список пользователей\n"
        "/ban [ID] [время] - Заблокировать\n"
        "/unban [ID] - Разблокировать\n"
        "/bans - Список блокировок\n"
        "/broadcast [текст] - Рассылка\n\n"
        "**Примеры ban:**\n"
        "/ban 123456789 permanent\n"
        "/ban 123456789 7d\n"
        "/ban 123456789 3h\n\n"
        "**Форматы времени:**\n"
        "• permanent - навсегда\n"
        "• 7d - 7 дней\n"
        "• 3h - 3 часа"
    )
    
    bot.send_message(message.chat.id, commands_text, parse_mode="Markdown")

# Альтернативные команды через /
@bot.message_handler(commands=["admin"])
def admin_command(message):
    if is_admin(message.chat.id):
        admin_panel(message)
    else:
        bot.send_message(message.chat.id, "🚫 Доступ запрещен")

@bot.message_handler(commands=["stats"])
def stats_command(message):
    if is_admin(message.chat.id):
        show_statistics(message)
    else:
        bot.send_message(message.chat.id, "🚫 Доступ запрещен")

@bot.message_handler(commands=["users"])
def users_command(message):
    if is_admin(message.chat.id):
        list_users(message)
    else:
        bot.send_message(message.chat.id, "🚫 Доступ запрещен")

@bot.message_handler(commands=["bans"])
def bans_command(message):
    if is_admin(message.chat.id):
        list_bans(message)
    else:
        bot.send_message(message.chat.id, "🚫 Доступ запрещен")

# ============================================================
# ОСНОВНЫЕ ФУНКЦИИ БОТА
# ============================================================

@bot.message_handler(func=lambda m: m.text == "➕ Добавить напоминание")
@check_access
def add_reminder(message):
    try:
        bot.send_message(message.chat.id, "📅 Выберите год:", reply_markup=year_keyboard())
    except Exception as e:
        print(f"Error in add_reminder: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при создании напоминания")

@bot.message_handler(func=lambda m: m.text == "📋 Список напоминаний")
@check_access
def list_reminders(message):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT text, remind_time FROM reminders 
            WHERE chat_id = ? AND done = 0 
            ORDER BY remind_time ASC LIMIT 10
        """, (message.chat.id,))
        reminders = cursor.fetchall()
        conn.close()
        
        if not reminders:
            bot.send_message(message.chat.id, "📭 У вас нет активных напоминаний")
            return
        
        text = "📋 **Ваши напоминания:**\n\n"
        for reminder in reminders:
            remind_time = datetime.fromisoformat(reminder[1]).strftime("%d.%m.%Y")
            text += f"• {remind_time}: {reminder[0]}\n"
        
        bot.send_message(message.chat.id, text, parse_mode="Markdown")
    except Exception as e:
        print(f"Error in list_reminders: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при получении списка")

@bot.message_handler(func=lambda m: m.text == "❌ Удалить напоминание")
@check_access
def delete_reminder(message):
    bot.send_message(message.chat.id, "❌ Функция удаления напоминаний в разработке")

@bot.message_handler(func=lambda m: m.text == "🎂 Добавить день рождения")
@check_access
def add_birthday(message):
    bot.send_message(message.chat.id, "Введите: Имя ГГГГ-ММ-ДД\nПример: Анна 1990-05-15")

@bot.message_handler(func=lambda m: m.text == "🎉 Сколько дней до ДР")
@check_access
def days_to_birthday(message):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name, birth_date FROM birthdays WHERE chat_id = ?", (message.chat.id,))
        birthdays = cursor.fetchall()
        conn.close()
        
        if not birthdays:
            bot.send_message(message.chat.id, "🎂 У вас нет сохраненных дней рождения")
            return
        
        today = datetime.now(TZ).date()
        text = "🎉 **Дни рождения:**\n\n"
        
        for name, birth_date in birthdays:
            bdate = datetime.strptime(birth_date, "%Y-%m-%d").date()
            next_bd = bdate.replace(year=today.year)
            
            if next_bd < today:
                next_bd = next_bd.replace(year=today.year + 1)
            
            days_left = (next_bd - today).days
            text += f"• {name}: {days_left} дней ({(next_bd).strftime('%d.%m')})\n"
        
        bot.send_message(message.chat.id, text, parse_mode="Markdown")
    except Exception as e:
        print(f"Error in days_to_birthday: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при подсчете")

@bot.message_handler(func=lambda m: m.text == "⏱ Таймер")
@check_access
def timer_help(message):
    bot.send_message(message.chat.id, "Введите: количество минут текст\nПример: 10 Сделать чай")

@bot.message_handler(func=lambda m: "-" in m.text and len(m.text.split()) == 2)
@check_access
def save_birthday(message):
    try:
        name, birth_date = message.text.split()
        # Проверка формата даты
        datetime.strptime(birth_date, "%Y-%m-%d")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO birthdays(chat_id, name, birth_date) VALUES (?, ?, ?)",
                       (message.chat.id, name, birth_date))
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, f"🎂 День рождения {name} ({birth_date}) сохранен!")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Неверный формат даты. Используйте ГГГГ-ММ-ДД")
    except Exception as e:
        print(f"Error in save_birthday: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при сохранении")

@bot.message_handler(func=lambda m: m.text and m.text.split()[0].isdigit())
@check_access
def set_timer(message):
    try:
        parts = message.text.split(maxsplit=1)
        minutes = int(parts[0])
        text_ = parts[1] if len(parts) > 1 else "Таймер!"

        end_time = datetime.now(TZ) + timedelta(minutes=minutes)

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO timers(chat_id, end_time, text) VALUES (?, ?, ?)",
                       (message.chat.id, end_time.isoformat(), text_))
        conn.commit()
        conn.close()

        bot.send_message(message.chat.id, f"⏱ Таймер на {minutes} минут установлен")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Введите число минут")
    except Exception as e:
        print(f"Error in set_timer: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при установке таймера")

# ============================================================
# ОБРАБОТЧИК ПО УМОЛЧАНИЮ
# ============================================================

@bot.message_handler(func=lambda m: True)
@check_access
def handle_other_messages(message):
    if message.text not in ["➕ Добавить напоминание", "📋 Список напоминаний", 
                           "❌ Удалить напоминание", "🎂 Добавить день рождения", 
                           "🎉 Сколько дней до ДР", "⏱ Таймер", "⚙️ Админ панель", 
                           "📊 Статистика", "👥 Список пользователей", "🔨 Заблокировать", 
                           "🔓 Разблокировать", "🚫 Список блокировок", 
                           "📜 Логи действий", "📢 Рассылка", "📋 Команды",
                           "◀️ Назад в меню"]:
        bot.send_message(message.chat.id, "Используйте кнопки меню")

# ============================================================
# CHECKER (исправлен)
# ============================================================

def checker():
    while True:
        try:
            now = datetime.now(TZ)
            conn = get_db_connection()
            cursor = conn.cursor()

            # Таймеры
            cursor.execute("SELECT id, chat_id, end_time, text FROM timers")
            timers = cursor.fetchall()

            for tid, chat_id, end_time, text_ in timers:
                try:
                    end_dt = datetime.fromisoformat(end_time)
                    if end_dt <= now:
                        try:
                            bot.send_message(chat_id, f"⏱ Таймер закончился!\n\n{text_}")
                        except:
                            pass
                        cursor.execute("DELETE FROM timers WHERE id=?", (tid,))
                except:
                    pass

            # Дни рождения
            cursor.execute("SELECT chat_id, name, birth_date FROM birthdays")
            bds = cursor.fetchall()

            today = now.date()

            for chat_id, name, bd in bds:
                try:
                    bdate = datetime.strptime(bd, "%Y-%m-%d").date()
                    next_bd = bdate.replace(year=today.year)

                    if next_bd < today:
                        next_bd = next_bd.replace(year=today.year + 1)

                    if (next_bd - today).days == 0 and now.hour == 9:
                        try:
                            bot.send_message(chat_id, f"🎉 Сегодня день рождения у {name}!")
                        except:
                            pass
                except:
                    pass

            # Проверка истекших блокировок
            cursor.execute("SELECT chat_id, until FROM bans WHERE until != 'permanent'")
            bans = cursor.fetchall()
            
            for chat_id, until in bans:
                try:
                    until_dt = datetime.fromisoformat(until)
                    if now > until_dt:
                        cursor.execute("DELETE FROM bans WHERE chat_id=?", (chat_id,))
                        try:
                            bot.send_message(chat_id, "🔓 Срок вашей блокировки истек")
                        except:
                            pass
                except:
                    pass

            conn.commit()
            conn.close()

        except Exception as e:
            print(f"Error in checker: {e}")
            try:
                conn.close()
            except:
                pass
        
        time.sleep(30)

# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    print(f"Бот запущен. Админ ID: {ADMIN_ID}")
    print("Нажмите Ctrl+C для остановки")
    
    # Запускаем checker в отдельном потоке
    checker_thread = threading.Thread(target=checker, daemon=True)
    checker_thread.start()
    
    # Запускаем бота с обработкой ошибок
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            print(f"Ошибка в polling: {e}")
            time.sleep(5)
