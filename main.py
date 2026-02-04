import logging
import asyncio
import random
import aiosqlite
import ssl
import aiohttp
import time

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from aiocryptopay import AioCryptoPay

# ⚙️ КОНФИГУРАЦИЯ
BOT_TOKEN = "8595517681:AAGO7Ati4Jkm9LbzZ3LLUnw9-5xvdbqGRUc"
CRYPTO_PAY_TOKEN = "514479:AAb64Swo8pexGV3iVkgI4MqdlYYsg22BhOZ"
ADMIN_IDS = [8576762452, 8119723042]
DB_NAME = "flor_casino_premium.db"
MIN_BET = 0.1

logging.basicConfig(level=logging.INFO)
crypto = AioCryptoPay(token=CRYPTO_PAY_TOKEN)
dp = Dispatcher()

# --- СОСТОЯНИЯ ---
class States(StatesGroup):
    waiting_for_bet = State()
    waiting_for_turn = State()
    waiting_for_withdraw = State()
    admin_giving_balance = State()
    admin_broadcast = State()

# --- БАЗА ДАННЫХ ---
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

# --- КЛАВИАТУРЫ ---
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

# --- ОСНОВНЫЕ КОМАНДЫ ---
@dp.message(CommandStart())
@dp.callback_query(F.data == "start_over")
async def cmd_start(event: types.Message | types.CallbackQuery, state: FSMContext = None):
    if state: await state.clear()
    uid = event.from_user.id
    name = event.from_user.first_name
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (uid, name))
        await db.commit()
    text = f"👋 Привет, {name}!\n\n💎 **FLOR CASINO** — честные игры и быстрые выплаты."
    if isinstance(event, types.Message):
        await event.answer(text, reply_markup=main_menu_kb(uid), parse_mode="Markdown")
    else:
        await event.message.edit_text(text, reply_markup=main_menu_kb(uid), parse_mode="Markdown")

# --- ПОМОЩЬ И ПРАВИЛА ---
@dp.callback_query(F.data == "menu_help")
async def help_cmd(c: types.CallbackQuery):
    text = "ℹ️ **ПОДДЕРЖКА**\n\nПо всем вопросам пишите: @bolvink"
    kb = InlineKeyboardBuilder().button(text="🔙 НАЗАД", callback_data="start_over")
    await c.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "menu_rules")
async def rules_cmd(c: types.CallbackQuery):
    text = "📜 **ПРАВИЛА**\n\n1. Минимальная ставка: 0.1$\n2. Вывод от 1$ через CryptoBot\n3. Запрещено иметь мультиаккаунты."
    kb = InlineKeyboardBuilder().button(text="🔙 НАЗАД", callback_data="start_over")
    await c.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")

# --- ПРОФИЛЬ И БОНУС ---
@dp.callback_query(F.data == "menu_profile")
async def profile_cmd(c: types.CallbackQuery):
    u = await get_user(c.from_user.id)
    text = f"👤 **ПРОФИЛЬ**\n\n🆔 Ваш ID: `{u['user_id']}`\n💰 Баланс: `{u['balance']:.2f}$`"
    kb = InlineKeyboardBuilder().button(text="🔙 НАЗАД", callback_data="start_over")
    await c.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "menu_bonus")
async def bonus_cmd(c: types.CallbackQuery):
    u = await get_user(c.from_user.id)
    now = int(time.time())
    if now - u['last_bonus'] < 86400:
        return await c.answer("❌ Бонус доступен 1 раз в 24 часа!", show_alert=True)
    
    amt = round(random.uniform(0.01, 0.05), 2)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id = ?", (amt, now, c.from_user.id))
        await db.commit()
    await c.answer(f"🎁 Начислено: {amt}$", show_alert=True)
    await cmd_start(c)

# --- АДМИН ПАНЕЛЬ ---
@dp.callback_query(F.data == "menu_admin")
async def admin_main(c: types.CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardBuilder()
    kb.button(text="💰 ВЫДАТЬ БАЛАНС", callback_data="admin_give_bal")
    kb.button(text="📢 РАССЫЛКА", callback_data="admin_start_broadcast")
    kb.button(text="🔙 НАЗАД", callback_data="start_over")
    await c.message.edit_text("🔒 **АДМИН-ПАНЕЛЬ**", reply_markup=kb.adjust(1).as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_give_bal")
async def admin_give_bal_start(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id not in ADMIN_IDS: return
    await c.message.answer("Введите ID игрока и сумму через пробел (например: 12345 50):")
    await state.set_state(States.admin_giving_balance)

@dp.message(States.admin_giving_balance)
async def admin_give_bal_proc(m: types.Message, state: FSMContext):
    try:
        uid, amt = m.text.split()
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (float(amt), int(uid)))
            await db.commit()
        await m.answer(f"✅ Успешно выдано {amt}$ игроку {uid}")
    except: await m.answer("❌ Ошибка! Формат: [ID] [СУММА]")
    await state.clear()

@dp.callback_query(F.data == "admin_start_broadcast")
async def admin_broadcast_start(c: types.CallbackQuery, state: FSMContext):
    if c.from_user.id not in ADMIN_IDS: return
    await c.message.answer("Введите текст рассылки:")
    await state.set_state(States.admin_broadcast)

@dp.message(States.admin_broadcast)
async def admin_broadcast_proc(m: types.Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            users = await cur.fetchall()
    
    count = 0
    for user in users:
        try:
            await m.copy_to(user[0])
            count += 1
            await asyncio.sleep(0.05)
        except: continue
    
    await m.answer(f"✅ Рассылка завершена! Получили {count} человек.")
    await state.clear()

# --- КОШЕЛЕК И ВЫВОД ---
@dp.callback_query(F.data == "menu_wallet")
async def wallet_view(c: types.CallbackQuery):
    u = await get_user(c.from_user.id)
    txt = f"💳 **КОШЕЛЕК**\n\n💰 Ваш баланс: `{u['balance']:.2f}$`"
    kb = InlineKeyboardBuilder()
    kb.button(text="📤 ВЫВЕСТИ (CRYPTOBOT ЧЕК)", callback_data="withdraw")
    kb.button(text="🔙 НАЗАД", callback_data="start_over")
    await c.message.edit_text(txt, reply_markup=kb.adjust(1).as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "withdraw")
async def withdraw_cmd(c: types.CallbackQuery, state: FSMContext):
    u = await get_user(c.from_user.id)
    if u['balance'] < 1.0: return await c.answer("❌ Минимальный вывод от 1$", show_alert=True)
    await c.message.answer("💸 Введите сумму для вывода в $:")
    await state.set_state(States.waiting_for_withdraw)

@dp.message(States.waiting_for_withdraw)
async def process_withdrawal(m: types.Message, state: FSMContext):
    try:
        amount = float(m.text.replace(',', '.'))
        u = await get_user(m.from_user.id)
        if amount < 1.0 or amount > u['balance']: return await m.answer("❌ Ошибка суммы!")
        
        check = await crypto.create_check(asset='USDT', amount=amount)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, m.from_user.id))
            await db.commit()
            
        await m.answer(f"✅ **Вывод готов!**\nСумма: {amount}$\nЧек: {check.bot_check_url}", parse_mode="Markdown")
    except: await m.answer("❌ Ошибка!")
    await state.clear()

# --- ИГРЫ (ОСНОВНОЙ БЛОК) ---
@dp.callback_query(F.data == "menu_games")
async def games_list(c: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    games = [("🎯 Дартс", "darts"), ("⚽ Футбол", "football"), ("🎲 Кубик", "dice"), ("🎳 Боулинг", "bowling"), ("🏀 Баскет", "basket"), ("💣 Мины", "mines")]
    for n, code in games: kb.button(text=n, callback_data=f"play_{code}")
    kb.button(text="🔙 Назад", callback_data="start_over")
    await c.message.edit_text("🎰 **ВЫБЕРИТЕ ИГРУ**", reply_markup=kb.adjust(2).as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("play_"))
async def game_start(c: types.CallbackQuery, state: FSMContext):
    game = c.data.split("_")[1]
    await state.update_data(g=game)
    await c.message.answer(f"🕹 Игра: {game.upper()}\nВведите вашу ставку:")
    await state.set_state(States.waiting_for_bet)

@dp.message(States.waiting_for_bet)
async def handle_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(',', '.'))
        u = await get_user(m.from_user.id)
        if bet < MIN_BET or bet > u['balance']: return await m.answer("❌ Недостаточно средств!")
        
        data = await state.get_data()
        game = data['g']
        
        if game == "mines":
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (bet, m.from_user.id))
                await db.commit()
            f = ["0"]*20 + ["M"]*5
            random.shuffle(f)
            await state.update_data(field=f, bet=bet, opened=0, mult=1.0)
            return await m.answer(f"💣 МИНЫ | Ставка: {bet}$", reply_markup=get_mines_kb(f, bet))

        emo = {"darts":"🎯","football":"⚽","dice":"🎲","bowling":"🎳","basket":"🏀"}[game]
        await state.update_data(bet=bet, emo=emo)
        await m.answer(f"🕹 Бросайте {emo}!")
        await state.set_state(States.waiting_for_turn)
    except: await m.answer("Введите число!")

@dp.message(States.waiting_for_turn, F.dice)
async def dice_logic(m: types.Message, state: FSMContext):
    data = await state.get_data()
    if m.dice.emoji != data['emo']: return
    
    bet, p_val = data['bet'], m.dice.value
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (bet, m.from_user.id))
        await db.commit()

    await m.answer("🏦 Ход Банкира...")
    b_dice = await m.answer_dice(emoji=data['emo'])
    b_val = b_dice.dice.value
    await asyncio.sleep(4)

    win = bet * 1.9 if p_val > b_val else (bet * 0.93 if p_val == b_val else 0)
    res = "🎉 ВИН!" if win > bet else ("🤝 НИЧЬЯ" if win > 0 else "😔 ЛУЗ")

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, m.from_user.id))
        await db.commit()
    
    await m.answer(f"{res}\nВыигрыш: {win:.2f}$", reply_markup=main_menu_kb(m.from_user.id))
    await state.clear()

# --- МИНЫ ЛОГИКА ---
def get_mines_kb(f, win, over=False):
    kb = InlineKeyboardBuilder()
    for i, cell in enumerate(f):
        t = ("💣" if cell=="M" else "💎") if over else ("🟦" if cell!="O" else "💎")
        kb.button(text=t, callback_data="ignore" if over or cell=="O" else f"mine_click_{i}")
    kb.adjust(5)
    if not over: kb.row(types.InlineKeyboardButton(text=f"💰 ЗАБРАТЬ {win:.2f}$", callback_data="mine_cashout"))
    else: kb.row(types.InlineKeyboardButton(text="🔙 МЕНЮ", callback_data="start_over"))
    return kb.as_markup()

@dp.callback_query(F.data.startswith("mine_click_"))
async def mine_click(c: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    idx = int(c.data.split("_")[2])
    f, b, o = data["field"], data["bet"], data["opened"]
    if f[idx] == "M":
        await c.message.edit_text("💥 БОМБА!", reply_markup=get_mines_kb(f, 0, True))
        await state.clear()
    else:
        f[idx] = "O"
        o += 1
        m = 1.0 + (o * 0.4)
        await state.update_data(field=f, opened=o, mult=m)
        await c.message.edit_text(f"💎 x{m:.2f} | Сумма: {b*m:.2f}$", reply_markup=get_mines_kb(f, b*m))
    await c.answer()

@dp.callback_query(F.data == "mine_cashout")
async def mine_cash(c: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    win = data["bet"] * data.get("mult", 1.0)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, c.from_user.id))
        await db.commit()
    await c.message.edit_text(f"🤑 ВЫИГРЫШ: +{win:.2f}$", reply_markup=main_menu_kb(c.from_user.id))
    await state.clear()

@dp.callback_query(F.data == "ignore")
async def ignore_cb(c: types.CallbackQuery): await c.answer()

# --- ЗАПУСК ---
async def main():
    await init_db()
    ssl_c = ssl.create_default_context()
    ssl_c.check_hostname = False
    ssl_c.verify_mode = ssl.CERT_NONE
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_c)) as cs:
        session = AiohttpSession()
        session._client_session = cs
        bot = Bot(token=BOT_TOKEN, session=session)
        print(">>> БОТ ВКЛЮЧЕН")
        await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
