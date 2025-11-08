import asyncio
import random
from datetime import datetime, timedelta, timezone

import matplotlib.pyplot as plt
from io import BytesIO

import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, Router, types, F, BaseMiddleware
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputFile, ChatMemberUpdated, \
    Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from zoneinfo import ZoneInfo
from aiogram.types import PreCheckoutQuery
from aiogram.types import InputMediaPhoto

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = "7920430649:AAEZyU_dF1C_xfLj5XGO3z0lZH5I4w521ss"
CRYPTO_TOKEN = "245713:AAjbBo91sdpf0dBvELDpIMaM7blgdG0EBss"  # из @CryptoBot -> BotFather style token
CRYPTO_API_URL = "https://pay.crypt.bot/api/"
CHECK_INTERVAL = 12  # как часто проверять счета (сек)

OWNER_IDS = [5747423404, 7510524298]  # ваш Telegram user_id (админ без КД)

MONGO_URL = "mongodb+srv://lastix12s_db_user:333111@khryak.p2sseyb.mongodb.net/"
DB_NAME = "khryaks"

# ================== ИНИЦ ==================
bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ================== MongoDB ==================
client = AsyncIOMotorClient("mongodb+srv://lastix12s_db_user:333111@khryak.p2sseyb.mongodb.net/")
db = client.khryaks
pigs_col = db.pigs
payments = db.payments
user_boosts = db.user_boosts
chat_boosts = db.chat_boosts
promo_codes_col = db.promo_codes
promo_uses_col = db.promo_uses
user_boosts_col = db.user_boosts
payments_col = db.payments
user_rp_col = db.user_rp
chats_col = db.chats
pigs = pigs_col

# ================== COOLDOWN ==================
def can_use_cooldown(last_iso: str | None, hours: int, uid: int) -> bool:
    """
    Проверяет, можно ли использовать действие с учетом кулдауна.
    Владельцы всегда могут использовать.
    """
    if is_owner(uid):
        return True
    if not last_iso:
        return True
    try:
        last_time = datetime.fromisoformat(last_iso)
    except Exception:
        return True
    return datetime.now() - last_time >= timedelta(hours=hours)


# ================== ИМЯ ПОЛЬЗОВАТЕЛЯ ==================
def fmt_name(user: types.User) -> str:
    """
    Возвращает полное имя пользователя, username или 'Игрок', если нет данных.
    """
    return user.full_name or user.username or "Игрок"


# ================== СТАТУС СВИНКИ ==================
def pig_status(weight: float, strength: float):
    """
    Вычисляет коэффициент K = strength / weight.
    Возвращает статус свинки и текстовое описание.

    1 — идеал; <1 — тяжеловата; >1 — худовата.
    """
    K = float(strength) / max(float(weight), 1e-6)

    if K < 0.5:
        return "obese", f"⚠️ Ожирение — свинка слишком тяжёлая. K={K:.2f} Урон свинки уменьшен на 50%!"
    elif K < 0.8:
        return "heavy", f"🙂 Нормально — свинка немного тяжеловата. K={K:.2f}"
    elif K <= 1.2:
        return "ideal", f"💎 Идеал — баланс веса и силы. K={K:.2f}"
    elif K <= 1.8:
        return "underweight", f"🍽 Недобор — свинка слегка худая. K={K:.2f}"
    else:
        return "starving", f"⚠️ Истощение — свинка слишком худая. K={K:.2f} Урон свинки уменьшен на 50%!"



WELCOME_MESSAGE = (
    "🐷 Добро пожаловать!\n\n"
    "Команды:\n"
    "/sway — раз в 24ч изменить вес и силу\n"
    "/profile — профиль\n"
    "/info_chat — инфо чата\n"
    "/farma — фарм обычных монет (30–150) раз в 4ч\n"
    "/shop — магазин (покупка Хрякоинов за TON)\n"
    "/boost — бусты (личные и на чат)\n"
    "/fight — бой (по ответу на сообщение)\n"
    "/balance — баланс монет и хрякоинов\n"
    "/case — кейсы\n"
    "/top — топ 10 по чату\n"
    "/global — глобальный топ 10\n"
    "/help — помощь\n"
)

async def bot_is_admin(chat_id: int) -> bool:
    """Проверяем, админ ли бот в чате."""
    me = await bot.get_me()
    member = await bot.get_chat_member(chat_id, me.id)
    return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]

class RequireAdminMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        # Проверяем только команды в группах
        if isinstance(event, types.Message) and event.chat.type in ("group", "supergroup"):
            if event.text and event.text.startswith("/"):
                bot_info = data.get("bot_info")
                if not bot_info:
                    bot_info = await bot.get_me()
                    data["bot_info"] = bot_info

                member = await bot.get_chat_member(event.chat.id, bot_info.id)

                if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                    await event.reply(
                        "⚠️ У меня нет прав администратора!\n"
                        "Пожалуйста, дайте права администратора (кроме анонимности)."
                    )
                    return  # блокируем выполнение команды

        # Если бот админ — передаём дальше
        return await handler(event, data)

# Подключение мидлвари к роутеру
router.message.middleware(RequireAdminMiddleware())

# Событие, когда меняют статус бота
@router.my_chat_member()
async def bot_status_change(update: types.ChatMemberUpdated):
    me = await bot.get_me()
    old_status = update.old_chat_member.status
    new_status = update.new_chat_member.status

    if update.new_chat_member.user.id == me.id:
        if old_status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR] and \
           new_status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            await bot.send_message(update.chat.id, f"🎉 Спасибо, что сделали меня админом!\n\n{WELCOME_MESSAGE}")
        elif new_status == ChatMemberStatus.KICKED:
            print(f"Бот был удален из чата {update.chat.id}")


# --- ОТСЛЕЖИВАНИЕ ЧАТОВ ---
@router.my_chat_member()
async def track_chats(update: ChatMemberUpdated):
    chat = update.chat
    status = update.new_chat_member.status

    # Debug вывод
    print(f"[DEBUG] chat_id={chat.id}, title={chat.title}, type={chat.type}, status={status}")

    if status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        chat_doc = {
            "chat_id": chat.id,
            "title": chat.title or "",
            "chat_type": chat.type or "unknown",
            "added_at": datetime.now(timezone.utc).isoformat()
        }
        # upsert=True => если есть, обновит; если нет, создаст
        await db.chats.update_one(
            {"chat_id": chat.id},
            {"$set": chat_doc},
            upsert=True
        )
    elif status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
        await db.chats.delete_one({"chat_id": chat.id})



# --- КОМАНДА /stats ---
@router.message(Command("stats"))
async def stats_handler(message: types.Message):
    # Получаем количество чатов по типам
    total = await db.chats.count_documents({})
    privates = await db.chats.count_documents({"chat_type": "private"})
    groups = await db.chats.count_documents({"chat_type": "group"})
    supergroups = await db.chats.count_documents({"chat_type": "supergroup"})
    channels = await db.chats.count_documents({"chat_type": "channel"})
    unknown = await db.chats.count_documents({"chat_type": "unknown"})

    text = (
        f"📊 Статистика бота:\n\n"
        f"Всего чатов: {total}\n"
        f"👤 Приватные: {privates}\n"
        f"👥 Группы: {groups}\n"
        f"🌐 Супергруппы: {supergroups}\n"
        f"📢 Каналы: {channels}\n"
        f"❓ Неизвестные: {unknown}"
    )
    await message.answer(text)


# --- КОМАНДА /sync (ручная синхронизация текущего чата) ---
@router.message(Command("sync"))
async def sync_chats(message: types.Message):
    chat = message.chat
    chat_doc = {
        "chat_id": chat.id,
        "title": chat.title or "",
        "chat_type": chat.type or "unknown",
        "added_at": datetime.now(timezone.utc).isoformat()
    }

    # upsert=True => если чат есть — обновит, если нет — создаст
    await db.chats.update_one(
        {"chat_id": chat.id},
        {"$set": chat_doc},
        upsert=True
    )

    await message.answer(f"✅ Синхронизирован чат: {chat_doc['title']} ({chat_doc['chat_type']})")


# Обработчик команды /start только в ЛС
@router.message(CommandStart())
async def cmd_start(message: types.Message):
    if message.chat.type != "private":
        return

    kb = InlineKeyboardBuilder()
    kb.button(
        text="➕ Добавить меня в чат",
        url=f"https://t.me/{(await bot.me()).username}?startgroup=start"
    )
    kb.adjust(1)

    await message.answer(
        "Привет! 👋\n\n"
        "Я — бот, чтобы выращивать своего хряка 🐷\n\n"
        "📌 Бот работает только в чатах.\n"
        "Раз в 24 часа игрок может использовать команду /sway, "
        "чтобы увеличить характеристики своего хряка.\n\n"
        "ℹ️ Если есть вопросы: /help или /faq",
        reply_markup=kb.as_markup()
    )


# --- FSM ДЛЯ /reklama ---
class ReklamaForm(StatesGroup):
    waiting_content = State()


@router.message(Command("reklama"))
async def cmd_reklama(message: types.Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return await message.answer("⛔ У вас нет доступа к этой команде.")
    await state.set_state(ReklamaForm.waiting_content)
    await message.answer(
        "✍️ Отправь *одним сообщением* пост для рассылки (текст/фото/видео/стикер и т.д.).\n"
        "Чтобы отменить — отправь /cancel.",
        parse_mode="Markdown"
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.clear()
    await message.answer("❎ Режим рассылки отменён.")


@router.message(ReklamaForm.waiting_content)
async def reklama_send(message: types.Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return

    # --- Получаем список чатов из MongoDB ---
    cursor = db.chats.find({}, {"chat_id": 1})
    chat_ids = []
    async for doc in cursor:
        chat_ids.append(doc["chat_id"])

    if not chat_ids:
        await message.answer("ℹ️ Нет чатов для рассылки (бот никуда не добавлен).")
        return await state.clear()

    sent = 0
    failed = 0
    failed_ids = []

    # Копируем исходное сообщение как есть
    for cid in chat_ids:
        try:
            await message.copy_to(cid)
            sent += 1
        except Exception:
            failed += 1
            failed_ids.append(cid)

    await state.clear()

    text = f"✅ Отправлено: {sent}\n"
    if failed:
        text += f"⚠️ Не доставлено: {failed}\n"
        # При желании можно логировать failed_ids
    await message.answer(text)



@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📚 Помощь:\n"
        "/sway — изменить вес и силу (24ч КД, учитываются бусты)\n"
        "/farma — фарм монет 30–150 раз в 4ч\n"
        "/shop — купить 🍵 Хрякоины за TON\n"
        "/profile — профиль\n"
        "/info_chat — инфо чата\n"
        "/top — топ 10 по чату\n"
        "/global — глобальный топ 10\n"
        "/boost — активировать бусты (монеты/хрякоины)\n"
        "/fight — бой (рандом урон, −50% при истощении/ожирении)\n"
        "/case — кейсы\n"
        "/activate — активировать промокод\n"
        "/balance — показать баланс\n",
    )


async def ensure_pig(user_id: int, chat_id: int, username: str):
    """Создает запись в MongoDB, если нет"""
    # Ищем только по user_id (без chat_id)
    pig = await pigs_col.find_one({"user_id": user_id})

    # Если записи нет — создаём новую
    if not pig:
        await pigs_col.insert_one({
            "user_id": user_id,
            "chat_id": chat_id,  # Добавляем для новых данных
            "username": username,
            "weight": 10.0,
            "strength": 10.0,
            "last_train": None,
            "death_at": None
        })


async def get_total_boost(user_id: int, chat_id: int):
    """
    Получает все активные бусты пользователя.
    """
    boosts = {"weight": 0.0, "strength": 0.0, "no_negative": False}

    cursor = db.user_boosts.find({"user_id": user_id, "chat_id": chat_id})
    async for doc in cursor:
        kind = doc.get("kind")
        value = float(doc.get("value", 0))
        if kind == "weight_pct":
            boosts["weight"] += value
        elif kind == "strength_pct":
            boosts["strength"] += value
        elif kind == "both_pct":
            boosts["weight"] += value
            boosts["strength"] += value
        elif kind == "no_negative":
            boosts["no_negative"] = True

    return boosts


# ================== КУЛДАУН ==================
def can_use_cooldown(last_time: str | None, hours: int = 4):
    if not last_time:
        return True
    last_dt = datetime.fromisoformat(last_time)
    return datetime.now() - last_dt >= timedelta(hours=hours)


# ================== ТАЙМЕРЫ ==================
active_timers = {}


def parse_time(text: str) -> int | None:
    text = text.lower().replace(" ", "")
    if text.endswith("ч") or text.endswith("h"):
        return int(text[:-1]) * 3600
    elif text.endswith("мин") or text.endswith("m"):
        return int(text[:-3] if text.endswith("мин") else text[:-1]) * 60
    elif text.isdigit():
        return int(text)
    return None


@router.message(lambda m: m.text and m.text.lower().startswith("таймер "))
async def create_timer(msg: types.Message):
    """
    Команда для создания таймера:
    таймер 10мин фарма
    """
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        await msg.answer("⏰ Пример: таймер 10мин фарма")
        return

    delay = parse_time(parts[1])
    if not delay:
        await msg.answer("❌ Неверный формат времени. 10м, 1ч, 30мин")
        return

    user_id = msg.from_user.id
    chat_id = msg.chat.id
    command_name = parts[2].strip().lower()  # фарма, sway и т.д.
    run_at = datetime.now() + timedelta(seconds=delay)
    task_id = f"{user_id}_{int(datetime.now().timestamp())}"

    if user_id not in active_timers:
        active_timers[user_id] = []

    async def run_later():
        await asyncio.sleep(delay)
        if command_name in ("фарм", "ферма"):
            fake_msg = types.Message(
                message_id=0,
                date=datetime.now(),
                chat=msg.chat,
                from_user=msg.from_user,
                text="фарм",
                bot=msg.bot
            )
            from handlers.farma import cmd_farma  # твой хендлер фармы
            await cmd_farma(fake_msg)
        # удалить таймер из списка
        active_timers[user_id] = [t for t in active_timers[user_id] if t["id"] != task_id]

    task = asyncio.create_task(run_later())
    active_timers[user_id].append({
        "id": task_id,
        "command": command_name,
        "time": run_at.strftime("%H:%M:%S"),
        "task": task
    })

    await msg.answer(
        f"✅ Таймер установлен!\nКоманда: `{command_name}`\nВремя: {run_at.strftime('%H:%M:%S')}",
        parse_mode="Markdown"
    )


@router.message(lambda m: m.text and m.text.lower() == "таймеры")
async def show_timers(msg: types.Message):
    user_id = msg.from_user.id
    timers = active_timers.get(user_id, [])
    if not timers:
        await msg.answer("ℹ️ У тебя нет активных таймеров.")
        return

    text = "⏰ **Твои таймеры:**\n\n"
    for t in timers:
        text += f"🆔 `{t['id']}` — {t['command']} (⏳ до {t['time']})\n"

    await msg.answer(text, parse_mode="Markdown")



# Функция для проверки и создания пользователя в базе
async def ensure_user(db, user_id: int, chat_id: int, username: str):
    user_doc = await db.pigs.find_one({"user_id": user_id, "chat_id": chat_id})
    if not user_doc:
        user_doc = {
            "user_id": user_id,
            "chat_id": chat_id,
            "username": username,
            "coins": 0,
            "last_farma": None,
        }
        await db.pigs.insert_one(user_doc)
    return user_doc

# --------------------- BALANCE ---------------------
@router.message(Command("balance"))
@router.message(F.text.lower().in_(["баланс", "balance"]))
async def cmd_balance(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("Команда /balance доступна только в групповых чатах.")

    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username or message.from_user.full_name

    # Получаем пользователя или создаем его
    user_doc = await ensure_user(db, user_id, chat_id, username)

    coins = user_doc.get("coins", 0)

    # Глобальные хрякоины (chat_id = 0)
    global_doc = await db.pigs.find_one({"user_id": user_id, "chat_id": 0})
    khryacoins = global_doc.get("khryacoins", 0) if global_doc else 0

    await message.answer(f"💰 Монеты (локальные): {coins}\n🍵 Хрякоины (глобальные): {khryacoins}")

# --------------------- FARMA ---------------------
@router.message(Command("farma"))
@router.message(F.text.lower().in_(["фарм", "фарма", "/ферма", "/farma"]))
async def cmd_farma(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("Команда /farma доступна только в групповых чатах.")

    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username or message.from_user.full_name

    # Получаем пользователя или создаем его
    user_doc = await ensure_user(db, user_id, chat_id, username)

    coins = user_doc.get("coins", 0)
    last_farma = user_doc.get("last_farma")

    # Проверяем кулдаун
    cooldown_hours = 4
    if last_farma:
        last_time = datetime.fromisoformat(last_farma)
        next_time = last_time + timedelta(hours=cooldown_hours)
        now = datetime.now()

        if now < next_time:
            remaining = next_time - now
            hours, remainder = divmod(int(remaining.total_seconds()), 3600)
            minutes = remainder // 60
            time_text = f"{hours}ч {minutes}м" if hours else f"{minutes}м"
            return await message.answer(
                f"⏳ Фарм доступен раз в {cooldown_hours} часа.\n"
                f"Следующая попытка через: <b>{time_text}</b>",
                parse_mode="HTML"
            )

    # Вычисляем награду
    reward = random.randint(30, 150)
    # Здесь вставь свою функцию get_total_boost
    boosts = await get_total_boost(user_id, chat_id)  # пример
    reward = int(round(reward * (1 + boosts.get("weight", 0.0))))

    # Обновляем пользователя
    await db.pigs.update_one(
        {"user_id": user_id, "chat_id": chat_id},
        {"$inc": {"coins": reward}, "$set": {"last_farma": datetime.now().isoformat()}}
    )

    await message.answer(f"🌾 Вы поработали на ферме и получили <b>{reward}</b> монет!", parse_mode="HTML")

# --------------------- ПОДАРОК ---------------------
@router.message(Command("gift"))
@router.message(F.text.lower().in_(["подарок", "gift"]))
async def cmd_gift(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("Команда /gift доступна только в групповых чатах.")

    sender_id = message.from_user.id
    chat_id = message.chat.id
    sender_name = message.from_user.username or message.from_user.full_name

    # Получаем или создаем отправителя
    sender_doc = await ensure_user(db, sender_id, chat_id, sender_name)

    if not message.reply_to_message:
        return await message.answer("💡 Используйте эту команду, ответив на сообщение пользователя, которому хотите подарить монеты.")

    receiver_id = message.reply_to_message.from_user.id
    receiver_name = message.reply_to_message.from_user.username or message.reply_to_message.from_user.full_name

    # Получаем или создаем получателя
    receiver_doc = await ensure_user(db, receiver_id, chat_id, receiver_name)

    coins_to_gift = 50  # Пример фиксированной суммы подарка

    # Проверяем, хватает ли монет у отправителя
    if sender_doc.get("coins", 0) < coins_to_gift:
        return await message.answer("❌ У вас недостаточно монет для подарка.")

    # Переводим монеты
    await db.pigs.update_one({"user_id": sender_id, "chat_id": chat_id}, {"$inc": {"coins": -coins_to_gift}})
    await db.pigs.update_one({"user_id": receiver_id, "chat_id": chat_id}, {"$inc": {"coins": coins_to_gift}})

    await message.answer(f"🎁 {coins_to_gift} монет переданы {receiver_name}!")

async def can_use_cooldown(last_time, hours=24):
    if not last_time:
        return True
    last_dt = datetime.fromisoformat(last_time)
    return datetime.now() - last_dt >= timedelta(hours=hours)

@router.message(F.text.lower().startswith("воровать"))
async def cmd_steal(message: types.Message):
    thief = message.from_user
    chat_id = message.chat.id

    # --- Определяем жертву ---
    if message.reply_to_message:
        victim = message.reply_to_message.from_user
    else:
        args = message.text.split()
        if len(args) < 2 or not args[1].startswith("@"):
            return await message.answer(
                "❌ Использование: `воровать @username` или ответом на сообщение",
                parse_mode="Markdown"
            )
        username = args[1].lstrip("@")
        victim = await pigs_col.find_one({"username": username})
        if not victim:
            return await message.answer(f"❌ Пользователь @{username} не найден в этом чате.")
        victim = types.User(id=victim["user_id"], is_bot=False, first_name=username)

    if victim.id == thief.id:
        return await message.answer("❌ Нельзя воровать у себя!")

    # --- Проверяем/создаём поля для вора ---
    thief_doc = await pigs_col.find_one({"user_id": thief.id})
    if not thief_doc:
        await pigs_col.insert_one({
            "user_id": thief.id,
            "chat_id": chat_id,
            "username": thief.username or thief.full_name,
            "coins": 0,
            "last_theft": None
        })
        thief_doc = await pigs_col.find_one({"user_id": thief.id})

    last_theft = thief_doc.get("last_theft")
    thief_coins = thief_doc.get("coins", 0)

    # --- Проверяем кулдаун (24 часа) ---
    if last_theft:
        last_theft_dt = datetime.fromisoformat(last_theft)
        next_time = last_theft_dt + timedelta(hours=24)
        if datetime.now() < next_time:
            return await message.answer(
                f"⏳ Воровать можно раз в 24 часа.\nСледующая попытка: {next_time.strftime('%Y-%m-%d %H:%M')}"
            )

    # --- Получаем данные жертвы ---
    victim_doc = await pigs_col.find_one({"user_id": victim.id})
    if not victim_doc:
        await message.answer(f"❌ У {victim.first_name} нет монет.")
        return

    victim_coins = victim_doc.get("coins", 0)

    success_chance = 0.5
    now_str = datetime.now().isoformat()

    # --- Успешная кража ---
    if random.random() < success_chance and victim_coins > 0:
        stolen = random.randint(1, min(100, victim_coins))
        await pigs_col.update_one(
            {"user_id": victim.id},
            {"$inc": {"coins": -stolen}}
        )
        await pigs_col.update_one(
            {"user_id": thief.id},
            {"$inc": {"coins": stolen}, "$set": {"last_theft": now_str}}
        )

        thief_mention = f'<a href="tg://user?id={thief.id}">{thief.full_name}</a>'
        victim_mention = f'<a href="tg://user?id={victim.id}">{victim.first_name}</a>'
        return await message.answer(
            f"💰 {thief_mention} украл {stolen} монет у {victim_mention}!",
            parse_mode="HTML"
        )

    # --- Неудачная кража (штраф) ---
    penalty = max(1, int(thief_coins * 0.05)) if thief_coins > 0 else 0
    await pigs_col.update_one(
        {"user_id": thief.id},
        {"$inc": {"coins": -penalty}, "$set": {"last_theft": now_str}}
    )

    thief_mention = f'<a href="tg://user?id={thief.id}">{thief.full_name}</a>'
    return await message.answer(
        f"🚨 Попытка провалилась! {thief_mention} потерял {penalty} монет.",
        parse_mode="HTML"
    )


# ================== 💖 Романтика ==================
@router.message(F.text.regexp(r"^(погладить|поцеловать)"))
async def cmd_romance(message: types.Message):
    action_text = message.text.lower().strip()
    if not message.reply_to_message:
        return await message.answer("⚠️ Ответь на сообщение игрока, чтобы проявить романтику!")

    actor = message.from_user
    target = message.reply_to_message.from_user

    if actor.id == target.id:
        return await message.answer("❌ Нельзя проявлять романтику к самому себе!")

    # Определяем действие
    if action_text.startswith("погладить"):
        action = "погладил(а)"
        emotes = ["🐷🤲", "✨🐖✨", "🥰", "💞", "🤗"]
    else:
        action = "поцеловал(а)"
        emotes = ["😘🐷", "💋🐽", "😍", "❤️", "😚"]

    emoji = random.choice(emotes)

    # Форматируем упоминания с кликабельными тегами
    actor_mention = f"<a href='tg://user?id={actor.id}'>{actor.first_name}</a>"
    target_mention = f"<a href='tg://user?id={target.id}'>{target.first_name}</a>"

    # Текст красивый и пингуемый
    text = (
        f"{emoji} {actor_mention} {action} {target_mention}! {emoji}"
    )

    # Ответ именно на сообщение "жертвы"
    await message.reply_to_message.reply(text, parse_mode="HTML")




# ================== 💖 Предложение брака ==================
@router.message(F.text.lower() == "брак")
async def propose_marriage(message: types.Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return await message.answer("⚠️ Ответьте на сообщение игрока, чтобы предложить брак!")

    proposer = message.from_user
    partner = message.reply_to_message.from_user

    if proposer.id == partner.id:
        return await message.answer("❌ Нельзя жениться на себе!")

    # Проверяем, состоит ли кто-то уже в браке
    proposer_doc = await pigs.find_one({"user_id": proposer.id})
    partner_doc = await pigs.find_one({"user_id": partner.id})

    if proposer_doc and proposer_doc.get("partner_id"):
        return await message.answer("❌ Вы уже состоите в браке!")
    if partner_doc and partner_doc.get("partner_id"):
        return await message.answer(f"❌ {partner.full_name} уже состоит в браке!")

    proposer_mention = f'<a href="tg://user?id={proposer.id}">{proposer.full_name}</a>'
    partner_mention = f'<a href="tg://user?id={partner.id}">{partner.full_name}</a>'

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💍 Принять", callback_data=f"marry_accept:{proposer.id}:{partner.id}"),
            InlineKeyboardButton(text="❌ Отказаться", callback_data=f"marry_decline:{proposer.id}:{partner.id}")
        ]
    ])

    text = (
        f"💌 <b>Предложение руки и сердца!</b>\n\n"
        f"🤵 {proposer_mention}\n"
        f"👰 {partner_mention}\n\n"
        "✨ Хотите соединить свои судьбы? 💞"
    )

    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ================== 💍 Обработка принятия/отказа ==================
@router.callback_query(F.data.startswith("marry_"))
async def marry_handler(cb: types.CallbackQuery):
    action, proposer_id, partner_id = cb.data.split(":")
    proposer_id, partner_id = int(proposer_id), int(partner_id)

    if cb.from_user.id != partner_id:
        return await cb.answer("⛔ Это не ваше предложение!", show_alert=True)

    now = datetime.now().isoformat()

    if action == "marry_accept":
        # Обновляем обоих пользователей
        await pigs.update_one({"user_id": proposer_id}, {"$set": {"partner_id": partner_id, "married_at": now}}, upsert=True)
        await pigs.update_one({"user_id": partner_id}, {"$set": {"partner_id": proposer_id, "married_at": now}}, upsert=True)

        proposer_user = await cb.bot.get_chat(proposer_id)
        partner_user = await cb.bot.get_chat(partner_id)

        proposer_mention = f'<a href="tg://user?id={proposer_user.id}">{proposer_user.full_name}</a>'
        partner_mention = f'<a href="tg://user?id={partner_user.id}">{partner_user.full_name}</a>'

        await cb.message.edit_text(f"💍 {partner_mention} принял(а) предложение от {proposer_mention}! ❤️", parse_mode="HTML")

        wedding_text = (
            f"🎉 <b>Свадьба состоялась!</b>\n\n"
            f"🤵 {proposer_mention}\n"
            f"👰 {partner_mention}\n\n"
            f"💖 Пусть ваше счастье длится вечно!\n"
            f"🗓 Дата: {datetime.now().strftime('%d.%m.%Y')}"
        )
        await cb.bot.send_message(cb.message.chat.id, wedding_text, parse_mode="HTML")

    elif action == "marry_decline":
        proposer_user = await cb.bot.get_chat(proposer_id)
        partner_user = await cb.bot.get_chat(partner_id)

        proposer_mention = f'<a href="tg://user?id={proposer_user.id}">{proposer_user.full_name}</a>'
        partner_mention = f'<a href="tg://user?id={partner_user.id}">{partner_user.full_name}</a>'

        await cb.message.edit_text(f"❌ {partner_mention} отклонил(а) предложение брака от {proposer_mention}.", parse_mode="HTML")


# ================== 💔 Развод ==================
@router.message(F.text.lower() == "развод")
async def divorce(message: types.Message):
    user_id = message.from_user.id

    user_doc = await pigs.find_one({"user_id": user_id})
    if not user_doc or not user_doc.get("partner_id"):
        return await message.answer("💔 Вы пока не состоите в браке.")

    partner_id = user_doc["partner_id"]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, развестись", callback_data=f"divorce_yes:{user_id}:{partner_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"divorce_no:{user_id}")
        ]
    ])

    await message.answer("⚠️ Вы уверены, что хотите развестись?", reply_markup=kb)


# ================== 💔 Обработка развода ==================
@router.callback_query(F.data.startswith("divorce_"))
async def handle_divorce_callback(cb: types.CallbackQuery):
    data = cb.data.split(":")
    action = data[0]
    user_id = int(data[1])

    if action == "divorce_no":
        await cb.message.edit_text("❌ Развод отменён.")
        return

    _, user_id, partner_id = data
    user_id, partner_id = int(user_id), int(partner_id)

    if cb.from_user.id != user_id:
        return await cb.answer("⛔ Это не ваше подтверждение!", show_alert=True)

    # Развод — обнуляем partner_id и married_at
    await pigs.update_many({"user_id": {"$in": [user_id, partner_id]}}, {"$set": {"partner_id": None, "married_at": None}})

    user_mention = f'<a href="tg://user?id={user_id}">{cb.from_user.full_name}</a>'
    partner_user = await cb.bot.get_chat(partner_id)
    partner_mention = f'<a href="tg://user?id={partner_user.id}">{partner_user.full_name}</a>'

    await cb.message.edit_text(f"💔 <b>Развод состоялся!</b>\n\n{user_mention} и {partner_mention} теперь свободны 🕊", parse_mode="HTML")


# ================== 💍 Мой брак ==================
@router.message(F.text.lower() == "мой брак")
async def my_marriage(message: types.Message):
    user_id = message.from_user.id

    user_doc = await pigs.find_one({"user_id": user_id})
    if not user_doc or not user_doc.get("partner_id"):
        return await message.answer("💔 Вы пока не состоите в браке.")

    partner_id = user_doc["partner_id"]
    partner = await message.bot.get_chat(partner_id)

    user_mention = f'<a href="tg://user?id={user_id}">{message.from_user.full_name}</a>'
    partner_mention = f'<a href="tg://user?id={partner.id}">{partner.full_name}</a>'

    married_at = user_doc["married_at"]
    start = datetime.fromisoformat(married_at)
    now = datetime.now()
    delta = now - start
    years = delta.days // 365
    months = (delta.days % 365) // 30
    days = (delta.days % 365) % 30

    text = (
        f"👰🤵 <b>Ваш брак:</b>\n\n"
        f"{user_mention} 💞 {partner_mention}\n\n"
        f"🗓 Зарегистрирован: {start.strftime('%d.%m.%Y')}\n"
        f"⏱ Вместе уже: {years} г. {months} мес. {days} дн."
    )

    await message.reply(text, parse_mode="HTML")


# ================== КОНСТАНТЫ ==================
KHRY_PACKS = [250, 500, 1000]
PACK_TON_PRICE = {250: 0.1, 500: 0.1, 1000: 0.1}  # Цена в TON
PACK_STARS_PRICE = {pack: int(pack * 0.5) for pack in KHRY_PACKS}  # 1 Хрякоин = 0.5 ⭐

SHOP_BANNER = "https://cdn.discordapp.com/attachments/1395838378859040779/1428639893264928808/raw.png?ex=68f33c15&is=68f1ea95&hm=162dabe988a46180230fddca4179a82374734e0a0f90839e904731d82d3ce27b&"

# ================== МАГАЗИН ==================
@router.message(Command("shop"))
async def cmd_shop(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Оплата в TON", callback_data="shop_currency:ton")],
        [InlineKeyboardButton(text="⭐ Оплата в Stars", callback_data="shop_currency:stars")]
    ])
    await message.answer_photo(
        photo=SHOP_BANNER,
        caption="🏪 <b>Магазин Хрякоинов</b>\n\nВыберите валюту оплаты:",
        reply_markup=kb,
        parse_mode="HTML"
    )

# ================== ВЫБОР ВАЛЮТЫ ==================
@router.callback_query(lambda c: c.data.startswith("shop_currency:"))
async def choose_currency(callback: types.CallbackQuery):
    currency = callback.data.split(":")[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for pack in KHRY_PACKS:
        if currency == "ton":
            price = PACK_TON_PRICE[pack]
            text = f"🍵 {pack} Хрякоинов — {price:.2f} TON"
            data = f"shop_buy_ton:{pack}"
        else:
            price = PACK_STARS_PRICE[pack]
            text = f"🍵 {pack} Хрякоинов — {price} ⭐"
            data = f"shop_buy_stars:{pack}"
        kb.inline_keyboard.append([InlineKeyboardButton(text=text, callback_data=data)])
    kb.inline_keyboard.append([InlineKeyboardButton(text="⬅ Назад", callback_data="shop_main")])

    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=SHOP_BANNER,
            caption=(
                f"🏪 <b>Магазин Хрякоинов</b>\n\n"
                f"Вы выбрали валюту: {'💎 TON' if currency=='ton' else '⭐ Stars'}\n\n"
                "Теперь выберите пакет:"
            ),
            parse_mode="HTML"
        ),
        reply_markup=kb
    )
    await callback.answer()

# ================== ВОЗВРАТ В ГЛАВНОЕ МЕНЮ ==================
@router.callback_query(lambda c: c.data == "shop_main")
async def back_to_main(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Оплата в TON", callback_data="shop_currency:ton")],
        [InlineKeyboardButton(text="⭐ Оплата в Stars", callback_data="shop_currency:stars")]
    ])
    await callback.message.edit_media(
        media=InputMediaPhoto(
            media=SHOP_BANNER,
            caption="🏪 <b>Магазин Хрякоинов</b>\n\nВыберите валюту оплаты:",
            parse_mode="HTML"
        ),
        reply_markup=kb
    )
    await callback.answer()

# ================== TON ОПЛАТА ==================
async def create_invoice(amount_ton: float, description: str = "Покупка Хрякоинов"):
    async with aiohttp.ClientSession() as session:
        headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN}
        payload = {"amount": amount_ton, "currency_type": "crypto", "asset": "TON", "description": description}
        async with session.post(CRYPTO_API_URL + "createInvoice", headers=headers, json=payload) as resp:
            data = await resp.json()
            if data.get("ok"):
                return data["result"]["pay_url"], data["result"]["invoice_id"]
            return None, None

@router.callback_query(lambda c: c.data.startswith("shop_buy_ton:"))
async def cb_buy_kh_ton(callback: types.CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    pack = int(callback.data.split(":")[1])
    amount_ton = PACK_TON_PRICE.get(pack, 0.01)
    pay_url, invoice_id = await create_invoice(amount_ton, description=f"Покупка {pack} 🍵 Хрякоинов")
    if not pay_url:
        return await callback.answer("❌ Не удалось создать счёт, попробуйте позже.", show_alert=True)

    # Сохраняем в Mongo
    await payments.update_one(
        {"invoice_id": invoice_id},
        {"$set": {"user_id": user_id, "type": "buy_khryacoins", "amount": pack, "status": "pending"}},
        upsert=True
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 Оплатить TON", url=pay_url)]])
    try:
        await bot.send_photo(
            user_id,
            photo=SHOP_BANNER,
            caption=(
                f"💎 <b>Покупка {pack} 🍵 Хрякоинов</b>\n\n"
                f"💰 Сумма: <b>{amount_ton} TON</b>\n\n"
                "Нажмите кнопку ниже, чтобы оплатить.\nПосле оплаты хрякоины зачислятся автоматически 🐷"
            ),
            parse_mode="HTML",
            reply_markup=kb
        )
    except Exception:
        return await callback.answer("❌ Не удалось отправить чек в ЛС, пользователь не начал диалог с ботом.", show_alert=True)

    await callback.message.edit_caption(caption="✅ Чек на оплату отправлен в ЛС пользователя!", parse_mode="HTML")
    await callback.answer()

# ================== STARS ОПЛАТА ==================
@router.callback_query(lambda c: c.data.startswith("shop_buy_stars:"))
async def cb_buy_kh_stars(callback: types.CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    pack = int(callback.data.split(":")[1])
    prices = [LabeledPrice(label=f"Покупка {pack} 🍵 Хрякоинов", amount=PACK_STARS_PRICE[pack])]
    try:
        await bot.send_invoice(
            chat_id=user_id,
            title="Покупка Хрякоинов",
            description=f"Покупка {pack} 🍵 Хрякоинов",
            payload=f"khryak_stars:{pack}",
            provider_token="YOUR_PROVIDER_TOKEN",
            currency="XTR",
            prices=prices
        )
    except Exception:
        return await callback.answer("❌ Не удалось отправить чек в ЛС, пользователь должен начать диалог с ботом.", show_alert=True)

    await callback.message.edit_caption(caption="✅ Чек на оплату отправлен в ЛС пользователя!", parse_mode="HTML")
    await callback.answer()

# ================== ОБРАБОТКА STARS ПЛАТЕЖЕЙ ==================
@router.pre_checkout_query()
async def pre_checkout(pre_checkout: PreCheckoutQuery):
    await pre_checkout.answer(ok=True)

@router.message(lambda m: m.successful_payment)
async def successful_payment_handler(message: types.Message):
    successful_payment: SuccessfulPayment = message.successful_payment
    payload = successful_payment.invoice_payload
    user_id = message.from_user.id

    # Определяем, сколько хрякоинов купить
    if payload.startswith("khryak_stars:"):
        pack = int(payload.split(":")[1])
    else:
        pack = int(successful_payment.total_amount)

    # ✅ Добавляем пользователю хрякоины в глобальный профиль (chat_id = 0)
    await pigs.update_one(
        {"user_id": user_id, "chat_id": 0},
        {"$inc": {"khryacoins": pack}},
        upsert=True
    )

    # Сообщение об успешной оплате
    await message.answer_photo(
        photo=SHOP_BANNER,
        caption="✅ Платёж прошёл успешно! Хрякоины зачислены. Спасибо 💚",
        parse_mode="HTML"
    )

    # 🔍 (необязательно, но полезно)
    print(f"[STARS PAYMENT] User {user_id} получил {pack} хрякоинов.")


# ================== ЧЕКЕР TON ==================
async def check_invoices_loop(bot: Bot):
    await asyncio.sleep(2)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                cursor = payments.find({"status": "pending"})
                async for pay in cursor:
                    invoice_id = pay["invoice_id"]
                    user_id = pay["user_id"]
                    ptype = pay["type"]
                    amount = pay["amount"]

                    headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN}
                    url = CRYPTO_API_URL + f"getInvoices?invoice_ids={invoice_id}"
                    async with session.get(url, headers=headers) as resp:
                        data = await resp.json()

                    if not data.get("ok"):
                        continue

                    items = data["result"].get("items", [])
                    if not items:
                        continue

                    status = items[0].get("status")

                    if status == "paid" and ptype == "buy_khryacoins":
                        # ✅ Зачисляем хрякоины в глобальный кошелёк (chat_id = 0)
                        await pigs.update_one(
                            {"user_id": user_id, "chat_id": 0},
                            {"$inc": {"khryacoins": amount}},
                            upsert=True
                        )

                        await payments.update_one(
                            {"invoice_id": invoice_id},
                            {"$set": {"status": "paid"}}
                        )

                        try:
                            await bot.send_photo(
                                user_id,
                                photo=SHOP_BANNER,
                                caption=(
                                    f"✅ <b>Оплата прошла успешно!</b>\n\n"
                                    f"💰 Вы приобрели: <b>{amount} 🍵 Хрякоинов</b>\n"
                                    f"🐷 Хрякоины зачислены на ваш баланс!"
                                ),
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            print(f"Не удалось отправить чек пользователю {user_id}: {e}")

                        print(f"[TON PAYMENT] Пользователь {user_id} получил {amount} хрякоинов (invoice: {invoice_id})")

                    elif status == "expired":
                        await payments.update_one(
                            {"invoice_id": invoice_id},
                            {"$set": {"status": "expired"}}
                        )

        except Exception as e:
            print("Ошибка в чекере инвойсов:", e)

        # ⏳ Пауза между проверками, чтобы не спамить API
        await asyncio.sleep(CHECK_INTERVAL)


boost_banner_url = (
    "https://cdn.discordapp.com/attachments/1395838378859040779/1428642548242448446/"
    "content.png?ex=68f33e8e&is=68f1ed0e&hm=ef81aefcf4ab497f18a41e13b24db453089307f5b878390f09d4c6c2732dc335&"
)

# ================== SAFE EDIT ==================
async def safe_edit(callback: types.CallbackQuery, text: str, kb: InlineKeyboardMarkup = None, photo_url: str = None):
    """Редактирует сообщение безопасно, подстраиваясь под фото и клавиатуру"""
    try:
        markup = kb
        msg = callback.message
        if photo_url:
            await msg.edit_media(
                media=InputMediaPhoto(media=photo_url, caption=text, parse_mode="HTML"),
                reply_markup=markup
            )
        else:
            await msg.edit_caption(caption=text, parse_mode="HTML") if msg.photo else await msg.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")

# ================== ПОЛУЧЕНИЕ БАЛАНСА ==================
async def get_pig_balance(user_id: int, chat_id: int):
    """Возвращает (локальные монеты, глобальные хрякоины)"""
    user = await pigs.find_one({"user_id": user_id, "chat_id": chat_id})
    coins = user["coins"] if user and "coins" in user else 0

    global_user = await pigs.find_one({"user_id": user_id, "chat_id": 0})
    khryacoins = global_user["khryacoins"] if global_user and "khryacoins" in global_user else 0

    return coins, khryacoins

# ================== ОБНОВЛЕНИЕ БАЛАНСА ==================
async def update_balance(user_id: int, chat_id: int, coins: int = 0, khryacoins: int = 0):
    """Обновляет баланс пользователя в MongoDB"""
    if coins != 0:
        await pigs.update_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"$inc": {"coins": coins}},
            upsert=True
        )
    if khryacoins != 0:
        await pigs.update_one(
            {"user_id": user_id, "chat_id": 0},
            {"$inc": {"khryacoins": khryacoins}},
            upsert=True
        )

# ================== БУСТЫ ==================
@router.message(Command("boost"))
async def cmd_boost(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚹 Буст себе", callback_data="boost_self")],
        [InlineKeyboardButton(text="👥 Буст для чата", callback_data="boost_chat")]
    ])
    await message.answer_photo(
        photo=boost_banner_url,
        caption="⚡ <b>Выберите тип буста:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )


# ================== ЛИЧНЫЕ БУСТЫ ==================
@router.callback_query(F.data == "boost_self")
async def cb_boost_self(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔺 +20% к фарму веса на 7д — 1500 монет", callback_data="self_w_20")],
        [InlineKeyboardButton(text="💪 +20% к фарму силы на 7д — 1500 монет", callback_data="self_s_20")],
        [InlineKeyboardButton(text="💠 +20% к весу и силе на 7д — 3000 монет", callback_data="self_both_20")],
        [InlineKeyboardButton(text="🛡 Без минусов в /sway на 10д — 250 🍵", callback_data="self_no_neg")],
        [InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_main")]
    ])
    await safe_edit(callback, "⚡ <b>Личные бусты:</b>", kb, photo_url=boost_banner_url)
    await callback.answer()

@router.callback_query(F.data.in_({"self_w_20", "self_s_20", "self_both_20", "self_no_neg"}))
async def cb_buy_self_boost(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    coins, kh = await get_pig_balance(user_id, chat_id)

    if callback.data == "self_w_20":
        if coins < 1500:
            return await callback.answer("Недостаточно монет (нужно 1500).", show_alert=True)
        await update_balance(user_id, chat_id, coins=-1500)
        await add_user_boost(user_id, chat_id, "weight_pct", 0.2, 7)
        text = "✅ Активирован личный буст: +20% к фарму веса на 7 дней."
    elif callback.data == "self_s_20":
        if coins < 1500:
            return await callback.answer("Недостаточно монет (нужно 1500).", show_alert=True)
        await update_balance(user_id, chat_id, coins=-1500)
        await add_user_boost(user_id, chat_id, "strength_pct", 0.2, 7)
        text = "✅ Активирован личный буст: +20% к фарму силы на 7 дней."
    elif callback.data == "self_both_20":
        if coins < 3000:
            return await callback.answer("Недостаточно монет (нужно 3000).", show_alert=True)
        await update_balance(user_id, chat_id, coins=-3000)
        await add_user_boost(user_id, chat_id, "both_pct", 0.2, 7)
        text = "✅ Активирован личный буст: +20% к фарму веса и силы на 7 дней."
    else:
        if kh < 250:
            return await callback.answer("Недостаточно 🍵 Хрякоинов (нужно 250).", show_alert=True)
        await update_balance(user_id, chat_id, khryacoins=-250)
        await add_user_boost(user_id, chat_id, "no_negative", 1.0, 10)
        text = "✅ Личный буст активирован: 10 дней без минусов в /sway."

    await safe_edit(callback, text, photo_url=boost_banner_url)
    await callback.answer()

async def add_user_boost(user_id: int, chat_id: int, kind: str, value: float, days: int):
    """Добавляет или продлевает личный буст пользователя"""
    now = datetime.now()
    new_exp = now + timedelta(days=days)
    boost = await user_boosts.find_one({"user_id": user_id, "chat_id": chat_id, "kind": kind})
    if boost and boost["expires_at"] > now:
        new_exp = boost["expires_at"] + timedelta(days=days)
        value = max(boost["value"], value)
    await user_boosts.update_one(
        {"user_id": user_id, "chat_id": chat_id, "kind": kind},
        {"$set": {"value": value, "expires_at": new_exp}},
        upsert=True
    )

# ================== ЧАТОВЫЕ БУСТЫ ==================
@router.callback_query(F.data == "boost_chat")
async def cb_boost_chat(callback: types.CallbackQuery):
    if callback.message.chat.type == "private":
        return await callback.answer("❌ Чатовые бусты можно покупать только в группах.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 +10% к фарму в /sway (10д) — 250 🍵", callback_data="chat_boost_10")],
        [InlineKeyboardButton(text="👥 +20% к фарму в /sway (10д) — 500 🍵", callback_data="chat_boost_20")],
        [InlineKeyboardButton(text="👥 +50% к фарму в /sway (10д) — 1000 🍵", callback_data="chat_boost_50")],
        [InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_main")]
    ])
    await safe_edit(callback, "⚡ <b>Бусты для чата:</b>", kb, photo_url=boost_banner_url)
    await callback.answer()

@router.callback_query(F.data.in_({"chat_boost_10", "chat_boost_20", "chat_boost_50"}))
async def cb_buy_chat_boost(callback: types.CallbackQuery):
    if callback.message.chat.type == "private":
        return await callback.answer("❌ Чатовые бусты нельзя активировать в личке. Только в группе!", show_alert=True)

    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    _, kh = await get_pig_balance(user_id, chat_id)

    cost_map = {"chat_boost_10": 250, "chat_boost_20": 500, "chat_boost_50": 1000}
    val_map = {"chat_boost_10": 0.10, "chat_boost_20": 0.20, "chat_boost_50": 0.50}

    cost = cost_map[callback.data]
    val = val_map[callback.data]

    if kh < cost:
        return await callback.answer(f"Недостаточно 🍵 Хрякоинов (нужно {cost}).", show_alert=True)

    await update_balance(user_id, chat_id, khryacoins=-cost)
    await set_chat_boost(chat_id, val, 10)

    await safe_edit(callback, f"✅ Для чата активирован буст: +{int(val*100)}% в /sway на 10 дней.", photo_url=boost_banner_url)
    await callback.answer()

async def set_chat_boost(chat_id: int, value: float, days: int):
    now = datetime.now()
    new_exp = now + timedelta(days=days)
    boost = await chat_boosts.find_one({"chat_id": chat_id, "kind": "both_pct"})
    if boost and boost["expires_at"] > now:
        new_exp = boost["expires_at"] + timedelta(days=days)
        value = max(boost["value"], value)
    await chat_boosts.update_one(
        {"chat_id": chat_id, "kind": "both_pct"},
        {"$set": {"value": value, "expires_at": new_exp}},
        upsert=True
    )

# ================== НАЗАД ==================
@router.callback_query(F.data == "back_to_main")
async def cb_back_to_main(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚹 Буст себе", callback_data="boost_self")],
        [InlineKeyboardButton(text="👥 Буст для чата", callback_data="boost_chat")]
    ])
    await safe_edit(callback, "⚡ <b>Выберите тип буста:</b>", kb, photo_url=boost_banner_url)
    await callback.answer()




# ================== Кейс ==================
@router.message(Command("case"))
async def cmd_case(message: types.Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="🎁 Кейс за монеты — 500 💰", callback_data="case_coins")
    kb.button(text="🎁 Кейс за Хрякоины — 50 🍵", callback_data="case_khrya")
    kb.adjust(1)

    case_image_url = "https://cdn.discordapp.com/attachments/1395838378859040779/1428639672438886470/raw.png?ex=68f73060&is=68f5dee0&hm=0ee0452de30af26a979c52b10e18277832755b6fc4f3d4053dacc8e54777903a&"
    await message.answer_photo(photo=case_image_url, caption="Выберите тип кейса:", reply_markup=kb.as_markup())


@router.callback_query(F.data.in_({"case_coins", "case_khrya"}))
async def cb_open_case(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    username = callback.from_user.first_name

    # Получаем баланс
    user = await pigs_col.find_one({"user_id": user_id, "chat_id": chat_id})
    coins = user.get("coins", 0) if user else 0

    global_user = await pigs_col.find_one({"user_id": user_id, "chat_id": 0})
    kh = global_user.get("khryacoins", 0) if global_user else 0

    if callback.data == "case_coins":
        cost = 500
        if coins < cost:
            return await callback.answer("Недостаточно монет (нужно 500).", show_alert=True)
        await pigs_col.update_one({"user_id": user_id, "chat_id": chat_id}, {"$inc": {"coins": -cost}}, upsert=True)
        case_type = "Монетный кейс 💰"
        slots = 3
        rewards = [
            {"chance": 40, "type": "nothing", "text": "Пустой слот..."},
            {"chance": 25, "type": "coins", "amount": 100, "text": "💰 100 монет"},
            {"chance": 15, "type": "khryacoins", "amount": 5, "text": "🍵 5 Хрякоинов"},
            {"chance": 10, "type": "buff", "buff": ("weight_pct", 0.10), "days": 2, "text": "+10% к фарму веса на 2 дня"},
            {"chance": 7, "type": "buff", "buff": ("strength_pct", 0.10), "days": 2, "text": "+10% к фарму силы на 2 дня"},
            {"chance": 3, "type": "buff", "buff": ("both_pct", 0.15), "days": 2, "text": "Эпик: +15% к фарму силы и веса на 2 дня"}
        ]
    else:
        cost = 50
        if kh < cost:
            return await callback.answer("Недостаточно Хрякоинов (нужно 50).", show_alert=True)
        await pigs_col.update_one({"user_id": user_id, "chat_id": 0}, {"$inc": {"khryacoins": -cost}}, upsert=True)
        case_type = "Хрякоиновый кейс 🍵"
        slots = 3
        rewards = [
            {"chance": 30, "type": "coins", "amount": 500, "text": "💰 500 монет"},
            {"chance": 20, "type": "khryacoins", "amount": 15, "text": "🍵 15 Хрякоинов"},
            {"chance": 15, "type": "buff", "buff": ("both_pct", 0.20), "days": 5, "text": "🔥 Эпик: +20% к фарму веса и силы на 5 дней"},
            {"chance": 10, "type": "buff", "buff": ("both_pct", 0.30), "days": 7, "text": "🌟 Легендарка: +30% к фарму веса и силы на 7 дней"},
            {"chance": 10, "type": "buff", "buff": ("weight_pct", 0.50), "days": 3, "text": "⚡ Бафф: +50% к весу на 3 дня"},
            {"chance": 10, "type": "coins", "amount": 1000, "text": "💰 1000 монет"},
            {"chance": 5, "type": "jackpot", "text": "💎 Джекпот: 25 🍵 Хрякоинов!"}
        ]

    msg = await callback.message.reply(f"🎁 {username} открывает {case_type}...")

    # Анимация
    animation_frames = ["🔹", "🔸", "🔹", "🔸", "🎁"]
    for frame in animation_frames:
        await asyncio.sleep(1.2)
        await msg.edit_text(f"🎁 {username} крутит {case_type}... {frame}")

    # Раздача наград
    dropped = []
    for _ in range(slots):
        roll = random.randint(1, 100)
        cumulative = 0
        for r in rewards:
            cumulative += r["chance"]
            if roll <= cumulative:
                dropped.append(r)
                break

    results_text = ""
    for reward in dropped:
        if reward["type"] == "buff":
            kind, value = reward["buff"]
            await add_user_boost(user_id, chat_id, kind, value, reward["days"])
        elif reward["type"] == "coins":
            await pigs_col.update_one({"user_id": user_id, "chat_id": chat_id}, {"$inc": {"coins": reward["amount"]}}, upsert=True)
        elif reward["type"] == "khryacoins":
            await pigs_col.update_one({"user_id": user_id, "chat_id": 0}, {"$inc": {"khryacoins": reward["amount"]}}, upsert=True)
        elif reward["type"] == "jackpot":
            await pigs_col.update_one({"user_id": user_id, "chat_id": 0}, {"$inc": {"khryacoins": 25}}, upsert=True)
        results_text += f"• {reward['text']}\n"

    await msg.edit_text(f"🎉 {username} открыл {case_type}!\n\n{results_text}")
    await callback.answer()





@router.message(Command("info_chat"))
async def cmd_info_chat(message: types.Message):
    bot = message.bot
    chat = await bot.get_chat(message.chat.id)  # свежие данные о чате
    chat_id = chat.id

    # Количество участников
    try:
        members_count = await bot.get_chat_member_count(chat_id)
    except Exception as e:
        members_count = f"Не удалось получить ({e})"

    # Владелец и админы
    try:
        owner = None
        admins = await bot.get_chat_administrators(chat_id)
        owner = next((adm for adm in admins if adm.status == "creator"), None)
        owner_name = owner.user.mention_html() if owner else "❓ Неизвестен"
        admins_count = len(admins)
    except Exception:
        owner_name = "❓ Неизвестен"
        admins_count = "Не удалось получить"

    # Описание
    description = chat.description if chat.description else "—"

    # Ссылка-приглашение
    invite_link = chat.invite_link if chat.invite_link else "—"

    # Буст чата из Mongo
    row = await chat_boosts.find_one({"chat_id": chat_id, "kind": "both_pct"})
    if row:
        value = row.get("value", 0)
        expires_at_str = row.get("expires_at")
        boost_info = "❌ Буст чата сейчас не активен."
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)
            now = datetime.now()
            if expires_at > now:
                days_left = (expires_at - now).days
                boost_info = f"✅ Активный буст: +{int(value*100)}% к фарму в /sway, осталось {days_left} дн."
    else:
        boost_info = "❌ Буст чата сейчас не активен."

    # Формируем текст
    text = (
        f"ℹ️ <b>Информация о чате</b>\n\n"
        f"📛 <b>Название:</b> {chat.title}\n"
        f"🆔 <b>ID:</b> <code>{chat_id}</code>\n"
        f"📂 <b>Тип:</b> {chat.type}\n"
        f"👥 <b>Участников:</b> {members_count}\n"
        f"📝 <b>Описание:</b> {description}\n"
        f"🔗 <b>Ссылка-приглашение:</b> {invite_link}\n\n"
        f"{boost_info}"
    )

    await message.answer(text, parse_mode="HTML")

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import random
from aiogram import types, F
from aiogram.filters import Command

# --- список «админов без КД» (по user_id Telegram) ---
OWNER_IDS = [5747423404, 7510524298]

# --- часовые пояса ---
TZ_KYIV = ZoneInfo("Europe/Kyiv")
TZ_MSK  = ZoneInfo("Europe/Moscow")


# ---------- helpers ----------
def parse_iso_dt(s: str) -> datetime:
    """Безопасный парсер: если сохранено без tzinfo — считаем, что это UTC."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def can_use_today(last_train_iso: str | None, *, now_utc: datetime):
    """Ограничение: 1 раз в календарный день по Киеву."""
    now_kyiv = now_utc.astimezone(TZ_KYIV)
    now_msk = now_utc.astimezone(TZ_MSK)

    next_midnight_kyiv = (now_kyiv + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight_msk = (now_msk + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    if not last_train_iso:
        return True, next_midnight_kyiv, next_midnight_msk

    last_dt_kyiv = parse_iso_dt(last_train_iso).astimezone(TZ_KYIV)
    if last_dt_kyiv.date() < now_kyiv.date():
        return True, next_midnight_kyiv, next_midnight_msk

    return False, next_midnight_kyiv, next_midnight_msk


def sample_delta(low: int, high: int, favor: str = "neutral", bias: float = 0.85) -> int:
    if low > high:
        low, high = high, low
    if favor == "increase":
        mode = low + (high - low) * bias
    elif favor == "decrease":
        mode = high - (high - low) * bias
    else:
        mode = (low + high) / 2
    return int(round(random.triangular(low, high, mode)))


async def ensure_pig(user_id: int, chat_id: int, username: str):
    """Создает запись в MongoDB, если нет"""
    pig = await pigs_col.find_one({"user_id": user_id, "chat_id": chat_id})
    if not pig:
        await pigs_col.insert_one({
            "user_id": user_id,
            "chat_id": chat_id,
            "username": username,
            "weight": 10.0,
            "strength": 10.0,
            "last_train": None,
            "death_at": None
        })


async def get_total_boost(user_id: int, chat_id: int):
    """Возвращает словарь активных бустов пользователя"""
    return {}


def fmt_name(user: types.User) -> str:
    """Форматированное упоминание пользователя"""
    return f'<a href="tg://user?id={user.id}">{user.first_name}</a>'


# ------------------- Обработчик /sway -------------------
@router.message(Command("sway"))
@router.message(F.text.lower().in_(["растить", "Растить"]))
async def cmd_sway(message: types.Message):
    if message.chat.type not in ("group", "supergroup"):
        return await message.answer("Команда /sway доступна только в групповых чатах.")

    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username or message.from_user.full_name

    await ensure_pig(user_id, chat_id, username)

    pig = await pigs_col.find_one({"user_id": user_id, "chat_id": chat_id})

    weight = pig.get("weight", 10.0)
    strength = pig.get("strength", 10.0)
    last_train = pig.get("last_train")
    death_at = pig.get("death_at")

    now_utc = datetime.now(timezone.utc)

    # Проверка ограничения "1 раз в день"
    if user_id not in OWNER_IDS:
        can_use, next_midnight_kyiv, next_midnight_msk = can_use_today(last_train, now_utc=now_utc)
        if not can_use:
            return await message.answer(
                f"⏳ Тренироваться можно 1 раз в день.\n"
                f"👉 Следующая тренировка после полуночи: {next_midnight_kyiv.strftime('%Y-%m-%d %H:%M')} (Киев) / "
                f"{next_midnight_msk.strftime('%H:%M')} (МСК)"
            )

    boosts = await get_total_boost(user_id, chat_id)

    s_norm = (float(strength) + 1.0) / 4.0
    w_norm = (float(weight) + 1.0) / 6.0
    K_now = s_norm / max(w_norm, 1e-6)

    TARGET = 1.0
    TOL = 0.10

    if K_now > TARGET + TOL:
        w_favor = "increase"
        s_favor = "neutral"
    elif K_now < TARGET - TOL:
        w_favor = "neutral"
        s_favor = "increase"
    else:
        w_favor = "neutral"
        s_favor = "neutral"

    base_w_delta = sample_delta(1, 5, favor=w_favor, bias=0.85)
    base_s_delta = sample_delta(1, 3, favor=s_favor, bias=0.85)

    if boosts.get("no_negative"):
        base_w_delta = max(0, base_w_delta)
        base_s_delta = max(0, base_s_delta)

    w_delta = base_w_delta * (1.0 + boosts.get("weight", 0.0))
    s_delta = base_s_delta * (1.0 + boosts.get("strength", 0.0))

    w_delta = max(1, int(round(w_delta)))
    s_delta = max(1, int(round(s_delta)))

    new_weight = max(1.0, float(weight) + w_delta)
    new_strength = max(1.0, float(strength) + s_delta)

    status_code, status_text = pig_status(new_weight, new_strength)

    # Обновляем MongoDB
    await pigs_col.update_one(
        {"user_id": user_id, "chat_id": chat_id},
        {"$set": {"weight": new_weight, "strength": new_strength, "last_train": now_utc.isoformat()}}
    )

    w_diff = new_weight - float(weight)
    s_diff = new_strength - float(strength)
    w_sign = f"{'+' if w_diff >= 0 else ''}{w_diff:.0f}"
    s_sign = f"{'+' if s_diff >= 0 else ''}{s_diff:.0f}"

    now_kyiv = datetime.now(timezone.utc).astimezone(TZ_KYIV)
    now_msk = datetime.now(timezone.utc).astimezone(TZ_MSK)
    next_kyiv = (now_kyiv + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    next_msk = (now_msk + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    await message.answer(
        f"🏋️ {fmt_name(message.from_user)}, тренировка завершена!\n"
        f"⚖️ Вес: {float(weight):.0f} → {new_weight:.0f} ({w_sign})\n"
        f"💪 Сила: {float(strength):.0f} → {new_strength:.0f} ({s_sign})\n"
        f"{status_text}\n\n"
        f"🔁 Следующая тренировка после полуночи: "
        f"{next_kyiv.strftime('%Y-%m-%d %H:%M')} (Киев) / {next_msk.strftime('%H:%M')} (МСК)",
        parse_mode="HTML"
    )


@router.message(Command("my_pigs"))
@router.message(F.text.lower().in_(["мои хряки"]))
async def cmd_my_pigs(message: types.Message):
    user_id = message.from_user.id

    # Получаем все записи пользователя, кроме глобальных (chat_id != 0)
    pigs_list = await pigs_col.find({"user_id": user_id, "chat_id": {"$ne": 0}}).to_list(length=None)

    if not pigs_list:
        return await message.answer("🐷 У вас пока нет свинок ни в одном чате!")

    text_lines = ["🐖 <b>Ваши свинки по чатам:</b>\n"]

    for pig in pigs_list:
        chat_id = pig.get("chat_id", 0)
        weight = pig.get("weight", 10.0)
        strength = pig.get("strength", 10.0)

        try:
            chat = await message.bot.get_chat(chat_id)
            chat_title = chat.title or f"Chat {chat_id}"
            if chat.username:  # публичный чат
                chat_display = f"<a href='https://t.me/{chat.username}'>{chat_title}</a>"
            else:
                chat_display = chat_title
        except Exception:
            chat_display = f"Chat {chat_id}"

        text_lines.append(
            f"• {chat_display} — ⚖️ Вес: {weight:.0f}, 💪 Сила: {strength:.0f}"
        )

    await message.answer("\n".join(text_lines), parse_mode="HTML", disable_web_page_preview=True)





import re
from aiogram import types, F
from aiogram.filters import Command

# --- шаблон для проверки username ---
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{5,}$")

# --- админы без КД ---
OWNER_IDS = [5747423404, 7510524298]

# ================== ТОП В ЧАТЕ ==================
@router.message(Command("top"))
@router.message(F.text.lower().in_(["топ", "top"]))
async def cmd_top_chat(message: types.Message):
    if message.chat.type not in ("group", "supergroup"):
        return await message.answer("Команда /top доступна только в групповых чатах.")

    chat_id = message.chat.id

    pigs = await pigs_col.find(
        {"chat_id": chat_id, "user_id": {"$nin": OWNER_IDS}},
        {"user_id": 1, "username": 1, "weight": 1, "strength": 1, "wins": 1, "losses": 1}
    ).sort([
        ("weight", -1),
        ("strength", -1),
        ("wins", -1)
    ]).to_list(10)

    if not pigs:
        return await message.answer("Нет данных для топа в этом чате.")

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = ["🏆 <b>Топ 10 хряков чата</b> 🐷\n"]

    for i, pig in enumerate(pigs, 1):
        uid = pig.get("user_id")
        uname = pig.get("username")
        weight = pig.get("weight", 0)
        strength = pig.get("strength", 0)
        wins = pig.get("wins", 0)
        losses = pig.get("losses", 0)

        try:
            member = await message.bot.get_chat_member(chat_id, uid)
            display_name = member.user.full_name or uname or "Игрок"
        except Exception:
            display_name = uname or "Игрок"

        if uname and USERNAME_PATTERN.match(uname):
            name_link = f"<a href='https://t.me/{uname}'>{display_name}</a>"
        else:
            name_link = f"<a href='tg://user?id={uid}'>{display_name}</a>"

        lines.append(
            f"{medals[i-1]} <b>{i}. {name_link}</b> — "
            f"⚖️ {float(weight):.1f} кг | 💪 {int(strength)} | 🏆 {int(wins)} | ❌ {int(losses)}"
        )

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# ================== ГЛОБАЛЬНЫЙ ТОП ==================
@router.message(Command("global"))
@router.message(F.text.lower().in_(["глобал", "global"]))
async def cmd_top_global(message: types.Message):
    pigs = await pigs_col.find(
        {"user_id": {"$nin": OWNER_IDS}},
        {"user_id": 1, "username": 1, "weight": 1, "strength": 1, "wins": 1, "losses": 1}
    ).sort([
        ("weight", -1),
        ("strength", -1),
        ("wins", -1)
    ]).to_list(10)

    if not pigs:
        return await message.answer("Нет данных для глобального топа.")

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = ["🌍 <b>Глобальный топ 10 хряков</b> 🐷\n"]

    for i, pig in enumerate(pigs, 1):
        uid = pig.get("user_id")
        uname = pig.get("username")
        weight = pig.get("weight", 0)
        strength = pig.get("strength", 0)
        wins = pig.get("wins", 0)
        losses = pig.get("losses", 0)

        try:
            user = await message.bot.get_chat(uid)
            display_name = user.full_name or uname or "Игрок"
        except Exception:
            display_name = uname or "Игрок"

        if uname and USERNAME_PATTERN.match(uname):
            name_link = f"<a href='https://t.me/{uname}'>{display_name}</a>"
        else:
            name_link = f"<a href='tg://user?id={uid}'>{display_name}</a>"

        lines.append(
            f"{medals[i-1]} <b>{i}. {name_link}</b> — "
            f"⚖️ {float(weight):.1f} кг | 💪 {int(strength)} | 🏆 {int(wins)} | ❌ {int(losses)}"
        )

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True
    )



from datetime import datetime, timedelta
import aiosqlite
from aiogram import types, F
from aiogram.filters import Command


# --------------------- PROFILE ---------------------
@router.message(Command("profile"))
@router.message(F.text.lower().in_(["профиль", "Профиль"]))
async def cmd_profile(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username or message.from_user.full_name
    now = datetime.now()

    # --------------------- Получение данных ---------------------
    user_doc = await db.pigs.find_one({"user_id": user_id, "chat_id": chat_id})
    if not user_doc:
        return await message.answer("🐷 У вас ещё нет свинки! Попробуйте команду /start, добавить бота в чат и начать растить свинку командой /sway.")

    weight = user_doc.get("weight", 0.0)
    strength = user_doc.get("strength", 0.0)
    coins = user_doc.get("coins", 0)
    wins = user_doc.get("wins", 0)
    losses = user_doc.get("losses", 0)

    # Глобальные хрякоины
    global_doc = await db.pigs.find_one({"user_id": user_id, "chat_id": 0})
    kh = global_doc.get("khryacoins", 0) if global_doc else 0

    # Топ по весу в чате
    cursor = db.pigs.find({"chat_id": chat_id}).sort("weight", -1)
    top_position = "-"
    idx = 0
    async for doc in cursor:
        idx += 1
        if doc["user_id"] == user_id:
            top_position = idx
            break

    # Активность
    messages_cursor = db.messages.find({"user_id": user_id, "chat_id": chat_id})
    dates = []
    async for doc in messages_cursor:
        created_at = doc.get("created_at")
        if created_at:
            dates.append(datetime.fromisoformat(created_at).date())

    unique_dates = set(dates)
    active_days = sum(1 for d in unique_dates if now.date() - d <= timedelta(days=1))
    active_weeks = sum(1 for d in unique_dates if now.date() - d <= timedelta(weeks=1))
    active_months = sum(1 for d in unique_dates if now.date() - d <= timedelta(days=30))
    total_activity = len(unique_dates)

    # Активные бусты
    boosts_cursor = db.user_boosts.find({"user_id": user_id, "chat_id": chat_id})
    boost_texts = []
    async for boost in boosts_cursor:
        kind = boost.get("kind")
        value = boost.get("value", 0)
        expires_at_str = boost.get("expires_at")
        if not expires_at_str:
            continue
        expires_at = datetime.fromisoformat(expires_at_str)
        if expires_at > now:
            until = expires_at.strftime("%d.%m.%Y")
            pct = int(value * 100)
            if kind == "weight_pct":
                boost_texts.append(f"🔺 +{pct}% к весу до {until}")
            elif kind == "strength_pct":
                boost_texts.append(f"💪 +{pct}% к силе до {until}")
            elif kind == "both_pct":
                boost_texts.append(f"💠 +{pct}% к весу и силе до {until}")
            elif kind == "no_negative":
                boost_texts.append(f"🛡 Без минусов в /sway до {until}")

    boost_info = "\n".join(boost_texts) if boost_texts else "❌ Нет активных бустов"

    # --------------------- Формируем текст профиля ---------------------
    text = (
        f"👤 <b>Пользователь:</b> <a href='tg://user?id={user_id}'>{username}</a>\n\n"
        f"⚖️ <b>Вес:</b> {weight:.1f} кг | 💪 <b>Сила:</b> {strength:.1f}\n"
        f"📊 <b>Топ по весу:</b> {top_position}\n"
        f"💰 <b>Монеты:</b> {coins} | 🍵 <b>Хрякоины:</b> {kh}\n"
        f"🏆 <b>Победы:</b> {wins} | ❌ <b>Поражения:</b> {losses}\n\n"
        f"📈 <b>Активность</b> (д | н | м | всего): {active_days} | {active_weeks} | {active_months} | {total_activity}\n\n"
        f"🔥 <b>Активные бусты:</b>\n{boost_info}"
    )

    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

import asyncio, random, time
from aiogram import types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder


battles = {}
battle_counter = 0


# ================== ВСПОМОГАТЕЛЬНЫЕ ==================

async def ensure_user_row(user: types.User, chat_id: int):
    """Проверяет и создаёт запись, если нет"""
    existing = await pigs_col.find_one({"user_id": user.id, "chat_id": chat_id})
    if not existing:
        await pigs_col.insert_one({
            "user_id": user.id,
            "chat_id": chat_id,
            "username": user.username or user.full_name,
            "weight": 10.0,
            "strength": 10.0,
            "wins": 0,
            "losses": 0,
        })
    else:
        # Обновляем ник, если изменился
        current_name = user.username or user.full_name
        if existing.get("username") != current_name:
            await pigs_col.update_one(
                {"_id": existing["_id"]},
                {"$set": {"username": current_name}}
            )


def hp_bar(current, max_hp=100, length=8):
    filled = int(length * current / max_hp)
    return "🟥" * filled + "⬛" * (length - filled)


def calculate_damage(attacker_stats, defender_stats):
    weight, strength = attacker_stats
    target_weight, _ = defender_stats

    if random.random() < 0.1:
        return 0, "💨 Промах!"

    base = random.randint(10, 20)
    damage = base + (strength * 0.3) + (weight * 0.2) - (target_weight * 0.25)

    if random.random() < 0.1:
        damage *= 1.6
        return max(6, int(damage)), "💥 Критический удар!"

    return max(5, int(damage)), None


def format_hp(battle):
    a = battle["attacker"]
    d = battle["defender"]
    return (
        f"{a.first_name}: {hp_bar(battle['hp'][a.id])} ({battle['hp'][a.id]} HP)\n"
        f"{d.first_name}: {hp_bar(battle['hp'][d.id])} ({battle['hp'][d.id]} HP)"
    )


# ================== ЗАВЕРШЕНИЕ БОЯ ==================

async def end_battle(battle_key, msg, winner=None, loser=None, reason=None):
    """Завершает бой, обновляет статистику"""
    battle = battles.pop(battle_key, None)
    if not battle:
        return

    text = ""
    if winner and loser:
        text += f"🏆 <b>{winner.first_name}</b> побеждает <b>{loser.first_name}</b>!\n"
        text += f"💪 Сила +1 | ⚖️ Вес +1 у победителя\n❌ Поражение у {loser.first_name}\n"

        # обновляем в базе
        await pigs_col.update_one(
            {"user_id": winner.id, "chat_id": battle["chat_id"]},
            {"$inc": {"wins": 1, "strength": 1, "weight": 1}}
        )
        await pigs_col.update_one(
            {"user_id": loser.id, "chat_id": battle["chat_id"]},
            {"$inc": {"losses": 1, "weight": 0.5}}
        )

    elif reason:
        text = f"⏳ Бой завершён: {reason}"

    try:
        await msg.edit_text(text, parse_mode="HTML")
    except Exception:
        await msg.answer(text, parse_mode="HTML")


# ================== /КОМАНДА FIGHT ==================

@router.message(Command("fight"))
async def cmd_fight(message: types.Message):
    global battle_counter
    if message.chat.type not in ("group", "supergroup"):
        return await message.answer("⚠️ Команда /fight доступна только в группах!")

    attacker = message.from_user
    chat_id = message.chat.id
    defender = None

    # поиск по @username
    args = message.text.split(maxsplit=1)
    if len(args) == 2 and args[1].startswith("@"):
        username = args[1].lstrip("@").lower()
        pig = await pigs_col.find_one({"username": {"$regex": f"^{username}$", "$options": "i"}, "chat_id": chat_id})
        if pig:
            member = await message.chat.get_member(pig["user_id"])
            defender = member.user

    # если реплай
    if not defender and message.reply_to_message:
        defender = message.reply_to_message.from_user

    if not defender:
        return await message.answer("⚔️ Укажите соперника: ответьте на сообщение или напишите /fight @username")
    if defender.id == attacker.id:
        return await message.answer("Нельзя сражаться с самим собой!")

    await ensure_user_row(attacker, chat_id)
    await ensure_user_row(defender, chat_id)

    battle_counter += 1
    battle_id = battle_counter
    battle_key = (chat_id, battle_id)

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Принять бой", callback_data=f"fight_accept:{chat_id}:{battle_id}")
    kb.button(text="❌ Отказаться", callback_data=f"fight_decline:{chat_id}:{battle_id}")
    kb.adjust(2)

    msg = await message.answer(
        f"🥊 <b>Бой предложен!</b>\n"
        f"<a href='tg://user?id={attacker.id}'>{attacker.first_name}</a> вызывает "
        f"<a href='tg://user?id={defender.id}'>{defender.first_name}</a> на дуэль!\n\n"
        f"⚔️ У {defender.first_name} есть 60 секунд, чтобы принять вызов.",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )

    battles[battle_key] = {
        "chat_id": chat_id,
        "attacker": attacker,
        "defender": defender,
        "state": "waiting",
        "message": msg,
        "last_action": time.time()
    }

    asyncio.create_task(auto_cancel_invite(battle_key, msg))


async def auto_cancel_invite(battle_key, msg):
    await asyncio.sleep(60)
    if battle_key in battles and battles[battle_key]["state"] == "waiting":
        await end_battle(battle_key, msg, reason="никто не принял вызов ⌛")


# ================== CALLBACK ==================

@router.callback_query(F.data.startswith("fight_"))
async def fight_handler(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    action = parts[0]
    chat_id = int(parts[1])
    battle_id = int(parts[2])
    battle_key = (chat_id, battle_id)

    battle = battles.get(battle_key)
    if not battle:
        return await cb.answer("❌ Бой не найден", show_alert=True)

    battle["last_action"] = time.time()

    # отказ
    if action == "fight_decline":
        if cb.from_user.id != battle["defender"].id:
            return await cb.answer("Это не ваша кнопка!", show_alert=True)
        return await end_battle(battle_key, cb.message, reason="отказ от боя ❌")

    # принятие
    if action == "fight_accept":
        if cb.from_user.id != battle["defender"].id:
            return await cb.answer("Это не ваша кнопка!", show_alert=True)

        battle.update({
            "state": "fighting",
            "hp": {battle["attacker"].id: 100, battle["defender"].id: 100},
            "turn": battle["attacker"].id,
            "skip": None
        })

        kb = InlineKeyboardBuilder()
        kb.button(text="⚔️ Атаковать", callback_data=f"fight_attack:{chat_id}:{battle_id}")
        await cb.message.edit_text(
            f"🔥 <b>Бой начался!</b>\n"
            f"<a href='tg://user?id={battle['attacker'].id}'>{battle['attacker'].first_name}</a> 🗡 "
            f"vs <a href='tg://user?id={battle['defender'].id}'>{battle['defender'].first_name}</a> 🛡\n\n"
            f"Ходит атакующий: <b>{battle['attacker'].first_name}</b>",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )

        asyncio.create_task(auto_end_inactive(battle_key, cb.message))
        return

    # атака
    if action == "fight_attack":
        if battle["state"] != "fighting":
            return await cb.answer("Бой ещё не начался!", show_alert=True)

        user_id = cb.from_user.id
        if user_id != battle["turn"]:
            return await cb.answer("Сейчас не ваш ход!", show_alert=True)

        attacker = battle["attacker"] if user_id == battle["attacker"].id else battle["defender"]
        defender = battle["defender"] if attacker == battle["attacker"] else battle["attacker"]

        att_stats = await pigs_col.find_one({"user_id": attacker.id, "chat_id": chat_id}, {"weight": 1, "strength": 1})
        def_stats = await pigs_col.find_one({"user_id": defender.id, "chat_id": chat_id}, {"weight": 1, "strength": 1})

        damage, effect = calculate_damage(
            (att_stats["weight"], att_stats["strength"]),
            (def_stats["weight"], def_stats["strength"])
        )

        battle["hp"][defender.id] = max(0, battle["hp"][defender.id] - damage)

        text = f"⚔️ <b>{attacker.first_name}</b> атакует!\n"
        if effect:
            text += f"{effect}\n"
        text += f"💥 Нанесено <b>{damage}</b> урона по <b>{defender.first_name}</b>\n\n{format_hp(battle)}"

        if battle["hp"][defender.id] <= 0:
            return await end_battle(battle_key, cb.message, attacker, defender)

        # 10% шанс оглушения
        if random.random() < 0.1:
            battle["skip"] = defender.id
            text += f"\n💫 <b>{defender.first_name}</b> оглушён и пропускает ход!"
        else:
            battle["skip"] = None

        battle["turn"] = defender.id
        kb = InlineKeyboardBuilder()
        kb.button(text="⚔️ Атаковать", callback_data=f"fight_attack:{chat_id}:{battle_id}")

        await cb.message.edit_text(
            text + f"\n\n➡️ Ходит <b>{defender.first_name}</b>",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )


# ================== АВТОЗАВЕРШЕНИЕ ==================

async def auto_end_inactive(battle_key, msg):
    while battle_key in battles:
        await asyncio.sleep(10)
        if battle_key not in battles:
            break
        battle = battles[battle_key]
        if time.time() - battle["last_action"] > 60:
            await end_battle(battle_key, msg, reason="⏰ бой завершён по бездействию")
            break


@router.message(Command("faq"))
async def cmd_faq(message: types.Message):
    faq_url = "https://telegra.ph/HRYAKBOT--GID-DLYA-NOVYH-POLZOVATELEJ-09-12"
    text = (
        "📜 <b>Инструкция по игре</b>\n\n"
        "Здесь вы найдёте правила, описание команд и советы по уходу за свинкой:\n"
        f"<a href='{faq_url}'>🔗 Открыть инструкцию</a>"
    )
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=False)


# ================== УТИЛИТЫ ==================
def is_owner(user_id: int) -> bool:
    """Проверка, является ли пользователь владельцем/админом"""
    return user_id in OWNER_IDS


@router.message(Command("ad"))
async def cmd_admin_panel(message: types.Message):
    if not is_owner(message.from_user.id):
        return await message.answer("⛔ У вас нет доступа к этой команде.")

    text = (
        "⚙️ <b>Админ-панель</b>\n\n"
        "💰 <b>Финансы:</b>\n"
        " • /givecoins @username количество – выдать монеты\n"
        " • /givekh @username количество – выдать хрякоины\n\n"
        "🏋️ <b>Характеристики:</b>\n"
        " • /add_weight user_id value – добавить вес\n"
        " • /remove_weight user_id value – убрать вес\n"
        " • /add_strength user_id value – добавить силу\n"
        " • /remove_strength user_id value – убрать силу\n\n"
        "🚫 <b>Модерация:</b>\n"
        " • /mute время(мин) (по ответу на сообщение) – замутить\n"
        " • /ban (по ответу на сообщение) – забанить\n"
        " • /unmute (по ответу на сообщение) – размутить\n"
        " • /unban (по ответу на сообщение) – разбанить\n\n"
        "👮 <b>Прочее:</b>\n"
        " • /admins chat_id – список админов чата\n"
        " • /id – айди текущего чата\n"
        " • /reset_all – полный сброс базы\n"
    )
    await message.answer(text, parse_mode="HTML")


# ================== КОМАНДЫ ==================

@router.message(Command("givecoins"))
async def cmd_givecoins(message: types.Message):
    if not is_owner(message.from_user.id):
        return await message.answer("❌ Только администратор может выдавать монеты.")

    args = message.text.split()
    if len(args) != 3:
        return await message.answer("Использование: /givecoins @username количество")

    username = args[1].lstrip("@")
    try:
        amount = int(args[2])
    except ValueError:
        return await message.answer("Количество монет должно быть числом.")
    if amount <= 0:
        return await message.answer("Количество монет должно быть больше нуля.")

    pig = await pigs_col.find_one({"username": username})
    if not pig:
        return await message.answer(f"Пользователь @{username} не найден.")

    await pigs_col.update_one(
        {"user_id": pig["user_id"]},
        {"$inc": {"coins": amount}}
    )
    await message.answer(f"✅ @{username} выдано {amount} монет.")


@router.message(Command("givekh"))
async def cmd_givekh(message: types.Message):
    if not is_owner(message.from_user.id):
        return await message.answer("❌ Только администратор может выдавать хрякоины.")

    args = message.text.split()
    if len(args) != 3:
        return await message.answer("Использование: /givekh <user_id | @username> <количество>")

    target = args[1].lstrip("@")
    try:
        amount = int(args[2])
    except ValueError:
        return await message.answer("Количество должно быть числом.")
    if amount <= 0:
        return await message.answer("Количество должно быть больше 0.")

    uid = None
    if target.isdigit():
        uid = int(target)
    else:
        try:
            member = await message.chat.get_member(target)
            uid = member.user.id
        except Exception:
            return await message.answer("❌ Не удалось найти пользователя в этом чате.")

    await pigs_col.update_one(
        {"user_id": uid, "chat_id": 0},
        {"$setOnInsert": {"coins": 0, "khryacoins": 0, "strength": 0, "weight": 0, "username": ""}},
        upsert=True
    )
    await pigs_col.update_one(
        {"user_id": uid, "chat_id": 0},
        {"$inc": {"khryacoins": amount}}
    )

    await message.answer(f"✅ Пользователю <b>{target}</b> выдано <b>{amount}</b> 🍵 Хрякоинов.", parse_mode="HTML")


# ================== Вес и сила ==================
async def adjust_stat(message: types.Message, stat: str, increase: bool, args: list[str]):
    if not is_owner(message.from_user.id):
        return await message.answer("❌ У вас нет прав.")
    if len(args) != 2:
        return await message.answer(f"Использование: /{('add' if increase else 'remove')}_{stat} user_id value")

    try:
        uid = int(args[0])
        value = float(args[1])
    except ValueError:
        return await message.answer("user_id должен быть целым, value — числом.")

    if not increase:
        value = -value

    await pigs_col.update_one(
        {"user_id": uid},
        {"$inc": {stat: value}}
    )
    action = "увеличена" if increase else "уменьшена"
    await message.answer(f"✅ {stat.capitalize()} пользователя {uid} {action} на {abs(value)}.")


@router.message(Command("add_weight"))
async def cmd_add_weight(message: types.Message, command):
    await adjust_stat(message, "weight", True, (command.args or "").split())


@router.message(Command("remove_weight"))
async def cmd_remove_weight(message: types.Message, command):
    await adjust_stat(message, "weight", False, (command.args or "").split())


@router.message(Command("add_strength"))
async def cmd_add_strength(message: types.Message, command):
    await adjust_stat(message, "strength", True, (command.args or "").split())


@router.message(Command("remove_strength"))
async def cmd_remove_strength(message: types.Message, command):
    await adjust_stat(message, "strength", False, (command.args or "").split())


# ================== Модерация ==================
@router.message(Command("mute"))
async def cmd_mute(message: types.Message):
    if not is_owner(message.from_user.id):
        return await message.answer("⛔ У вас нет доступа к этой команде.")
    if not message.reply_to_message:
        return await message.answer("⚠️ Нужно ответить на сообщение пользователя, которого хочешь замутить.")

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return await message.answer("⚠️ Укажи время мута в минутах: `/mute 30`", parse_mode="Markdown")

    minutes = int(args[1])
    if not (1 <= minutes <= 1000):
        return await message.answer("⚠️ Время мута должно быть от 1 до 1000 минут.")

    target = message.reply_to_message.from_user
    until_date = message.date + timedelta(minutes=minutes)

    try:
        await message.bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target.id,
            permissions=types.ChatPermissions(can_send_messages=False),
            until_date=until_date
        )
        await message.answer(f"🔇 Пользователь {target.mention_html()} замучен на {minutes} минут.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("ban"))
async def cmd_ban(message: types.Message):
    if not is_owner(message.from_user.id):
        return await message.answer("⛔ У вас нет доступа к этой команде.")
    if not message.reply_to_message:
        return await message.answer("⚠️ Нужно ответить на сообщение пользователя, которого хочешь забанить.")

    target = message.reply_to_message.from_user
    try:
        await message.bot.ban_chat_member(chat_id=message.chat.id, user_id=target.id)
        await message.answer(f"🚫 Пользователь {target.mention_html()} забанен.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")



# ================== СНЯТЬ МУТ ==================
@router.message(Command("unmute"))
async def cmd_unmute(message: types.Message):
    if not is_owner(message.from_user.id):
        return await message.answer("⛔ У вас нет доступа к этой команде.")
    if not message.reply_to_message:
        return await message.answer("⚠️ Нужно ответить на сообщение пользователя, которого хочешь размутить.")

    target = message.reply_to_message.from_user
    chat_id = message.chat.id

    try:
        await message.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            permissions=types.ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_change_info=False,
                can_invite_users=True,
                can_pin_messages=False,
                can_manage_topics=True
            ),
            until_date=None
        )
        await message.answer(f"🔊 Пользователь {target.mention_html()} размучен.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# ================== СНЯТЬ БАН ==================
@router.message(Command("unban"))
async def cmd_unban(message: types.Message):
    if not is_owner(message.from_user.id):
        return await message.answer("⛔ У вас нет доступа к этой команде.")
    if not message.reply_to_message:
        return await message.answer("⚠️ Нужно ответить на сообщение пользователя, которого хочешь разбанить.")

    target = message.reply_to_message.from_user
    chat_id = message.chat.id

    try:
        await message.bot.unban_chat_member(chat_id=chat_id, user_id=target.id, only_if_banned=True)
        await message.answer(f"✅ Пользователь {target.mention_html()} разбанен.", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# ================== СПИСОК АДМИНОВ ==================
@router.message(Command("admins"))
async def get_admins(message: types.Message):
    if not is_owner(message.from_user.id):
        return await message.answer("❌ У вас нет прав для этой команды.")

    args = message.text.split()
    if len(args) < 2:
        return await message.answer("⚠️ Укажите ID чата.\nПример: `/admins -1001234567890`", parse_mode="Markdown")

    chat_id = args[1]
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except Exception as e:
        return await message.answer(f"Ошибка: {e}")

    text = f"👮 Администраторы чата `{chat_id}`:\n\n"
    for admin in admins:
        user = admin.user
        if not user.is_bot:
            text += f"👉 [{user.full_name}](tg://user?id={user.id})\n"
    await message.answer(text)


# ================== ID ЧАТА ==================
@router.message(Command("id"))
async def get_chat_id(message: types.Message):
    if not is_owner(message.from_user.id):
        return await message.answer("❌ У вас нет прав для этой команды.")

    chat = message.chat
    await message.answer(f"Тип чата: {chat.type}\n🆔 ID этого чата: `{chat.id}`", parse_mode="Markdown")


# ================== RESET ВСЕЙ БД ==================
@router.message(Command("reset_all"))
async def cmd_reset_all(message: types.Message):
    if not is_owner(message.from_user.id):
        return await message.answer("❌ У вас нет прав для этой команды.")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⚠️ Подтвердить (1/2)", callback_data=f"reset_step1:{message.from_user.id}")]]
    )
    await message.answer("Вы уверены, что хотите сбросить все данные?", reply_markup=kb)


@router.callback_query(F.data.startswith("reset_step1:"))
async def reset_step1(callback: types.CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    if callback.from_user.id != user_id:
        return await callback.answer("❌ Не для вас кнопка", show_alert=True)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🚨 Подтвердить (2/2)", callback_data=f"reset_step2:{user_id}")]]
    )
    await callback.message.edit_text("⚠️ Вы точно уверены? Это действие необратимо!", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("reset_step2:"))
async def reset_step2(callback: types.CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    if callback.from_user.id != user_id:
        return await callback.answer("❌ Не для вас кнопка", show_alert=True)

    try:
        await pigs_col.delete_many({})
        await promo_codes_col.delete_many({})
        await promo_uses_col.delete_many({})
        await user_boosts_col.delete_many({})
        await payments_col.delete_many({})
        await callback.message.edit_text("✅ Все данные сброшены!")
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при сбросе: {e}")

    await callback.answer()


# ================== СОЗДАНИЕ ПРОМОКОДА ==================
@router.message(Command("createcode"))
async def create_code_cmd(msg: types.Message):
    if msg.from_user.id not in OWNER_IDS:
        return await msg.reply("🚫 Только владелец бота может создавать промокоды.")

    parts = msg.text.split()
    if len(parts) < 4:
        return await msg.reply(
            "Использование:\n/createcode <код> <тип_награды> <значение> [лимит_активаций]\n"
            "Пример:\n/createcode PIGGY khryacoins 500 10",
            parse_mode="Markdown"
        )

    _, code, reward_type, value, *rest = parts
    try:
        value = float(value)
    except ValueError:
        return await msg.reply("❌ Значение должно быть числом.")

    max_uses = int(rest[0]) if rest else None
    if reward_type not in ("coins", "khryacoins", "boost_weight", "boost_strength"):
        return await msg.reply("❌ Неверный тип награды. Разрешено: coins, khryacoins, boost_weight, boost_strength")

    existing = await promo_codes_col.find_one({"code": code.upper()})
    if existing:
        return await msg.reply("⚠️ Такой промокод уже существует!")

    await promo_codes_col.insert_one({
        "code": code.upper(),
        "reward_type": reward_type,
        "reward_value": value,
        "created_by": msg.from_user.id,
        "created_at": datetime.now(),
        "max_uses": max_uses,
        "uses_count": 0
    })

    limit_text = f"{max_uses} активаций" if max_uses else "♾️ бесконечный"
    await msg.reply(
        f"✅ Промокод <b>{code.upper()}</b> создан!\n"
        f"Тип: <code>{reward_type}</code>\n"
        f"Значение: <b>{value}</b>\n"
        f"Лимит: {limit_text}", parse_mode="HTML"
    )


# ================== АКТИВАЦИЯ ПРОМОКОДА ==================
@router.message(Command("activate"))
async def activate_code_cmd(msg: types.Message):
    parts = msg.text.split()
    if len(parts) != 2:
        return await msg.reply("Использование: /activate <код>", parse_mode="Markdown")

    code = parts[1].upper()
    uid = msg.from_user.id
    chat_id = msg.chat.id

    code_data = await promo_codes_col.find_one({"code": code})
    if not code_data:
        return await msg.reply("❌ Такого промокода не существует!")

    used = await promo_uses_col.find_one({"user_id": uid, "code": code})
    if used:
        return await msg.reply("⚠️ Ты уже активировал этот промокод!")

    if code_data.get("max_uses") is not None and code_data.get("uses_count", 0) >= code_data["max_uses"]:
        return await msg.reply("🚫 Этот промокод больше нельзя активировать (лимит исчерпан).")

    # Создаём пользователя, если нет
    await pigs_col.update_one({"user_id": uid, "chat_id": chat_id}, {"$setOnInsert": {"coins": 0, "khryacoins": 0, "strength": 0, "weight": 0}}, upsert=True)
    await pigs_col.update_one({"user_id": uid, "chat_id": 0}, {"$setOnInsert": {"coins": 0, "khryacoins": 0, "strength": 0, "weight": 0}}, upsert=True)

    reward_text = ""
    if code_data["reward_type"] == "coins":
        await pigs_col.update_one({"user_id": uid, "chat_id": chat_id}, {"$inc": {"coins": code_data["reward_value"]}})
        reward_text = f"💰 {int(code_data['reward_value'])} монет!"
    elif code_data["reward_type"] == "khryacoins":
        await pigs_col.update_one({"user_id": uid, "chat_id": 0}, {"$inc": {"khryacoins": code_data["reward_value"]}})
        reward_text = f"🐷 {int(code_data['reward_value'])} Хрякоинов!"
    elif code_data["reward_type"] == "boost_weight":
        expires_at = datetime.now() + timedelta(hours=1)
        await user_boosts_col.update_one({"user_id": uid, "chat_id": chat_id, "kind": "weight_pct"},
                                         {"$set": {"value": code_data["reward_value"], "expires_at": expires_at}}, upsert=True)
        reward_text = f"💪 Буст к весу +{code_data['reward_value']}% на 1 час!"
    elif code_data["reward_type"] == "boost_strength":
        expires_at = datetime.now() + timedelta(hours=1)
        await user_boosts_col.update_one({"user_id": uid, "chat_id": chat_id, "kind": "strength_pct"},
                                         {"$set": {"value": code_data["reward_value"], "expires_at": expires_at}}, upsert=True)
        reward_text = f"⚔️ Буст к силе +{code_data['reward_value']}% на 1 час!"

    # Логируем использование
    await promo_uses_col.insert_one({"user_id": uid, "code": code, "used_at": datetime.now()})
    await promo_codes_col.update_one({"code": code}, {"$inc": {"uses_count": 1}})

    remaining = code_data.get("max_uses")
    remain_text = "♾️" if remaining is None else f"{remaining - code_data.get('uses_count', 0) - 1} осталось"

    await msg.reply(
        f"✅ Промокод <b>{code}</b> активирован!\n"
        f"Ты получил {reward_text}\n\n"
        f"🔁 Осталось активаций: <b>{remain_text}</b>",
        parse_mode="HTML"
    )


user_rp_col = db.user_rp
chats_col = db.chats

# ================== ДОБАВЛЕНИЕ МОЕГО РП ==================
@router.message(F.text.startswith("+мойрп"))
async def cmd_add_my_rp(message: types.Message):
    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        return await message.reply(
            "Использование:\n"
            "`+мойрп <эмодзи> <действие> <триггер>`\n"
            "Пример: `+мойрп 💋 поцеловал целую`\n\n",
            parse_mode="Markdown"
        )

    _, emoji, action, trigger = parts
    user_id = message.from_user.id

    await user_rp_col.update_one(
        {"user_id": user_id, "trigger": trigger.lower()},
        {"$set": {"emoji": emoji, "action": action}},
        upsert=True
    )

    await message.reply(
        f"✅ Твоя РП-команда сохранена!\n{emoji} {action}\nТриггер: <b>{trigger}</b>",
        parse_mode="HTML"
    )

# ================== СПИСОК МОИХ РП ==================
@router.message(Command("моирп"))
@router.message(F.text.lower().in_(["мои рп", "мои рпшки", "рп список"]))
async def cmd_list_my_rp(message: types.Message):
    user_id = message.from_user.id
    cursor = user_rp_col.find({"user_id": user_id})
    rows = await cursor.to_list(length=100)

    if not rows:
        return await message.reply(
            "😿 У тебя пока нет своих РП-команд.\nДобавь так: `+мойрп 💋 поцеловал целую`",
            parse_mode="Markdown"
        )

    text = "📜 <b>Твои РП-команды:</b>\n"
    text += "\n".join([f"{row['emoji']} — {row['action']} <i>(триггер: {row['trigger']})</i>" for row in rows])
    await message.reply(text, parse_mode="HTML")

# ================== ОБРАБОТКА РП ==================
@router.message(F.text.regexp(r"^(\S+)\s*"))
async def handle_rp_action(message: types.Message):
    text = message.text.strip().lower()
    user_id = message.from_user.id

    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
        target = f'<a href="tg://user?id={target_user.id}">{target_user.first_name}</a>'
        first = text.split(maxsplit=1)[0]
    else:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return
        first, target_text = parts
        target = target_text

    row = await user_rp_col.find_one({"user_id": user_id, "$or": [{"emoji": first}, {"trigger": first}]})
    if not row:
        return

    sender = f'<a href="tg://user?id={user_id}">{message.from_user.first_name}</a>'
    await message.reply(f"{row['emoji']} {sender} {row['action']} {target} 😳", parse_mode="HTML")

# ================== АВТОТРЕК ЧАТОВ ==================
@router.message()
async def auto_track_chat(message: types.Message):
    # Пропускаем команды
    if message.text and message.text.startswith("/"):
        return

    chat = message.chat
    chat_type = chat.type if chat.type else "unknown"
    chat_title = chat.title if chat.title else ""

    exists = await chats_col.find_one({"chat_id": chat.id})
    if not exists:
        await chats_col.insert_one({
            "chat_id": chat.id,
            "title": chat_title,
            "chat_type": chat_type,
            "added_at": datetime.now(timezone.utc)
        })
        print(f"[AUTO_TRACK] Добавлен чат: {chat_title} ({chat_type})")




# ================== ЗАПУСК ==================
async def main():
    asyncio.create_task(check_invoices_loop(bot))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

import motor.motor_asyncio
import logging

logging.getLogger("motor").setLevel(logging.INFO)

if __name__ == "__main__":
    asyncio.run(main())
