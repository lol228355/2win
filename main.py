import asyncio
import logging
import sqlite3
import re
import html
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.exceptions import TelegramBadRequest

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# --- КОНФИГУРАЦИЯ ---
# Токен считывается из переменной окружения BOT_TOKEN. 
# Если переменная не установлена (как было при ошибках), используется токен по умолчанию.
TOKEN = os.environ.get("BOT_TOKEN") 
DEFAULT_TOKEN = "8389575987:AAFu7A8NSmK3D6AynohVIw5QDPiYqRSNhbY"
TOKEN = os.environ.get("BOT_TOKEN", DEFAULT_TOKEN)
if TOKEN == DEFAULT_TOKEN:
    logging.warning("Используется токен по умолчанию. На Railway настройте переменную окружения BOT_TOKEN.")

# ВАШИ АДМИН ID
ADMIN_IDS = [
    8227071592,  # @eza6ka
    8394356460  # @Dom_sot
]

# СПИСОК КАНАЛОВ ДЛЯ ОБЯЗАТЕЛЬНОЙ ПОДПИСКИ
REQUIRED_CHANNELS = [
    {"url": "https://t.me/mymaksi", "id": "@mymaksi", "name": "Канал 1"},
    {"url": "https://t.me/Crypto_hapka", "id": "@Crypto_hapka", "name": "Канал 2"},
    # ВАЖНО: Замените ID на реальный ID канала или группы (начинается с -100)
    {"url": "https://t.me/+nTCkyUL-ycUxNGFi", "id": "-1000000000000", "name": "Канал 3"}
]

PAYOUT_CHANNEL_URL = "https://t.me/mymaksi"
PAYOUT_AMOUNT = 5.0 # Сумма, начисляемая за один успешный тикет

config_data = {
    "price_text": f"💰 *Прайс:* {PAYOUT_AMOUNT}$\n⏳ *БезХолд* 25мин",
    "menu_photo": None,
    "is_work_on": False
}

active_chats = {}

# --- STATES ---
class UserState(StatesGroup):
    sending_numbers = State()
    withdrawing = State()

class AdminState(StatesGroup):
    changing_price = State()
    changing_photo = State()
    broadcasting = State()
    payout_manage = State()
    selecting_payout_user = State()
    payout_check_uploading = State()

# --- БД ФУНКЦИИ ---
def db_start():
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()

    # 1. Таблица пользователей (users)
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        orders_count INTEGER DEFAULT 0,
        balance REAL DEFAULT 0.0
    )''')
    try:
        cur.execute("SELECT balance FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE users ADD COLUMN balance REAL DEFAULT 0.0")

    # 2. Таблица тикетов (tickets)
    cur.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        admin_id INTEGER,
        status TEXT, -- pending, paid, failed
        amount REAL,
        date TEXT,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )''')

    conn.commit()
    conn.close()

def add_user_to_db(user_id):
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()

def get_user_stats(user_id):
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("SELECT orders_count, balance FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    return (result[0], result[1]) if result else (0, 0.0)

def increment_user_orders(user_id):
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("UPDATE users SET orders_count = orders_count + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_payout_to_balance(user_id, amount):
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def reset_user_balance(user_id):
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = 0.0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = [row[0] for row in cur.fetchall()]
    conn.close()
    return users

def get_all_users_stats():
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("SELECT user_id, orders_count, balance FROM users ORDER BY orders_count DESC")
    results = cur.fetchall()
    conn.close()
    return results

def add_ticket(user_id, admin_id, amount):
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("INSERT INTO tickets (user_id, admin_id, status, amount, date) VALUES (?, ?, 'pending', ?, datetime('now'))",
                (user_id, admin_id, amount))
    conn.commit()
    conn.close()

def get_pending_tickets():
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("SELECT t.id, t.user_id, u.balance FROM tickets t JOIN users u ON t.user_id = u.user_id WHERE t.status = 'pending'")
    results = cur.fetchall()
    conn.close()
    return results

def update_ticket_status(ticket_id, status):
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
    conn.commit()
    conn.close()

# --- ПРОВЕРКА ПОДПИСКИ ---
async def check_subscription(bot: Bot, user_id: int) -> bool:
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["id"], user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except TelegramBadRequest:
            return True
        except Exception as e:
            logging.warning(f"Ошибка проверки подписки канала {channel['id']}: {e}")
            return True
    return True

# --- КЛАВИАТУРЫ ---
def get_subs_keyboard():
    kb = []
    for channel in REQUIRED_CHANNELS:
        kb.append([InlineKeyboardButton(text=f"👉 Подписаться: {channel['name']}", url=channel['url'])])
    kb.append([InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subs")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_main_keyboard():
    kb = [[KeyboardButton(text="📱 Сдать номер"), KeyboardButton(text="💰 Прайс")],
          [KeyboardButton(text="💰 Баланс")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True, input_field_placeholder="Меню")

def get_chat_management_keyboard():
    kb = [
        [KeyboardButton(text="✅ Номер взят")],
        [KeyboardButton(text="❌ Закончить чат")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_chat_keyboard():
    kb = [[KeyboardButton(text="❌ Закончить чат")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_balance_keyboard(balance):
    kb = []
    if balance > 0.001:
        kb.append([InlineKeyboardButton(text="💸 Вывести", callback_data="request_withdrawal")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_payout_keyboard(ticket_id):
    kb = [
        [InlineKeyboardButton(text="✅ Отстоял (Начислить)", callback_data=f"payout_success_{ticket_id}")],
        [InlineKeyboardButton(text="❌ Не отстоял", callback_data=f"payout_fail_{ticket_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_admin_payout_request_keyboard(user_id, amount):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Отправить чек ({amount:.2f} $)", callback_data=f"start_payout_{user_id}_{amount}")]
    ])

def get_admin_check_sent_keyboard(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☑️ Чек отправлен (Подтвердить)", callback_data=f"confirm_payout_{user_id}")]
    ])

def get_admin_keyboard():
    status_btn_text = "🟢 START WORK (Включить)" if not config_data["is_work_on"] else "🔴 STOP WORK (Выключить)"
    status_callback = "toggle_work"

    kb = [
        [InlineKeyboardButton(text=status_btn_text, callback_data=status_callback)],
        [InlineKeyboardButton(text="📢 Реклама", callback_data="broadcast")],
        [InlineKeyboardButton(text="💰 Выплаты (Pending)", callback_data="payout_menu")],
        [InlineKeyboardButton(text="👥 Юзеры и юзы", callback_data="show_users_stats")],
        [InlineKeyboardButton(text="✏️ Прайс", callback_data="edit_price"),
         InlineKeyboardButton(text="🖼 Фото", callback_data="edit_photo")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="close_admin")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- ХЕНДЛЕРЫ ---
# Декораторы @Command("start"), @F.data, @F.text удалены из функций, 
# регистрация происходит только в dp.message.register/dp.callback_query.register

async def cmd_start(message: types.Message, state: FSMContext, bot: Bot):
    if message.chat.id in active_chats:
        await message.answer("❌ У вас активен диалог! Нажмите 'Закончить чат'.", reply_markup=get_cancel_chat_keyboard())
        return

    await state.clear()
    user_id = message.from_user.id
    add_user_to_db(user_id)

    if not await check_subscription(bot, user_id):
        await message.answer("👋 Привет! Для доступа подпишись на каналы:", reply_markup=get_subs_keyboard())
        return

    orders, balance = get_user_stats(user_id)
    username = message.from_user.username if message.from_user.username else "Не указан"

    work_status_icon = "🟢" if config_data["is_work_on"] else "🔴"

    caption_text = (
        f"👋 <b>Привет, {message.from_user.first_name}!</b>\n\n"
        f"📊 <b>Твоя статистика:</b>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"👤 Логин: @{username}\n"
        f"📥 Заявок отправлено: <b>{orders}</b>\n"
        f"💸 Начислено: <b>{balance:.2f} $</b>\n"
        f"⚙️ Статус ворка: {work_status_icon}\n"
        f"➖➖➖➖➖➖➖➖➖\n"
        f"{config_data['price_text']}\n\n"
        f"👇 <i>Выбери действие в меню:</i>"
    )

    if config_data["menu_photo"]:
        await message.answer_photo(config_data["menu_photo"], caption=caption_text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    else:
        await message.answer(caption_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

async def cb_check_subs(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    if await check_subscription(bot, callback.from_user.id):
        await callback.message.delete()
        await callback.answer("✅ Подписка подтверждена!")
        await cmd_start(callback.message, state, bot) 
    else:
        await callback.answer("❌ Вы не подписались на все каналы!", show_alert=True)

async def show_price(message: types.Message, bot: Bot):
    if message.chat.id in active_chats: return
    if not await check_subscription(bot, message.from_user.id):
        await message.answer("❌ Подпишитесь на каналы!", reply_markup=get_subs_keyboard())
        return
    await message.answer(config_data["price_text"], parse_mode="Markdown")

async def show_balance_menu(message: types.Message, bot: Bot):
    if not await check_subscription(bot, message.from_user.id):
        await message.answer("❌ Подпишитесь на каналы!", reply_markup=get_subs_keyboard())
        return

    _, balance = get_user_stats(message.from_user.id)

    if balance > 0.001:
        text = f"💰 Ваш текущий баланс: **{balance:.2f} $**\n\nНажмите 'Вывести' для запроса выплаты."
    else:
        text = "💰 Ваш текущий баланс: **0.00 $**\n\nНакопите средства, чтобы запросить вывод."

    await message.answer(text, reply_markup=get_balance_keyboard(balance), parse_mode="Markdown")

async def request_withdrawal(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    username = callback.from_user.username or "Не указан"

    _, balance = get_user_stats(user_id)

    if balance < 0.001:
        await callback.answer("❌ На балансе нет средств для вывода.", show_alert=True)
        return

    current_state = await state.get_state()
    if current_state == UserState.withdrawing.state:
        await callback.answer("⚠️ Ваш запрос на вывод уже в обработке.", show_alert=True)
        return

    await state.set_state(UserState.withdrawing)

    safe_username = html.escape(username)
    text_to_admin = (
        f"🚨 <b>ЗАПРОС НА ВЫВОД СРЕДСТВ</b>\n"
        f"👤 Пользователь: <a href='tg://user?id={user_id}'>{callback.from_user.first_name}</a> (@{safe_username})\n"
        f"💸 Сумма: <b>{balance:.2f} $</b>\n"
        f"🆔 ID: <code>{user_id}</code>"
    )

    admin_kb = get_admin_payout_request_keyboard(user_id, balance)

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text_to_admin, reply_markup=admin_kb, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Ошибка отправки запроса админу {admin_id}: {e}")

    await callback.message.edit_text(
        f"✅ Запрос на вывод **{balance:.2f} $** отправлен администраторам.\nОжидайте обработки!",
        parse_mode="Markdown",
        reply_markup=None
    )
    await callback.answer("Запрос отправлен!")

async def ask_numbers(message: types.Message, state: FSMContext, bot: Bot):
    if message.chat.id in active_chats: return

    if not await check_subscription(bot, message.from_user.id):
        await message.answer("❌ Подпишитесь на каналы!", reply_markup=get_subs_keyboard())
        return

    if not config_data["is_work_on"]:
        await message.answer("⛔ <b>В данный момент прием заявок закрыт!</b>\nОжидайте начала ворка.", parse_mode="HTML")
        return

    await state.set_state(UserState.sending_numbers)
    await message.answer(
        "📝 **Введите номера (каждый с новой строки):**\n\n"
        "Форматы:\n"
        "✅ `+79171479470`\n"
        "✅ `89171479470`\n"
        "✅ `9171479470`",
        parse_mode="Markdown"
    )

async def receive_numbers(message: types.Message, state: FSMContext):
    # Логика команд внутри состояния
    if message.text in ["💰 Прайс", "💰 Баланс", "/start"]:
        await state.clear()
        await message.answer("🛑 Отмена ввода.", reply_markup=get_main_keyboard())
        return

    # Проверка статуса ворка
    if not config_data["is_work_on"]:
        await state.clear()
        await message.answer("⛔ <b>Ворк был остановлен админом.</b>", parse_mode="HTML")
        return

    # Валидация номеров
    phone_pattern = re.compile(r'^(\+7|7|8)?\d{10}$') 

    lines = message.text.strip().split('\n')
    valid_numbers = []
    bad_lines = []

    for line in lines:
        clean_line = line.strip()
        if not clean_line: continue
            
        if phone_pattern.match(clean_line):
            valid_numbers.append(clean_line)
        else:
            bad_lines.append(clean_line)

    if bad_lines:
        bad_text = "\n".join(bad_lines[:5])
        await message.answer(
            f"❌ **Ошибка формата!**\nСтроки не приняты:\n`{bad_text}`\n\n"
            "Принимаются только цифры. Исправьте.",
            parse_mode="Markdown"
        )
        return

    if not valid_numbers:
        await message.answer("❌ Вы не прислали ни одного номера.")
        return

    # Регистрация заявки
    user_id = message.from_user.id
    add_user_to_db(user_id)
    increment_user_orders(user_id)

    username = message.from_user.username or "NoUsername"
    safe_username = html.escape(username)

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Принять (Начать чат)", callback_data=f"connect_{user_id}")]])

    final_text = "\n".join(valid_numbers)

    # Отправка админу
    text_to_admin = (
        f"🔔 <b>НОВАЯ ЗАЯВКА</b>\n"
        f"👤 <a href='tg://user?id={user_id}'>{message.from_user.first_name}</a> (@{safe_username})\n"
        f"📝 Номера:\n"
        f"<code>{final_text}</code>"
    )

    current_bot = message.bot
    for admin_id in ADMIN_IDS:
        try:
            await current_bot.send_message(admin_id, text_to_admin, reply_markup=admin_kb, parse_mode="HTML")
            await asyncio.sleep(0.01)
        except Exception as e:
            logging.error(f"Ошибка отправки админу {admin_id}: {e}")

    await message.answer("📨 <b>Заявка отправлена!</b>\nОжидайте ответа администратора.", reply_markup=get_main_keyboard(), parse_mode="HTML")
    await state.clear()

# --- АДМИНКА И УПРАВЛЕНИЕ ---
async def admin_panel(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        users_list = get_all_users()
        pending_count = len(get_pending_tickets())
        status_text = "🟢 ВКЛЮЧЕН" if config_data["is_work_on"] else "🔴 ВЫКЛЮЧЕН"
        await message.answer(
            f"⚙️ **Админка**\n"
            f"👥 Людей: {len(users_list)}\n"
            f"💰 Ожидают проверки (Тикеты): {pending_count}\n"
            f"📡 Статус ворка: {status_text}",
            reply_markup=get_admin_keyboard(),
            parse_mode="Markdown"
        )
    else:
        await message.answer("⛔ У вас нет доступа к этой команде.")

# --- ХЕНДЛЕР: Статистика Пользователей (Юзы) ---
async def cb_show_users_stats(callback: types.CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS: return

    stats = get_all_users_stats()
    
    if not stats:
        await callback.answer("❌ В базе нет пользователей.")
        return

    await callback.answer("Подготовка списка...")
    
    response_text = "📊 **Список пользователей (ТОП-100 по юзам):**\n\n"
    
    for i, (user_id, orders_count, balance) in enumerate(stats[:100]):
        try:
            user_info = await bot.get_chat(user_id)
            first_name = html.escape(user_info.first_name or "Пользователь") 
            
            user_line = f"{i+1}. <a href='tg://user?id={user_id}'>{first_name}</a> (<code>{user_id}</code>)\n" \
                        f"   - Юзов: **{orders_count}**\n" \
                        f"   - Баланс: {balance:.2f} $\n"

        except Exception:
            user_line = f"{i+1}. Пользователь (<code>{user_id}</code>)\n" \
                        f"   - Юзов: **{orders_count}**\n" \
                        f"   - Баланс: {balance:.2f} $\n"
        
        if len(response_text) + len(user_line) > 4000:
            await callback.message.answer(response_text, parse_mode="HTML", disable_web_page_preview=True)
            response_text = "Продолжение списка:\n\n"
        
        response_text += user_line
        await asyncio.sleep(0.02)

    if response_text != "Продолжение списка:\n\n":
        await callback.message.answer(response_text, parse_mode="HTML", disable_web_page_preview=True)


async def cb_toggle_work(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return

    # 1. Меняем статус
    config_data["is_work_on"] = not config_data["is_work_on"]

    # 2. Обновляем сообщение 
    users_list = get_all_users()
    pending_count = len(get_pending_tickets())
    status_text = "🟢 ВКЛЮЧЕН" if config_data["is_work_on"] else "🔴 ВЫКЛЮЧЕН"

    text = (
        f"⚙️ **Админка**\n"
        f"👥 Людей: {len(users_list)}\n"
        f"💰 Ожидают проверки (Тикеты): {pending_count}\n"
        f"📡 Статус ворка: {status_text}"
    )

    try:
        await callback.message.edit_text(text=text, reply_markup=get_admin_keyboard(), parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка обновления админки: {e}")

    await callback.answer(f"Статус изменен на: {status_text}")

async def cb_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(AdminState.broadcasting)
    await callback.message.answer("📢 Введите текст рассылки:")
    await callback.answer()

async def send_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    users_list = get_all_users()
    sent = 0
    status = await message.answer("⏳ Рассылка...")
    for uid in users_list:
        try:
            await message.copy_to(uid)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception: pass
    await status.edit_text(f"✅ Рассылка: {sent}/{len(users_list)}")
    await state.clear()

async def cb_edit_price(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(AdminState.changing_price)
    await callback.message.answer("Новый прайс:")
    await callback.answer()

async def set_price(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    config_data["price_text"] = message.text
    await message.answer("✅ Прайс обновлен")
    await state.clear()

async def cb_edit_photo(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS: return
    await state.set_state(AdminState.changing_photo)
    await callback.message.answer("Пришлите фото:")
    await callback.answer()

async def set_photo(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    config_data["menu_photo"] = message.photo[-1].file_id
    await message.answer("✅ Фото обновлено")
    await state.clear()

async def cb_close(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    await callback.message.delete()


async def cb_payout_menu(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return

    tickets = get_pending_tickets()

    try:
        await callback.message.edit_text(f"💰 Ожидают проверки (Тикеты): **{len(tickets)}**", parse_mode="Markdown", reply_markup=None)
    except Exception:
        await callback.message.answer(f"💰 Ожидают проверки (Тикеты): **{len(tickets)}**", parse_mode="Markdown", reply_markup=None)


    if not tickets:
        await callback.answer("✅ Нет заявок в ожидании выплаты.")
        return

    await callback.answer("Список заявок отправлен!")
    current_bot = callback.bot
    for ticket_id, user_id, user_balance in tickets:
        try:
            user_info = await current_bot.get_chat(user_id)
            username = user_info.username or "NoUsername"
            first_name = user_info.first_name or "Пользователь"

            payout_text = (
                f"ID Тикета: <code>{ticket_id}</code>\n"
                f"Пользователь: <a href='tg://user?id={user_id}'>{first_name}</a> (@{username})\n"
                f"Баланс: {user_balance:.2f} $\n"
            )
            await callback.message.answer(payout_text, reply_markup=get_payout_keyboard(ticket_id), parse_mode="HTML")
            await asyncio.sleep(0.2)
        except Exception as e:
            logging.error(f"Ошибка получения инфо о юзере {user_id}: {e}")
            await callback.message.answer(f"❌ Тикет <code>{ticket_id}</code> (ID: <code>{user_id}</code>). Ошибка.", parse_mode="HTML")


async def cb_payout_success(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return

    ticket_id = int(callback.data.split("_")[-1])
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()

    cur.execute("SELECT user_id, amount, status FROM tickets WHERE id = ?", (ticket_id,))
    ticket_info = cur.fetchone()
    conn.close()

    if not ticket_info or ticket_info[2] != 'pending':
        await callback.answer("❌ Заявка уже обработана!", show_alert=True)
        try: await callback.message.delete()
        except Exception: pass
        return

    user_id, amount, _ = ticket_info

    add_payout_to_balance(user_id, amount)
    update_ticket_status(ticket_id, 'paid')

    try:
        await callback.bot.send_message(
            user_id,
            f"🎉 **Выплата начислена!**\nВам зачислено **{amount:.2f} $** по прайсу.\n\n"
            "Вы можете запросить вывод в меню 'Баланс'.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление юзеру {user_id} о начислении: {e}")

    await callback.message.edit_text(f"✅ Тикет <code>{ticket_id}</code>: **УСПЕХ** (+{amount:.2f} $ начислено юзеру).", parse_mode="HTML")
    await callback.answer("✅ Выплата начислена и юзер уведомлен.")


async def cb_payout_fail(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return

    ticket_id = int(callback.data.split("_")[-1])

    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,))
    status = cur.fetchone()
    conn.close()

    if not status or status[0] != 'pending':
        await callback.answer("❌ Заявка уже обработана!", show_alert=True)
        try: await callback.message.delete()
        except Exception: pass
        return

    update_ticket_status(ticket_id, 'failed')
    await callback.message.edit_text(f"❌ Тикет <code>{ticket_id}</code>: **ОТКАЗ** (Статус изменен на failed).", parse_mode="HTML")
    await callback.answer("❌ Статус изменен на 'Не отстоял'.")

# --- ПРОЦЕСС ВЫВОДА СРЕДСТВ АДМИНОМ ---

async def admin_start_payout(callback: types.CallbackQuery, state: FSMContext, dp: Dispatcher):
    if callback.from_user.id not in ADMIN_IDS: return

    user_id = int(callback.data.split("_")[2])
    
    # Проверка состояния пользователя (что он еще в процессе вывода)
    user_state = dp.fsm.get_context(callback.bot, user_id, user_id)
    current_user_state = await user_state.get_state()

    if current_user_state != UserState.withdrawing.state:
        await callback.answer("❌ Пользователь отменил запрос или он уже обработан.", show_alert=True)
        try: await callback.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
        return

    await state.set_state(AdminState.payout_check_uploading)
    await state.update_data(payout_user_id=user_id)

    await callback.message.edit_text(
        f"👉 Вы начали процесс выплаты для ID <code>{user_id}</code>.\n\n"
        f"1. **Отправьте чек** в канал выплат: <a href='{PAYOUT_CHANNEL_URL}'>{PAYOUT_CHANNEL_URL}</a>\n"
        f"2. **Нажмите кнопку** для подтверждения.",
        reply_markup=get_admin_check_sent_keyboard(user_id),
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    await callback.answer()

async def admin_confirm_payout(callback: types.CallbackQuery, state: FSMContext, dp: Dispatcher):
    if callback.from_user.id not in ADMIN_IDS: return

    data = await state.get_data()
    user_id = data.get("payout_user_id")

    if user_id is None:
        await callback.answer("❌ Ошибка контекста. Начните процесс заново.", show_alert=True)
        await state.clear()
        return

    _, final_balance = get_user_stats(user_id)

    # 1. Обнуление баланса
    reset_user_balance(user_id)

    # 2. Уведомление пользователя о списании и кнопкой
    payout_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Забрать выплату здесь", url=PAYOUT_CHANNEL_URL)]
    ])

    try:
        await callback.bot.send_message(
            user_id,
            f"🎉 **Выплата завершена!**\nС вашего баланса списано **{final_balance:.2f} $**.\n\n"
            "Чек отправлен в канал. Нажмите кнопку, чтобы забрать выплату.",
            reply_markup=payout_kb,
            parse_mode="Markdown"
        )
        # Сброс пользовательского состояния
        user_state = dp.fsm.get_context(callback.bot, user_id, user_id)
        await user_state.clear()
    except Exception as e:
        logging.error(f"Не удалось уведомить юзера {user_id} о завершении выплаты: {e}")

    # 3. Уведомление админа
    await callback.message.edit_text(
        f"✅ **Выплата для ID <code>{user_id}</code> завершена!**\nБаланс обнулен.",
        parse_mode="HTML",
        reply_markup=None
    )
    await callback.answer("✅ Выплата подтверждена и завершена.")
    await state.clear()


# --- ЧАТ И МОСТ ---
async def start_chat(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = int(callback.data.split("_")[1])

    # ЗАЩИТА ОТ ВТОРОГО АДМИНА
    if user_id in active_chats:
        if active_chats[user_id] == callback.from_user.id:
            await callback.answer("ℹ️ Вы уже общаетесь с этим пользователем!")
        else:
            await callback.answer("⛔ Заявку уже забрал другой админ!", show_alert=True)
            try: await callback.message.edit_reply_markup(reply_markup=None)
            except Exception: pass
        return

    admin_id = callback.from_user.id
    active_chats[admin_id] = user_id
    active_chats[user_id] = admin_id

    try: await callback.message.edit_reply_markup(reply_markup=None)
    except Exception: pass

    await callback.bot.send_message(admin_id, "✅ Чат начат. Выберите действие:", reply_markup=get_chat_management_keyboard())
    await callback.bot.send_message(user_id, "👨‍💻 Админ на связи!", reply_markup=get_cancel_chat_keyboard())
    await callback.answer()

async def number_taken(message: types.Message):
    admin_id = message.chat.id
    if admin_id in active_chats:
        user_id = active_chats.pop(admin_id)
        active_chats.pop(user_id, None)

        add_ticket(user_id, admin_id, PAYOUT_AMOUNT)

        try:
            await message.bot.send_message(
                user_id,
                "✅ **Спасибо за номер!**\nВаша заявка принята и поставлена на холд.\n\n"
                "Ожидайте вечера для проверки и начисления выплаты.",
                reply_markup=get_main_keyboard(),
                parse_mode="Markdown"
            )
        except Exception as e:
             logging.error(f"Не удалось отправить уведомление юзеру {user_id} о взятии номера: {e}")

        await message.answer("✅ Номер взят. Чат завершен, заявка в ожидании выплаты.", reply_markup=get_main_keyboard())


async def end_chat(message: types.Message):
    sender = message.chat.id
    if sender in active_chats:
        partner = active_chats.pop(sender)
        active_chats.pop(partner, None)
        try:
            await message.bot.send_message(partner, "🛑 Чат завершен", reply_markup=get_main_keyboard())
        except Exception: pass
        await message.answer("🛑 Чат завершен", reply_markup=get_main_keyboard())

# --- Обработка входящих сообщений в чате-мосте ---
async def bridge(message: types.Message):
    # Игнорируем команды, которые могли быть отправлены (если не обработаны ранее)
    if message.text and message.text.startswith("/"): return 
    
    sender = message.chat.id
    if sender in active_chats:
        try:
            # Пересылаем сообщение партнеру по чату
            await message.copy_to(active_chats[sender])
        except Exception:
            # Обработка ошибки, если партнёр заблокировал бота
            await message.answer("❌ Пользователь заблокировал бота.")
            # Завершаем чат в одностороннем порядке
            partner = active_chats.pop(sender)
            active_chats.pop(partner, None)

# --- ГЛАВНАЯ ФУНКЦИЯ ---
async def main():
    print("Бот запускается...")
    db_start() 
    
    # Инициализация бота
    if not TOKEN:
        logging.error("Токен Telegram не найден. Запуск невозможен.")
        return

    bot = Bot(token=TOKEN) 
    dp = Dispatcher(storage=MemoryStorage())

    # --- РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ---
    
    # Сообщения
    dp.message.register(cmd_start, Command("start")) 
    dp.message.register(admin_panel, Command("admin")) 
    dp.message.register(show_price, F.text == "💰 Прайс")
    dp.message.register(show_balance_menu, F.text == "💰 Баланс")
    dp.message.register(ask_numbers, F.text == "📱 Сдать номер")
    dp.message.register(receive_numbers, UserState.sending_numbers)
    
    dp.message.register(number_taken, F.text == "✅ Номер взят")
    dp.message.register(end_chat, F.text == "❌ Закончить чат")
    dp.message.register(bridge) # Мост должен быть последним, чтобы ловить все, что не команда/кнопка

    # Callback'и (Кнопки)
    dp.callback_query.register(cb_check_subs, F.data == "check_subs")
    dp.callback_query.register(request_withdrawal, F.data == "request_withdrawal")
    
    # Админка
    dp.callback_query.register(cb_show_users_stats, F.data == "show_users_stats")
    dp.callback_query.register(cb_toggle_work, F.data == "toggle_work")
    dp.callback_query.register(cb_broadcast, F.data == "broadcast")
    dp.message.register(send_broadcast, AdminState.broadcasting)
    dp.callback_query.register(cb_edit_price, F.data == "edit_price")
    dp.message.register(set_price, AdminState.changing_price)
    dp.callback_query.register(cb_edit_photo, F.data == "edit_photo")
    dp.message.register(set_photo, AdminState.changing_photo)
    dp.callback_query.register(cb_close, F.data == "close_admin")
    
    # Выплаты (Тикеты)
    dp.callback_query.register(cb_payout_menu, F.data == "payout_menu")
    dp.callback_query.register(cb_payout_success, F.data.startswith("payout_success_"))
    dp.callback_query.register(cb_payout_fail, F.data.startswith("payout_fail_"))
    
    # Вывод средств админом
    dp.callback_query.register(admin_start_payout, F.data.startswith("start_payout_"))
    dp.callback_query.register(admin_confirm_payout, F.data.startswith("confirm_payout_") & AdminState.payout_check_uploading)

    # Чат-мост
    dp.callback_query.register(start_chat, F.data.startswith("connect_"))

    try:
        await bot.delete_webhook(drop_pending_updates=True) 
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Критическая ошибка при поллинге: {e}")
    finally:
        print("Закрытие сессии бота...")
        if bot and bot.session:
            await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен (KeyboardInterrupt)")
    except SystemExit:
        pass
