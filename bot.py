# -*- coding: utf-8 -*-

"""
Telegram Coin Game Bot
Python 3.10+

Install:
    pip install python-telegram-bot==21.11.1

BOT_TOKEN باید در Environment تنظیم شود.
"""

import asyncio
import html
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

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
# CONFIG
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

MIN_GAME_SCORE = 60
MAX_GAME_ROLLS = 3


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("COIN_GAME_BOT")


# ============================================================
# GAME SETTINGS
# ============================================================

GAME_ALIASES = {
    "تاس": "dice",
    "dice": "dice",

    "بولینگ": "bowling",
    "بولينگ": "bowling",
    "bowling": "bowling",

    "دارت": "darts",
    "دارتس": "darts",
    "darts": "darts",

    "بسکتبال": "basketball",
    "بسکتبال": "basketball",
    "basketball": "basketball",
}

GAME_NAMES = {
    "dice": "تاس",
    "bowling": "بولینگ",
    "darts": "دارت",
    "basketball": "بسکتبال",
}

GAME_EMOJIS = {
    "dice": "🎲",
    "bowling": "🎳",
    "darts": "🎯",
    "basketball": "🏀",
}


# بازی‌های فعال در حافظه
GAMES = {}
GAME_COUNTER = 0


# ============================================================
# HELPERS
# ============================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def escape(value):
    return html.escape(str(value or ""))


def format_amount(value):
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def normalize_digits(text):
    if not text:
        return text

    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789",
    )

    return text.translate(table)


def mention(user):
    if not user:
        return ""

    name = escape(
        user.first_name
        or user.username
        or str(user.id)
    )

    return f'<a href="tg://user?id={user.id}">{name}</a>'


def db():
    return sqlite3.connect(
        DB_FILE,
        timeout=30,
    )


# ============================================================
# DATABASE
# ============================================================

def init_db():

    with closing(db()) as conn:

        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 0,
                referrer_id INTEGER,
                referral_claimed INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT,
                amount INTEGER,
                balance_after INTEGER,
                description TEXT,
                created_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER UNIQUE,
                reward INTEGER,
                created_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                amount INTEGER,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                processed_at TEXT,
                processed_by INTEGER
            )
        """)

        conn.commit()


def ensure_user(user):

    if not user:
        return

    with closing(db()) as conn:

        cur = conn.cursor()

        cur.execute(
            "SELECT user_id FROM users WHERE user_id=?",
            (user.id,),
        )

        row = cur.fetchone()

        if row:
            cur.execute("""
                UPDATE users
                SET username=?, first_name=?
                WHERE user_id=?
            """, (
                user.username,
                user.first_name,
                user.id,
            ))

        else:
            cur.execute("""
                INSERT INTO users
                (
                    user_id,
                    username,
                    first_name,
                    balance,
                    created_at
                )
                VALUES (?, ?, ?, 0, ?)
            """, (
                user.id,
                user.username,
                user.first_name,
                now(),
            ))

        conn.commit()


def get_balance(user_id):

    with closing(db()) as conn:

        cur = conn.cursor()

        cur.execute(
            "SELECT balance FROM users WHERE user_id=?",
            (user_id,),
        )

        row = cur.fetchone()

        return int(row[0]) if row else 0


def change_balance(
    user_id,
    amount,
    transaction_type="manual",
    description="",
):

    with closing(db()) as conn:

        cur = conn.cursor()

        cur.execute(
            "SELECT balance FROM users WHERE user_id=?",
            (user_id,),
        )

        row = cur.fetchone()

        if not row:
            return False

        old_balance = int(row[0])
        new_balance = old_balance + int(amount)

        if new_balance < 0:
            return False

        cur.execute("""
            UPDATE users
            SET balance=?
            WHERE user_id=?
        """, (
            new_balance,
            user_id,
        ))

        cur.execute("""
            INSERT INTO transactions
            (
                user_id,
                type,
                amount,
                balance_after,
                description,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            transaction_type,
            amount,
            new_balance,
            description,
            now(),
        ))

        conn.commit()

        return True


def get_user_object(user_id):

    with closing(db()) as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                user_id,
                username,
                first_name,
                balance,
                referrer_id,
                referral_claimed,
                created_at
            FROM users
            WHERE user_id=?
        """, (
            user_id,
        ))

        row = cur.fetchone()

        if not row:
            return None

        return {
            "user_id": row[0],
            "username": row[1],
            "first_name": row[2],
            "balance": row[3],
            "referrer_id": row[4],
            "referral_claimed": row[5],
            "created_at": row[6],
        }


# ============================================================
# REFERRAL
# ============================================================

def process_referral(
    user_id,
    referrer_id,
):

    if not referrer_id:
        return False

    if user_id == referrer_id:
        return False

    user = get_user_object(user_id)

    if not user:
        return False

    if user["referrer_id"]:
        return False

    referrer = get_user_object(referrer_id)

    if not referrer:
        return False

    with closing(db()) as conn:

        cur = conn.cursor()

        cur.execute("""
            UPDATE users
            SET referrer_id=?
            WHERE user_id=?
        """, (
            referrer_id,
            user_id,
        ))

        cur.execute("""
            INSERT OR IGNORE INTO referrals
            (
                referrer_id,
                referred_id,
                reward,
                created_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            referrer_id,
            user_id,
            REFERRAL_REWARD,
            now(),
        ))

        conn.commit()

    change_balance(
        referrer_id,
        REFERRAL_REWARD,
        "referral",
        "پاداش زیرمجموعه",
    )

    return True


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

    ensure_user(user)

    if context.args:

        arg = context.args[0]

        if arg.startswith("ref_"):

            try:
                referrer_id = int(
                    arg.replace("ref_", "")
                )

                process_referral(
                    user.id,
                    referrer_id,
                )

            except Exception:
                pass

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💰 موجودی",
                callback_data="menu_balance",
            ),
            InlineKeyboardButton(
                "🎮 بازی‌ها",
                callback_data="menu_games",
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 زیرمجموعه",
                callback_data="menu_referral",
            ),
            InlineKeyboardButton(
                "📝 درخواست",
                callback_data="menu_request",
            ),
        ],
        [
            InlineKeyboardButton(
                "ℹ️ راهنما",
                callback_data="menu_help",
            ),
        ],
    ])

    if user.id in OWNER_IDS:

        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                "⚙️ پنل مدیریت",
                callback_data="admin_panel",
            )
        ])

    await update.message.reply_text(
        (
            "سلام 👋\n\n"
            f"خوش اومدی {mention(user)}\n\n"
            "🪙 سیستم سکه\n"
            "🎮 بازی‌های امتیازی\n\n"
            "از منوی زیر استفاده کن."
        ),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ============================================================
# BALANCE
# ============================================================

async def show_balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    ensure_user(user)

    balance = get_balance(user.id)

    text = (
        "💰 موجودی شما\n\n"
        f"🪙 {format_amount(balance)} {COIN_NAME}"
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


# ============================================================
# REFERRAL
# ============================================================

async def show_referral(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    ensure_user(user)

    bot = await context.bot.get_me()

    link = (
        f"https://t.me/{bot.username}"
        f"?start=ref_{user.id}"
    )

    with closing(db()) as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*)
            FROM referrals
            WHERE referrer_id=?
        """, (
            user.id,
        ))

        count = cur.fetchone()[0]

    text = (
        "👥 زیرمجموعه\n\n"
        f"تعداد: {count}\n"
        f"پاداش هر نفر: {REFERRAL_REWARD} {COIN_NAME}\n\n"
        "لینک دعوت:\n"
        f"{link}"
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


# ============================================================
# REQUEST
# ============================================================

async def create_request(
    update: Update,
    amount,
):

    user = update.effective_user

    ensure_user(user)

    amount = int(amount)

    if amount < MIN_REQUEST:

        await update.message.reply_text(
            f"❌ حداقل مقدار درخواست {MIN_REQUEST:,} {COIN_NAME} است."
        )

        return

    balance = get_balance(user.id)

    if balance < amount:

        await update.message.reply_text(
            "❌ موجودی کافی نیست."
        )

        return

    with closing(db()) as conn:

        cur = conn.cursor()

        cur.execute("""
            INSERT INTO requests
            (
                user_id,
                username,
                amount,
                status,
                created_at
            )
            VALUES (?, ?, ?, 'pending', ?)
        """, (
            user.id,
            user.username or "",
            amount,
            now(),
        ))

        request_id = cur.lastrowid

        conn.commit()

    text = (
        "📝 درخواست جدید\n\n"
        f"🆔 درخواست: {request_id}\n"
        f"👤 کاربر: {mention(user)}\n"
        f"ID: <code>{user.id}</code>\n"
        f"💰 مقدار: {format_amount(amount)} {COIN_NAME}\n\n"
        "⚠️ این درخواست فقط برای ثبت داخلی است."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ تأیید",
                callback_data=f"request_approve_{request_id}",
            ),
            InlineKeyboardButton(
                "❌ رد",
                callback_data=f"request_reject_{request_id}",
            ),
        ]
    ])

    try:

        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    except Exception as e:

        logger.error(
            "Could not send request: %s",
            e,
        )

    await update.message.reply_text(
        "✅ درخواست شما ثبت شد."
    )


# ============================================================
# GAMES
# ============================================================

def parse_game_command(text):

    if not text:
        return None

    text = normalize_digits(text).strip()

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    match = re.fullmatch(
        r"(\d+)\s+([^\s]+)\s+(\d+)",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    rolls = int(
        match.group(1)
    )

    game_name = match.group(2).lower()

    amount = int(
        match.group(3)
    )

    if game_name not in GAME_ALIASES:

        return {
            "error": "game"
        }

    if rolls < 1 or rolls > MAX_GAME_ROLLS:

        return {
            "error": "rolls"
        }

    if amount < MIN_GAME_SCORE:

        return {
            "error": "amount"
        }

    return {
        "rolls": rolls,
        "game": GAME_ALIASES[game_name],
        "amount": amount,
    }


def get_game_result(
    game,
    value,
):

    if game == "dice":
        return value

    if game == "bowling":
        return value

    if game == "darts":
        return value

    if game == "basketball":
        return 1 if value >= 4 else 0

    return value


async def send_game_roll(
    bot,
    chat_id,
    game,
):

    message = await bot.send_dice(
        chat_id=chat_id,
        emoji=GAME_EMOJIS[game],
    )

    value = message.dice.value

    score = get_game_result(
        game,
        value,
    )

    return value, score


async def creator_turn(
    context,
    chat_id,
    game_data,
):

    game = game_data["game"]
    rolls = game_data["rolls"]

    scores = []

    for _ in range(rolls):

        await asyncio.sleep(0.5)

        value, score = await send_game_roll(
            context.bot,
            chat_id,
            game,
        )

        scores.append(score)

        await asyncio.sleep(
            ROLL_DELAY
        )

    game_data["creator_scores"] = scores

    return scores


async def opponent_turn(
    context,
    chat_id,
    game_data,
):

    game = game_data["game"]
    rolls = game_data["rolls"]

    scores = []

    for _ in range(rolls):

        await asyncio.sleep(0.5)

        value, score = await send_game_roll(
            context.bot,
            chat_id,
            game,
        )

        scores.append(score)

        await asyncio.sleep(
            ROLL_DELAY
        )

    game_data["opponent_scores"] = scores

    return scores


async def bot_turn(
    context,
    chat_id,
    game_data,
):

    game = game_data["game"]
    rolls = game_data["rolls"]

    scores = []

    for _ in range(rolls):

        await asyncio.sleep(0.5)

        value, score = await send_game_roll(
            context.bot,
            chat_id,
            game,
        )

        scores.append(score)

        await asyncio.sleep(
            ROLL_DELAY
        )

    game_data["bot_scores"] = scores

    return scores


async def create_game(
    update,
    context,
    data,
):

    global GAME_COUNTER

    user = update.effective_user

    GAME_COUNTER += 1

    game_id = GAME_COUNTER

    GAMES[game_id] = {
        "id": game_id,
        "creator_id": user.id,
        "creator_name": (
            user.first_name
            or user.username
            or str(user.id)
        ),
        "game": data["game"],
        "rolls": data["rolls"],
        "amount": data["amount"],
        "status": "waiting",
        "creator_scores": [],
        "opponent_id": None,
        "opponent_name": None,
        "opponent_scores": [],
        "bot_scores": [],
    }

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🤖 بازی با ربات",
                callback_data=f"game_bot_{game_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "👥 بازی با دوستان",
                callback_data=f"game_friend_{game_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ لغو بازی",
                callback_data=f"game_cancel_{game_id}",
            )
        ],
    ])

    await update.message.reply_text(
        (
            "🎮 بازی ساخته شد!\n\n"
            f"{GAME_EMOJIS[data['game']]} "
            f"{GAME_NAMES[data['game']]}\n"
            f"👤 سازنده: {mention(user)}\n"
            f"🔢 تعداد پرتاب: {data['rolls']}\n"
            f"🪙 امتیاز بازی: "
            f"{format_amount(data['amount'])} {COIN_NAME}\n\n"
            "انتخاب کن:"
        ),
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def run_bot_game(
    query,
    context,
    game_data,
):

    if game_data["status"] != "waiting":

        await query.answer(
            "این بازی قبلاً شروع شده.",
            show_alert=True,
        )

        return

    if query.from_user.id != game_data["creator_id"]:

        await query.answer(
            "فقط سازنده بازی می‌تواند این گزینه را بزند.",
            show_alert=True,
        )

        return

    await query.answer()

    game_data["status"] = "creator_turn"

    try:

        await query.edit_message_text(
            (
                "🤖 بازی شروع شد!\n\n"
                f"{GAME_EMOJIS[game_data['game']]} "
                f"{GAME_NAMES[game_data['game']]}\n\n"
                "👤 ابتدا سازنده بازی می‌کند..."
            )
        )

    except Exception:
        pass

    creator_scores = await creator_turn(
        context,
        query.message.chat.id,
        game_data,
    )

    creator_total = sum(
        creator_scores
    )

    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text=(
            "👤 نوبت سازنده تمام شد.\n"
            f"📊 امتیاز سازنده: {creator_total}\n\n"
            "🤖 حالا نوبت ربات است..."
        ),
    )

    game_data["status"] = "bot_turn"

    bot_scores = await bot_turn(
        context,
        query.message.chat.id,
        game_data,
    )

    bot_total = sum(
        bot_scores
    )

    if creator_total > bot_total:

        result = "🏆 سازنده امتیاز بیشتری گرفت."

    elif creator_total < bot_total:

        result = "🤖 ربات امتیاز بیشتری گرفت."

    else:

        result = "🤝 نتیجه مساوی شد."

    game_data["status"] = "finished"

    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text=(
            "🎮 بازی تمام شد!\n\n"
            f"👤 سازنده: {creator_total}\n"
            f"🤖 ربات: {bot_total}\n\n"
            f"{result}\n\n"
            f"🪙 امتیاز بازی: "
            f"{format_amount(game_data['amount'])} {COIN_NAME}"
        ),
    )

    GAMES.pop(
        game_data["id"],
        None,
    )


async def start_friend_game(
    query,
    context,
    game_data,
):

    if game_data["status"] != "waiting":

        await query.answer(
            "این بازی قبلاً شروع شده.",
            show_alert=True,
        )

        return

    if query.from_user.id != game_data["creator_id"]:

        await query.answer(
            "فقط سازنده می‌تواند بازی را شروع کند.",
            show_alert=True,
        )

        return

    await query.answer()

    game_data["status"] = "creator_turn"

    try:

        await query.edit_message_text(
            (
                "👥 بازی با دوستان شروع شد!\n\n"
                "👤 اول سازنده بازی می‌کند..."
            )
        )

    except Exception:
        pass

    creator_scores = await creator_turn(
        context,
        query.message.chat.id,
        game_data,
    )

    creator_total = sum(
        creator_scores
    )

    game_data["status"] = "waiting_opponent"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👥 ورود به بازی",
                callback_data=f"game_join_{game_data['id']}",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ لغو بازی",
                callback_data=f"game_cancel_{game_data['id']}",
            )
        ],
    ])

    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text=(
            "👤 نوبت سازنده تمام شد.\n\n"
            f"📊 امتیاز سازنده: {creator_total}\n\n"
            "👥 حالا حریف می‌تواند وارد شود."
        ),
        reply_markup=keyboard,
    )


async def join_friend_game(
    query,
    context,
    game_data,
):

    user = query.from_user

    if game_data["status"] != "waiting_opponent":

        await query.answer(
            "این بازی آماده ورود نیست.",
            show_alert=True,
        )

        return

    if user.id == game_data["creator_id"]:

        await query.answer(
            "❌ سازنده نمی‌تواند حریف خودش باشد.",
            show_alert=True,
        )

        return

    game_data["opponent_id"] = user.id

    game_data["opponent_name"] = (
        user.first_name
        or user.username
        or str(user.id)
    )

    game_data["status"] = "opponent_turn"

    await query.answer(
        "وارد بازی شدی!",
    )

    try:

        await query.edit_message_text(
            (
                "👥 حریف پیدا شد!\n\n"
                f"👤 سازنده: "
                f"{escape(game_data['creator_name'])}\n"
                f"👥 حریف: {mention(user)}\n\n"
                "🎮 حالا نوبت حریف است..."
            ),
            parse_mode="HTML",
        )

    except Exception:
        pass

    opponent_scores = await opponent_turn(
        context,
        query.message.chat.id,
        game_data,
    )

    creator_total = sum(
        game_data["creator_scores"]
    )

    opponent_total = sum(
        opponent_scores
    )

    if creator_total > opponent_total:

        result = "🏆 سازنده امتیاز بیشتری گرفت."

    elif creator_total < opponent_total:

        result = "🏆 حریف امتیاز بیشتری گرفت."

    else:

        result = "🤝 بازی مساوی شد."

    game_data["status"] = "finished"

    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text=(
            "🎮 بازی تمام شد!\n\n"
            f"👤 سازنده: {creator_total}\n"
            f"👥 حریف: {opponent_total}\n\n"
            f"{result}\n\n"
            f"🪙 امتیاز بازی: "
            f"{format_amount(game_data['amount'])} {COIN_NAME}"
        ),
    )

    GAMES.pop(
        game_data["id"],
        None,
    )


async def cancel_game(query):

    try:

        game_id = int(
            query.data.split("_")[-1]
        )

    except Exception:

        await query.answer(
            "بازی نامعتبر است.",
            show_alert=True,
        )

        return

    game_data = GAMES.get(
        game_id
    )

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

    GAMES.pop(
        game_id,
        None,
    )

    await query.answer(
        "بازی لغو شد.",
    )

    try:

        await query.edit_message_text(
            "❌ بازی لغو شد."
        )

    except Exception:
        pass


# ============================================================
# EVEN / ODD
# ============================================================

async def play_even_odd(
    update,
    context,
    choice,
):

    user = update.effective_user

    await update.message.reply_text(
        (
            f"🎲 {mention(user)}\n\n"
            f"انتخاب: {choice}\n"
            "در حال انداختن تاس..."
        ),
        parse_mode="HTML",
    )

    await asyncio.sleep(0.5)

    message = await context.bot.send_dice(
        chat_id=update.effective_chat.id,
        emoji="🎲",
    )

    value = message.dice.value

    if choice == "زوج":

        correct = value % 2 == 0

    else:

        correct = value % 2 != 0

    if correct:

        result = "✅ انتخاب درست بود!"

    else:

        result = "❌ انتخاب درست نبود."

    await update.message.reply_text(
        (
            f"🎲 نتیجه: {value}\n"
            f"انتخاب: {choice}\n\n"
            f"{result}"
        )
    )


# ============================================================
# GAME MENU
# ============================================================

async def show_games(
    update,
    context,
):

    text = (
        "🎮 بازی‌ها\n\n"

        "برای ساخت بازی:\n\n"

        "🎲 تاس\n"
        "1 تاس 100\n"
        "2 تاس 100\n"
        "3 تاس 100\n\n"

        "🎳 بولینگ\n"
        "1 بولینگ 100\n"
        "2 بولینگ 100\n"
        "3 بولینگ 100\n\n"

        "🎯 دارت\n"
        "1 دارت 100\n"
        "2 دارت 100\n"
        "3 دارت 100\n\n"

        "🏀 بسکتبال\n"
        "1 بسکتبال 100\n"
        "2 بسکتبال 100\n"
        "3 بسکتبال 100\n\n"

        "حداقل امتیاز بازی: 60\n"
        "حداکثر تعداد پرتاب: 3"
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


# ============================================================
# TRANSFER
# ============================================================

async def find_user_by_username(username):

    username = username.lstrip("@")

    with closing(db()) as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT user_id
            FROM users
            WHERE LOWER(username)=LOWER(?)
        """, (
            username,
        ))

        row = cur.fetchone()

        return row[0] if row else None


async def transfer_coins(
    update,
    amount,
    target_user_id,
):

    sender = update.effective_user

    ensure_user(sender)

    amount = int(amount)

    if amount <= 0:

        await update.message.reply_text(
            "❌ مقدار نامعتبر است."
        )

        return

    if target_user_id == sender.id:

        await update.message.reply_text(
            "❌ نمی‌توانی به خودت انتقال بدهی."
        )

        return

    sender_balance = get_balance(
        sender.id
    )

    if sender_balance < amount:

        await update.message.reply_text(
            "❌ موجودی کافی نیست."
        )

        return

    target = get_user_object(
        target_user_id
    )

    if not target:

        await update.message.reply_text(
            "❌ کاربر پیدا نشد."
        )

        return

    ok = change_balance(
        sender.id,
        -amount,
        "transfer_out",
        "انتقال به کاربر",
    )

    if not ok:

        await update.message.reply_text(
            "❌ انتقال انجام نشد."
        )

        return

    change_balance(
        target_user_id,
        amount,
        "transfer_in",
        "دریافت انتقال",
    )

    await update.message.reply_text(
        (
            "✅ انتقال انجام شد.\n\n"
            f"مقدار: {format_amount(amount)} {COIN_NAME}"
        )
    )


# ============================================================
# ADMIN
# ============================================================

async def admin_panel(
    update,
    context,
):

    user = update.effective_user

    if user.id not in OWNER_IDS:

        if update.callback_query:
            await update.callback_query.answer(
                "دسترسی ندارید.",
                show_alert=True,
            )
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👥 کاربران",
                callback_data="admin_users",
            )
        ],
        [
            InlineKeyboardButton(
                "📝 درخواست‌ها",
                callback_data="admin_requests",
            )
        ],
        [
            InlineKeyboardButton(
                "➕ افزایش موجودی",
                callback_data="admin_add_help",
            ),
        ],
        [
            InlineKeyboardButton(
                "➖ کاهش موجودی",
                callback_data="admin_sub_help",
            ),
        ],
    ])

    if update.callback_query:

        await update.callback_query.answer()

        await update.callback_query.message.reply_text(
            "⚙️ پنل مدیریت",
            reply_markup=keyboard,
        )

    else:

        await update.message.reply_text(
            "⚙️ پنل مدیریت",
            reply_markup=keyboard,
        )


async def admin_users(
    update,
    context,
):

    if update.effective_user.id not in OWNER_IDS:
        return

    with closing(db()) as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT user_id, username, first_name, balance
            FROM users
            ORDER BY balance DESC
            LIMIT 100
        """)

        rows = cur.fetchall()

    if not rows:

        text = "هیچ کاربری وجود ندارد."

    else:

        lines = [
            "👥 کاربران:",
            "",
        ]

        for index, row in enumerate(
            rows,
            start=1,
        ):

            user_id, username, first_name, balance = row

            name = (
                f"@{username}"
                if username
                else first_name or "-"
            )

            lines.append(
                f"{index}_ "
                f"{format_amount(balance)} {COIN_NAME} "
                f"{escape(name)} "
                f"({user_id})"
            )

        text = "\n".join(lines)

    await update.callback_query.answer()

    await update.callback_query.message.reply_text(
        text,
        parse_mode="HTML",
    )


async def admin_requests(
    update,
    context,
):

    if update.effective_user.id not in OWNER_IDS:
        return

    with closing(db()) as conn:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                id,
                user_id,
                username,
                amount,
                status,
                created_at
            FROM requests
            WHERE status='pending'
            ORDER BY id DESC
            LIMIT 50
        """)

        rows = cur.fetchall()

    if not rows:

        text = "📝 درخواست در انتظار وجود ندارد."

    else:

        lines = [
            "📝 درخواست‌های در انتظار:",
            "",
        ]

        for row in rows:

            request_id = row[0]
            user_id = row[1]
            username = row[2]
            amount = row[3]

            name = (
                f"@{username}"
                if username
                else "-"
            )

            lines.append(
                f"#{request_id}\n"
                f"👤 {name}\n"
                f"🆔 {user_id}\n"
                f"💰 {format_amount(amount)} {COIN_NAME}\n"
            )

        text = "\n".join(lines)

    await update.callback_query.answer()

    await update.callback_query.message.reply_text(
        text
    )


# ============================================================
# CALLBACK
# ============================================================

async def callback_handler(
    update,
    context,
):

    query = update.callback_query

    if not query:
        return

    data = query.data or ""

    user = query.from_user

    ensure_user(user)

    # --------------------------------------------------------
    # MENU
    # --------------------------------------------------------

    if data == "menu_balance":

        await show_balance(
            update,
            context,
        )

        return

    if data == "menu_games":

        await show_games(
            update,
            context,
        )

        return

    if data == "menu_referral":

        await show_referral(
            update,
            context,
        )

        return

    if data == "menu_request":

        await query.answer()

        await query.message.reply_text(
            (
                f"📝 حداقل درخواست: "
                f"{MIN_REQUEST:,} {COIN_NAME}\n\n"
                "مقدار را به صورت عدد ارسال کن."
            )
        )

        context.user_data[
            "waiting_request"
        ] = True

        return

    if data == "menu_help":

        await query.answer()

        await query.message.reply_text(
            (
                "ℹ️ راهنما\n\n"
                "💰 موجودی\n"
                "برای دیدن موجودی.\n\n"

                "🎮 بازی\n"
                "مثال:\n"
                "1 تاس 100\n"
                "2 بولینگ 100\n"
                "3 دارت 500\n"
                "1 بسکتبال 1000\n\n"

                "حداقل امتیاز بازی 60 است."
            )
        )

        return

    # --------------------------------------------------------
    # GAME BOT
    # --------------------------------------------------------

    if data.startswith("game_bot_"):

        try:
            game_id = int(
                data.split("_")[-1]
            )
        except Exception:
            await query.answer(
                "بازی نامعتبر است.",
                show_alert=True,
            )
            return

        game_data = GAMES.get(
            game_id
        )

        if not game_data:

            await query.answer(
                "❌ بازی پیدا نشد.",
                show_alert=True,
            )

            return

        await run_bot_game(
            query,
            context,
            game_data,
        )

        return

    # --------------------------------------------------------
    # GAME FRIEND
    # --------------------------------------------------------

    if data.startswith("game_friend_"):

        try:
            game_id = int(
                data.split("_")[-1]
            )
        except Exception:
            await query.answer(
                "بازی نامعتبر است.",
                show_alert=True,
            )
            return

        game_data = GAMES.get(
            game_id
        )

        if not game_data:

            await query.answer(
                "❌ بازی پیدا نشد.",
                show_alert=True,
            )

            return

        await start_friend_game(
            query,
            context,
            game_data,
        )

        return

    # --------------------------------------------------------
    # GAME JOIN
    # --------------------------------------------------------

    if data.startswith("game_join_"):

        try:
            game_id = int(
                data.split("_")[-1]
            )
        except Exception:
            await query.answer(
                "بازی نامعتبر است.",
                show_alert=True,
            )
            return

        game_data = GAMES.get(
            game_id
        )

        if not game_data:

            await query.answer(
                "❌ بازی پیدا نشد.",
                show_alert=True,
            )

            return

        await join_friend_game(
            query,
            context,
            game_data,
        )

        return

    # --------------------------------------------------------
    # GAME CANCEL
    # --------------------------------------------------------

    if data.startswith("game_cancel_"):

        await cancel_game(
            query
        )

        return

    # --------------------------------------------------------
    # ADMIN PANEL
    # --------------------------------------------------------

    if data == "admin_panel":

        await admin_panel(
            update,
            context,
        )

        return

    if data == "admin_users":

        await admin_users(
            update,
            context,
        )

        return

    if data == "admin_requests":

        await admin_requests(
            update,
            context,
        )

        return

    if data == "admin_add_help":

        if user.id not in OWNER_IDS:
            return

        await query.answer()

        await query.message.reply_text(
            "فرمت:\nافزایش USER_ID AMOUNT\n\nمثال:\nافزایش 123456789 500"
        )

        return

    if data == "admin_sub_help":

        if user.id not in OWNER_IDS:
            return

        await query.answer()

        await query.message.reply_text(
            "فرمت:\nکاهش USER_ID AMOUNT\n\nمثال:\nکاهش 123456789 500"
        )

        return

    # --------------------------------------------------------
    # REQUEST APPROVE / REJECT
    # --------------------------------------------------------

    if data.startswith("request_approve_"):

        if user.id not in OWNER_IDS:

            await query.answer(
                "دسترسی ندارید.",
                show_alert=True,
            )

            return

        request_id = int(
            data.split("_")[-1]
        )

        with closing(db()) as conn:

            cur = conn.cursor()

            cur.execute("""
                SELECT
                    user_id,
                    amount,
                    status
                FROM requests
                WHERE id=?
            """, (
                request_id,
            ))

            row = cur.fetchone()

            if not row:

                await query.answer(
                    "درخواست پیدا نشد.",
                    show_alert=True,
                )

                return

            target_user_id = row[0]
            amount = int(row[1])
            status = row[2]

            if status != "pending":

                await query.answer(
                    "این درخواست قبلاً پردازش شده.",
                    show_alert=True,
                )

                return

            cur.execute("""
                UPDATE requests
                SET status='approved',
                    processed_at=?,
                    processed_by=?
                WHERE id=?
            """, (
                now(),
                user.id,
                request_id,
            ))

            conn.commit()

        await query.answer(
            "درخواست تأیید شد."
        )

        try:

            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "✅ درخواست شما تأیید شد."
                ),
            )

        except Exception:
            pass

        try:

            await query.edit_message_reply_markup(
                reply_markup=None
            )

        except Exception:
            pass

        return

    if data.startswith("request_reject_"):

        if user.id not in OWNER_IDS:

            await query.answer(
                "دسترسی ندارید.",
                show_alert=True,
            )

            return

        request_id = int(
            data.split("_")[-1]
        )

        with closing(db()) as conn:

            cur = conn.cursor()

            cur.execute("""
                SELECT user_id, status
                FROM requests
                WHERE id=?
            """, (
                request_id,
            ))

            row = cur.fetchone()

            if not row:

                await query.answer(
                    "درخواست پیدا نشد.",
                    show_alert=True,
                )

                return

            target_user_id = row[0]

            if row[1] != "pending":

                await query.answer(
                    "این درخواست قبلاً پردازش شده.",
                    show_alert=True,
                )

                return

            cur.execute("""
                UPDATE requests
                SET status='rejected',
                    processed_at=?,
                    processed_by=?
                WHERE id=?
            """, (
                now(),
                user.id,
                request_id,
            ))

            conn.commit()

        await query.answer(
            "درخواست رد شد."
        )

        try:

            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "❌ درخواست شما رد شد."
                ),
            )

        except Exception:
            pass

        try:

            await query.edit_message_reply_markup(
                reply_markup=None
            )

        except Exception:
            pass

        return


# ============================================================
# GROUP TEXT
# ============================================================

async def handle_group_text(
    update,
    context,
):

    if not update.message:
        return

    if update.effective_chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        return

    if (
        ALLOWED_GROUP_ID
        and update.effective_chat.id != ALLOWED_GROUP_ID
    ):
        return

    user = update.effective_user

    ensure_user(user)

    text = (
        update.message.text
        or ""
    ).strip()

    normalized = normalize_digits(
        text
    )

    # --------------------------------------------------------
    # موجودی
    # --------------------------------------------------------

    if normalized in (
        "م",
        "موجودی",
    ):

        balance = get_balance(
            user.id
        )

        await update.message.reply_text(
            (
                f"💰 {mention(user)}\n\n"
                f"موجودی: "
                f"{format_amount(balance)} {COIN_NAME}"
            ),
            parse_mode="HTML",
        )

        return

    # --------------------------------------------------------
    # دستور بازی جدید
    # --------------------------------------------------------

    parsed = parse_game_command(
        normalized
    )

    if parsed:

        if parsed.get("error") == "rolls":

            await update.message.reply_text(
                "❌ تعداد پرتاب باید بین ۱ تا ۳ باشد."
            )

            return

        if parsed.get("error") == "amount":

            await update.message.reply_text(
                "❌ امتیاز بازی باید حداقل ۶۰ باشد."
            )

            return

        if parsed.get("error") == "game":

            await update.message.reply_text(
                (
                    "❌ بازی نامعتبر است.\n\n"
                    "🎲 تاس\n"
                    "🎳 بولینگ\n"
                    "🎯 دارت\n"
                    "🏀 بسکتبال"
                )
            )

            return

        await create_game(
            update,
            context,
            parsed,
        )

        return

    # --------------------------------------------------------
    # زوج
    # --------------------------------------------------------

    if normalized in (
        "زوج",
    ):

        await play_even_odd(
            update,
            context,
            "زوج",
        )

        return

    # --------------------------------------------------------
    # فرد
    # --------------------------------------------------------

    if normalized in (
        "فرد",
    ):

        await play_even_odd(
            update,
            context,
            "فرد",
        )

        return

    # --------------------------------------------------------
    # انتقال
    # --------------------------------------------------------

    transfer_match = re.fullmatch(
        r"انتقال\s+(\d+)\s+@?([A-Za-z0-9_]+)",
        normalized,
        flags=re.IGNORECASE,
    )

    if transfer_match:

        amount = int(
            transfer_match.group(1)
        )

        username = transfer_match.group(2)

        target_user_id = await find_user_by_username(
            username
        )

        if not target_user_id:

            await update.message.reply_text(
                "❌ کاربر پیدا نشد."
            )

            return

        await transfer_coins(
            update,
            amount,
            target_user_id,
        )

        return


# ============================================================
# PRIVATE TEXT
# ============================================================

async def handle_private_text(
    update,
    context,
):

    if not update.message:
        return

    user = update.effective_user

    ensure_user(user)

    text = (
        update.message.text
        or ""
    ).strip()

    normalized = normalize_digits(
        text
    )

    # --------------------------------------------------------
    # REQUEST WAITING
    # --------------------------------------------------------

    if context.user_data.get(
        "waiting_request"
    ):

        if normalized.isdigit():

            amount = int(
                normalized
            )

            context.user_data[
                "waiting_request"
            ] = False

            await create_request(
                update,
                amount,
            )

            return

    # --------------------------------------------------------
    # BALANCE
    # --------------------------------------------------------

    if normalized in (
        "م",
        "موجودی",
        "موجودی من",
    ):

        balance = get_balance(
            user.id
        )

        await update.message.reply_text(
            (
                "💰 موجودی شما\n\n"
                f"{format_amount(balance)} {COIN_NAME}"
            )
        )

        return

    # --------------------------------------------------------
    # REFERRAL
    # --------------------------------------------------------

    if normalized in (
        "زیرمجموعه",
        "دعوت",
        "رفرال",
    ):

        await show_referral(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # GAMES
    # --------------------------------------------------------

    if normalized in (
        "بازی",
        "بازی ها",
        "بازی‌ها",
    ):

        await show_games(
            update,
            context,
        )

        return

    # --------------------------------------------------------
    # REQUEST
    # --------------------------------------------------------

    if normalized in (
        "درخواست",
        "برداشت",
    ):

        context.user_data[
            "waiting_request"
        ] = True

        await update.message.reply_text(
            (
                f"📝 مقدار درخواست را بفرست.\n"
                f"حداقل: {MIN_REQUEST:,} {COIN_NAME}"
            )
        )

        return

    # --------------------------------------------------------
    # HELP
    # --------------------------------------------------------

    if normalized in (
        "راهنما",
        "کمک",
    ):

        await update.message.reply_text(
            (
                "ℹ️ راهنما\n\n"
                "💰 موجودی\n"
                "🎮 بازی‌ها\n"
                "👥 زیرمجموعه\n"
                "📝 درخواست\n\n"
                "مثال بازی:\n"
                "1 تاس 100\n"
                "2 بولینگ 100\n"
                "3 دارت 500\n"
                "1 بسکتبال 1000"
            )
        )

        return

    # --------------------------------------------------------
    # ADMIN ADD
    # --------------------------------------------------------

    match = re.fullmatch(
        r"افزایش\s+(\d+)\s+(\d+)",
        normalized,
    )

    if match and user.id in OWNER_IDS:

        target_id = int(
            match.group(1)
        )

        amount = int(
            match.group(2)
        )

        target = get_user_object(
            target_id
        )

        if not target:

            await update.message.reply_text(
                "❌ کاربر پیدا نشد."
            )

            return

        change_balance(
            target_id,
            amount,
            "admin_add",
            "افزایش توسط مدیر",
        )

        await update.message.reply_text(
            "✅ موجودی افزایش یافت."
        )

        return

    # --------------------------------------------------------
    # ADMIN SUB
    # --------------------------------------------------------

    match = re.fullmatch(
        r"کاهش\s+(\d+)\s+(\d+)",
        normalized,
    )

    if match and user.id in OWNER_IDS:

        target_id = int(
            match.group(1)
        )

        amount = int(
            match.group(2)
        )

        ok = change_balance(
            target_id,
            -amount,
            "admin_sub",
            "کاهش توسط مدیر",
        )

        if ok:

            await update.message.reply_text(
                "✅ موجودی کاهش یافت."
            )

        else:

            await update.message.reply_text(
                "❌ موجودی کافی نیست یا کاربر وجود ندارد."
            )

        return

    # --------------------------------------------------------
    # ADMIN REFERRAL
    # --------------------------------------------------------

    match = re.fullmatch(
        r"زیرمجموعه\s+(\d+)\s+(\d+)",
        normalized,
    )

    if match and user.id in OWNER_IDS:

        target_id = int(
            match.group(1)
        )

        count = int(
            match.group(2)
        )

        await update.message.reply_text(
            (
                "⚠️ تعداد زیرمجموعه واقعی از دیتابیس "
                "محاسبه می‌شود و با دستور دستی تغییر نمی‌کند."
            )
        )

        return


# ============================================================
# COMMANDS
# ============================================================

async def balance_command(
    update,
    context,
):

    await show_balance(
        update,
        context,
    )


async def games_command(
    update,
    context,
):

    await show_games(
        update,
        context,
    )


async def referral_command(
    update,
    context,
):

    await show_referral(
        update,
        context,
    )


async def admin_command(
    update,
    context,
):

    await admin_panel(
        update,
        context,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    global TOKEN

    init_db()

    TOKEN = os.getenv(
        "BOT_TOKEN"
    )

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN در Environment تنظیم نشده است."
        )

    application = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # COMMANDS
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "balance",
            balance_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "games",
            games_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "referral",
            referral_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin_command,
        )
    )

    # --------------------------------------------------------
    # CALLBACKS
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # --------------------------------------------------------
    # GROUP
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS
            & filters.TEXT
            & ~filters.COMMAND,
            handle_group_text,
        )
    )

    # --------------------------------------------------------
    # PRIVATE
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & filters.TEXT
            & ~filters.COMMAND,
            handle_private_text,
        )
    )

    logger.info(
        "Bot started."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
