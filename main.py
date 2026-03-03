import logging
import asyncio
import random
import aiosqlite
import time
import requests
import json
from datetime import datetime
from typing import Dict, Any, Union

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiocryptopay import AioCryptoPay

# ⚙️ КОНФИГУРАЦИЯ
BOT_TOKEN = "8295201485:AAHenneA5CoKNz9SjDk89B33kv89-FxzKHY"
CRYPTO_PAY_TOKEN = "538568:AAuVgbjq7EYZWydFojnRp6CvMIbYaDOZDa8"

# ❗ ВАЖНО: ID админов
ADMIN_IDS = [8119723042, 8448843727]

MIN_BET = 0.1
MIN_DEPOSIT = 0.1
MIN_WITHDRAW = 1.0
BONUS_AMOUNT = 0.05
REFERRAL_REWARD = 0.1
REQUIRED_BIO_TEXT = "@Andcasino_bot_bot лучший бот для игр на $ с шансом 80% победы"

DB_NAME = "andron_casino.db"

# Дефолтные настройки для мин
DEFAULT_MINES_CONFIG = {
    "1": 0.85, "3": 0.80, "5": 0.75, "8": 0.70,
    "10": 0.65, "15": 0.60, "20": 0.50, "24": 0.40
}

# Глобальные настройки коэффициентов
GAME_CONFIG = {
    "dice_win": 1.8,     # Коэффициент победы (увеличено) [cite: 167]
    "dice_draw": 0.93,    # Комиссия 7% при ничьей [cite: 167]
    "mines_config": DEFAULT_MINES_CONFIG.copy() 
}

IMAGE_KEYS = {
    "start": "🏠 Главное меню",
    "profile": "👤 Профиль",
    "wallet": "💳 Кошелек",
    "refs": "🤝 Рефералы",
    "bonus": "🎁 Бонус",
    "help": "ℹ️ Помощь",
    "rules": "📜 Правила",
    "games_menu": "🎮 Меню выбора игр",
    "game_mines": "💣 Игра: Мины",
    "game_dice": "🎲 Игра: Кубик/Дартс",
    "game_sport": "⚽ Игра: Спорт",
    "res_win": "🏆 Результат: Победа",
    "res_lose": "💀 Результат: Проигрыш",
    "res_draw": "⚖️ Результат: Ничья"
}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
crypto = AioCryptoPay(token=CRYPTO_PAY_TOKEN)
dp = Dispatcher()

# --- СОСТОЯНИЯ ---
class States(StatesGroup):
    waiting_for_captcha = State()
    waiting_for_bet = State()
    waiting_for_mines_count = State()
    waiting_for_turn = State()
    waiting_for_withdraw = State()
    waiting_for_deposit = State()
    admin_giving_balance = State()
    admin_set_dice_win = State()
    admin_set_dice_draw = State()
    admin_set_mines_specific = State()
    admin_upload_photo = State()

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, 
                username TEXT, 
                balance REAL DEFAULT 0.0,
                last_bonus INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                is_verified INTEGER DEFAULT 0,
                referrer_id INTEGER DEFAULT 0,
                referral_paid INTEGER DEFAULT 0,
                total_deposited REAL DEFAULT 0.0,
                total_withdrawn REAL DEFAULT 0.0,
                total_bets REAL DEFAULT 0.0,
                created_at INTEGER DEFAULT 0,
                games_played INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                dice_win REAL DEFAULT 1.8,
                dice_draw REAL DEFAULT 0.93,
                mines_config TEXT
            )
        """)
        await db.execute("CREATE TABLE IF NOT EXISTS images (key_name TEXT PRIMARY KEY, file_id TEXT)")
        
        cursor = await db.execute("SELECT COUNT(*) FROM settings")
        if (await cursor.fetchone())[0] == 0:
             await db.execute("INSERT INTO settings (id, dice_win, dice_draw, mines_config) VALUES (1, 1.8, 0.93, ?)", (json.dumps(DEFAULT_MINES_CONFIG),))
        await db.commit()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def get_user(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def load_settings():
    global GAME_CONFIG
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT dice_win, dice_draw, mines_config FROM settings WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            if row:
                GAME_CONFIG["dice_win"] = row[0]
                GAME_CONFIG["dice_draw"] = row[1]
                if row[2]: GAME_CONFIG["mines_config"] = json.loads(row[2])

async def send_or_edit_media(event, key, text, markup=None):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT file_id FROM images WHERE key_name = ?", (key,))
        row = await cursor.fetchone()
        file_id = row[0] if row else None

    chat_id = event.from_user.id
    if file_id:
        try:
            if isinstance(event, types.CallbackQuery): await event.message.delete()
            await bot.send_photo(chat_id, photo=file_id, caption=text, reply_markup=markup, parse_mode="Markdown")
            return
        except: pass
    
    if isinstance(event, types.CallbackQuery):
        try: await event.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        except: await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
    else:
        await event.answer(text, reply_markup=markup, parse_mode="Markdown")

# --- КЛАВИАТУРЫ ---
def main_menu_kb(user_id):
    kb = InlineKeyboardBuilder()
    kb.button(text="🎰 ИГРАТЬ", callback_data="menu_games")
    kb.button(text="👤 ПРОФИЛЬ", callback_data="menu_profile")
    kb.button(text="💳 КОШЕЛЕК", callback_data="menu_wallet")
    kb.button(text="🤝 РЕФЕРАЛЫ", callback_data="menu_refs")
    kb.button(text="🎁 БОНУС", callback_data="menu_bonus")
    if user_id in ADMIN_IDS:
        kb.button(text="🔐 АДМИНКА", callback_data="admin_home")
    kb.adjust(1, 2, 2, 1)
    return kb.as_markup()

# --- ОБРАБОТЧИКИ ИГР ---
@dp.callback_query(F.data == "menu_games")
async def games_list(c: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    gs = [("🎯 Дартс", "darts"), ("🎲 Кубик", "dice"), ("⚽ Футбол", "football"), 
          ("🏀 Баскет", "basket"), ("🎳 Боулинг", "bowling"), ("💣 Мины", "mines")]
    for n, code in gs: kb.button(text=n, callback_data=f"play_{code}")
    kb.button(text="🔙 Назад", callback_data="start_over")
    kb.adjust(2)
    await send_or_edit_media(c, "games_menu", "🎰 **ВЫБЕРИТЕ ИГРУ**", kb.as_markup())

@dp.message(States.waiting_for_turn, F.dice)
async def dice_logic(m: types.Message, state: FSMContext):
    data = await state.get_data()
    if m.dice.emoji != data['emo']: return
    
    bet = data['bet']
    # Снимаем ставку
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance - ?, total_bets = total_bets + ?, games_played = games_played + 1 WHERE user_id = ?", (bet, bet, m.from_user.id))
        await db.commit()

    b_dice = await m.answer_dice(emoji=data['emo'])
    await asyncio.sleep(4)

    # Логика всех игр (Dice/Sport) использует общие коэффициенты из настроек
    if m.dice.value > b_dice.dice.value:
        mult = GAME_CONFIG["dice_win"]
        res_text = "🏆 ПОБЕДА"
    elif m.dice.value == b_dice.dice.value:
        mult = GAME_CONFIG["dice_draw"] # 0.93 (Комиссия 7%)
        res_text = "🤝 НИЧЬЯ"
    else:
        mult = 0
        res_text = "💀 ПРОИГРЫШ"

    win = round(bet * mult, 2)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, m.from_user.id))
        await db.commit()

    text = f"**{res_text}!**\nВы: {m.dice.value} | Бот: {b_dice.dice.value}\nВыплата: `{win}$`"
    await m.answer(text, reply_markup=main_menu_kb(m.from_user.id))
    await state.clear()

# --- СТАРТ ---
@dp.message(CommandStart())
@dp.callback_query(F.data == "start_over")
async def cmd_start(event: Union[types.Message, types.CallbackQuery]):
    uid = event.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username, created_at) VALUES (?, ?, ?)", 
                         (uid, event.from_user.first_name, int(time.time())))
        await db.commit()
    
    text = f"👋 **Привет, {event.from_user.first_name}!**\nВыбирай игру и начни выигрывать!"
    await send_or_edit_media(event, "start", text, main_menu_kb(uid))

async def main():
    await init_db()
    await load_settings()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
