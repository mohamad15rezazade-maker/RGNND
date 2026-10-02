# -*- coding: utf-8 -*-

"""
Telegram Betting Bot
Python 3.10+
python-telegram-bot==21.11.1

واحد: داگز
Database: SQLite

فقط این سه مقدار را تغییر بده:
TOKEN
CHANNEL_ID
ALLOWED_GROUP_ID
"""

import asyncio
import html
import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
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

TOKEN = "8702782622:AAGodf6yWKOl9atHwztrFxZPRNJ6ezeXv3o"

CHANNEL_ID = -1004372755284

ALLOWED_GROUP_ID = -1003919206941

OWNER_IDS = [8935601841, 8458210170]

DB_FILE = "bot.db"

MIN_BET = 60
MIN_WITHDRAW = 2000

REFERRAL_REWARD = 60

MAX_ROLLS = 3
ROLL_DELAY = 3.2


# ============================================================
# لاگ
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("DAWGS_BOT")


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


# ============================================================
# نام بازی‌ها
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
# زمان
# ============================================================

def now_str() -> str:
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# ============================================================
# ابزارها
# ============================================================

def escape(text) -> str:
    return html.escape(str(text))


def mention(user) -> str:
    name = user.first_name or user.username or str(user.id)

    return (
        f'<a href="tg://user?id={user.id}">'
        f'{escape(name)}'
        f'</a>'
    )


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


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


def is_group(update: Update) -> bool:
    chat = update.effective_chat

    return bool(
        chat
        and chat.type in (
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        )
    )


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
# جدول جایزه
# ============================================================

def get_multiplier(amount: int) -> Decimal:

    if amount <= 100:
        return Decimal("1.8")

    if amount <= 200:
        return Decimal("1.3")

    if amount <= 500:
        return Decimal("0.96")

    if amount <= 1000:
        return Decimal("0.85")

    if amount <= 2000:
        return Decimal("0.8")

    return Decimal("0.76")


def calculate_reward(amount: int) -> int:

    multiplier = get_multiplier(amount)

    result = (
        Decimal(amount) * multiplier
    ).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )

    return int(result)


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
            CREATE TABLE IF NOT EXISTS withdrawals (
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

        db.commit()


# ============================================================
# کاربر
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
                SELECT referrer_id,
                       referral_claimed
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
                "زیرمجموعه‌گیری",
                callback_data="menu_ref",
            ),
            InlineKeyboardButton(
                "برداشت",
                callback_data="menu_withdraw",
            ),
        ],
        [
            InlineKeyboardButton(
                "موجودی",
                callback_data="menu_balance",
            ),
        ],
    ]

    if is_owner(user_id):

        buttons.append(
            [
                InlineKeyboardButton(
                    "پنل مدیریت",
                    callback_data="admin_panel",
                )
            ]
        )

    return InlineKeyboardMarkup(buttons)


# ============================================================
# /start
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
                            "یک نفر با لینک تو عضو شد!\n"
                            "+60 داگز"
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
            "بازی‌ها را در گپ مجاز انجام بده."
        )

        return

    await update.message.reply_text(
        "به ربات خوش آمدی.\n\n"
        "واحد حساب: داگز",
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
            f"موجودی {escape(user.first_name or user.username or str(user.id))}: "
            f"{format_amount(balance)} داگز"
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
            "لینک دعوت شما:\n\n"
            f"{link}\n\n"
            "به ازای هر زیرمجموعه:\n"
            "+60 داگز\n\n"
            "هر کاربر فقط یک بار برای یک معرف حساب می‌شود."
        )
    )


# ============================================================
# برداشت
# ============================================================

async def withdraw_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.user_data["waiting_withdraw"] = True

    await update.message.reply_text(
        (
            "مبلغ برداشت را ارسال کن.\n\n"
            f"حداقل برداشت: {format_amount(MIN_WITHDRAW)} داگز\n\n"
            "مثال:\n"
            "2000"
        )
    )


async def create_withdrawal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    amount: int,
):

    user = update.effective_user

    await ensure_user(user)

    if amount < MIN_WITHDRAW:

        await update.message.reply_text(
            (
                f"حداقل برداشت "
                f"{format_amount(MIN_WITHDRAW)} داگز است."
            )
        )

        return

    balance = await get_balance(user.id)

    if balance < amount:

        await update.message.reply_text(
            (
                "موجودی کافی نیست.\n\n"
                f"موجودی: {format_amount(balance)} داگز"
            )
        )

        return

    new_balance = await change_balance(
        user.id,
        -amount,
        "withdraw",
        "کسر بابت درخواست برداشت",
    )

    if new_balance is None:

        await update.message.reply_text(
            "خطا در کسر موجودی."
        )

        return

    async with DB_LOCK:

        with closing(db_connect()) as db:

            cursor = db.execute(
                """
                INSERT INTO withdrawals (
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

            withdrawal_id = cursor.lastrowid

            db.commit()

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "تایید",
                    callback_data=f"wd_ok_{withdrawal_id}",
                ),
                InlineKeyboardButton(
                    "رد",
                    callback_data=f"wd_no_{withdrawal_id}",
                ),
            ]
        ]
    )

    username_text = (
        f"@{user.username}"
        if user.username
        else "بدون username"
    )

    channel_text = (
        "درخواست برداشت جدید\n\n"
        f"شناسه درخواست: {withdrawal_id}\n"
        f"کاربر: {mention(user)}\n"
        f"Username: {escape(username_text)}\n"
        f"User ID: {user.id}\n"
        f"مبلغ: {format_amount(amount)} داگز\n"
        f"موجودی بعد از کسر: {format_amount(new_balance)} داگز"
    )

    try:

        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=channel_text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    except Exception as e:

        logger.exception(
            "Cannot send withdrawal to channel"
        )

        # اگر ارسال کانال شکست خورد، پول کاربر برگردد.
        await change_balance(
            user.id,
            amount,
            "withdraw_rollback",
            "بازگشت برداشت به دلیل خطای ارسال",
        )

        async with DB_LOCK:

            with closing(db_connect()) as db:

                db.execute(
                    """
                    UPDATE withdrawals
                    SET status = 'failed'
                    WHERE id = ?
                    """,
                    (withdrawal_id,),
                )

                db.commit()

        await update.message.reply_text(
            "ارسال درخواست برداشت با خطا مواجه شد و مبلغ به موجودی برگشت."
        )

        return

    await update.message.reply_text(
        (
            "درخواست برداشت ثبت شد.\n\n"
            f"مبلغ: {format_amount(amount)} داگز\n"
            f"شماره درخواست: {withdrawal_id}\n"
            "پس از بررسی مالک، درخواست تایید یا رد می‌شود."
        )
    )


# ============================================================
# پردازش پیام‌های خصوصی
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

    # برداشت در انتظار مبلغ
    if context.user_data.get("waiting_withdraw"):

        amount = parse_number(normalized)

        if amount is None:

            await update.message.reply_text(
                "فقط مبلغ عددی ارسال کن."
            )

            return

        context.user_data["waiting_withdraw"] = False

        await create_withdrawal(
            update,
            context,
            amount,
        )

        return

    if normalized in ("م", "موجودی"):

        await send_balance(
            update,
            user.id,
        )

        return

    if text in (
        "زیرمجموعه‌گیری",
        "زیرمجموعه گیری",
        "زیرمجموعه",
    ):

        await referral_message(
            update,
            context,
        )

        return

    if text == "برداشت":

        await withdraw_start(
            update,
            context,
        )

        return

    if text == "پنل مدیریت" and is_owner(user.id):

        await show_admin_panel(
            update,
        )

        return


# ============================================================
# تبدیل مبلغ بازی
# ============================================================

def validate_bet(amount: int) -> Optional[str]:

    if amount < MIN_BET:

        return (
            f"حداقل شرط {format_amount(MIN_BET)} داگز است."
        )

    return None


# ============================================================
# تجزیه بازی
# ============================================================

def parse_game_command(
    text: str,
):

    text = normalize_digits(text).strip()

    # اجازه می‌دهد:
    # 1 تاس 100
    # ۳ تاس ۴۰۰۰
    # 1تاس100
    # 3 بولینگ 777

    compact = re.sub(
        r"\s+",
        " ",
        text,
    )

    match = re.fullmatch(
        r"(\d+)\s*([^\s\d]+)\s*(\d+)",
        compact,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    count_text = match.group(1)
    game_text = match.group(2)
    amount_text = match.group(3)

    count = parse_number(count_text)
    amount = parse_number(amount_text)

    if count is None or amount is None:
        return None

    game_key = GAME_ALIASES.get(
        game_text.lower()
    )

    if not game_key:
        return None

    if count < 1 or count > MAX_ROLLS:
        return {
            "error": (
                f"تعداد پرتاب باید بین 1 تا "
                f"{MAX_ROLLS} باشد."
            )
        }

    if amount < MIN_BET:
        return {
            "error": (
                f"حداقل شرط {format_amount(MIN_BET)} داگز است."
            )
        }

    return {
        "count": count,
        "game": game_key,
        "amount": amount,
    }


# ============================================================
# تجزیه زوج / فرد
# ============================================================

def parse_even_odd(text: str):

    text = normalize_digits(text).strip()

    match = re.fullmatch(
        r"(\d+)\s*(زوج|فرد)",
        text,
    )

    if not match:
        return None

    amount = parse_number(
        match.group(1)
    )

    if amount is None:
        return None

    if amount < MIN_BET:

        return {
            "error": (
                f"حداقل شرط {format_amount(MIN_BET)} داگز است."
            )
        }

    return {
        "amount": amount,
        "choice": match.group(2),
    }


# ============================================================
# ارسال تاس
# ============================================================

async def send_game_roll(
    bot,
    chat_id: int,
    game: str,
):

    emoji = GAME_EMOJIS[game]

    message = await bot.send_dice(
        chat_id=chat_id,
        emoji=emoji,
    )

    value = message.dice.value

    # بسکتبال تلگرام عملاً 1 تا 5 دارد.
    # 4 و 5 گل هستند.
    # اگر مقدار 6 توسط API ارائه شود، آن هم گل حساب می‌شود.
    if game == "basketball":

        score = value if value >= 4 else 0

    else:

        score = value

    return value, score


# ============================================================
# بازی زوج / فرد
# ============================================================

async def run_even_odd_game(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    amount: int,
    choice: str,
):

    user = update.effective_user

    balance = await get_balance(user.id)

    if balance < amount:

        await update.message.reply_text(
            (
                "موجودی کافی نیست.\n"
                f"موجودی شما: {format_amount(balance)} داگز"
            )
        )

        return

    await change_balance(
        user.id,
        -amount,
        "bet",
        f"شرط {choice}",
    )

    await update.message.reply_text(
        (
            f"{mention(user)}\n"
            "بریز 🎲"
        ),
        parse_mode="HTML",
    )

    await asyncio.sleep(0.4)

    dice_message = await context.bot.send_dice(
        chat_id=update.effective_chat.id,
        emoji="🎲",
    )

    await asyncio.sleep(ROLL_DELAY)

    value = dice_message.dice.value

    is_even = value % 2 == 0

    won = (
        choice == "زوج"
        and is_even
    ) or (
        choice == "فرد"
        and not is_even
    )

    if won:

        reward = calculate_reward(amount)

        new_balance = await change_balance(
            user.id,
            reward,
            "win",
            f"برد بازی {choice}",
        )

        await update.message.reply_text(
            (
                f"{mention(user)} برنده شد!\n\n"
                f"نتیجه تاس: {value}\n"
                f"انتخاب: {choice}\n"
                f"جایزه: {format_amount(reward)} داگز\n"
                f"موجودی: {format_amount(new_balance or 0)} داگز"
            ),
            parse_mode="HTML",
        )

    else:

        balance_after = await get_balance(
            user.id
        )

        await update.message.reply_text(
            (
                f"{mention(user)} باخت.\n\n"
                f"نتیجه تاس: {value}\n"
                f"انتخاب: {choice}\n"
                f"موجودی: {format_amount(balance_after)} داگز"
            ),
            parse_mode="HTML",
        )


# ============================================================
# بازی معمولی
# ============================================================

async def create_game(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: dict,
):

    global GAME_COUNTER

    user = update.effective_user

    amount = data["amount"]

    balance = await get_balance(
        user.id
    )

    if balance < amount:

        await update.message.reply_text(
            (
                "موجودی کافی نیست.\n"
                f"موجودی شما: {format_amount(balance)} داگز\n"
                f"شرط: {format_amount(amount)} داگز"
            )
        )

        return

    GAME_COUNTER += 1

    game_id = GAME_COUNTER

    GAMES[game_id] = {
        "id": game_id,
        "creator_id": user.id,
        "creator_name": user.first_name or "",
        "creator_username": user.username,
        "game": data["game"],
        "count": data["count"],
        "amount": amount,
        "chat_id": update.effective_chat.id,
        "status": "waiting",
        "opponent_id": None,
        "opponent_name": None,
    }

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "بازی با ربات",
                    callback_data=f"game_bot_{game_id}",
                ),
                InlineKeyboardButton(
                    "بازی با دوست",
                    callback_data=f"game_friend_{game_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "لغو بازی",
                    callback_data=f"game_cancel_{game_id}",
                )
            ],
        ]
    )

    game_name = GAME_NAMES[data["game"]]

    await update.message.reply_text(
        (
            f"{GAME_EMOJIS[data['game']]} "
            f"بازی {game_name}\n\n"
            f"بازیکن: {mention(user)}\n"
            f"تعداد پرتاب: {data['count']}\n"
            f"شرط: {format_amount(amount)} داگز\n\n"
            "نوع بازی را انتخاب کن:"
        ),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ============================================================
# بازی با ربات
# ============================================================

async def run_bot_game(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    game_id: int,
):

    game_data = GAMES.get(game_id)

    if not game_data:
        await query.answer(
            "این بازی دیگر وجود ندارد.",
            show_alert=True,
        )
        return

    if game_data["status"] != "waiting":

        await query.answer(
            "این بازی قبلاً شروع شده.",
            show_alert=True,
        )

        return

    user = query.from_user

    if user.id != game_data["creator_id"]:

        await query.answer(
            "فقط سازنده بازی می‌تواند این گزینه را بزند.",
            show_alert=True,
        )

        return

    amount = game_data["amount"]

    balance = await get_balance(
        user.id
    )

    if balance < amount:

        del GAMES[game_id]

        await query.answer(
            "موجودی کافی نیست.",
            show_alert=True,
        )

        try:
            await query.edit_message_text(
                "بازی لغو شد چون موجودی کافی نیست."
            )
        except Exception:
            pass

        return

    game_data["status"] = "running"

    await change_balance(
        user.id,
        -amount,
        "bet",
        f"شروع بازی با ربات - {GAME_NAMES[game_data['game']]}",
    )

    await query.answer()

    try:
        await query.edit_message_text(
            (
                f"{GAME_EMOJIS[game_data['game']]} "
                f"بازی شروع شد!\n\n"
                f"{mention(user)}\n"
                "بریز"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

    player_total = 0
    bot_total = 0

    for i in range(game_data["count"]):

        await context.bot.send_message(
            chat_id=game_data["chat_id"],
            text=(
                f"{mention(user)}\n"
                f"پرتاب {i + 1} از {game_data['count']}\n"
                "بریز"
            ),
            parse_mode="HTML",
        )

        await asyncio.sleep(0.3)

        _, score = await send_game_roll(
            context.bot,
            game_data["chat_id"],
            game_data["game"],
        )

        player_total += score

        await asyncio.sleep(ROLL_DELAY)

    await context.bot.send_message(
        chat_id=game_data["chat_id"],
        text="حالا ربات می‌ریزد...",
    )

    for i in range(game_data["count"]):

        await asyncio.sleep(0.5)

        _, score = await send_game_roll(
            context.bot,
            game_data["chat_id"],
            game_data["game"],
        )

        bot_total += score

        await asyncio.sleep(ROLL_DELAY)

    if player_total > bot_total:

        reward = calculate_reward(
            amount
        )

        new_balance = await change_balance(
            user.id,
            reward,
            "win",
            "برد بازی با ربات",
        )

        result = (
            f"{mention(user)} برنده شد!\n\n"
            f"امتیاز شما: {player_total}\n"
            f"امتیاز ربات: {bot_total}\n\n"
            f"جایزه: {format_amount(reward)} داگز\n"
            f"موجودی: {format_amount(new_balance or 0)} داگز"
        )

    elif player_total < bot_total:

        balance_after = await get_balance(
            user.id
        )

        result = (
            f"{mention(user)} باخت.\n\n"
            f"امتیاز شما: {player_total}\n"
            f"امتیاز ربات: {bot_total}\n\n"
            f"موجودی: {format_amount(balance_after)} داگز"
        )

    else:

        await change_balance(
            user.id,
            amount,
            "refund",
            "مساوی بازی با ربات",
        )

        balance_after = await get_balance(
            user.id
        )

        result = (
            f"{mention(user)}\n"
            "بازی مساوی شد.\n\n"
            f"امتیاز شما: {player_total}\n"
            f"امتیاز ربات: {bot_total}\n\n"
            f"شرط برگشت داده شد.\n"
            f"موجودی: {format_amount(balance_after)} داگز"
        )

    await context.bot.send_message(
        chat_id=game_data["chat_id"],
        text=result,
        parse_mode="HTML",
    )

    GAMES.pop(game_id, None)


# ============================================================
# بازی با دوست
# ============================================================

async def start_friend_game(
    query,
    game_id: int,
):

    game_data = GAMES.get(game_id)

    if not game_data:

        await query.answer(
            "بازی پیدا نشد.",
            show_alert=True,
        )

        return

    if game_data["status"] != "waiting":

        await query.answer(
            "این بازی شروع شده.",
            show_alert=True,
        )

        return

    user = query.from_user

    if user.id != game_data["creator_id"]:

        await query.answer(
            "فقط سازنده بازی می‌تواند این گزینه را بزند.",
            show_alert=True,
        )

        return

    game_data["status"] = "friend_waiting"

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "شرکت می‌کنم",
                    callback_data=f"join_{game_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "لغو",
                    callback_data=f"game_cancel_{game_id}",
                )
            ],
        ]
    )

    await query.answer()

    try:

        await query.edit_message_text(
            (
                f"{GAME_EMOJIS[game_data['game']]} "
                "بازی با دوست\n\n"
                f"سازنده: {mention(user)}\n"
                f"تعداد پرتاب: {game_data['count']}\n"
                f"شرط هر بازیکن: "
                f"{format_amount(game_data['amount'])} داگز\n\n"
                "بازیکن دوم روی «شرکت می‌کنم» بزند."
            ),
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    except Exception:
        pass


# ============================================================
# ورود بازیکن دوم
# ============================================================

async def join_friend_game(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    game_id: int,
):

    game_data = GAMES.get(game_id)

    if not game_data:

        await query.answer(
            "بازی پیدا نشد.",
            show_alert=True,
        )

        return

    if game_data["status"] != "friend_waiting":

        await query.answer(
            "این بازی قابل ورود نیست.",
            show_alert=True,
        )

        return

    user = query.from_user

    if user.id == game_data["creator_id"]:

        await query.answer(
            "سازنده نمی‌تواند وارد بازی خودش شود.",
            show_alert=True,
        )

        return

    await ensure_user(user)

    amount = game_data["amount"]

    balance = await get_balance(
        user.id
    )

    if balance < amount:

        await query.answer(
            "موجودی کافی نیست.",
            show_alert=True,
        )

        return

    creator_balance = await get_balance(
        game_data["creator_id"]
    )

    if creator_balance < amount:

        game_data["status"] = "cancelled"

        await query.answer(
            "موجودی سازنده کافی نیست.",
            show_alert=True,
        )

        try:
            await query.edit_message_text(
                "بازی لغو شد؛ موجودی سازنده کافی نیست."
            )
        except Exception:
            pass

        GAMES.pop(game_id, None)

        return

    await change_balance(
        game_data["creator_id"],
        -amount,
        "friend_bet",
        "شرط بازی با دوست",
    )

    await change_balance(
        user.id,
        -amount,
        "friend_bet",
        "شرط بازی با دوست",
    )

    game_data["opponent_id"] = user.id
    game_data["opponent_name"] = (
        user.first_name or user.username or str(user.id)
    )
    game_data["status"] = "running"

    await query.answer()

    try:
        await query.edit_message_text(
            (
                f"{GAME_EMOJIS[game_data['game']]} "
                "بازی شروع شد!\n\n"
                f"بازیکن اول: "
                f"{mention(await get_user_object(context, game_data['creator_id']))}\n"
                f"بازیکن دوم: {mention(user)}"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

    await run_friend_game(
        context,
        game_data,
    )


# ============================================================
# گرفتن اطلاعات کاربر
# ============================================================

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
# اجرای بازی دو نفره
# ============================================================

async def run_friend_game(
    context: ContextTypes.DEFAULT_TYPE,
    game_data: dict,
):

    chat_id = game_data["chat_id"]

    creator = await get_user_object(
        context,
        game_data["creator_id"],
    )

    opponent = await get_user_object(
        context,
        game_data["opponent_id"],
    )

    creator_total = 0
    opponent_total = 0

    # بازیکن اول
    for i in range(game_data["count"]):

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"{mention(creator)}\n"
                f"پرتاب {i + 1} از {game_data['count']}\n"
                "بریز"
            ),
            parse_mode="HTML",
        )

        await asyncio.sleep(0.3)

        _, score = await send_game_roll(
            context.bot,
            chat_id,
            game_data["game"],
        )

        creator_total += score

        await asyncio.sleep(ROLL_DELAY)

    # بازیکن دوم
    for i in range(game_data["count"]):

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"{mention(opponent)}\n"
                f"پرتاب {i + 1} از {game_data['count']}\n"
                "بریز"
            ),
            parse_mode="HTML",
        )

        await asyncio.sleep(0.3)

        _, score = await send_game_roll(
            context.bot,
            chat_id,
            game_data["game"],
        )

        opponent_total += score

        await asyncio.sleep(ROLL_DELAY)

    amount = game_data["amount"]

    # برنده
    if creator_total > opponent_total:

        reward = amount * 2

        new_balance = await change_balance(
            game_data["creator_id"],
            reward,
            "friend_win",
            "برد بازی با دوست",
        )

        result = (
            f"{mention(creator)} برنده شد!\n\n"
            f"امتیاز {mention(creator)}: {creator_total}\n"
            f"امتیاز {mention(opponent)}: {opponent_total}\n\n"
            f"جایزه: {format_amount(reward)} داگز\n"
            f"موجودی برنده: {format_amount(new_balance or 0)} داگز"
        )

    elif opponent_total > creator_total:

        reward = amount * 2

        new_balance = await change_balance(
            game_data["opponent_id"],
            reward,
            "friend_win",
            "برد بازی با دوست",
        )

        result = (
            f"{mention(opponent)} برنده شد!\n\n"
            f"امتیاز {mention(creator)}: {creator_total}\n"
            f"امتیاز {mention(opponent)}: {opponent_total}\n\n"
            f"جایزه: {format_amount(reward)} داگز\n"
            f"موجودی برنده: {format_amount(new_balance or 0)} داگز"
        )

    else:

        await change_balance(
            game_data["creator_id"],
            amount,
            "refund",
            "بازگشت شرط بازی مساوی",
        )

        await change_balance(
            game_data["opponent_id"],
            amount,
            "refund",
            "بازگشت شرط بازی مساوی",
        )

        result = (
            "بازی مساوی شد.\n\n"
            f"امتیاز {mention(creator)}: {creator_total}\n"
            f"امتیاز {mention(opponent)}: {opponent_total}\n\n"
            "شرط هر دو بازیکن برگشت داده شد."
        )

    await context.bot.send_message(
        chat_id=chat_id,
        text=result,
        parse_mode="HTML",
    )

    GAMES.pop(
        game_data["id"],
        None,
    )


# ============================================================
# لغو بازی
# ============================================================

async def cancel_game(
    query,
    game_id: int,
):

    game_data = GAMES.get(game_id)

    if not game_data:

        await query.answer(
            "بازی پیدا نشد.",
            show_alert=True,
        )

        return

    if query.from_user.id != game_data["creator_id"]:

        await query.answer(
            "فقط سازنده می‌تواند بازی را لغو کند.",
            show_alert=True,
        )

        return

    if game_data["status"] not in (
        "waiting",
        "friend_waiting",
    ):

        await query.answer(
            "بازی شروع شده و قابل لغو نیست.",
            show_alert=True,
        )

        return

    GAMES.pop(
        game_id,
        None,
    )

    await query.answer(
        "بازی لغو شد."
    )

    try:
        await query.edit_message_text(
            "بازی لغو شد."
        )
    except Exception:
        pass


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

    text = update.message.text.strip()

    normalized = normalize_digits(text)

    # موجودی
    if normalized in ("م", "موجودی"):

        balance = await get_balance(
            user.id
        )

        await update.message.reply_text(
            (
                f"موجودی {escape(user.first_name or user.username or str(user.id))}: "
                f"{format_amount(balance)} داگز"
            ),
            parse_mode="HTML",
        )

        return

    # زوج / فرد
    even_odd = parse_even_odd(
        normalized
    )

    if even_odd:

        if "error" in even_odd:

            await update.message.reply_text(
                even_odd["error"]
            )

            return

        await run_even_odd_game(
            update,
            context,
            even_odd["amount"],
            even_odd["choice"],
        )

        return

    # بازی‌های تاس، بولینگ، بسکتبال، دارت
    game = parse_game_command(
        normalized
    )

    if game:

        if "error" in game:

            await update.message.reply_text(
                game["error"]
            )

            return

        await create_game(
            update,
            context,
            game,
        )

        return

    # انتقال
    transfer = parse_transfer_command(
        normalized
    )

    if transfer:

        await handle_transfer(
            update,
            context,
            transfer,
        )

        return


# ============================================================
# تجزیه انتقال
# ============================================================

def parse_transfer_command(
    text: str,
):

    text = normalize_digits(text).strip()

    # انتقال 500 @username
    # واریز 500 @username

    match = re.fullmatch(
        r"(انتقال|واریز)\s+(\d+)\s+@([A-Za-z0-9_]{5,32})",
        text,
        flags=re.IGNORECASE,
    )

    if match:

        amount = parse_number(
            match.group(2)
        )

        return {
            "amount": amount,
            "username": match.group(3),
        }

    # انتقال 500 در ریپلای
    match = re.fullmatch(
        r"(انتقال|واریز)\s+(\d+)",
        text,
        flags=re.IGNORECASE,
    )

    if match:

        amount = parse_number(
            match.group(2)
        )

        return {
            "amount": amount,
            "username": None,
        }

    return None


# ============================================================
# انتقال
# ============================================================

async def handle_transfer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: dict,
):

    sender = update.effective_user

    amount = data["amount"]

    if not amount or amount <= 0:

        await update.message.reply_text(
            "مبلغ انتقال نامعتبر است."
        )

        return

    if amount < MIN_BET:

        await update.message.reply_text(
            f"حداقل مبلغ انتقال {MIN_BET} داگز است."
        )

        return

    target_id = None
    target_user = None

    # انتقال با ریپلای
    if update.message.reply_to_message:

        target_user = (
            update.message.reply_to_message.from_user
        )

        if target_user:

            target_id = target_user.id

    # انتقال با username
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

                    target_id = int(
                        row["user_id"]
                    )

                    class Target:
                        pass

                    target_user = Target()
                    target_user.id = target_id
                    target_user.username = row["username"]
                    target_user.first_name = row["first_name"]

    if not target_id:

        await update.message.reply_text(
            (
                "کاربر پیدا نشد.\n\n"
                "برای انتقال می‌توانی روی پیام کاربر ریپلای کنی:\n"
                "انتقال 500\n\n"
                "یا:\n"
                "انتقال 500 @username"
            )
        )

        return

    if target_id == sender.id:

        await update.message.reply_text(
            "نمی‌توانی به خودت انتقال بدهی."
        )

        return

    await ensure_user(
        target_user
    )

    sender_balance = await get_balance(
        sender.id
    )

    if sender_balance < amount:

        await update.message.reply_text(
            (
                "موجودی کافی نیست.\n"
                f"موجودی شما: {format_amount(sender_balance)} داگز"
            )
        )

        return

    # کسر از فرستنده
    sender_new_balance = await change_balance(
        sender.id,
        -amount,
        "transfer_out",
        f"انتقال به {target_id}",
    )

    if sender_new_balance is None:

        await update.message.reply_text(
            "انتقال انجام نشد."
        )

        return

    # اضافه به گیرنده
    receiver_new_balance = await change_balance(
        target_id,
        amount,
        "transfer_in",
        f"دریافت از {sender.id}",
    )

    if receiver_new_balance is None:

        await change_balance(
            sender.id,
            amount,
            "transfer_rollback",
            "بازگشت انتقال ناموفق",
        )

        await update.message.reply_text(
            "انتقال انجام نشد و مبلغ برگشت داده شد."
        )

        return

    await update.message.reply_text(
        (
            f"انتقال انجام شد.\n\n"
            f"فرستنده: {mention(sender)}\n"
            f"گیرنده: {mention(target_user)}\n"
            f"مبلغ: {format_amount(amount)} داگز\n"
            f"موجودی شما: {format_amount(sender_new_balance)} داگز"
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
                    "موجودی کاربران",
                    callback_data="admin_users",
                )
            ],
            [
                InlineKeyboardButton(
                    "درخواست‌های برداشت",
                    callback_data="admin_withdrawals",
                )
            ],
            [
                InlineKeyboardButton(
                    "افزایش موجودی",
                    callback_data="admin_add_help",
                ),
                InlineKeyboardButton(
                    "کاهش موجودی",
                    callback_data="admin_sub_help",
                ),
            ],
            [
                InlineKeyboardButton(
                    "افزایش زیرمجموعه",
                    callback_data="admin_ref_help",
                )
            ],
        ]
    )

    await update.message.reply_text(
        "پنل مدیریت",
        reply_markup=keyboard,
    )


# ============================================================
# لیست کاربران
# ============================================================

async def admin_users(
    query,
):

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

        text = "کاربری ثبت نشده است."

    else:

        lines = [
            "موجودی کاربران:",
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
                f"{index}_"
                f"{username} - "
                f"{format_amount(int(row['balance']))} داگز"
                f" | ID: {row['user_id']}"
            )

        text = "\n".join(lines)

    await query.answer()

    await query.edit_message_text(
        text[:4096]
    )


# ============================================================
# راهنمای افزایش/کاهش
# ============================================================

async def admin_add_help(
    query,
):

    if not is_owner(query.from_user.id):
        await query.answer(
            "دسترسی ندارید.",
            show_alert=True,
        )
        return

    await query.answer()

    await query.message.reply_text(
        (
            "برای افزایش موجودی:\n\n"
            "افزایش user_id amount\n\n"
            "مثال:\n"
            "افزایش 123456789 500"
        )
    )


async def admin_sub_help(
    query,
):

    if not is_owner(query.from_user.id):
        await query.answer(
            "دسترسی ندارید.",
            show_alert=True,
        )
        return

    await query.answer()

    await query.message.reply_text(
        (
            "برای کاهش موجودی:\n\n"
            "کاهش user_id amount\n\n"
            "مثال:\n"
            "کاهش 123456789 500"
        )
    )


async def admin_ref_help(
    query,
):

    if not is_owner(query.from_user.id):
        await query.answer(
            "دسترسی ندارید.",
            show_alert=True,
        )
        return

    await query.answer()

    await query.message.reply_text(
        (
            "افزایش زیرمجموعه:\n\n"
            "زیرمجموعه user_id count\n\n"
            "مثال:\n"
            "زیرمجموعه 123456789 5\n\n"
            "برای هر مورد 60 داگز اضافه می‌شود."
        )
    )


# ============================================================
# برداشت‌های مدیریت
# ============================================================

async def admin_withdrawals(
    query,
):

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
                FROM withdrawals
                WHERE status = 'pending'
                ORDER BY id ASC
                LIMIT 30
                """
            ).fetchall()

    await query.answer()

    if not rows:

        await query.message.reply_text(
            "درخواست برداشت در انتظار وجود ندارد."
        )

        return

    for row in rows:

        username = (
            f"@{row['username']}"
            if row["username"]
            else "بدون username"
        )

        text = (
            f"درخواست برداشت #{row['id']}\n\n"
            f"User ID: {row['user_id']}\n"
            f"Username: {username}\n"
            f"مبلغ: {format_amount(row['amount'])} داگز\n"
            f"زمان: {row['created_at']}"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "تایید",
                        callback_data=f"wd_ok_{row['id']}",
                    ),
                    InlineKeyboardButton(
                        "رد",
                        callback_data=f"wd_no_{row['id']}",
                    ),
                ]
            ]
        )

        await query.message.reply_text(
            text,
            reply_markup=keyboard,
        )


# ============================================================
# تایید / رد برداشت
# ============================================================

async def process_withdrawal_callback(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    withdrawal_id: int,
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
                FROM withdrawals
                WHERE id = ?
                """,
                (withdrawal_id,),
            ).fetchone()

            if not row:

                await query.answer(
                    "درخواست پیدا نشد.",
                    show_alert=True,
                )

                return

            if row["status"] != "pending":

                await query.answer(
                    "این درخواست قبلاً پردازش شده.",
                    show_alert=True,
                )

                return

            if approve:

                db.execute(
                    """
                    UPDATE withdrawals
                    SET status = 'approved',
                        processed_at = ?,
                        processed_by = ?
                    WHERE id = ?
                    """,
                    (
                        now_str(),
                        query.from_user.id,
                        withdrawal_id,
                    ),
                )

            else:

                # در صورت رد، مبلغ به کاربر برگردانده می‌شود.
                user_row = db.execute(
                    """
                    SELECT balance
                    FROM users
                    WHERE user_id = ?
                    """,
                    (row["user_id"],),
                ).fetchone()

                if user_row:

                    old_balance = int(
                        user_row["balance"]
                    )

                    new_balance = (
                        old_balance
                        + int(row["amount"])
                    )

                    db.execute(
                        """
                        UPDATE users
                        SET balance = ?
                        WHERE user_id = ?
                        """,
                        (
                            new_balance,
                            row["user_id"],
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
                            row["user_id"],
                            "withdraw_rejected",
                            row["amount"],
                            new_balance,
                            "بازگشت مبلغ برداشت رد شده",
                            now_str(),
                        ),
                    )

                db.execute(
                    """
                    UPDATE withdrawals
                    SET status = 'rejected',
                        processed_at = ?,
                        processed_by = ?
                    WHERE id = ?
                    """,
                    (
                        now_str(),
                        query.from_user.id,
                        withdrawal_id,
                    ),
                )

            db.commit()

    await query.answer(
        "انجام شد."
    )

    try:

        if approve:

            await query.edit_message_text(
                (
                    f"درخواست برداشت #{withdrawal_id}\n"
                    "تایید شد."
                )
            )

        else:

            await query.edit_message_text(
                (
                    f"درخواست برداشت #{withdrawal_id}\n"
                    "رد شد و مبلغ به موجودی کاربر برگشت."
                )
            )

    except Exception:
        pass

    try:

        if approve:

            await context.bot.send_message(
                chat_id=row["user_id"],
                text=(
                    "درخواست برداشت شما تایید شد.\n\n"
                    f"مبلغ: {format_amount(row['amount'])} داگز"
                ),
            )

        else:

            await context.bot.send_message(
                chat_id=row["user_id"],
                text=(
                    "درخواست برداشت شما رد شد.\n\n"
                    f"مبلغ {format_amount(row['amount'])} داگز "
                    "به موجودی شما برگشت."
                ),
            )

    except Exception:
        pass


# ============================================================
# دستورات مدیریت متنی
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

    # افزایش موجودی
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
                "کاربر پیدا نشد."
            )

        else:

            await update.message.reply_text(
                (
                    "موجودی افزایش یافت.\n\n"
                    f"کاربر: {user_id}\n"
                    f"مبلغ: {format_amount(amount)} داگز\n"
                    f"موجودی جدید: {format_amount(new_balance)} داگز"
                )
            )

        return

    # کاهش موجودی
    match = re.fullmatch(
        r"کاهش\s+(\d+)\s+(\d+)",
        text,
    )

    if match:

        user_id = int(match.group(1))
        amount = int(match.group(2))

        balance = await get_balance(
            user_id
        )

        if balance < amount:

            await update.message.reply_text(
                "موجودی کاربر کافی نیست."
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
                "موجودی کاهش یافت.\n\n"
                f"کاربر: {user_id}\n"
                f"مبلغ: {format_amount(amount)} داگز\n"
                f"موجودی جدید: {format_amount(new_balance or 0)} داگز"
            )
        )

        return

    # افزایش زیرمجموعه
    match = re.fullmatch(
        r"زیرمجموعه\s+(\d+)\s+(\d+)",
        text,
    )

    if match:

        user_id = int(match.group(1))
        count = int(match.group(2))

        if count <= 0:

            await update.message.reply_text(
                "تعداد نامعتبر است."
            )

            return

        reward = (
            count * REFERRAL_REWARD
        )

        balance = await get_balance(
            user_id
        )

        if balance == 0:

            target = await get_user_object(
                context,
                user_id,
            )

            await ensure_user(target)

        new_balance = await change_balance(
            user_id,
            reward,
            "admin_referral_add",
            f"افزایش {count} زیرمجموعه توسط مالک",
        )

        await update.message.reply_text(
            (
                "زیرمجموعه اضافه شد.\n\n"
                f"کاربر: {user_id}\n"
                f"تعداد: {count}\n"
                f"پاداش: {format_amount(reward)} داگز\n"
                f"موجودی جدید: {format_amount(new_balance or 0)} داگز"
            )
        )

        return


# ============================================================
# Callbackها
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    data = query.data or ""

    # -----------------------------
    # موجودی
    # -----------------------------

    if data == "menu_balance":

        await query.answer()

        balance = await get_balance(
            query.from_user.id
        )

        await query.message.reply_text(
            (
                f"موجودی: "
                f"{format_amount(balance)} داگز"
            )
        )

        return

    # -----------------------------
    # زیرمجموعه
    # -----------------------------

    if data == "menu_ref":

        await query.answer()

        me = await context.bot.get_me()

        link = (
            f"https://t.me/{me.username}"
            f"?start=ref_{query.from_user.id}"
        )

        await query.message.reply_text(
            (
                "لینک دعوت شما:\n\n"
                f"{link}\n\n"
                "پاداش هر زیرمجموعه: 60 داگز"
            )
        )

        return

    # -----------------------------
    # برداشت
    # -----------------------------

    if data == "menu_withdraw":

        await query.answer()

        context.user_data["waiting_withdraw"] = True

        await query.message.reply_text(
            (
                "مبلغ برداشت را ارسال کن.\n\n"
                f"حداقل برداشت: {format_amount(MIN_WITHDRAW)} داگز"
            )
        )

        return

    # -----------------------------
    # پنل مدیریت
    # -----------------------------

    if data == "admin_panel":

        if not is_owner(query.from_user.id):

            await query.answer(
                "دسترسی ندارید.",
                show_alert=True,
            )

            return

        await query.answer()

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "موجودی کاربران",
                        callback_data="admin_users",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "درخواست‌های برداشت",
                        callback_data="admin_withdrawals",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "افزایش موجودی",
                        callback_data="admin_add_help",
                    ),
                    InlineKeyboardButton(
                        "کاهش موجودی",
                        callback_data="admin_sub_help",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "افزایش زیرمجموعه",
                        callback_data="admin_ref_help",
                    )
                ],
            ]
        )

        await query.message.reply_text(
            "پنل مدیریت:",
            reply_markup=keyboard,
        )

        return

    if data == "admin_users":

        await admin_users(query)

        return

    if data == "admin_withdrawals":

        await admin_withdrawals(query)

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

    # -----------------------------
    # بازی با ربات
    # -----------------------------

    if data.startswith("game_bot_"):

        game_id = int(
            data.split("_")[-1]
        )

        await run_bot_game(
            query,
            context,
            game_id,
        )

        return

    # -----------------------------
    # بازی با دوست
    # -----------------------------

    if data.startswith("game_friend_"):

        game_id = int(
            data.split("_")[-1]
        )

        await start_friend_game(
            query,
            game_id,
        )

        return

    # -----------------------------
    # شرکت در بازی
    # -----------------------------

    if data.startswith("join_"):

        game_id = int(
            data.split("_")[-1]
        )

        await join_friend_game(
            query,
            context,
            game_id,
        )

        return

    # -----------------------------
    # لغو بازی
    # -----------------------------

    if data.startswith("game_cancel_"):

        game_id = int(
            data.split("_")[-1]
        )

        await cancel_game(
            query,
            game_id,
        )

        return

    # -----------------------------
    # تایید برداشت
    # -----------------------------

    if data.startswith("wd_ok_"):

        withdrawal_id = int(
            data.split("_")[-1]
        )

        await process_withdrawal_callback(
            query,
            context,
            withdrawal_id,
            True,
        )

        return

    # -----------------------------
    # رد برداشت
    # -----------------------------

    if data.startswith("wd_no_"):

        withdrawal_id = int(
            data.split("_")[-1]
        )

        await process_withdrawal_callback(
            query,
            context,
            withdrawal_id,
            False,
        )

        return


# ============================================================
# هندلر اصلی پیام
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

    # -----------------------------
    # گروه
    # -----------------------------

    if is_group(update):

        if is_allowed_group(update):

            await handle_group_text(
                update,
                context,
            )

        return

    # -----------------------------
    # خصوصی
    # -----------------------------

    if update.effective_chat.type == ChatType.PRIVATE:

        # اول دستورات مالک
        if is_owner(user.id):

            before = (
                update.message.text or ""
            )

            if (
                normalize_digits(before).startswith("افزایش ")
                or normalize_digits(before).startswith("کاهش ")
                or normalize_digits(before).startswith("زیرمجموعه ")
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

    logger.exception(
        "Unhandled error",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":

        raise RuntimeError(
            "TOKEN را در بالای فایل وارد کنید."
        )

    init_db()

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    # /start
    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    # callback buttons
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # متن‌ها
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
        "Bot started."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
