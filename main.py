import logging
import asyncio
import random
import aiosqlite

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiocryptopay import AioCryptoPay

# ⚙️ КОНФИГУРАЦИЯ
BOT_TOKEN = "8595517681:AAGO7Ati4Jkm9LbzZ3LLUnw9-5xvdbqGRUc"
CRYPTO_PAY_TOKEN = "514479:AAb64Swo8pexGV3iVkgI4MqdlYYsg22BhOZ"
ADMIN_IDS = [8576762452, 8119723042]
DB_NAME = "flor_casino_premium.db"
MIN_BET = 0.1

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
crypto = AioCryptoPay(token=CRYPTO_PAY_TOKEN)
dp = Dispatcher()

class States(StatesGroup):
    waiting_for_bet = State()
    waiting_for_turn = State()
    waiting_for_withdraw = State()
    admin_broadcast = State()
    admin_giving_balance = State()

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, 
                username TEXT, 
                balance REAL DEFAULT 0.0,
                last_bonus INTEGER DEFAULT 0
            )
        """)
        await db.commit()

async def get_user(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

# 🎨 ГЛАВНОЕ МЕНЮ
def main_menu_kb(user_id):
    kb = InlineKeyboardBuilder()
    kb.button(text="🎰 ИГРАТЬ", callback_data="menu_games")
    kb.button(text="👤 ПРОФИЛЬ", callback_data="menu_profile")
    kb.button(text="💳 КОШЕЛЕК", callback_data="menu_wallet")
    kb.button(text="🎁 БОНУС", callback_data="menu_bonus")
    kb.button(text="ℹ️ ПОМОЩЬ", callback_data="menu_help")
    kb.button(text="📜 ПРАВИЛА", callback_data="menu_rules")
    if user_id in ADMIN_IDS: 
        kb.button(text="🔒 АДМИНКА", callback_data="menu_admin")
    kb.adjust(1, 2, 1, 2, 1)
    return kb.as_markup()

# 🚀 СТАРТ
@dp.message(CommandStart())
@dp.callback_query(F.data == "start_over")
async def cmd_start(event: types.Message | types.CallbackQuery, state: FSMContext = None):
    if state: await state.clear()
    uid = event.from_user.id
    name = event.from_user.first_name
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (uid, name))
        await db.commit()

    text = f"👋 Привет, {name}!\n\n💎 FLOR CASINO — честные игры и быстрые выплаты."
    
    if isinstance(event, types.Message):
        await event.answer("Загрузка...", reply_markup=types.ReplyKeyboardRemove())
        await event.answer(text, reply_markup=main_menu_kb(uid))
    else:
        await event.message.edit_text(text, reply_markup=main_menu_kb(uid))
        await event.answer()

# 💳 КОШЕЛЕК (ИСПРАВЛЕН @bolvink)
@dp.callback_query(F.data == "menu_wallet")
async def wallet_view(callback: types.CallbackQuery):
    u = await get_user(callback.from_user.id)
    txt = f"💳 КОШЕЛЕК\n\nБаланс: {u['balance']:.2f}$"
    kb = InlineKeyboardBuilder()
    kb.button(text="🤖 АВТО-ПОПОЛНЕНИЕ", callback_data="deposit_auto")
    kb.button(text="👨‍💻 ЧЕРЕЗ ПОДДЕРЖКУ", url="https://t.me/bolvink")
    kb.button(text="📤 ВЫВЕСТИ", callback_data="withdraw")
    kb.button(text="🔙 НАЗАД", callback_data="start_over")
    await callback.message.edit_text(txt, reply_markup=kb.adjust(1).as_markup())
    await callback.answer()

# 🕹 ИГРЫ
@dp.callback_query(F.data == "menu_games")
async def games_list(callback: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    games = [("🎯 Дартс", "darts"), ("⚽ Футбол", "football"), ("🎲 Кубик", "dice"), ("🎳 Боулинг", "bowling"), ("🏀 Баскет", "basket"), ("💣 Мины", "mines")]
    for n, c in games: kb.button(text=n, callback_data=f"play_{c}")
    kb.button(text="🔙 Назад", callback_data="start_over")
    await callback.message.edit_text("🎰 ВЫБЕРИТЕ ИГРУ", reply_markup=kb.adjust(2).as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("play_"))
async def game_bet_step(callback: types.CallbackQuery, state: FSMContext):
    game = callback.data.split("_")[1]
    await state.update_data(g=game)
    await callback.message.answer(f"🕹 Выбрано: {game.upper()}\nВведите ставку:")
    await state.set_state(States.waiting_for_bet)
    await callback.answer()

@dp.message(States.waiting_for_bet)
async def start_game_logic(message: types.Message, state: FSMContext):
    try: bet = float(message.text.replace(',', '.'))
    except: return
    u = await get_user(message.from_user.id)
    if bet < MIN_BET or bet > u['balance']: return await message.answer("❌ Ошибка баланса")

    data = await state.get_data()
    game = data['g']

    if game == "mines":
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (bet, message.from_user.id))
            await db.commit()
        field = ["0"] * 20 + ["M"] * 5 
        random.shuffle(field)
        await state.update_data(field=field, bet=bet, opened=0, mult=1.0)
        await message.answer(f"💣 MINES | {bet:.2f}$", reply_markup=get_mines_kb(field, bet))
        return

    emo = {"darts":"🎯","football":"⚽","dice":"🎲","bowling":"🎳","basket":"🏀"}[game]
    await state.update_data(bet=bet, emo=emo)
    await message.answer(f"🕹 Отправь {emo} в чат!")
    await state.set_state(States.waiting_for_turn)

@dp.message(States.waiting_for_turn, F.dice)
async def handle_player_turn(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if message.dice.emoji != data.get('emo'): return
    
    bet, emo = data['bet'], data['emo']
    p_val = message.dice.value
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (bet, message.from_user.id))
        await db.commit()

    await message.answer("🏦 Ход Банкира...")
    b_msg = await message.answer_dice(emoji=emo)
    b_val = b_msg.dice.value
    await asyncio.sleep(4)

    win = 0
    if p_val > b_val:
        win = bet * 1.9
        res = f"🎉 ПОБЕДА! +{win:.2f}$"
    elif p_val == b_val:
        win = bet * 0.93 
        res = f"🤝 НИЧЬЯ! Возврат (комиссия 7%): +{win:.2f}$"
    else: res = "😔 ПРОИГРЫШ"

    async with aiosqlite.connect(DB_NAME) as db:
        if win > 0: await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, message.from_user.id))
        await db.commit()
    await message.answer(f"Итог: {p_val} vs {b_val}\n\n{res}", reply_markup=main_menu_kb(message.from_user.id))
    await state.clear()

# ℹ️ ПОМОЩЬ (ИСПРАВЛЕН @bolvink)
@dp.callback_query(F.data == "menu_help")
async def help_v(c: types.CallbackQuery):
    await c.message.edit_text("ℹ️ ПОМОЩЬ\n\nSupport - @bolvink", reply_markup=main_menu_kb(c.from_user.id))
    await c.answer()

@dp.callback_query(F.data == "menu_profile")
async def prof_v(c: types.CallbackQuery):
    u = await get_user(c.from_user.id)
    await c.message.edit_text(f"👤 ПРОФИЛЬ\n\nID: {u['user_id']}\nБаланс: {u['balance']:.2f}$", reply_markup=main_menu_kb(c.from_user.id))
    await c.answer()

# 🔒 АДМИНКА
@dp.callback_query(F.data == "menu_admin")
async def adm_v(c: types.CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardBuilder().button(text="💸 ВЫДАТЬ", callback_data="adm_give").button(text="📢 РАССЫЛКА", callback_data="adm_broadcast").adjust(1)
    await c.message.edit_text("⚙️ АДМИНКА", reply_markup=kb.as_markup())
    await c.answer()

@dp.callback_query(F.data == "adm_give")
async def adm_g(c: types.CallbackQuery, state: FSMContext):
    await c.message.answer("Введите ID и сумму через пробел:")
    await state.set_state(States.admin_giving_balance)
    await c.answer()

@dp.message(States.admin_giving_balance)
async def adm_ge(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        uid, amt = message.text.split()
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (float(amt), int(uid)))
            await db.commit()
        await message.answer("✅ Баланс успешно обновлен!")
    except: await message.answer("Ошибка формата!")
    await state.clear()

# 💣 МИНЫ (КЛАВИАТУРА)
def get_mines_kb(f, win, over=False):
    kb = InlineKeyboardBuilder()
    for i, cell in enumerate(f):
        text = ("💣" if cell == "M" else "💎") if over else ("🟦" if cell != "O" else "💎")
        kb.button(text=text, callback_data="ignore" if over or cell=="O" else f"mine_click_{i}")
    kb.adjust(5)
    if not over: kb.row(types.InlineKeyboardButton(text=f"💰 ЗАБРАТЬ {win:.2f}$", callback_data="mine_cashout"))
    else: kb.row(types.InlineKeyboardButton(text="🔙 МЕНЮ", callback_data="start_over"))
    return kb.as_markup()

@dp.callback_query(F.data.startswith("mine_click_"))
async def m_click(c: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    idx = int(c.data.split("_")[2])
    f, b, o = data["field"], data["bet"], data["opened"]
    if f[idx] == "M":
        await c.message.edit_text("💥 БОМБА! Проигрыш.", reply_markup=get_mines_kb(f, 0, True))
        await state.clear()
    else:
        f[idx] = "O"
        o += 1
        m = 1.0 + (o * 0.5)
        await state.update_data(field=f, opened=o, mult=m)
        await c.message.edit_text(f"💎 x{m:.2f} | Сумма: {b*m:.2f}$", reply_markup=get_mines_kb(f, b*m))
    await c.answer()

@dp.callback_query(F.data == "mine_cashout")
async def m_cash(c: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    w = data["bet"] * data.get("mult", 1.0)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (w, c.from_user.id))
        await db.commit()
    await c.message.edit_text(f"🤑 ВЫИГРЫШ: +{w:.2f}$", reply_markup=main_menu_kb(c.from_user.id))
    await state.clear()
    await c.answer()

@dp.callback_query(F.data == "ignore")
async def ign(c: types.CallbackQuery): await c.answer()

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
