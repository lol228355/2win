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
BOT_TOKEN = "8295201485:AAHenneA5CoKNz9SjDk89B33kv89-FxzKHY" # [cite: 1]
CRYPTO_PAY_TOKEN = "538568:AAuVgbjq7EYZWydFojnRp6CvMIbYaDOZDa8" # [cite: 1]

# ❗ ВАЖНО: ID админов (числа).
ADMIN_IDS = [8119723042, 8448843727] # [cite: 2]

MIN_BET = 0.1
MIN_DEPOSIT = 0.1
MIN_WITHDRAW = 1.0
BONUS_AMOUNT = 0.05
REFERRAL_REWARD = 0.1
REQUIRED_BIO_TEXT = "@Andcasino_bot_bot лучший бот для игр на $ с шансом 80% победы" # [cite: 2]

DB_NAME = "andron_casino.db" # [cite: 2]

# Дефолтные настройки для мин (чуть повышена отдача)
DEFAULT_MINES_CONFIG = {
    "1": 0.90, "3": 0.85, "5": 0.80, "8": 0.75,
    "10": 0.70, "15": 0.60, "20": 0.50, "24": 0.40
}

# Глобальные настройки (синхронизируются с БД)
GAME_CONFIG = {
    "dice_win": 1.8,   # Повышено для возможности играть [cite: 3]
    "dice_draw": 0.93, # Комиссия 7% (возврат 93%) [cite: 4]
    "mines_config": DEFAULT_MINES_CONFIG.copy() 
}

IMAGE_KEYS = {
    "start": "🏠 Главное меню (Приветствие)",
    "profile": "👤 Профиль",
    "wallet": "💳 Кошелек",
    "refs": "🤝 Рефералы",
    "bonus": "🎁 Бонус",
    "help": "ℹ️ Помощь",
    "rules": "📜 Правила",
    "games_menu": "🎮 Меню выбора игр",
    "game_mines": "💣 Игра: Мины (Заставка)",
    "game_dice": "🎲 Игра: Кубик/Дартс (Заставка)",
    "game_sport": "⚽ Игра: Спорт (Заставка)",
    "res_win": "🏆 Результат: Победа",
    "res_lose": "💀 Результат: Проигрыш",
    "res_draw": "⚖️ Результат: Ничья"
} # [cite: 4, 5]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
crypto = AioCryptoPay(token=CRYPTO_PAY_TOKEN)
dp = Dispatcher()

# --- ФУНКЦИИ ДЛЯ РАБОТЫ С CRYPTOBOT API ---
async def create_crypto_check(amount: float):
    url = "https://pay.crypt.bot/api/createCheck"
    payload = {"asset": "USDT", "amount": str(amount)}
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN, "Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json=payload)
        res = response.json()
        if res.get("ok"): # [cite: 6]
            return {"success": True, "check_url": res["result"]["bot_check_url"], "check_id": res["result"].get("check_id")}
        return {"success": False, "error": str(res.get("error"))} # [cite: 7]
    except Exception as e:
        return {"success": False, "error": str(e)}

# --- СОСТОЯНИЯ ---
class States(StatesGroup):
    waiting_for_captcha = State()
    waiting_for_bet = State()
    waiting_for_mines_count = State()
    waiting_for_turn = State()
    waiting_for_withdraw = State()
    waiting_for_deposit = State()
    admin_giving_balance = State()
    admin_manage_ban = State()
    admin_set_dice_win = State()
    admin_set_dice_draw = State()
    admin_upload_photo = State() # [cite: 8]

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
        """) # [cite: 9, 10, 11]
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT, 
                amount REAL,
                status TEXT DEFAULT 'pending', 
                invoice_id TEXT,
                check_id TEXT,
                created_at INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """) # [cite: 12, 13]
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                dice_win REAL DEFAULT 1.8,
                dice_draw REAL DEFAULT 0.93,
                mines_config TEXT
            )
        """) # [cite: 14]
        await db.execute("CREATE TABLE IF NOT EXISTS images (key_name TEXT PRIMARY KEY, file_id TEXT)") # [cite: 15]
        
        cursor = await db.execute("SELECT COUNT(*) FROM settings")
        if (await cursor.fetchone())[0] == 0:
             await db.execute("INSERT INTO settings (id, dice_win, dice_draw, mines_config) VALUES (1, 1.8, 0.93, ?)", (json.dumps(DEFAULT_MINES_CONFIG),))
        await db.commit()

async def load_settings():
    global GAME_CONFIG
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT dice_win, dice_draw, mines_config FROM settings WHERE id = 1") as cursor:
            row = await cursor.fetchone()
            if row:
                GAME_CONFIG["dice_win"] = row[0] # [cite: 21]
                GAME_CONFIG["dice_draw"] = row[1]
                if row[2]:
                    try: GAME_CONFIG["mines_config"] = json.loads(row[2])
                    except: GAME_CONFIG["mines_config"] = DEFAULT_MINES_CONFIG.copy() # [cite: 22]
        await db.commit()

async def get_user(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone() # [cite: 24, 25]

# --- УТИЛИТЫ ---
async def send_or_edit_media(event: Union[types.Message, types.CallbackQuery], key: str, text: str, markup=None):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT file_id FROM images WHERE key_name = ?", (key,))
        row = await cursor.fetchone()
        file_id = row[0] if row else None # [cite: 29]

    chat_id = event.from_user.id if isinstance(event, types.CallbackQuery) else event.chat.id
    
    if file_id:
        try:
            if isinstance(event, types.CallbackQuery):
                await event.message.delete() # [cite: 34]
            await bot.send_photo(chat_id, photo=file_id, caption=text, reply_markup=markup, parse_mode="Markdown") # [cite: 35]
            return
        except Exception: pass
    
    if isinstance(event, types.CallbackQuery):
        try:
            if event.message.photo: # [cite: 37]
                await event.message.delete()
                await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
            else:
                await event.message.edit_text(text, reply_markup=markup, parse_mode="Markdown") # [cite: 38]
        except TelegramBadRequest:
            await bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
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
    kb.button(text="ℹ️ ПОМОЩЬ", callback_data="menu_help")
    kb.button(text="📜 ПРАВИЛА", callback_data="menu_rules")
    if int(user_id) in ADMIN_IDS:
        kb.button(text="🔐 АДМИНКА", callback_data="admin_home")
    kb.adjust(1, 2, 2, 2, 1) # [cite: 44, 45]
    return kb.as_markup()

# --- ЛОГИКА ИГР ---
@dp.callback_query(F.data == "menu_games")
async def games_list(c: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    gs = [("🎯 Дартс", "darts"), ("🎲 Кубик", "dice"), ("⚽ Футбол", "football"), 
          ("🏀 Баскет", "basket"), ("🎳 Боулинг", "bowling"), ("💣 Мины", "mines")]
    for n, code in gs: kb.button(text=n, callback_data=f"play_{code}")
    kb.button(text="🔙 Назад", callback_data="start_over")
    kb.adjust(2)
    await send_or_edit_media(c, "games_menu", "🎰 **ВЫБЕРИТЕ ИГРУ**", kb.as_markup()) # [cite: 68]

@dp.callback_query(F.data.startswith("play_"))
async def game_start(c: types.CallbackQuery, state: FSMContext):
    game = c.data.split("_")[1]
    await state.update_data(g=game)
    img_key = "game_mines" if game == "mines" else "game_sport" if game in ["football", "basket"] else "game_dice"
    await send_or_edit_media(c, img_key, f"🕹 Выбрано: **{game.upper()}**\nВведите ставку:", InlineKeyboardBuilder().button(text="🔙 Отмена", callback_data="start_over").as_markup())
    await state.set_state(States.waiting_for_bet)

@dp.message(States.waiting_for_bet)
async def handle_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(',', '.'))
        u = await get_user(m.from_user.id)
        if bet < MIN_BET: return await m.answer(f"❌ Мин: {MIN_BET}$")
        if bet > float(u['balance']): return await m.answer("❌ Недостаточно средств")
        
        data = await state.get_data()
        if data['g'] == "mines":
            await state.update_data(bet=bet)
            kb = InlineKeyboardBuilder()
            for count in [1, 3, 5, 8, 10, 15, 20, 24]: kb.button(text=f"💣 {count}", callback_data=f"mines_set_{count}")
            kb.adjust(4).button(text="❌ Отмена", callback_data="start_over")
            await send_or_edit_media(m, "game_mines", f"💣 **Mines**\nСтавка: `{bet}$`", kb.as_markup())
            await state.set_state(States.waiting_for_mines_count)
        else:
            emo = {"darts":"🎯", "dice":"🎲", "football":"⚽", "basket":"🏀", "bowling":"🎳"}[data['g']]
            await state.update_data(bet=bet, emo=emo)
            await m.answer(f"Отправьте эмодзи {emo} для броска!")
            await state.set_state(States.waiting_for_turn)
    except Exception: await m.answer("❌ Введите число")

@dp.message(States.waiting_for_turn, F.dice)
async def dice_logic(m: types.Message, state: FSMContext):
    data = await state.get_data()
    if m.dice.emoji != data['emo']: return
    
    bet = data['bet']
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance - ?, total_bets = total_bets + ?, games_played = games_played + 1 WHERE user_id = ?", (bet, bet, m.from_user.id))
        await db.commit() # [cite: 80, 81]

    b_dice = await m.answer_dice(emoji=data['emo'])
    await asyncio.sleep(4)
    
    if m.dice.value > b_dice.dice.value:
        mult, res_key, res_text = GAME_CONFIG["dice_win"], "res_win", "🏆 ПОБЕДА"
    elif m.dice.value == b_dice.dice.value:
        mult, res_key, res_text = GAME_CONFIG["dice_draw"], "res_draw", "🤝 НИЧЬЯ" # [cite: 82]
    else:
        mult, res_key, res_text = 0, "res_lose", "💀 ПРОИГРЫШ"
    
    win = round(bet * mult, 2)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, m.from_user.id))
        await db.commit() # [cite: 83]
    
    text = f"**{res_text}!**\nВы: {m.dice.value} | Бот: {b_dice.dice.value}\nВыплата: `{win:.2f}$`"
    await send_or_edit_media(m, res_key, text, main_menu_kb(m.from_user.id))
    await state.clear()

# --- МИНЫ ---
def get_mines_kb(f, win, over=False):
    kb = InlineKeyboardBuilder()
    for i, cell in enumerate(f):
        if over: kb.button(text="💣" if cell=="M" else "💎", callback_data="ignore")
        else: kb.button(text="💎" if cell=="O" else "🟦", callback_data=f"m_cl_{i}")
    kb.adjust(5)
    if not over and win > 0: kb.row(types.InlineKeyboardButton(text=f"💰 ЗАБРАТЬ {win:.2f}$", callback_data="m_cash"))
    else: kb.row(types.InlineKeyboardButton(text="🔙 МЕНЮ", callback_data="start_over"))
    return kb.as_markup()

@dp.callback_query(States.waiting_for_mines_count, F.data.startswith("mines_set_"))
async def start_mines_game(c: types.CallbackQuery, state: FSMContext):
    mines_count = int(c.data.split("_")[2])
    data = await state.get_data()
    bet = data['bet']
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance - ?, total_bets = total_bets + ?, games_played = games_played + 1 WHERE user_id = ?", (bet, bet, c.from_user.id))
        await db.commit() # [cite: 73]
    
    f = ["M"] * mines_count + ["0"] * (25 - mines_count)
    random.shuffle(f)
    await state.update_data(field=f, mines_count=mines_count, opened=0, mult=1.0)
    await send_or_edit_media(c, "game_mines", f"💣 **MINES** | Ставка: `{bet}$`", get_mines_kb(f, 0))

@dp.callback_query(States.waiting_for_mines_count, F.data.startswith("m_cl_"))
async def mine_click(c: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    idx = int(c.data.split("_")[2])
    f = data["field"].copy()
    if f[idx] == "M":
        await send_or_edit_media(c, "res_lose", "💥 **ВЗРЫВ!**", get_mines_kb(f, 0, True))
        await state.clear()
    else:
        f[idx] = "O"
        o = data["opened"] + 1
        # Расчет коэффицента
        total, mines = 25, data['mines_count']
        chance = 1.0
        for i in range(o): chance *= (total - mines - i) / (total - i)
        mult = round(1.0 + ((1/chance) - 1.0) * GAME_CONFIG["mines_config"].get(str(mines), 0.9), 2)
        await state.update_data(field=f, opened=o, mult=mult)
        await c.message.edit_caption(caption=f"💎 **MINES** | x{mult}\nВыигрыш: `{data['bet']*mult:.2f}$`", reply_markup=get_mines_kb(f, data['bet']*mult))

@dp.callback_query(States.waiting_for_mines_count, F.data == "m_cash")
async def mine_cash(c: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    win = round(data["bet"] * data["mult"], 2)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, c.from_user.id))
        await db.commit() # [cite: 79]
    await send_or_edit_media(c, "res_win", f"🤑 **ЗАБРАНО: {win:.2f}$**", main_menu_kb(c.from_user.id))
    await state.clear()

# --- АДМИНКА (УПРАВЛЕНИЕ) ---
@dp.callback_query(F.data == "admin_home")
async def adm_panel(c: types.CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardBuilder()
    kb.button(text="💰 ВЫДАТЬ БАЛАНС", callback_data="adm_give")
    kb.button(text="⚙️ КОЭФФИЦИЕНТЫ", callback_data="adm_sets")
    kb.button(text="🖼 КАРТИНКИ", callback_data="adm_imgs")
    kb.button(text="🔙 НАЗАД", callback_data="start_over")
    await c.message.edit_text("🔐 **АДМИН-ПАНЕЛЬ**", reply_markup=kb.adjust(1).as_markup())

@dp.callback_query(F.data == "adm_sets")
async def adm_sets(c: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text=f"🎲 Победа: x{GAME_CONFIG['dice_win']}", callback_data="set_win")
    kb.button(text=f"⚖️ Ничья: x{GAME_CONFIG['dice_draw']}", callback_data="set_draw")
    kb.button(text="🔙 Назад", callback_data="admin_home")
    await c.message.edit_text("⚙️ **НАСТРОЙКА ИГР**", reply_markup=kb.adjust(1).as_markup())

@dp.callback_query(F.data == "set_win")
async def set_win_ask(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("Введите новый множитель победы (напр. 1.85):")
    await state.set_state(States.admin_set_dice_win)

@dp.message(States.admin_set_dice_win)
async def set_win_fin(m: types.Message, state: FSMContext):
    try:
        val = float(m.text)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE settings SET dice_win = ? WHERE id = 1", (val,))
            await db.commit()
        await load_settings()
        await m.answer(f"✅ Установлено: x{val}")
    except: await m.answer("Ошибка")
    await state.clear()

@dp.callback_query(F.data == "set_draw")
async def set_draw_ask(c: types.CallbackQuery, state: FSMContext):
    await m_text = "Введите множитель ничьей (0.93 = 7% комиссия):"
    await c.message.answer(m_text)
    await state.set_state(States.admin_set_dice_draw)

@dp.message(States.admin_set_dice_draw)
async def set_draw_fin(m: types.Message, state: FSMContext):
    try:
        val = float(m.text)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE settings SET dice_draw = ? WHERE id = 1", (val,))
            await db.commit()
        await load_settings()
        await m.answer(f"✅ Установлено: x{val}")
    except: await m.answer("Ошибка")
    await state.clear()

# --- СТАРТ ---
@dp.message(CommandStart())
@dp.callback_query(F.data == "start_over")
async def cmd_start(event: Union[types.Message, types.CallbackQuery]):
    uid = event.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username, created_at) VALUES (?, ?, ?)", (uid, event.from_user.first_name, int(time.time())))
        await db.commit() # [cite: 50]
    await send_or_edit_media(event, "start", "👋 Привет! Готов играть?", main_menu_kb(uid))

async def main():
    await init_db()
    await load_settings() # [cite: 101]
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
