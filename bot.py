# -*- coding: utf-8 -*-

"""
Telegram Coin Game Bot
Python 3.10+
python-telegram-bot==21.11.1

واحد: سکه
Database: SQLite

BOT_TOKEN باید در Environment تنظیم شود.
"""

import asyncio
import html
import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# ============================================================
# تنظیمات
# ============================================================

TOKEN = None

CHANNEL_ID = -1004372755284
ALLOWED_GROUP_ID = -1003919206941

OWNER_IDS = {
    8935601841,
    8458210170,
}

DB_FILE = "bot.db"

COIN_NAME = "سکه"

MIN_REQUEST = 2000

REFERRAL_REWARD = 60

GAME_REWARD = 20

MAX_ROLLS = 3

ROLL_DELAY = 2.0


# ============================================================
# لاگ
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("COIN_BOT")


# ============================================================
# متغیرهای داخلی
# ============================================================

DB_LOCK = asyncio.Lock()

GAMES = {}

GAME_COUNTER = 0


# ============================================================
# اعداد فارسی
# ============================================================

DIGIT_TABLE = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


def normalize_digits(text: str) -> str:
    return text.translate(DIGIT_TABLE)


def parse_number(value: str) -> Optional[int]:

    value = normalize_digits(value)

    value = value.replace(",", "")
    value = value.replace("٬", "")
    value = value.strip()

    if not re.fullmatch(r"\d+", value):
        return None

    try:
        return int(value)
    except Exception:
        return None


def format_amount(amount: int) -> str:
    return f"{amount:,}"


# ============================================================
# بازی‌ها
# ============================================================

GAME_ALIASES = {
    "تاس": "dice",
    "dice": "dice",

    "بولینگ": "bowling",
    "بولينگ": "bowling",
    "bowling": "bowling",

    "بسکتبال": "basketball",
    "basketball": "basketball",

    "دارت": "darts",
    "dart": "darts",
    "darts": "darts",
}

GAME_NAMES = {
    "dice": "تاس",
    "bowling": "بولینگ",
    "basketball": "بسکتبال",
    "darts": "دارت",
}

GAME_EMOJIS = {
    "dice": "🎲",
    "bowling": "🎳",
    "basketball": "🏀",
    "darts": "🎯",
}


# ============================================================
# زمان و ابزار
# ============================================================

def now_str() -> str:
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def escape(text) -> str:
    return html.escape(str(text))


def mention(user) -> str:

    name = (
        getattr(user, "first_name", None)
        or getattr(user, "username", None)
        or str(user.id)
    )

    return (
        f'<a href="tg://user?id={user.id}">'
        f'{escape(name)}'
        f'</a>'
    )


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


def is_group(update: Update) -> bool:

    chat = update.effective_chat

    return bool(
        chat
        and chat.type in (
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        )
    )


def is_allowed_group(update: Update) -> bool:

    chat = update.effective_chat

    return bool(
        chat
        and chat.type in (
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        )
        and chat.id == ALLOWED_GROUP_ID
    )


# ============================================================
# دیتابیس
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    return conn


def init_db():

    with closing(db_connect()) as db:

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER NOT NULL DEFAULT 0,
                referrer_id INTEGER,
                referral_claimed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER NOT NULL UNIQUE,
                reward INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        # درخواست فقط برای بررسی داخلی
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                processed_at TEXT,
                processed_by INTEGER
            )
            """
        )

        db.commit()


# ============================================================
# کاربران
# ============================================================

async def ensure_user(
    user,
    referrer_id: Optional[int] = None,
):

    async with DB_LOCK:

        with closing(db_connect()) as db:

            row = db.execute(
                """
                SELECT *
                FROM users
                WHERE user_id = ?
                """,
                (user.id,),
            ).fetchone()

            if row is None:

                db.execute(
                    """
                    INSERT INTO users (
                        user_id,
                        username,
                        first_name,
                        balance,
                        referrer_id,
                        referral_claimed,
                        created_at
                    )
                    VALUES (?, ?, ?, 0, ?, 0, ?)
                    """,
                    (
                        user.id,
                        user.username,
                        user.first_name,
                        referrer_id,
                        now_str(),
                    ),
                )

            else:

                db.execute(
                    """
                    UPDATE users
                    SET username = ?,
                        first_name = ?
                    WHERE user_id = ?
                    """,
                    (
                        user.username,
                        user.first_name,
                        user.id,
                    ),
                )

            db.commit()


async def get_balance(user_id: int) -> int:

    async with DB_LOCK:

        with closing(db_connect()) as db:

            row = db.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

            if not row:
                return 0

            return int(row["balance"])


async def change_balance(
    user_id: int,
    amount: int,
    transaction_type: str,
    description: str = "",
) -> Optional[int]:

    async with DB_LOCK:

        with closing(db_connect()) as db:

            row = db.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

            if not row:
                return None

            old_balance = int(row["balance"])

            new_balance = old_balance + amount

            if new_balance < 0:
                return None

            db.execute(
                """
                UPDATE users
                SET balance = ?
                WHERE user_id = ?
                """,
                (
                    new_balance,
                    user_id,
                ),
            )

            db.execute(
                """
                INSERT INTO transactions (
                    user_id,
                    type,
                    amount,
                    balance_after,
                    description,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    transaction_type,
                    amount,
                    new_balance,
                    description,
                    now_str(),
                ),
            )

            db.commit()

            return new_balance


async def get_user_object(
    context,
    user_id: int,
):

    try:

        return await context.bot.get_chat(
            user_id
        )

    except Exception:

        class Dummy:
            pass

        dummy = Dummy()
        dummy.id = user_id
        dummy.first_name = str(user_id)
        dummy.username = None

        return dummy


# ============================================================
# زیرمجموعه
# ============================================================

async def process_referral(
    new_user,
    referrer_id: int,
):

    if new_user.id == referrer_id:
        return False

    async with DB_LOCK:

        with closing(db_connect()) as db:

            referrer = db.execute(
                """
                SELECT user_id
                FROM users
                WHERE user_id = ?
                """,
                (referrer_id,),
            ).fetchone()

            if not referrer:
                return False

            existing = db.execute(
                """
                SELECT id
                FROM referrals
                WHERE referred_id = ?
                """,
                (new_user.id,),
            ).fetchone()

            if existing:
                return False

            user = db.execute(
                """
                SELECT referral_claimed
                FROM users
                WHERE user_id = ?
                """,
                (new_user.id,),
            ).fetchone()

            if not user:
                return False

            if int(user["referral_claimed"]) == 1:
                return False

            db.execute(
                """
                UPDATE users
                SET referrer_id = ?,
                    referral_claimed = 1
                WHERE user_id = ?
                """,
                (
                    referrer_id,
                    new_user.id,
                ),
            )

            balance_row = db.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id = ?
                """,
                (referrer_id,),
            ).fetchone()

            if not balance_row:
                return False

            new_balance = (
                int(balance_row["balance"])
                + REFERRAL_REWARD
            )

            db.execute(
                """
                UPDATE users
                SET balance = ?
                WHERE user_id = ?
                """,
                (
                    new_balance,
                    referrer_id,
                ),
            )

            db.execute(
                """
                INSERT INTO referrals (
                    referrer_id,
                    referred_id,
                    reward,
                    created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    referrer_id,
                    new_user.id,
                    REFERRAL_REWARD,
                    now_str(),
                ),
            )

            db.execute(
                """
                INSERT INTO transactions (
                    user_id,
                    type,
                    amount,
                    balance_after,
                    description,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    referrer_id,
                    "referral",
                    REFERRAL_REWARD,
                    new_balance,
                    "پاداش زیرمجموعه",
                    now_str(),
                ),
            )

            db.commit()

    return True


# ============================================================
# منوی خصوصی
# ============================================================

def private_menu(user_id: int):

    buttons = [
        [
            InlineKeyboardButton(
                "👥 زیرمجموعه‌گیری",
                callback_data="menu_ref",
            ),
            InlineKeyboardButton(
                "📤 درخواست بررسی",
                callback_data="menu_request",
            ),
        ],
        [
            InlineKeyboardButton(
                "🪙 موجودی",
                callback_data="menu_balance",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎮 بازی‌ها",
                callback_data="menu_games",
            ),
        ],
    ]

    if is_owner(user_id):

        buttons.append(
            [
                InlineKeyboardButton(
                    "⚙️ پنل مدیریت",
                    callback_data="admin_panel",
                )
            ]
        )

    return InlineKeyboardMarkup(buttons)


# ============================================================
# START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user:
        return

    referrer_id = None

    if context.args:

        arg = context.args[0].strip()

        match = re.fullmatch(
            r"ref_(\d+)",
            arg,
        )

        if match:
            referrer_id = int(match.group(1))

    await ensure_user(
        user,
        referrer_id,
    )

    if referrer_id:

        try:

            rewarded = await process_referral(
                user,
                referrer_id,
            )

            if rewarded:

                try:

                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=(
                            "🎉 یک نفر با لینک تو عضو شد!\n\n"
                            f"🪙 +{REFERRAL_REWARD} {COIN_NAME}"
                        ),
                    )

                except Exception:
                    pass

        except Exception:
            logger.exception(
                "Referral error"
            )

    if update.effective_chat.type != ChatType.PRIVATE:

        await update.message.reply_text(
            "ربات آماده است.\n"
            "🎮 بازی‌ها را در گپ مجاز انجام بده."
        )

        return

    await update.message.reply_text(
        "🎮 به ربات خوش آمدی!\n\n"
        f"🪙 واحد حساب: {COIN_NAME}\n"
        "سکه‌ها مجازی هستند.",
        reply_markup=private_menu(user.id),
    )


# ============================================================
# موجودی
# ============================================================

async def send_balance(
    update: Update,
    user_id: int,
):

    balance = await get_balance(user_id)

    user = update.effective_user

    await update.message.reply_text(
        (
            f"🪙 موجودی "
            f"{escape(user.first_name or user.username or str(user.id))}: "
            f"{format_amount(balance)} {COIN_NAME}"
        ),
        parse_mode="HTML",
    )


# ============================================================
# زیرمجموعه
# ============================================================

async def referral_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    me = await context.bot.get_me()

    link = (
        f"https://t.me/{me.username}"
        f"?start=ref_{update.effective_user.id}"
    )

    await update.message.reply_text(
        (
            "👥 لینک دعوت شما:\n\n"
            f"{link}\n\n"
            f"🎁 پاداش هر زیرمجموعه: "
            f"{REFERRAL_REWARD} {COIN_NAME}\n\n"
            "هر کاربر فقط یک بار برای یک معرف حساب می‌شود."
        )
    )


# ============================================================
# درخواست بررسی
# ============================================================

async def request_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data["waiting_request"] = True

    await update.message.reply_text(
        (
            "📤 مبلغ درخواست بررسی را ارسال کن.\n\n"
            f"حداقل مقدار: {format_amount(MIN_REQUEST)} {COIN_NAME}\n\n"
            "مثال:\n"
            "2000\n\n"
            "⚠️ این درخواست فقط برای بررسی داخلی سکه‌های مجازی "
            "به کانال ارسال می‌شود."
        )
    )


async def create_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    amount: int,
):

    user = update.effective_user

    await ensure_user(user)

    if amount < MIN_REQUEST:

        await update.message.reply_text(
            (
                f"❌ حداقل مقدار "
                f"{format_amount(MIN_REQUEST)} {COIN_NAME} است."
            )
        )

        return

    balance = await get_balance(user.id)

    if balance < amount:

        await update.message.reply_text(
            (
                "❌ موجودی کافی نیست.\n\n"
                f"موجودی: {format_amount(balance)} {COIN_NAME}"
            )
        )

        return

    # نکته:
    # موجودی اینجا کسر نمی‌شود.
    # درخواست فقط در جدول ثبت و به کانال ارسال می‌شود.

    async with DB_LOCK:

        with closing(db_connect()) as db:

            cursor = db.execute(
                """
                INSERT INTO requests (
                    user_id,
                    username,
                    amount,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (
                    user.id,
                    user.username,
                    amount,
                    now_str(),
                ),
            )

            request_id = cursor.lastrowid

            db.commit()

    username_text = (
        f"@{user.username}"
        if user.username
        else "بدون username"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ تأیید",
                    callback_data=f"req_ok_{request_id}",
                ),
                InlineKeyboardButton(
                    "❌ رد",
                    callback_data=f"req_no_{request_id}",
                ),
            ]
        ]
    )

    channel_text = (
        "📤 درخواست بررسی سکه\n\n"
        f"🆔 شماره درخواست: {request_id}\n"
        f"👤 کاربر: {mention(user)}\n"
        f"🔗 Username: {escape(username_text)}\n"
        f"🆔 User ID: {user.id}\n"
        f"🪙 مقدار: {format_amount(amount)} {COIN_NAME}\n"
        f"💰 موجودی فعلی: {format_amount(balance)} {COIN_NAME}\n\n"
        "⚠️ این فقط یک درخواست بررسی داخلی است و پرداختی انجام نمی‌شود."
    )

    try:

        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=channel_text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    except Exception:

        logger.exception(
            "Cannot send request to channel"
        )

        async with DB_LOCK:

            with closing(db_connect()) as db:

                db.execute(
                    """
                    UPDATE requests
                    SET status = 'failed'
                    WHERE id = ?
                    """,
                    (request_id,),
                )

                db.commit()

        await update.message.reply_text(
            "❌ ارسال درخواست به کانال انجام نشد."
        )

        return

    await update.message.reply_text(
        (
            "✅ درخواست ثبت شد.\n\n"
            f"🪙 مقدار: {format_amount(amount)} {COIN_NAME}\n"
            f"🆔 شماره درخواست: {request_id}\n\n"
            "درخواست فقط برای بررسی داخلی به کانال ارسال شد."
        )
    )


# ============================================================
# بازی‌ها
# ============================================================

async def show_games(
    update: Update,
):

    text = (
        "🎮 بازی‌های ربات\n\n"
        "🎲 تاس\n"
        "🎳 بولینگ\n"
        "🏀 بسکتبال\n"
        "🎯 دارت\n\n"
        f"🎁 پاداش هر بازی موفق: "
        f"{GAME_REWARD} {COIN_NAME}\n\n"
        "دستورات گروه:\n"
        "تاس\n"
        "بولینگ\n"
        "بسکتبال\n"
        "دارت\n"
        "فرد\n"
        "زوج"
    )

    if update.callback_query:

        await update.callback_query.answer()

        await update.callback_query.message.reply_text(
            text
        )

    else:

        await update.message.reply_text(
            text
        )


async def send_game_roll(
    bot,
    chat_id: int,
    game: str,
):

    message = await bot.send_dice(
        chat_id=chat_id,
        emoji=GAME_EMOJIS[game],
    )

    value = message.dice.value

    if game == "basketball":

        score = value if value >= 4 else 0

    else:

        score = value

    return value, score


async def play_single_game(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    game: str,
):

    user = update.effective_user

    total = 0

    await update.message.reply_text(
        (
            f"{GAME_EMOJIS[game]} "
            f"{GAME_NAMES[game]}\n\n"
            f"🎁 جایزه: {GAME_REWARD} {COIN_NAME}"
        )
    )

    for i in range(MAX_ROLLS):

        await asyncio.sleep(0.3)

        value, score = await send_game_roll(
            context.bot,
            update.effective_chat.id,
            game,
        )

        total += score

        await asyncio.sleep(ROLL_DELAY)

    await change_balance(
        user.id,
        GAME_REWARD,
        "game_reward",
        f"پاداش بازی {GAME_NAMES[game]}",
    )

    balance = await get_balance(user.id)

    await update.message.reply_text(
        (
            f"🎉 {mention(user)}\n\n"
            f"{GAME_EMOJIS[game]} بازی تمام شد.\n"
            f"📊 امتیاز: {total}\n"
            f"🎁 پاداش: {GAME_REWARD} {COIN_NAME}\n"
            f"🪙 موجودی: {format_amount(balance)} {COIN_NAME}"
        ),
        parse_mode="HTML",
    )


async def play_even_odd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    choice: str,
):

    user = update.effective_user

    await update.message.reply_text(
        f"🎲 {mention(user)}\nبریز!",
        parse_mode="HTML",
    )

    await asyncio.sleep(0.4)

    message = await context.bot.send_dice(
        chat_id=update.effective_chat.id,
        emoji="🎲",
    )

    value = message.dice.value

    correct = (
        choice == "زوج"
        and value % 2 == 0
    ) or (
        choice == "فرد"
        and value % 2 != 0
    )

    if correct:

        await change_balance(
            user.id,
            GAME_REWARD,
            "even_odd_reward",
            f"پاداش بازی {choice}",
        )

        balance = await get_balance(
            user.id
        )

        text = (
            f"🎉 {mention(user)}\n\n"
            f"🎲 نتیجه: {value}\n"
            f"انتخاب: {choice}\n"
            f"✅ درست بود!\n\n"
            f"🎁 +{GAME_REWARD} {COIN_NAME}\n"
            f"🪙 موجودی: {format_amount(balance)} {COIN_NAME}"
        )

    else:

        balance = await get_balance(
            user.id
        )

        text = (
            f"🎲 نتیجه: {value}\n"
            f"انتخاب: {choice}\n"
            "❌ این بار درست نبود.\n\n"
            f"🪙 موجودی: {format_amount(balance)} {COIN_NAME}"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# انتقال سکه
# ============================================================

def parse_transfer_command(text: str):

    text = normalize_digits(text).strip()

    match = re.fullmatch(
        r"(انتقال|واریز)\s+(\d+)\s+@([A-Za-z0-9_]{5,32})",
        text,
        flags=re.IGNORECASE,
    )

    if match:

        return {
            "amount": parse_number(match.group(2)),
            "username": match.group(3),
        }

    match = re.fullmatch(
        r"(انتقال|واریز)\s+(\d+)",
        text,
        flags=re.IGNORECASE,
    )

    if match:

        return {
            "amount": parse_number(match.group(2)),
            "username": None,
        }

    return None


async def handle_transfer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: dict,
):

    sender = update.effective_user

    amount = data["amount"]

    if not amount or amount <= 0:

        await update.message.reply_text(
            "❌ مبلغ نامعتبر است."
        )

        return

    target_id = None
    target_user = None

    if update.message.reply_to_message:

        target_user = (
            update.message.reply_to_message.from_user
        )

        if target_user:
            target_id = target_user.id

    elif data["username"]:

        username = data["username"].lower()

        async with DB_LOCK:

            with closing(db_connect()) as db:

                row = db.execute(
                    """
                    SELECT user_id,
                           username,
                           first_name
                    FROM users
                    WHERE lower(username) = ?
                    LIMIT 1
                    """,
                    (username,),
                ).fetchone()

        if row:

            target_id = int(row["user_id"])

            class Target:
                pass

            target_user = Target()
            target_user.id = target_id
            target_user.username = row["username"]
            target_user.first_name = row["first_name"]

    if not target_id:

        await update.message.reply_text(
            (
                "❌ کاربر پیدا نشد.\n\n"
                "مثال:\n"
                "انتقال 500 @username\n\n"
                "یا روی پیام کاربر ریپلای کن:\n"
                "انتقال 500"
            )
        )

        return

    if target_id == sender.id:

        await update.message.reply_text(
            "❌ نمی‌توانی به خودت انتقال بدهی."
        )

        return

    await ensure_user(target_user)

    balance = await get_balance(sender.id)

    if balance < amount:

        await update.message.reply_text(
            (
                "❌ موجودی کافی نیست.\n"
                f"🪙 موجودی: {format_amount(balance)} {COIN_NAME}"
            )
        )

        return

    sender_new = await change_balance(
        sender.id,
        -amount,
        "transfer_out",
        f"انتقال به {target_id}",
    )

    if sender_new is None:

        await update.message.reply_text(
            "❌ انتقال انجام نشد."
        )

        return

    receiver_new = await change_balance(
        target_id,
        amount,
        "transfer_in",
        f"دریافت از {sender.id}",
    )

    if receiver_new is None:

        await change_balance(
            sender.id,
            amount,
            "transfer_rollback",
            "بازگشت انتقال ناموفق",
        )

        await update.message.reply_text(
            "❌ انتقال ناموفق بود و سکه‌ها برگشت داده شدند."
        )

        return

    await update.message.reply_text(
        (
            "✅ انتقال انجام شد.\n\n"
            f"👤 فرستنده: {mention(sender)}\n"
            f"👤 گیرنده: {mention(target_user)}\n"
            f"🪙 مقدار: {format_amount(amount)} {COIN_NAME}\n"
            f"💰 موجودی شما: {format_amount(sender_new)} {COIN_NAME}"
        ),
        parse_mode="HTML",
    )


# ============================================================
# پنل مدیریت
# ============================================================

async def show_admin_panel(
    update: Update,
):

    user = update.effective_user

    if not is_owner(user.id):
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "👥 موجودی کاربران",
                    callback_data="admin_users",
                )
            ],
            [
                InlineKeyboardButton(
                    "📤 درخواست‌ها",
                    callback_data="admin_requests",
                )
            ],
            [
                InlineKeyboardButton(
                    "➕ راهنمای افزایش",
                    callback_data="admin_add_help",
                ),
                InlineKeyboardButton(
                    "➖ راهنمای کاهش",
                    callback_data="admin_sub_help",
                ),
            ],
            [
                InlineKeyboardButton(
                    "👥 افزایش زیرمجموعه",
                    callback_data="admin_ref_help",
                )
            ],
        ]
    )

    if update.callback_query:

        await update.callback_query.answer()

        await update.callback_query.message.reply_text(
            "⚙️ پنل مدیریت:",
            reply_markup=keyboard,
        )

    else:

        await update.message.reply_text(
            "⚙️ پنل مدیریت:",
            reply_markup=keyboard,
        )


# ============================================================
# کاربران ادمین
# ============================================================

async def admin_users(query):

    if not is_owner(query.from_user.id):

        await query.answer(
            "دسترسی ندارید.",
            show_alert=True,
        )

        return

    async with DB_LOCK:

        with closing(db_connect()) as db:

            rows = db.execute(
                """
                SELECT user_id,
                       username,
                       first_name,
                       balance
                FROM users
                ORDER BY balance DESC
                """
            ).fetchall()

    if not rows:

        text = "کاربری ثبت نشده."

    else:

        lines = [
            "🪙 موجودی کاربران:",
            "",
        ]

        for index, row in enumerate(
            rows,
            start=1,
        ):

            username = (
                f"@{row['username']}"
                if row["username"]
                else "بدون_username"
            )

            lines.append(
                f"{index}_ "
                f"{username} | "
                f"{format_amount(int(row['balance']))} {COIN_NAME} | "
                f"ID: {row['user_id']}"
            )

        text = "\n".join(lines)

    await query.answer()

    await query.message.reply_text(
        text[:4096]
    )


# ============================================================
# راهنمای ادمین
# ============================================================

async def admin_add_help(query):

    if not is_owner(query.from_user.id):

        await query.answer(
            "دسترسی ندارید.",
            show_alert=True,
        )

        return

    await query.answer()

    await query.message.reply_text(
        (
            "➕ افزایش موجودی\n\n"
            "فرمت:\n"
            "افزایش user_id amount\n\n"
            "مثال:\n"
            "افزایش 123456789 500"
        )
    )


async def admin_sub_help(query):

    if not is_owner(query.from_user.id):

        await query.answer(
            "دسترسی ندارید.",
            show_alert=True,
        )

        return

    await query.answer()

    await query.message.reply_text(
        (
            "➖ کاهش موجودی\n\n"
            "فرمت:\n"
            "کاهش user_id amount\n\n"
            "مثال:\n"
            "کاهش 123456789 500"
        )
    )


async def admin_ref_help(query):

    if not is_owner(query.from_user.id):

        await query.answer(
            "دسترسی ندارید.",
            show_alert=True,
        )

        return

    await query.answer()

    await query.message.reply_text(
        (
            "👥 افزایش زیرمجموعه\n\n"
            "فرمت:\n"
            "زیرمجموعه user_id count\n\n"
            "مثال:\n"
            "زیرمجموعه 123456789 5\n\n"
            f"پاداش هر مورد: {REFERRAL_REWARD} {COIN_NAME}"
        )
    )


# ============================================================
# درخواست‌های مدیریت
# ============================================================

async def admin_requests(query):

    if not is_owner(query.from_user.id):

        await query.answer(
            "دسترسی ندارید.",
            show_alert=True,
        )

        return

    async with DB_LOCK:

        with closing(db_connect()) as db:

            rows = db.execute(
                """
                SELECT *
                FROM requests
                WHERE status = 'pending'
                ORDER BY id ASC
                LIMIT 30
                """
            ).fetchall()

    await query.answer()

    if not rows:

        await query.message.reply_text(
            "📭 درخواست در انتظار وجود ندارد."
        )

        return

    for row in rows:

        username = (
            f"@{row['username']}"
            if row["username"]
            else "بدون username"
        )

        text = (
            f"📤 درخواست #{row['id']}\n\n"
            f"🆔 User ID: {row['user_id']}\n"
            f"👤 Username: {username}\n"
            f"🪙 مقدار: {format_amount(row['amount'])} {COIN_NAME}\n"
            f"🕐 زمان: {row['created_at']}\n\n"
            "⚠️ درخواست فقط بررسی داخلی است."
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ تأیید",
                        callback_data=f"req_ok_{row['id']}",
                    ),
                    InlineKeyboardButton(
                        "❌ رد",
                        callback_data=f"req_no_{row['id']}",
                    ),
                ]
            ]
        )

        await query.message.reply_text(
            text,
            reply_markup=keyboard,
        )


# ============================================================
# پردازش درخواست
# ============================================================

async def process_request_callback(
    query,
    context,
    request_id: int,
    approve: bool,
):

    if not is_owner(query.from_user.id):

        await query.answer(
            "دسترسی ندارید.",
            show_alert=True,
        )

        return

    async with DB_LOCK:

        with closing(db_connect()) as db:

            row = db.execute(
                """
                SELECT *
                FROM requests
                WHERE id = ?
                """,
                (request_id,),
            ).fetchone()

            if not row:

                await query.answer(
                    "درخواست پیدا نشد.",
                    show_alert=True,
                )

                return

            if row["status"] != "pending":

                await query.answer(
                    "این درخواست قبلاً بررسی شده.",
                    show_alert=True,
                )

                return

            status = (
                "approved"
                if approve
                else "rejected"
            )

            db.execute(
                """
                UPDATE requests
                SET status = ?,
                    processed_at = ?,
                    processed_by = ?
                WHERE id = ?
                """,
                (
                    status,
                    now_str(),
                    query.from_user.id,
                    request_id,
                ),
            )

            db.commit()

    await query.answer("انجام شد.")

    try:

        if approve:

            await query.edit_message_text(
                (
                    f"📤 درخواست #{request_id}\n\n"
                    "✅ تأیید شد.\n"
                    "این تأیید فقط ثبت داخلی درخواست است."
                )
            )

        else:

            await query.edit_message_text(
                (
                    f"📤 درخواست #{request_id}\n\n"
                    "❌ رد شد."
                )
            )

    except Exception:
        pass

    try:

        await context.bot.send_message(
            chat_id=row["user_id"],
            text=(
                f"📤 وضعیت درخواست #{request_id}\n\n"
                + (
                    "✅ درخواست شما تأیید شد."
                    if approve
                    else "❌ درخواست شما رد شد."
                )
            ),
        )

    except Exception:
        pass


# ============================================================
# دستورات متنی ادمین
# ============================================================

async def handle_admin_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user or not is_owner(user.id):
        return

    text = normalize_digits(
        update.message.text.strip()
    )

    # --------------------------------------------------------
    # افزایش
    # --------------------------------------------------------

    match = re.fullmatch(
        r"افزایش\s+(\d+)\s+(\d+)",
        text,
    )

    if match:

        user_id = int(match.group(1))
        amount = int(match.group(2))

        target = await get_user_object(
            context,
            user_id,
        )

        await ensure_user(target)

        new_balance = await change_balance(
            user_id,
            amount,
            "admin_add",
            f"افزایش توسط مالک {user.id}",
        )

        if new_balance is None:

            await update.message.reply_text(
                "❌ کاربر پیدا نشد."
            )

        else:

            await update.message.reply_text(
                (
                    "✅ موجودی افزایش یافت.\n\n"
                    f"🆔 کاربر: {user_id}\n"
                    f"➕ مقدار: {format_amount(amount)} {COIN_NAME}\n"
                    f"🪙 موجودی جدید: "
                    f"{format_amount(new_balance)} {COIN_NAME}"
                )
            )

        return

    # --------------------------------------------------------
    # کاهش
    # --------------------------------------------------------

    match = re.fullmatch(
        r"کاهش\s+(\d+)\s+(\d+)",
        text,
    )

    if match:

        user_id = int(match.group(1))
        amount = int(match.group(2))

        target = await get_user_object(
            context,
            user_id,
        )

        await ensure_user(target)

        balance = await get_balance(
            user_id
        )

        if balance < amount:

            await update.message.reply_text(
                (
                    "❌ موجودی کافی نیست.\n"
                    f"موجودی: {format_amount(balance)} {COIN_NAME}"
                )
            )

            return

        new_balance = await change_balance(
            user_id,
            -amount,
            "admin_sub",
            f"کاهش توسط مالک {user.id}",
        )

        await update.message.reply_text(
            (
                "✅ موجودی کاهش یافت.\n\n"
                f"🆔 کاربر: {user_id}\n"
                f"➖ مقدار: {format_amount(amount)} {COIN_NAME}\n"
                f"🪙 موجودی جدید: "
                f"{format_amount(new_balance or 0)} {COIN_NAME}"
            )
        )

        return

    # --------------------------------------------------------
    # افزایش زیرمجموعه
    # --------------------------------------------------------

    match = re.fullmatch(
        r"زیرمجموعه\s+(\d+)\s+(\d+)",
        text,
    )

    if match:

        user_id = int(match.group(1))
        count = int(match.group(2))

        if count <= 0:

            await update.message.reply_text(
                "❌ تعداد نامعتبر است."
            )

            return

        target = await get_user_object(
            context,
            user_id,
        )

        await ensure_user(target)

        reward = (
            count * REFERRAL_REWARD
        )

        new_balance = await change_balance(
            user_id,
            reward,
            "admin_referral_add",
            f"افزایش {count} زیرمجموعه توسط مالک",
        )

        await update.message.reply_text(
            (
                "✅ زیرمجموعه اضافه شد.\n\n"
                f"🆔 کاربر: {user_id}\n"
                f"👥 تعداد: {count}\n"
                f"🎁 پاداش: {format_amount(reward)} {COIN_NAME}\n"
                f"🪙 موجودی جدید: "
                f"{format_amount(new_balance or 0)} {COIN_NAME}"
            )
        )

        return


# ============================================================
# پیام‌های گروه
# ============================================================

async def handle_group_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_allowed_group(update):
        return

    user = update.effective_user

    if not user:
        return

    await ensure_user(user)

    text = normalize_digits(
        update.message.text.strip()
    )

    # --------------------------------------------------------
    # موجودی
    # --------------------------------------------------------

    if text in ("م", "موجودی"):

        balance = await get_balance(
            user.id
        )

        await update.message.reply_text(
            (
                f"🪙 موجودی "
                f"{escape(user.first_name or user.username or str(user.id))}: "
                f"{format_amount(balance)} {COIN_NAME}"
            ),
            parse_mode="HTML",
        )

        return

    # --------------------------------------------------------
    # بازی مستقیم
    # --------------------------------------------------------

    if text.lower() in GAME_ALIASES:

        game = GAME_ALIASES[
            text.lower()
        ]

        await play_single_game(
            update,
            context,
            game,
        )

        return

    # --------------------------------------------------------
    # زوج / فرد
    # --------------------------------------------------------

    if text in ("زوج", "فرد"):

        await play_even_odd(
            update,
            context,
            text,
        )

        return

    # --------------------------------------------------------
    # انتقال
    # --------------------------------------------------------

    transfer = parse_transfer_command(
        text
    )

    if transfer:

        await handle_transfer(
            update,
            context,
            transfer,
        )

        return


# ============================================================
# پیام خصوصی
# ============================================================

async def handle_private_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if not user or not update.message:
        return

    await ensure_user(user)

    text = update.message.text.strip()

    normalized = normalize_digits(text)

    # --------------------------------------------------------
    # درخواست در انتظار مبلغ
    # --------------------------------------------------------

    if context.user_data.get("waiting_request"):

        amount = parse_number(
            normalized
        )

        if amount is None:

            await update.message.reply_text(
                "❌ فقط عدد وارد کن."
            )

            return

        context.user_data["waiting_request"] = False

        await create_request(
            update,
            context,
            amount,
        )

        return

    # --------------------------------------------------------
    # موجودی
    # --------------------------------------------------------

    if normalized in (
        "م",
        "موجودی",
    ):

        await send_balance(
            update,
            user.id,
        )

        return

    # --------------------------------------------------------
    # زیرمجموعه
    # --------------------------------------------------------

    if text in (
        "زیرمجموعه",
        "زیرمجموعه‌گیری",
        "زیرمجموعه گیری",
    ):

        await referral_message(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # درخواست
    # --------------------------------------------------------

    if text in (
        "برداشت",
        "درخواست",
        "درخواست بررسی",
    ):

        await request_start(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # بازی‌ها
    # --------------------------------------------------------

    if text in (
        "بازی",
        "بازی‌ها",
        "بازی ها",
    ):

        await show_games(
            update,
        )

        return

    # --------------------------------------------------------
    # راهنما
    # --------------------------------------------------------

    if text == "راهنما":

        await update.message.reply_text(
            (
                "❓ راهنما\n\n"
                "🪙 موجودی\n"
                "👥 زیرمجموعه\n"
                "🎮 بازی‌ها\n"
                "📤 درخواست\n\n"
                "در گروه مجاز می‌توانی بازی کنی.\n"
                f"🎁 پاداش هر بازی: {GAME_REWARD} {COIN_NAME}\n"
                f"👥 پاداش دعوت: {REFERRAL_REWARD} {COIN_NAME}\n"
                f"📤 حداقل درخواست: {MIN_REQUEST} {COIN_NAME}\n\n"
                "سکه‌ها مجازی هستند."
            )
        )

        return


# ============================================================
# Callback
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    data = query.data or ""

    # --------------------------------------------------------
    # موجودی
    # --------------------------------------------------------

    if data == "menu_balance":

        await query.answer()

        balance = await get_balance(
            query.from_user.id
        )

        await query.message.reply_text(
            (
                f"🪙 موجودی: "
                f"{format_amount(balance)} {COIN_NAME}"
            )
        )

        return

    # --------------------------------------------------------
    # زیرمجموعه
    # --------------------------------------------------------

    if data == "menu_ref":

        await query.answer()

        me = await context.bot.get_me()

        link = (
            f"https://t.me/{me.username}"
            f"?start=ref_{query.from_user.id}"
        )

        await query.message.reply_text(
            (
                "👥 لینک دعوت شما:\n\n"
                f"{link}\n\n"
                f"🎁 پاداش هر زیرمجموعه: "
                f"{REFERRAL_REWARD} {COIN_NAME}"
            )
        )

        return

    # --------------------------------------------------------
    # درخواست
    # --------------------------------------------------------

    if data == "menu_request":

        await query.answer()

        context.user_data["waiting_request"] = True

        await query.message.reply_text(
            (
                "📤 مقدار درخواست را ارسال کن.\n\n"
                f"حداقل مقدار: "
                f"{format_amount(MIN_REQUEST)} {COIN_NAME}\n\n"
                "مثال:\n"
                "2000"
            )
        )

        return

    # --------------------------------------------------------
    # بازی‌ها
    # --------------------------------------------------------

    if data == "menu_games":

        await query.answer()

        await show_games(
            update,
        )

        return

    # --------------------------------------------------------
    # پنل
    # --------------------------------------------------------

    if data == "admin_panel":

        if not is_owner(query.from_user.id):

            await query.answer(
                "❌ دسترسی ندارید.",
                show_alert=True,
            )

            return

        await query.answer()

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "👥 موجودی کاربران",
                        callback_data="admin_users",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📤 درخواست‌ها",
                        callback_data="admin_requests",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "➕ افزایش موجودی",
                        callback_data="admin_add_help",
                    ),
                    InlineKeyboardButton(
                        "➖ کاهش موجودی",
                        callback_data="admin_sub_help",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "👥 افزایش زیرمجموعه",
                        callback_data="admin_ref_help",
                    )
                ],
            ]
        )

        await query.message.reply_text(
            "⚙️ پنل مدیریت:",
            reply_markup=keyboard,
        )

        return

    if data == "admin_users":

        await admin_users(query)

        return

    if data == "admin_requests":

        await admin_requests(query)

        return

    if data == "admin_add_help":

        await admin_add_help(query)

        return

    if data == "admin_sub_help":

        await admin_sub_help(query)

        return

    if data == "admin_ref_help":

        await admin_ref_help(query)

        return

    # --------------------------------------------------------
    # تأیید درخواست
    # --------------------------------------------------------

    if data.startswith("req_ok_"):

        request_id = int(
            data.split("_")[-1]
        )

        await process_request_callback(
            query,
            context,
            request_id,
            True,
        )

        return

    # --------------------------------------------------------
    # رد درخواست
    # --------------------------------------------------------

    if data.startswith("req_no_"):

        request_id = int(
            data.split("_")[-1]
        )

        await process_request_callback(
            query,
            context,
            request_id,
            False,
        )

        return


# ============================================================
# هندلر اصلی
# ============================================================

async def message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    user = update.effective_user

    if not user:
        return

    await ensure_user(user)

    # گروه
    if is_group(update):

        if is_allowed_group(update):

            await handle_group_text(
                update,
                context,
            )

        return

    # خصوصی
    if update.effective_chat.type == ChatType.PRIVATE:

        # دستورات مدیریت
        if is_owner(user.id):

            text = normalize_digits(
                update.message.text or ""
            )

            if (
                text.startswith("افزایش ")
                or text.startswith("کاهش ")
                or text.startswith("زیرمجموعه ")
            ):

                await handle_admin_text(
                    update,
                    context,
                )

                return

        await handle_private_text(
            update,
            context,
        )


# ============================================================
# خطا
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.error(
        "Unhandled error: %s",
        context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    global TOKEN

    import os

    TOKEN = os.getenv(
        "BOT_TOKEN"
    )

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN در Environment تنظیم نشده است."
        )

    init_db()

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Bot started successfully."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
