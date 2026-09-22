#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║     『 Nɪʀᴏ ꭙ Fɪʟᴇs Bᴏᴛ 』 𖤐  —  Production Bot       ║
║     Built on python-telegram-bot v21.x  |  Bot API 9.4+ ║
╚══════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import os
import secrets
import string
from datetime import datetime, timezone
from typing import Optional

from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ─────────────────────────────────────────────
#  CONFIG  (hardcoded as requested)
# ─────────────────────────────────────────────
BOT_TOKEN     = "8832316948:AAGtFSWtUb-mV9cUwWg42_Oxd_quucUsuSU"
DB_CHANNEL_ID = -1004332152220
OWNER_ID      = 8189708860
MONGO_URI     = "mongodb+srv://Esh:1234567890ukwhat@cluster0.mnbnc7a.mongodb.net/?appName=Cluster0"
MONGO_DB_NAME = "niro_x_files"
AUTO_DEL_SEC  = 300          # 5 minutes
PORT          = int(os.environ.get("PORT", 8080))

# ─────────────────────────────────────────────
#  SMALL CAPS / DECORATIVE TEXT HELPERS
# ─────────────────────────────────────────────
BOT_NAME = "『 Nɪʀᴏ ꭙ Fɪʟᴇs Bᴏᴛ 』 𖤐"

def sc(text: str) -> str:
    """Return text unchanged — caller already uses smallcaps literals."""
    return text

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  DATABASE  (Motor / MongoDB)
# ─────────────────────────────────────────────
_mongo_client: Optional[AsyncIOMotorClient] = None

def get_db():
    return _mongo_client[MONGO_DB_NAME]

async def init_db():
    global _mongo_client
    _mongo_client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
    db = get_db()
    await db.users.create_index("user_id", unique=True)
    await db.links.create_index("token", unique=True)
    await db.settings.create_index("key", unique=True)
    logger.info("✅ MongoDB connected → %s", MONGO_DB_NAME)

async def db_get_or_create_user(user_id: int, username: str, full_name: str):
    db = get_db()
    now = datetime.now(timezone.utc)
    result = await db.users.find_one_and_update(
        {"user_id": user_id},
        {
            "$set": {"username": username, "full_name": full_name, "last_seen": now},
            "$setOnInsert": {"joined": now},
        },
        upsert=True,
        return_document=True,
    )
    return result

async def db_count_users() -> int:
    return await get_db().users.count_documents({})

async def db_new_today() -> int:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return await get_db().users.count_documents({"joined": {"$gte": today}})

async def db_save_link(token: str, msg_ids: list[int]) -> None:
    await get_db().links.insert_one({"token": token, "msg_ids": msg_ids})

async def db_get_link(token: str) -> Optional[dict]:
    return await get_db().links.find_one({"token": token})

async def db_get_setting(key: str):
    doc = await get_db().settings.find_one({"key": key})
    return doc["value"] if doc else None

async def db_set_setting(key: str, value) -> None:
    await get_db().settings.update_one(
        {"key": key}, {"$set": {"value": value}}, upsert=True
    )

async def db_get_channels() -> list[dict]:
    doc = await db_get_setting("force_channels")
    return doc if isinstance(doc, list) else []

async def db_set_channels(channels: list[dict]) -> None:
    await db_set_setting("force_channels", channels)

async def db_get_start_video() -> Optional[str]:
    return await db_get_setting("start_video_file_id")

async def db_get_verify_video() -> Optional[str]:
    return await db_get_setting("verify_video_file_id")

# ─────────────────────────────────────────────
#  UNIQUE TOKEN GENERATOR
# ─────────────────────────────────────────────
def make_token(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))

# ─────────────────────────────────────────────
#  FORCE-SUB CHECKER
# ─────────────────────────────────────────────
async def check_subscriptions(bot: Bot, user_id: int) -> list[dict]:
    """Return list of channels the user has NOT joined."""
    channels = await db_get_channels()
    not_joined = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(ch)
        except TelegramError as e:
            logger.warning("check_sub error for channel %s: %s", ch["id"], e)
            not_joined.append(ch)  # treat as not joined on error
    return not_joined

async def build_join_keyboard(bot: Bot, not_joined: list[dict], token: str = "") -> InlineKeyboardMarkup:
    """3 join buttons per row + a Verify row."""
    rows = []
    row = []
    for i, ch in enumerate(not_joined):
        invite = ch.get("invite_link", "")
        btn = InlineKeyboardButton(
            text="‣ Jᴏɪɴ",
            url=invite,
        )
        row.append(btn)
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Verify button on its own row
    cb_data = f"verify:{token}" if token else "verify:"
    rows.append([
        InlineKeyboardButton(
            text="✅ Vᴇʀɪꜰʏ Mᴇᴍʙᴇʀꜱʜɪᴘ",
            callback_data=cb_data,
        )
    ])
    return InlineKeyboardMarkup(rows)

# ─────────────────────────────────────────────
#  MESSAGES (all follow the text style)
# ─────────────────────────────────────────────
MSG_NOT_SUBBED = (
    "❌ <b>Aᴄᴄᴇss Dᴇɴɪᴇᴅ</b>\n\n"
    "<blockquote>Yᴏᴜ ʜᴀᴠᴇ ɴᴏᴛ ᴊᴏɪɴᴇᴅ ᴀʟʟ ᴏᴜʀ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟs ʏᴇᴛ.</blockquote>\n\n"
    "‣ Pʟᴇᴀsᴇ ᴊᴏɪɴ ᴀʟʟ ᴛʜᴇ ᴄʜᴀɴɴᴇʟs ʙᴇʟᴏᴡ ᴀɴᴅ ᴛʜᴇɴ ᴄʟɪᴄᴋ <b>Vᴇʀɪꜰʏ Mᴇᴍʙᴇʀꜱʜɪᴘ</b>."
)

MSG_VERIFY_OK = (
    "✅ <b>Vᴇʀɪꜰɪᴄᴀᴛɪᴏɴ Sᴜᴄᴄᴇssꜰᴜʟ!</b>\n\n"
    "<blockquote>Yᴏᴜ ʜᴀᴠᴇ ꜱᴜᴄᴄᴇssꜰᴜʟʟʏ ᴊᴏɪɴᴇᴅ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟs.</blockquote>\n\n"
    "‣ Fᴇᴛᴄʜɪɴɢ ʏᴏᴜʀ ꜰɪʟᴇs ɴᴏᴡ..."
)

MSG_VERIFY_FAIL = (
    "⚠️ <b>Sᴛɪʟʟ Nᴏᴛ Jᴏɪɴᴇᴅ</b>\n\n"
    "<blockquote>Iᴛ ꜱᴇᴇᴍꜱ ʏᴏᴜ ʜᴀᴠᴇɴ'ᴛ ᴊᴏɪɴᴇᴅ ᴀʟʟ ᴛʜᴇ ᴄʜᴀɴɴᴇʟs ʏᴇᴛ.</blockquote>\n\n"
    "‣ Pʟᴇᴀsᴇ ᴊᴏɪɴ ᴀʟʟ ᴄʜᴀɴɴᴇʟs ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ."
)

MSG_FILE_WARNING = (
    "\n\n⏳ <b>Aᴜᴛᴏ-Dᴇʟᴇᴛᴇ Wᴀʀɴɪɴɢ</b>\n"
    "<blockquote>Tʜᴇsᴇ ꜰɪʟᴇs ᴡɪʟʟ ʙᴇ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ꜰʀᴏᴍ ʏᴏᴜʀ ᴄʜᴀᴛ "
    "ɪɴ <b>5 Mɪɴᴜᴛᴇs</b>.\nPʟᴇᴀsᴇ sᴀᴠᴇ ᴛʜᴇᴍ ʙᴇꜰᴏʀᴇ ᴛʜᴇʏ ᴅɪsᴀᴘᴘᴇᴀʀ!</blockquote>"
)

MSG_NO_LINK = (
    "🔗 <b>Nᴏ Lɪɴᴋ Dᴇᴛᴇᴄᴛᴇᴅ</b>\n\n"
    "<blockquote>Tʜɪs ʙᴏᴛ ᴅᴇʟɪᴠᴇʀs ꜰɪʟᴇs ᴠɪᴀ ꜱᴘᴇᴄɪᴀʟ ʟɪɴᴋs ᴏɴʟʏ.\n"
    "Pʟᴇᴀsᴇ ᴏᴘᴇɴ ᴀ ᴠᴀʟɪᴅ ꜰɪʟᴇ ʟɪɴᴋ ᴛᴏ ɢᴇᴛ ᴛʜᴇ ᴄᴏɴᴛᴇɴᴛ.</blockquote>"
)

MSG_INVALID_LINK = (
    "❌ <b>Iɴᴠᴀʟɪᴅ Lɪɴᴋ</b>\n\n"
    "<blockquote>Tʜɪs ʟɪɴᴋ ɪs ᴇɪᴛʜᴇʀ ᴇxᴘɪʀᴇᴅ ᴏʀ ɪɴᴠᴀʟɪᴅ.\n"
    "Pʟᴇᴀsᴇ ᴄᴏɴᴛᴀᴄᴛ ᴛʜᴇ ᴀᴅᴍɪɴ ꜰᴏʀ ᴀ ɴᴇᴡ ʟɪɴᴋ.</blockquote>"
)

MSG_ONLINE = (
    "✨ <b>{}</b> ɪs Oɴʟɪɴᴇ!\n\n"
    "<blockquote>Pʟᴇᴀsᴇ ᴏᴘᴇɴ ᴍᴇ ᴡɪᴛʜ ᴀ ᴠᴀʟɪᴅ ꜰɪʟᴇ ʟɪɴᴋ ᴛᴏ ɢᴇᴛ ʏᴏᴜʀ ᴄᴏɴᴛᴇɴᴛ.</blockquote>"
).format(BOT_NAME)

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
async def get_or_create_invite(bot: Bot, channel_id: int) -> str:
    """Generate/retrieve an invite link for a channel."""
    try:
        chat = await bot.get_chat(channel_id)
        if chat.username:
            return f"https://t.me/{chat.username}"
        link = await bot.create_chat_invite_link(channel_id)
        return link.invite_link
    except TelegramError as e:
        logger.error("invite link error for %s: %s", channel_id, e)
        return ""

async def send_files_to_user(
    bot: Bot,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    msg_ids: list[int],
):
    """Copy messages from DB channel to user, then schedule deletion."""
    sent_ids = []
    for mid in msg_ids:
        try:
            sent = await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=DB_CHANNEL_ID,
                message_id=mid,
                protect_content=False,
            )
            sent_ids.append(sent.message_id)
        except TelegramError as e:
            logger.error("copy_message error mid=%s: %s", mid, e)

    if sent_ids:
        # Send warning message
        warn = await bot.send_message(
            chat_id=chat_id,
            text=MSG_FILE_WARNING,
            parse_mode=ParseMode.HTML,
        )
        sent_ids.append(warn.message_id)

        # Schedule auto-delete
        context.job_queue.run_once(
            callback=_delete_job,
            when=AUTO_DEL_SEC,
            data={"chat_id": chat_id, "msg_ids": sent_ids},
            name=f"del_{chat_id}_{sent_ids[0]}",
        )

async def _delete_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data["chat_id"]
    msg_ids = data["msg_ids"]
    try:
        await context.bot.delete_messages(chat_id=chat_id, message_ids=msg_ids)
    except TelegramError as e:
        logger.warning("auto-delete error: %s", e)

# ─────────────────────────────────────────────
#  /start HANDLER
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id

    await db_get_or_create_user(
        user.id,
        user.username or "",
        user.full_name or "",
    )

    args = context.args
    token = args[0] if args else None

    if not token:
        # No file link — show online video or text
        video_fid = await db_get_start_video()
        if video_fid:
            try:
                await context.bot.send_animation(
                    chat_id=chat_id,
                    animation=video_fid,
                    caption=MSG_ONLINE,
                    parse_mode=ParseMode.HTML,
                )
                return
            except TelegramError as e:
                logger.warning("start video send error: %s", e)
        await update.message.reply_text(MSG_ONLINE, parse_mode=ParseMode.HTML)
        return

    # Validate token
    link_doc = await db_get_link(token)
    if not link_doc:
        await update.message.reply_text(MSG_INVALID_LINK, parse_mode=ParseMode.HTML)
        return

    # Check force-sub
    not_joined = await check_subscriptions(context.bot, user.id)
    if not_joined:
        kbd = await build_join_keyboard(context.bot, not_joined, token=token)
        verify_video = await db_get_verify_video()
        if verify_video:
            try:
                await context.bot.send_animation(
                    chat_id=chat_id,
                    animation=verify_video,
                    caption=MSG_NOT_SUBBED,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kbd,
                )
                return
            except TelegramError as e:
                logger.warning("verify video send error: %s", e)
        await update.message.reply_text(
            MSG_NOT_SUBBED, parse_mode=ParseMode.HTML, reply_markup=kbd
        )
        return

    # All joined — deliver files
    await send_files_to_user(
        context.bot, context, chat_id, link_doc["msg_ids"]
    )

# ─────────────────────────────────────────────
#  VERIFY CALLBACK
# ─────────────────────────────────────────────
async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    chat_id = update.effective_chat.id
    token = query.data.split(":", 1)[1]

    not_joined = await check_subscriptions(context.bot, user.id)
    if not_joined:
        kbd = await build_join_keyboard(context.bot, not_joined, token=token)
        try:
            await query.edit_message_caption(
                caption=MSG_VERIFY_FAIL,
                parse_mode=ParseMode.HTML,
                reply_markup=kbd,
            )
        except TelegramError:
            try:
                await query.edit_message_text(
                    text=MSG_VERIFY_FAIL,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kbd,
                )
            except TelegramError:
                pass
        return

    # Passed — delete the join message and deliver
    try:
        await query.message.delete()
    except TelegramError:
        pass

    if not token:
        await context.bot.send_message(
            chat_id, MSG_VERIFY_OK, parse_mode=ParseMode.HTML
        )
        return

    link_doc = await db_get_link(token)
    if not link_doc:
        await context.bot.send_message(
            chat_id, MSG_INVALID_LINK, parse_mode=ParseMode.HTML
        )
        return

    await send_files_to_user(
        context.bot, context, chat_id, link_doc["msg_ids"]
    )

# ─────────────────────────────────────────────
#  /link COMMAND  (admin only)
# ─────────────────────────────────────────────
async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    reply = update.message.reply_to_message
    if not reply:
        await update.message.reply_text(
            "⚠️ <b>Rᴇᴘʟʏ ᴛᴏ ᴀ Mᴇssᴀɢᴇ</b>\n\n"
            "<blockquote>Rᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ꜰɪʀsᴛ ᴍᴇssᴀɢᴇ ᴀɴᴅ ᴜsᴇ /link ᴏʀ /link -i 2</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Parse -i N argument
    count = 1
    args = context.args
    if len(args) >= 2 and args[0] == "-i":
        try:
            count = max(1, int(args[1]))
        except ValueError:
            pass

    first_id = reply.message_id

    # Forward/collect messages into DB channel
    msg_ids = []
    if count == 1:
        # Single message — forward to DB channel
        try:
            fwd = await context.bot.forward_message(
                chat_id=DB_CHANNEL_ID,
                from_chat_id=update.effective_chat.id,
                message_id=first_id,
            )
            msg_ids.append(fwd.message_id)
        except TelegramError as e:
            await update.message.reply_text(
                f"❌ Eʀʀᴏʀ ꜰᴏʀᴡᴀʀᴅɪɴɢ ᴍᴇssᴀɢᴇ:\n<code>{e}</code>",
                parse_mode=ParseMode.HTML,
            )
            return
    else:
        # Multiple messages: replied + next (count-1)
        ids_to_fwd = [first_id + i for i in range(count)]
        try:
            fwds = await context.bot.forward_messages(
                chat_id=DB_CHANNEL_ID,
                from_chat_id=update.effective_chat.id,
                message_ids=ids_to_fwd,
            )
            msg_ids = [m.message_id for m in fwds]
        except TelegramError as e:
            await update.message.reply_text(
                f"❌ Eʀʀᴏʀ ꜰᴏʀᴡᴀʀᴅɪɴɢ ᴍᴇssᴀɢᴇs:\n<code>{e}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

    token = make_token()
    await db_save_link(token, msg_ids)

    me = await context.bot.get_me()
    bot_username = me.username
    share_link = f"https://t.me/{bot_username}?start={token}"

    await update.message.reply_text(
        f"🔗 <b>Lɪɴᴋ Gᴇɴᴇʀᴀᴛᴇᴅ Sᴜᴄᴄᴇssꜰᴜʟʟʏ!</b>\n\n"
        f"<blockquote>‣ Fɪʟᴇs: <b>{len(msg_ids)}</b>\n"
        f"‣ Tᴏᴋᴇɴ: <code>{token}</code></blockquote>\n\n"
        f"🌐 <b>Sʜᴀʀᴇ Lɪɴᴋ:</b>\n<code>{share_link}</code>",
        parse_mode=ParseMode.HTML,
    )

# ─────────────────────────────────────────────
#  ADMIN PANEL  (/admin)
# ─────────────────────────────────────────────

# ConversationHandler states
(
    ADMIN_MENU,
    AWAIT_ADD_CHANNEL,
    AWAIT_REMOVE_CHANNEL,
    AWAIT_START_VIDEO,
    AWAIT_VERIFY_VIDEO,
) = range(5)


def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Sᴛᴀᴛs", callback_data="adm:stats"),
            InlineKeyboardButton("📢 Cʜᴀɴɴᴇʟs", callback_data="adm:channels"),
        ],
        [
            InlineKeyboardButton("🎬 Sᴛᴀʀᴛ Vɪᴅᴇᴏ", callback_data="adm:setstartv"),
            InlineKeyboardButton("🎬 Vᴇʀɪꜰʏ Vɪᴅᴇᴏ", callback_data="adm:setverifyv"),
        ],
        [InlineKeyboardButton("✖ Cʟᴏsᴇ", callback_data="adm:close")],
    ])


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text(
            "🚫 <b>Aᴄᴄᴇss Dᴇɴɪᴇᴅ</b>\n"
            "<blockquote>Yᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴀᴜᴛʜᴏʀɪᴢᴇᴅ.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"⚙️ <b>Aᴅᴍɪɴ Pᴀɴᴇʟ — {BOT_NAME}</b>\n\n"
        "<blockquote>Sᴇʟᴇᴄᴛ ᴀɴ ᴏᴘᴛɪᴏɴ ʙᴇʟᴏᴡ.</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_main_keyboard(),
    )
    return ADMIN_MENU


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != OWNER_ID:
        return ADMIN_MENU

    action = query.data.split(":", 1)[1]

    # ── Stats ──────────────────────────────────
    if action == "stats":
        total = await db_count_users()
        today = await db_new_today()
        channels = await db_get_channels()
        await query.edit_message_text(
            f"📊 <b>Bᴏᴛ Sᴛᴀᴛɪsᴛɪᴄs</b>\n\n"
            f"<blockquote>"
            f"‣ Tᴏᴛᴀʟ Usᴇʀs : <b>{total}</b>\n"
            f"‣ Nᴇᴡ Tᴏᴅᴀʏ  : <b>{today}</b>\n"
            f"‣ Fᴏʀᴄᴇ Sᴜʙs : <b>{len(channels)}</b>"
            f"</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀ Bᴀᴄᴋ", callback_data="adm:back")]
            ]),
        )
        return ADMIN_MENU

    # ── Channels list ──────────────────────────
    elif action == "channels":
        channels = await db_get_channels()
        text = "📢 <b>Fᴏʀᴄᴇ-Sᴜʙ Cʜᴀɴɴᴇʟs</b>\n\n"
        if channels:
            for i, ch in enumerate(channels, 1):
                text += f"<blockquote>‣ [{i}] {ch.get('title','?')}  (<code>{ch['id']}</code>)</blockquote>\n"
        else:
            text += "<blockquote>Nᴏ ᴄʜᴀɴɴᴇʟs ᴀᴅᴅᴇᴅ ʏᴇᴛ.</blockquote>"

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("➕ Aᴅᴅ", callback_data="adm:addch"),
                    InlineKeyboardButton("➖ Rᴇᴍᴏᴠᴇ", callback_data="adm:remch"),
                ],
                [InlineKeyboardButton("◀ Bᴀᴄᴋ", callback_data="adm:back")],
            ]),
        )
        return ADMIN_MENU

    # ── Add channel ────────────────────────────
    elif action == "addch":
        await query.edit_message_text(
            "📢 <b>Aᴅᴅ Cʜᴀɴɴᴇʟ</b>\n\n"
            "<blockquote>Sᴇɴᴅ ᴛʜᴇ <b>Cʜᴀɴɴᴇʟ ID</b> (ᴇ.ɢ. -1001234567890).\nSᴇɴᴅ /cancel ᴛᴏ ᴀʙᴏʀᴛ.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return AWAIT_ADD_CHANNEL

    # ── Remove channel ─────────────────────────
    elif action == "remch":
        channels = await db_get_channels()
        if not channels:
            await query.edit_message_text(
                "⚠️ <b>Nᴏ Cʜᴀɴɴᴇʟs</b>\n<blockquote>Nᴏᴛʜɪɴɢ ᴛᴏ ʀᴇᴍᴏᴠᴇ.</blockquote>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀ Bᴀᴄᴋ", callback_data="adm:channels")]
                ]),
            )
            return ADMIN_MENU

        await query.edit_message_text(
            "📢 <b>Rᴇᴍᴏᴠᴇ Cʜᴀɴɴᴇʟ</b>\n\n"
            "<blockquote>Sᴇɴᴅ ᴛʜᴇ <b>Cʜᴀɴɴᴇʟ ID</b> ʏᴏᴜ ᴡɪsʜ ᴛᴏ ʀᴇᴍᴏᴠᴇ.\nSᴇɴᴅ /cancel ᴛᴏ ᴀʙᴏʀᴛ.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return AWAIT_REMOVE_CHANNEL

    # ── Set Start Video ────────────────────────
    elif action == "setstartv":
        await query.edit_message_text(
            "🎬 <b>Sᴇᴛ Sᴛᴀʀᴛ Vɪᴅᴇᴏ</b>\n\n"
            "<blockquote>Sᴇɴᴅ ᴀ <b>Vɪᴅᴇᴏ</b> ᴏʀ <b>Gɪꜰ</b> ᴍᴇssᴀɢᴇ ɴᴏᴡ.\nSᴇɴᴅ /cancel ᴛᴏ ᴀʙᴏʀᴛ.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return AWAIT_START_VIDEO

    # ── Set Verify Video ───────────────────────
    elif action == "setverifyv":
        await query.edit_message_text(
            "🎬 <b>Sᴇᴛ Vᴇʀɪꜰʏ Vɪᴅᴇᴏ</b>\n\n"
            "<blockquote>Sᴇɴᴅ ᴀ <b>Vɪᴅᴇᴏ</b> ᴏʀ <b>Gɪꜰ</b> ᴍᴇssᴀɢᴇ ɴᴏᴡ.\nSᴇɴᴅ /cancel ᴛᴏ ᴀʙᴏʀᴛ.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return AWAIT_VERIFY_VIDEO

    # ── Back / Close ───────────────────────────
    elif action in ("back", "close"):
        if action == "close":
            await query.message.delete()
            return ConversationHandler.END
        await query.edit_message_text(
            f"⚙️ <b>Aᴅᴍɪɴ Pᴀɴᴇʟ — {BOT_NAME}</b>\n\n"
            "<blockquote>Sᴇʟᴇᴄᴛ ᴀɴ ᴏᴘᴛɪᴏɴ ʙᴇʟᴏᴡ.</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_keyboard(),
        )
        return ADMIN_MENU

    return ADMIN_MENU


# ── ConversationHandler input steps ───────────

async def receive_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return AWAIT_ADD_CHANNEL
    raw = update.message.text.strip()
    try:
        ch_id = int(raw)
    except ValueError:
        await update.message.reply_text(
            "❌ <b>Iɴᴠᴀʟɪᴅ ID</b>\n<blockquote>Pʟᴇᴀsᴇ sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ɪɴᴛᴇɢᴇʀ ᴄʜᴀɴɴᴇʟ ID.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return AWAIT_ADD_CHANNEL

    # Validate bot is admin
    try:
        chat = await context.bot.get_chat(ch_id)
        bot_member = await context.bot.get_chat_member(ch_id, (await context.bot.get_me()).id)
        if bot_member.status not in ("administrator", "creator"):
            await update.message.reply_text(
                "⚠️ <b>Bᴏᴛ Nᴏᴛ Aᴅᴍɪɴ</b>\n"
                "<blockquote>Pʟᴇᴀsᴇ ᴍᴀᴋᴇ ᴛʜᴇ ʙᴏᴛ ᴀɴ ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀ ɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ ꜰɪʀsᴛ.</blockquote>",
                parse_mode=ParseMode.HTML,
            )
            return AWAIT_ADD_CHANNEL
        title = chat.title or str(ch_id)
    except TelegramError as e:
        await update.message.reply_text(
            f"❌ <b>Eʀʀᴏʀ</b>\n<blockquote>{e}</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return AWAIT_ADD_CHANNEL

    invite = await get_or_create_invite(context.bot, ch_id)
    channels = await db_get_channels()
    for ch in channels:
        if ch["id"] == ch_id:
            await update.message.reply_text(
                "⚠️ <b>Aʟʀᴇᴀᴅʏ Aᴅᴅᴇᴅ</b>\n<blockquote>Tʜɪs ᴄʜᴀɴɴᴇʟ ɪs ᴀʟʀᴇᴀᴅʏ ɪɴ ᴛʜᴇ ʟɪsᴛ.</blockquote>",
                parse_mode=ParseMode.HTML,
            )
            return ADMIN_MENU

    channels.append({"id": ch_id, "title": title, "invite_link": invite})
    await db_set_channels(channels)

    await update.message.reply_text(
        f"✅ <b>Cʜᴀɴɴᴇʟ Aᴅᴅᴇᴅ</b>\n"
        f"<blockquote>‣ {title} (<code>{ch_id}</code>)</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_main_keyboard(),
    )
    return ADMIN_MENU


async def receive_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return AWAIT_REMOVE_CHANNEL
    raw = update.message.text.strip()
    try:
        ch_id = int(raw)
    except ValueError:
        await update.message.reply_text(
            "❌ <b>Iɴᴠᴀʟɪᴅ ID</b>\n<blockquote>Sᴇɴᴅ ᴀ ᴠᴀʟɪᴅ ᴄʜᴀɴɴᴇʟ ID.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return AWAIT_REMOVE_CHANNEL

    channels = await db_get_channels()
    new_channels = [c for c in channels if c["id"] != ch_id]
    if len(new_channels) == len(channels):
        await update.message.reply_text(
            "⚠️ <b>Nᴏᴛ Fᴏᴜɴᴅ</b>\n<blockquote>Cʜᴀɴɴᴇʟ ɴᴏᴛ ɪɴ ᴛʜᴇ ʟɪsᴛ.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return AWAIT_REMOVE_CHANNEL

    await db_set_channels(new_channels)
    await update.message.reply_text(
        f"✅ <b>Cʜᴀɴɴᴇʟ Rᴇᴍᴏᴠᴇᴅ</b>\n<blockquote><code>{ch_id}</code></blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_main_keyboard(),
    )
    return ADMIN_MENU


async def receive_start_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return AWAIT_START_VIDEO
    msg = update.message
    file_id = None
    if msg.animation:
        file_id = msg.animation.file_id
    elif msg.video:
        file_id = msg.video.file_id
    elif msg.document and msg.document.mime_type and "video" in msg.document.mime_type:
        file_id = msg.document.file_id

    if not file_id:
        await msg.reply_text(
            "❌ <b>Nᴏ Vɪᴅᴇᴏ Dᴇᴛᴇᴄᴛᴇᴅ</b>\n<blockquote>Pʟᴇᴀsᴇ sᴇɴᴅ ᴀ ɢɪꜰ ᴏʀ ᴠɪᴅᴇᴏ.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return AWAIT_START_VIDEO

    await db_set_setting("start_video_file_id", file_id)
    await msg.reply_text(
        "✅ <b>Sᴛᴀʀᴛ Vɪᴅᴇᴏ Sᴇᴛ!</b>\n<blockquote>Tʜᴇ ꜱᴛᴀʀᴛ ᴠɪᴅᴇᴏ ʜᴀs ʙᴇᴇɴ ᴜᴘᴅᴀᴛᴇᴅ.</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_main_keyboard(),
    )
    return ADMIN_MENU


async def receive_verify_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return AWAIT_VERIFY_VIDEO
    msg = update.message
    file_id = None
    if msg.animation:
        file_id = msg.animation.file_id
    elif msg.video:
        file_id = msg.video.file_id
    elif msg.document and msg.document.mime_type and "video" in msg.document.mime_type:
        file_id = msg.document.file_id

    if not file_id:
        await msg.reply_text(
            "❌ <b>Nᴏ Vɪᴅᴇᴏ Dᴇᴛᴇᴄᴛᴇᴅ</b>\n<blockquote>Pʟᴇᴀsᴇ sᴇɴᴅ ᴀ ɢɪꜰ ᴏʀ ᴠɪᴅᴇᴏ.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return AWAIT_VERIFY_VIDEO

    await db_set_setting("verify_video_file_id", file_id)
    await msg.reply_text(
        "✅ <b>Vᴇʀɪꜰʏ Vɪᴅᴇᴏ Sᴇᴛ!</b>\n<blockquote>Tʜᴇ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ ᴠɪᴅᴇᴏ ʜᴀs ʙᴇᴇɴ ᴜᴘᴅᴀᴛᴇᴅ.</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_main_keyboard(),
    )
    return ADMIN_MENU


async def cancel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✖ <b>Cᴀɴᴄᴇʟʟᴇᴅ.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_main_keyboard(),
    )
    return ADMIN_MENU

# ─────────────────────────────────────────────
#  ERROR HANDLER
# ─────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling update:", exc_info=context.error)
    if isinstance(context.error, Forbidden):
        logger.info("User blocked the bot.")
    elif isinstance(context.error, BadRequest):
        logger.warning("Bad request: %s", context.error)

# ─────────────────────────────────────────────
#  PING  (keep-alive web endpoint for Render)
# ─────────────────────────────────────────────
async def ping(request):
    return web.Response(text="pong", status=200)

async def start_web():
    app = web.Application()
    app.router.add_get("/ping", ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("🌐 Ping server started on port %s", PORT)

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    async def _run():
        await init_db()
        await start_web()

        app = (
            Application.builder()
            .token(BOT_TOKEN)
            .build()
        )

        # Admin conversation
        admin_conv = ConversationHandler(
            entry_points=[CommandHandler("admin", admin_command)],
            states={
                ADMIN_MENU: [
                    CallbackQueryHandler(admin_callback, pattern=r"^adm:"),
                ],
                AWAIT_ADD_CHANNEL: [
                    CommandHandler("cancel", cancel_admin),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, receive_add_channel),
                ],
                AWAIT_REMOVE_CHANNEL: [
                    CommandHandler("cancel", cancel_admin),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, receive_remove_channel),
                ],
                AWAIT_START_VIDEO: [
                    CommandHandler("cancel", cancel_admin),
                    MessageHandler(
                        filters.ANIMATION | filters.VIDEO | filters.Document.VIDEO,
                        receive_start_video,
                    ),
                ],
                AWAIT_VERIFY_VIDEO: [
                    CommandHandler("cancel", cancel_admin),
                    MessageHandler(
                        filters.ANIMATION | filters.VIDEO | filters.Document.VIDEO,
                        receive_verify_video,
                    ),
                ],
            },
            fallbacks=[CommandHandler("cancel", cancel_admin)],
            per_message=False,
        )

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("link", link_command))
        app.add_handler(admin_conv)
        app.add_handler(CallbackQueryHandler(verify_callback, pattern=r"^verify:"))
        app.add_error_handler(error_handler)

        logger.info("🤖 %s is starting...", BOT_NAME)

        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=["message", "callback_query"])

        # Keep alive
        try:
            await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
