import asyncio
import logging
import sqlite3
import re
import html
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

# --- 1. НАСТРОЙКА И КОНФИГУРАЦИЯ ---
logging.basicConfig(level=logging.INFO)

# !!! ВАШИ ДАННЫЕ !!!
TOKEN = "8322812128:AAHyE02VILzjMnOfRpvUqWzZiw536_XnfpY"

ADMIN_IDS = [
    8111456168,  # @eza6ka
    8394356460   # @Dom_sot
]
# !!! КОНЕЦ ВАШИХ ДАННЫХ !!!

# СПИСОК КАНАЛОВ (Оставьте пустым [], если не нужны, или заполните)
REQUIRED_CHANNELS = [
    # {"url": "https://t.me/channel_link", "id": "@channel_id", "name": "Канал"}
]

PAYOUT_CHANNEL_URL = "https://t.me/mymaksi" # Ссылка на канал выплат (для кнопки "Вывести")
PRICE_PER_MINUTE = 0.40 # Цена за 1 минуту в $

config_data = {
    "price_text": f"💰 *Прайс:* **{PRICE_PER_MINUTE:.2f}$/минута**",
    "menu_photo": None,
    "is_work_on": True # Включен ли ворк по умолчанию
}

# --- 2. STATES (FSM) ---
class UserState(StatesGroup):
    sending_numbers = State()
    withdrawing = State()
    reporting_wrong_code = State() # Состояние, чтобы не спамили кнопкой "Неверный код"

class AdminState(StatesGroup):
    setting_price_per_minute = State()
    changing_photo = State()
    broadcasting = State()
    payout_check_uploading = State() # Для подтверждения выплаты денег
    payout_set_minutes = State() # Ввод минут
    payout_send_photo = State() # Отправка фото кода

# --- 3. БД ФУНКЦИИ ---
def db_start():
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()

    # Таблица пользователей
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        orders_count INTEGER DEFAULT 0,
        balance REAL DEFAULT 0.0
    )''')
    
    # Таблица тикетов (заявок)
    cur.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        admin_id INTEGER,
        status TEXT, -- pending, paid, failed, code_error
        minutes INTEGER DEFAULT 0,
        amount REAL DEFAULT 0.0,
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

def get_pending_tickets():
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    # Показываем тикеты, которые ждут обработки или где ошибка кода
    cur.execute("SELECT id, user_id, status FROM tickets WHERE status = 'pending' OR status = 'code_error'")
    results = cur.fetchall()
    conn.close()
    return results

def get_ticket_info(ticket_id):
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("SELECT user_id, amount, status, minutes FROM tickets WHERE id = ?", (ticket_id,))
    result = cur.fetchone()
    conn.close()
    return result

def add_ticket(user_id):
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("INSERT INTO tickets (user_id, status, date) VALUES (?, 'pending', datetime('now'))", (user_id,))
    conn.commit()
    return cur.lastrowid

def update_ticket_status(ticket_id, status, minutes=None, amount=None):
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    if minutes is not None and amount is not None:
        cur.execute("UPDATE tickets SET status = ?, minutes = ?, amount = ? WHERE id = ?", (status, minutes, amount, ticket_id))
    else:
        cur.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    return [row[0] for row in cur.fetchall()]

def get_all_users_stats():
    conn = sqlite3.connect('users.db')
    cur = conn.cursor()
    cur.execute("SELECT user_id, orders_count, balance FROM users ORDER BY orders_count DESC")
    return cur.fetchall()

# --- 4. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def check_subscription(bot: Bot, user_id: int) -> bool:
    if not REQUIRED_CHANNELS: return True
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["id"], user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception:
            return True # Если ошибка доступа, пропускаем
    return True

# --- 5. КЛАВИАТУРЫ ---
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

def get_balance_keyboard(balance):
    kb = []
    if balance > 0.001:
        kb.append([InlineKeyboardButton(text="💸 Вывести", callback_data="request_withdrawal")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# Админские клавиатуры
def get_payout_action_keyboard(ticket_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отстоял (Ввести минуты)", callback_data=f"payout_start_minutes_{ticket_id}")],
        [InlineKeyboardButton(text="❌ Не отстоял (Отмена)", callback_data=f"payout_fail_{ticket_id}")]
    ])

def get_user_wrong_code_keyboard(ticket_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Неверный код / Повторить", callback_data=f"report_wrong_code_{ticket_id}")]
    ])

def get_admin_resend_photo_keyboard(user_id, ticket_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Переотправить фото", callback_data=f"start_payout_photo_{user_id}_{ticket_id}")]
    ])

def get_admin_keyboard():
    status_btn = "🟢 ВОРК ВКЛЮЧЕН" if config_data["is_work_on"] else "🔴 ВОРК ВЫКЛЮЧЕН"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=status_btn, callback_data="toggle_work")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="broadcast")],
        [InlineKeyboardButton(text="📋 Активные заявки", callback_data="payout_menu")],
        [InlineKeyboardButton(text="👥 Топ юзеров", callback_data="show_users_stats")],
        [InlineKeyboardButton(text="✏️ Цена ($/мин)", callback_data="edit_price_per_minute"),
         InlineKeyboardButton(text="🖼 Фото меню", callback_data="edit_photo")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="close_admin")]
    ])

# --- 6. ХЕНДЛЕРЫ ПОЛЬЗОВАТЕЛЯ ---
async def cmd_start(message: types.Message, state: FSMContext, bot: Bot):
    await state.clear()
    user_id = message.from_user.id
    add_user_to_db(user_id)

    if not await check_subscription(bot, user_id):
        await message.answer("Подпишись на каналы для доступа:", reply_markup=get_subs_keyboard())
        return

    orders, balance = get_user_stats(user_id)
    status_icon = "🟢" if config_data["is_work_on"] else "🔴"
    
    text = (
        f"👋 <b>Привет, {html.escape(message.from_user.first_name)}!</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📥 Заявок: <b>{orders}</b>\n"
        f"💸 Баланс: <b>{balance:.2f} $</b>\n"
        f"⚙️ Статус: {status_icon}\n\n"
        f"{config_data['price_text']}"
    )

    if config_data["menu_photo"]:
        await message.answer_photo(config_data["menu_photo"], caption=text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=get_main_keyboard(), parse_mode="HTML")

async def cb_check_subs(call: types.CallbackQuery, state: FSMContext, bot: Bot):
    if await check_subscription(bot, call.from_user.id):
        await call.message.delete()
        await cmd_start(call.message, state, bot)
    else:
        await call.answer("❌ Подпишитесь на все каналы!", show_alert=True)

async def show_price(message: types.Message):
    await message.answer(config_data["price_text"], parse_mode="Markdown")

async def show_balance(message: types.Message):
    _, balance = get_user_stats(message.from_user.id)
    text = f"💰 Баланс: **{balance:.2f} $**"
    await message.answer(text, reply_markup=get_balance_keyboard(balance), parse_mode="Markdown")

async def request_withdrawal(call: types.CallbackQuery, state: FSMContext, bot: Bot):
    user_id = call.from_user.id
    _, balance = get_user_stats(user_id)
    
    if balance < 0.01:
        await call.answer("❌ Баланс пуст.", show_alert=True)
        return

    await state.set_state(UserState.withdrawing)
    
    # Кнопки для админа для подтверждения вывода
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Выплатить ({balance:.2f}$)", callback_data=f"admin_pay_money_{user_id}")]
    ])
    
    msg_text = (
        f"🚨 <b>ЗАПРОС ВЫВОДА</b>\n"
        f"👤: <a href='tg://user?id={user_id}'>{call.from_user.first_name}</a> (ID: <code>{user_id}</code>)\n"
        f"💰 Сумма: <b>{balance:.2f} $</b>"
    )
    
    for admin in ADMIN_IDS:
        try: await bot.send_message(admin, msg_text, reply_markup=kb, parse_mode="HTML")
        except: pass
        
    await call.message.edit_text("✅ Запрос отправлен админам.", reply_markup=None)

# СДАЧА НОМЕРА
async def ask_numbers(message: types.Message, state: FSMContext, bot: Bot):
    if not await check_subscription(bot, message.from_user.id):
        await message.answer("❌ Подписка!", reply_markup=get_subs_keyboard())
        return
    if not config_data["is_work_on"]:
        await message.answer("⛔ Ворк сейчас выключен.")
        return

    await state.set_state(UserState.sending_numbers)
    await message.answer("📝 <b>Отправьте номера (списком):</b>\n\nФорматы: <code>+7...</code>, <code>8...</code>, <code>7...</code>", parse_mode="HTML")

async def receive_numbers(message: types.Message, state: FSMContext, bot: Bot):
    if message.text in ["/start", "💰 Прайс", "💰 Баланс", "📱 Сдать номер"]:
        await state.clear()
        await message.answer("Отмена.", reply_markup=get_main_keyboard())
        return

    # ВАЛИДАЦИЯ НОМЕРОВ
    # Регулярка для проверки: +7, 7 или 8 в начале, и 10 цифр после
    phone_pattern = re.compile(r'^(\+7|7|8)?\d{10}$')
    
    lines = message.text.strip().split('\n')
    valid_numbers = []
    bad_lines = []

    for line in lines:
        clean_line = line.strip()
        if not clean_line: continue
        
        # Проверяем формат
        if phone_pattern.match(clean_line):
            valid_numbers.append(clean_line)
        else:
            bad_lines.append(clean_line)

    # Если есть плохие строки - отказ
    if bad_lines:
        bad_text = "\n".join(bad_lines[:5])
        await message.answer(f"❌ <b>Ошибка формата!</b>\nЭти строки не подходят:\n<code>{bad_text}</code>\n\nПринимаются только номера (11 цифр).", parse_mode="HTML")
        return

    # Если вообще нет валидных номеров (например, пустой текст)
    if not valid_numbers:
        await message.answer("❌ Не найдено корректных номеров.")
        return

    # Если всё ок -> Сохраняем
    user_id = message.from_user.id
    increment_user_orders(user_id)
    ticket_id = add_ticket(user_id) # Создаем тикет
    
    final_text = "\n".join(valid_numbers)

    # Кнопка для админа "Проверить тикет"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Проверить/Ответить", callback_data=f"show_ticket_{ticket_id}")]
    ])
    
    admin_text = (
        f"🔔 <b>НОВАЯ ЗАЯВКА #{ticket_id}</b>\n"
        f"👤: <a href='tg://user?id={user_id}'>{message.from_user.first_name}</a>\n"
        f"📝 Номера:\n<code>{final_text}</code>"
    )

    for admin in ADMIN_IDS:
        try: await bot.send_message(admin, admin_text, reply_markup=kb, parse_mode="HTML")
        except: pass

    await message.answer(f"✅ <b>Заявка #{ticket_id} принята!</b>\nОжидайте проверки и кода.", reply_markup=get_main_keyboard(), parse_mode="HTML")
    await state.clear()

# --- 7. ХЕНДЛЕРЫ АДМИНА ---
async def admin_panel_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("⚙️ Админ-панель:", reply_markup=get_admin_keyboard())

# Просмотр конкретного тикета
async def cb_show_ticket(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    ticket_id = int(call.data.split("_")[-1])
    info = get_ticket_info(ticket_id)
    
    if not info or info[2] not in ['pending', 'code_error']:
        await call.answer("Заявка уже обработана.", show_alert=True)
        return

    user_id = info[0]
    status_emoji = "⚠️ Ошибка кода" if info[2] == 'code_error' else "⏳ Новая"
    
    text = (
        f"📝 <b>Тикет #{ticket_id}</b>\n"
        f"👤 ID юзера: <code>{user_id}</code>\n"
        f"Статус: {status_emoji}\n\n"
        f"Что делаем?"
    )
    await call.message.answer(text, reply_markup=get_payout_action_keyboard(ticket_id), parse_mode="HTML")
    await call.answer()

# Начало ввода минут
async def cb_payout_start_minutes(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS: return
    ticket_id = int(call.data.split("_")[-1])
    
    info = get_ticket_info(ticket_id)
    if not info: return # Обработана

    await state.set_state(AdminState.payout_set_minutes)
    await state.update_data(current_ticket=ticket_id, t_user_id=info[0])
    
    await call.message.edit_text(f"⌨️ <b>Тикет #{ticket_id}</b>\nВведите количество минут (числом):", parse_mode="HTML")

# Обработка ввода минут
async def process_minutes(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    if not message.text.isdigit():
        await message.answer("❌ Введите целое число.")
        return

    minutes = int(message.text)
    data = await state.get_data()
    ticket_id = data.get("current_ticket")
    user_id = data.get("t_user_id")
    
    amount = minutes * PRICE_PER_MINUTE
    
    # Сохраняем и обновляем БД
    update_ticket_status(ticket_id, 'paid', minutes, amount)
    add_payout_to_balance(user_id, amount)
    
    # Переход к фото
    await state.set_state(AdminState.payout_send_photo)
    await state.update_data(amount=amount)
    
    await message.answer(
        f"✅ Начислено: <b>{amount:.2f}$</b> ({minutes} мин).\n"
        f"📸 <b>Теперь отправьте ФОТО (код/чек) для юзера:</b>\n"
        f"(Или напишите 'отмена' текстом, чтобы не отправлять фото)", 
        parse_mode="HTML"
    )

# Отправка фото юзеру
async def process_payout_photo(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    
    # Если админ передумал (написал текст вместо фото)
    if message.text:
        await state.clear()
        await message.answer("Фото не отправлено. Выход в меню.", reply_markup=get_admin_keyboard())
        return

    if not message.photo:
        await message.answer("❌ Это не фото.")
        return

    data = await state.get_data()
    user_id = data.get("t_user_id")
    ticket_id = data.get("current_ticket")
    amount = data.get("amount", 0.0)

    try:
        # Отправляем копию фото юзеру с кнопкой "Неверный код"
        caption = f"✅ Выплата: {amount:.2f}$\nВаш код ниже 👇"
        await message.copy_to(
            chat_id=user_id, 
            caption=caption, 
            reply_markup=get_user_wrong_code_keyboard(ticket_id)
        )
        await message.answer("✅ Фото и деньги отправлены!", reply_markup=get_admin_keyboard())
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки юзеру: {e}")

    await state.clear()

# Если нажал "Не отстоял"
async def cb_payout_fail(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    ticket_id = int(call.data.split("_")[-1])
    update_ticket_status(ticket_id, 'failed')
    await call.message.edit_text("❌ Тикет помечен как 'Не отстоял'.")

# --- ЛОГИКА "НЕВЕРНЫЙ КОД" ---
async def cb_user_wrong_code(call: types.CallbackQuery, state: FSMContext, bot: Bot):
    ticket_id = int(call.data.split("_")[-1])
    user_id = call.from_user.id
    
    # Защита от спама
    current_state = await state.get_state()
    if current_state == UserState.reporting_wrong_code.state:
        await call.answer("Уже отправлено! Ждите.", show_alert=True)
        return

    # Обновляем статус в БД на ошибку
    update_ticket_status(ticket_id, 'code_error')
    
    # Уведомляем админов
    kb = get_admin_resend_photo_keyboard(user_id, ticket_id)
    text_adm = f"🚨 <b>ЖАЛОБА НА КОД!</b>\nТикет #{ticket_id}\nЮзер: <a href='tg://user?id={user_id}'>{user_id}</a>"
    
    for admin in ADMIN_IDS:
        try: await bot.send_message(admin, text_adm, reply_markup=kb, parse_mode="HTML")
        except: pass

    await state.set_state(UserState.reporting_wrong_code)
    await call.message.answer("❌ Жалоба отправлена админу. Ожидайте новый код.")
    await call.answer()

# Админ нажимает "Переотправить фото"
async def cb_resend_photo_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS: return
    parts = call.data.split("_")
    user_id = int(parts[3])
    ticket_id = int(parts[4])
    
    await state.set_state(AdminState.payout_send_photo)
    await state.update_data(t_user_id=user_id, current_ticket=ticket_id, amount=0) # amount 0 т.к. уже начислено
    
    await call.message.answer("📸 <b>Отправьте правильное ФОТО для юзера:</b>", parse_mode="HTML")
    await call.answer()

# --- Остальные админские кнопки ---
async def cb_payout_menu(call: types.CallbackQuery):
    tickets = get_pending_tickets()
    if not tickets:
        await call.answer("Нет заявок.", show_alert=True)
        return
    
    await call.message.answer(f"Найдено {len(tickets)} заявок:")
    for t in tickets:
        status_icon = "⚠️" if t[2] == 'code_error' else "🆕"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➡️ Открыть", callback_data=f"show_ticket_{t[0]}")]])
        await call.message.answer(f"{status_icon} Тикет #{t[0]} (ID: {t[1]})", reply_markup=kb)
    await call.answer()

async def cb_edit_price(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS: return
    await state.set_state(AdminState.setting_price_per_minute)
    await call.message.answer("Введите новую цену за минуту (например 0.45):")

async def set_new_price(message: types.Message, state: FSMContext):
    global PRICE_PER_MINUTE
    try:
        PRICE_PER_MINUTE = float(message.text)
        config_data["price_text"] = f"💰 *Прайс:* **{PRICE_PER_MINUTE:.2f}$/минута**"
        await message.answer(f"✅ Цена изменена: {PRICE_PER_MINUTE}")
    except:
        await message.answer("❌ Ошибка. Введите число (через точку).")
    await state.clear()

async def cb_toggle_work(call: types.CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    config_data["is_work_on"] = not config_data["is_work_on"]
    await call.message.edit_reply_markup(reply_markup=get_admin_keyboard())

async def admin_pay_money(call: types.CallbackQuery, bot: Bot):
    if call.from_user.id not in ADMIN_IDS: return
    user_id = int(call.data.split("_")[-1])
    
    reset_user_balance(user_id)
    
    try:
        await bot.send_message(user_id, "✅ <b>Выплата произведена!</b> Проверьте ваш кошелек.", parse_mode="HTML")
    except: pass
    
    await call.message.edit_text(f"✅ Выплата для {user_id} подтверждена админом.")

# --- 8. ЗАПУСК ---
async def main():
    db_start()
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # User Handlers
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(ask_numbers, F.text == "📱 Сдать номер")
    dp.message.register(receive_numbers, UserState.sending_numbers)
    dp.message.register(show_price, F.text == "💰 Прайс")
    dp.message.register(show_balance, F.text == "💰 Баланс")
    dp.callback_query.register(cb_check_subs, F.data == "check_subs")
    dp.callback_query.register(request_withdrawal, F.data == "request_withdrawal")
    dp.callback_query.register(cb_user_wrong_code, F.data.startswith("report_wrong_code_"))

    # Admin Handlers
    dp.message.register(admin_panel_cmd, Command("admin"))
    dp.callback_query.register(cb_payout_menu, F.data == "payout_menu")
    dp.callback_query.register(cb_show_ticket, F.data.startswith("show_ticket_"))
    dp.callback_query.register(cb_payout_start_minutes, F.data.startswith("payout_start_minutes_"))
    dp.message.register(process_minutes, AdminState.payout_set_minutes)
    dp.message.register(process_payout_photo, AdminState.payout_send_photo)
    dp.callback_query.register(cb_payout_fail, F.data.startswith("payout_fail_"))
    dp.callback_query.register(cb_resend_photo_start, F.data.startswith("start_payout_photo_"))
    
    dp.callback_query.register(cb_toggle_work, F.data == "toggle_work")
    dp.callback_query.register(cb_edit_price, F.data == "edit_price_per_minute")
    dp.message.register(set_new_price, AdminState.setting_price_per_minute)
    dp.callback_query.register(admin_pay_money, F.data.startswith("admin_pay_money_"))
    
    # Close button
    dp.callback_query.register(lambda c: c.message.delete(), F.data == "close_admin")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())
