import os
import json
import asyncio
import time
from pathlib import Path
from collections import deque

import pyrogram
from pyrogram import Client, filters
from pyrogram.idle import idle
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import RPCError

# ============================================================
# PyTgCalls / Pyrogram compatibility fix
# PyTgCalls currently imports GroupcallForbidden,
# while Pyrogram exposes GroupCallForbidden.
# ============================================================

if hasattr(pyrogram.errors, "GroupCallForbidden") and not hasattr(
    pyrogram.errors, "GroupcallForbidden"
):
    pyrogram.errors.GroupcallForbidden = pyrogram.errors.GroupCallForbidden


from pytgcalls import PyTgCalls
from pytgcalls import filters as tgcall_filters
from pytgcalls.types import MediaStream, StreamEnded


# ============================================================
# CONFIG
# ============================================================

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")

GROUP_ID = int(os.getenv("GROUP_ID", "0"))

BRAND = os.getenv("BRAND", "@epic_india")

DATA_DIR = Path("epic_bot_data")
DATA_DIR.mkdir(exist_ok=True)

AUTH_FILE = DATA_DIR / "authorized.json"


# ============================================================
# VALIDATION
# ============================================================

if not API_ID:
    raise RuntimeError("API_ID is missing")

if not API_HASH:
    raise RuntimeError("API_HASH is missing")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GROUP_ID:
    raise RuntimeError("GROUP_ID is missing")

if not SESSION_STRING:
    raise RuntimeError(
        "SESSION_STRING is missing. "
        "A user session is required for VC playback."
    )


# ============================================================
# TELEGRAM CLIENTS
# ============================================================

# Bot account = commands/buttons
app = Client(
    "epic_india_music_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# User account = Voice Chat playback
user_app = Client(
    "epic_india_music_user",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)

# PyTgCalls attaches to USER client
call_py = PyTgCalls(user_app)


# ============================================================
# STATE
# ============================================================

queue = deque()

current_track = None
is_playing = False

play_lock = asyncio.Lock()

authorized_users = set()

downloads = Path("downloads")
downloads.mkdir(exist_ok=True)


# ============================================================
# AUTH STORAGE
# ============================================================

def load_auth():
    global authorized_users

    try:
        if AUTH_FILE.exists():
            data = json.loads(AUTH_FILE.read_text())
            authorized_users = set(int(x) for x in data)
    except Exception as e:
        print("AUTH LOAD ERROR:", e)
        authorized_users = set()


def save_auth():
    try:
        AUTH_FILE.write_text(
            json.dumps(sorted(list(authorized_users)), indent=2)
        )
    except Exception as e:
        print("AUTH SAVE ERROR:", e)


load_auth()


# ============================================================
# HELPERS
# ============================================================

async def is_group_admin(user_id: int) -> bool:
    try:
        member = await app.get_chat_member(GROUP_ID, user_id)

        return member.status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
        )

    except Exception:
        return False


async def is_authorized(user_id: int) -> bool:
    if user_id in authorized_users:
        return True

    return await is_group_admin(user_id)


async def ensure_group(message: Message) -> bool:
    if not message.chat:
        return False

    if message.chat.id != GROUP_ID:
        await message.reply_text(
            "❌ This command can only be used in the music group."
        )
        return False

    return True


async def get_user_name(user_id: int) -> str:
    try:
        user = await app.get_users(user_id)

        if user.username:
            return f"@{user.username}"

        return user.first_name or str(user_id)

    except Exception:
        return str(user_id)


# ============================================================
# YOUTUBE SEARCH
# ============================================================

def youtube_search(query: str):
    import yt_dlp

    # Direct URL
    if query.startswith("http://") or query.startswith("https://"):
        return {
            "title": query,
            "url": query,
            "webpage_url": query,
            "duration": 0,
            "thumbnail": None,
        }

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        result = ydl.extract_info(
            f"ytsearch1:{query}",
            download=False,
        )

    entries = result.get("entries") or []

    if not entries:
        return None

    item = entries[0]

    return {
        "title": item.get("title") or "Unknown",
        "url": item.get("webpage_url") or item.get("url"),
        "webpage_url": item.get("webpage_url") or item.get("url"),
        "duration": item.get("duration") or 0,
        "thumbnail": item.get("thumbnail"),
    }


async def search_youtube(query: str):
    loop = asyncio.get_running_loop()

    return await loop.run_in_executor(
        None,
        youtube_search,
        query,
    )


# ============================================================
# TIME FORMAT
# ============================================================

def format_duration(seconds):
    try:
        seconds = int(seconds)
    except Exception:
        return "Unknown"

    if seconds <= 0:
        return "Unknown"

    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)

    if h:
        return f"{h}:{m:02d}:{s:02d}"

    return f"{m}:{s:02d}"


# ============================================================
# PLAYBACK
# ============================================================

async def play_track(track):
    global current_track, is_playing

    current_track = track
    is_playing = True

    title = track["title"]
    url = track["url"]
    video = track.get("video", False)

    try:
        print("PLAYING:", title)

        if video:
            stream = MediaStream(url)
        else:
            stream = MediaStream(
                url,
                video_flags=MediaStream.Flags.IGNORE,
            )

        await call_py.play(
            GROUP_ID,
            stream,
        )

        print("VC PLAY SUCCESS:", title)

    except Exception as e:
        print("PLAY ERROR:", repr(e))

        is_playing = False
        current_track = None

        try:
            await app.send_message(
                GROUP_ID,
                f"❌ **Playback failed**\n\n"
                f"🎵 `{title}`\n"
                f"⚠️ `{str(e)[:500]}`",
            )
        except Exception:
            pass

        await play_next()


async def play_next():
    global current_track, is_playing

    async with play_lock:

        if queue:
            next_track = queue.popleft()
            await play_track(next_track)

        else:
            current_track = None
            is_playing = False

            try:
                await call_py.leave_call(GROUP_ID)
            except Exception:
                pass


# ============================================================
# STREAM END
# ============================================================

@call_py.on_update(tgcall_filters.stream_end())
async def stream_end_handler(_, update: StreamEnded):
    global current_track, is_playing

    print("STREAM ENDED:", update.chat_id)

    if update.chat_id != GROUP_ID:
        return

    current_track = None
    is_playing = False

    await play_next()


# ============================================================
# REQUEST MESSAGE
# ============================================================

async def send_request_card(message: Message, track, position=None):

    title = track["title"]
    duration = format_duration(track.get("duration", 0))

    text = (
        "🎵 **EPIC INDIA MUSIC BOT**\n\n"
        f"🎶 **{title}**\n"
        f"⏱ `{duration}`\n"
    )

    if position is not None:
        text += f"📌 Queue Position: `{position}`\n"

    text += f"\n👤 Requested by {await get_user_name(message.from_user.id)}"

    await message.reply_text(
        text,
        disable_web_page_preview=True,
    )


# ============================================================
# /start
# ============================================================

@app.on_message(filters.command("start"))
async def start_command(_, message: Message):

    await message.reply_text(
        "🎧 **EPIC INDIA MUSIC BOT**\n\n"
        "🎵 `/play song name` - Play audio\n"
        "🎬 `/vplay song name` - Play video\n"
        "⏭ `/skip` - Skip current\n"
        "⏹ `/stop` - Stop playback\n"
        "⏸ `/pause` - Pause\n"
        "▶️ `/resume` - Resume\n\n"
        "🔐 Admin commands:\n"
        "`/auth user_id`\n"
        "`/unauth user_id`\n"
        "`/reload`\n"
        "`/purge`"
    )


# ============================================================
# /play
# ============================================================

@app.on_message(filters.command("play"))
async def play_command(_, message: Message):

    if not await ensure_group(message):
        return

    if not message.from_user:
        return

    if not await is_authorized(message.from_user.id):
        await message.reply_text(
            "❌ You are not authorized to use the music player."
        )
        return

    if len(message.command) < 2:
        await message.reply_text(
            "🎵 Usage:\n`/play song name or YouTube URL`"
        )
        return

    query = message.text.split(None, 1)[1].strip()

    wait = await message.reply_text(
        "🔎 Searching..."
    )

    try:
        track = await search_youtube(query)

        if not track:
            await wait.edit_text(
                "❌ Song not found."
            )
            return

        track["video"] = False
        track["requested_by"] = message.from_user.id

        if is_playing:
            queue.append(track)

            position = len(queue)

            await wait.delete()

            await send_request_card(
                message,
                track,
                position,
            )

        else:
            await wait.edit_text(
                f"🎵 **Starting:**\n{track['title']}"
            )

            await play_track(track)

    except Exception as e:
        print("PLAY COMMAND ERROR:", repr(e))

        await wait.edit_text(
            f"❌ Error:\n`{str(e)[:700]}`"
        )


# ============================================================
# /vplay
# ============================================================

@app.on_message(filters.command("vplay"))
async def vplay_command(_, message: Message):

    if not await ensure_group(message):
        return

    if not message.from_user:
        return

    if not await is_authorized(message.from_user.id):
        await message.reply_text(
            "❌ You are not authorized to use the music player."
        )
        return

    if len(message.command) < 2:
        await message.reply_text(
            "🎬 Usage:\n`/vplay video name or YouTube URL`"
        )
        return

    query = message.text.split(None, 1)[1].strip()

    wait = await message.reply_text(
        "🔎 Searching video..."
    )

    try:
        track = await search_youtube(query)

        if not track:
            await wait.edit_text(
                "❌ Video not found."
            )
            return

        track["video"] = True
        track["requested_by"] = message.from_user.id

        if is_playing:
            queue.append(track)

            await wait.delete()

            await send_request_card(
                message,
                track,
                len(queue),
            )

        else:
            await wait.edit_text(
                f"🎬 **Starting video:**\n{track['title']}"
            )

            await play_track(track)

    except Exception as e:
        print("VPLAY ERROR:", repr(e))

        await wait.edit_text(
            f"❌ Error:\n`{str(e)[:700]}`"
        )


# ============================================================
# /open
# ============================================================

@app.on_message(filters.command("open"))
async def open_command(_, message: Message):

    if not await ensure_group(message):
        return

    if not message.from_user:
        return

    if not await is_authorized(message.from_user.id):
        return

    if len(message.command) < 2:
        await message.reply_text(
            "Usage:\n`/open YouTube_URL`"
        )
        return

    url = message.text.split(None, 1)[1].strip()

    track = await search_youtube(url)

    if not track:
        await message.reply_text(
            "❌ Could not open that URL."
        )
        return

    track["video"] = False
    track["requested_by"] = message.from_user.id

    if is_playing:
        queue.append(track)

        await message.reply_text(
            f"📌 Added to queue:\n"
            f"**{track['title']}**\n"
            f"Position: `{len(queue)}`"
        )

    else:
        await play_track(track)

        await message.reply_text(
            f"▶️ Playing:\n**{track['title']}**"
        )


# ============================================================
# /skip
# ============================================================

@app.on_message(filters.command("skip"))
async def skip_command(_, message: Message):

    global current_track, is_playing

    if not await ensure_group(message):
        return

    if not message.from_user:
        return

    if not await is_authorized(message.from_user.id):
        return

    if not is_playing:
        await message.reply_text(
            "❌ Nothing is playing."
        )
        return

    try:
        await call_py.leave_call(GROUP_ID)
    except Exception:
        pass

    current_track = None
    is_playing = False

    await message.reply_text(
        "⏭ **Skipped!**"
    )

    await asyncio.sleep(0.5)

    await play_next()


# ============================================================
# /stop
# ============================================================

@app.on_message(filters.command("stop"))
async def stop_command(_, message: Message):

    global current_track, is_playing

    if not await ensure_group(message):
        return

    if not message.from_user:
        return

    if not await is_authorized(message.from_user.id):
        return

    queue.clear()

    current_track = None
    is_playing = False

    try:
        await call_py.leave_call(GROUP_ID)
    except Exception as e:
        print("LEAVE CALL:", repr(e))

    await message.reply_text(
        "⏹ **Playback stopped.**\n"
        "🗑 Queue cleared."
    )


# ============================================================
# /pause
# ============================================================

@app.on_message(filters.command("pause"))
async def pause_command(_, message: Message):

    if not await ensure_group(message):
        return

    if not message.from_user:
        return

    if not await is_authorized(message.from_user.id):
        return

    try:
        await call_py.pause(GROUP_ID)

        await message.reply_text(
            "⏸ **Paused.**"
        )

    except Exception as e:
        await message.reply_text(
            f"❌ Pause failed:\n`{str(e)[:500]}`"
        )


# ============================================================
# /resume
# ============================================================

@app.on_message(filters.command("resume"))
async def resume_command(_, message: Message):

    if not await ensure_group(message):
        return

    if not message.from_user:
        return

    if not await is_authorized(message.from_user.id):
        return

    try:
        await call_py.resume(GROUP_ID)

        await message.reply_text(
            "▶️ **Resumed.**"
        )

    except Exception as e:
        await message.reply_text(
            f"❌ Resume failed:\n`{str(e)[:500]}`"
        )


# ============================================================
# /auth
# ============================================================

@app.on_message(filters.command("auth"))
async def auth_command(_, message: Message):

    if not await ensure_group(message):
        return

    if not message.from_user:
        return

    if not await is_group_admin(message.from_user.id):
        await message.reply_text(
            "❌ Only group admins can authorize users."
        )
        return

    if len(message.command) < 2:
        await message.reply_text(
            "Usage:\n`/auth USER_ID`"
        )
        return

    try:
        user_id = int(message.command[1])
    except ValueError:
        await message.reply_text(
            "❌ Invalid user ID."
        )
        return

    authorized_users.add(user_id)
    save_auth()

    await message.reply_text(
        f"✅ User `{user_id}` authorized."
    )


# ============================================================
# /unauth
# ============================================================

@app.on_message(filters.command("unauth"))
async def unauth_command(_, message: Message):

    if not await ensure_group(message):
        return

    if not message.from_user:
        return

    if not await is_group_admin(message.from_user.id):
        await message.reply_text(
            "❌ Only group admins can remove authorization."
        )
        return

    if len(message.command) < 2:
        await message.reply_text(
            "Usage:\n`/unauth USER_ID`"
        )
        return

    try:
        user_id = int(message.command[1])
    except ValueError:
        await message.reply_text(
            "❌ Invalid user ID."
        )
        return

    authorized_users.discard(user_id)
    save_auth()

    await message.reply_text(
        f"✅ User `{user_id}` unauthorized."
    )


# ============================================================
# /reload
# ============================================================

@app.on_message(filters.command("reload"))
async def reload_command(_, message: Message):

    if not await ensure_group(message):
        return

    if not message.from_user:
        return

    if not await is_group_admin(message.from_user.id):
        return

    load_auth()

    await message.reply_text(
        "🔄 **Authorization database reloaded.**"
    )


# ============================================================
# /purge
# ============================================================

@app.on_message(filters.command("purge"))
async def purge_command(_, message: Message):

    if not await ensure_group(message):
        return

    if not message.from_user:
        return

    if not await is_group_admin(message.from_user.id):
        return

    queue.clear()

    global current_track, is_playing

    current_track = None
    is_playing = False

    try:
        await call_py.leave_call(GROUP_ID)
    except Exception:
        pass

    await message.reply_text(
        "🧹 **Queue purged and VC stopped.**"
    )


# ============================================================
# /queue
# ============================================================

@app.on_message(filters.command("queue"))
async def queue_command(_, message: Message):

    if not await ensure_group(message):
        return

    if not queue:
        await message.reply_text(
            "📭 Queue is empty."
        )
        return

    lines = ["🎵 **Current Queue**\n"]

    for i, track in enumerate(queue, start=1):
        title = track["title"]

        if len(title) > 50:
            title = title[:47] + "..."

        lines.append(
            f"`{i}.` {title}"
        )

        if i >= 15:
            lines.append(
                f"\n...and {len(queue) - 15} more."
            )
            break

    await message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# CALLBACKS
# ============================================================

@app.on_callback_query(filters.regex("^skip$"))
async def skip_callback(_, query: CallbackQuery):

    if not query.from_user:
        return

    if not await is_authorized(query.from_user.id):
        await query.answer(
            "Not authorized.",
            show_alert=True,
        )
        return

    try:
        await call_py.leave_call(GROUP_ID)
    except Exception:
        pass

    global current_track, is_playing

    current_track = None
    is_playing = False

    await query.answer(
        "Skipped!"
    )

    await play_next()


@app.on_callback_query(filters.regex("^stop$"))
async def stop_callback(_, query: CallbackQuery):

    if not query.from_user:
        return

    if not await is_authorized(query.from_user.id):
        await query.answer(
            "Not authorized.",
            show_alert=True,
        )
        return

    queue.clear()

    global current_track, is_playing

    current_track = None
    is_playing = False

    try:
        await call_py.leave_call(GROUP_ID)
    except Exception:
        pass

    await query.answer(
        "Stopped!"
    )


# ============================================================
# STARTUP
# ============================================================

async def startup():

    print("=" * 60)
    print("EPIC INDIA MUSIC BOT")
    print("=" * 60)

    print("Starting BOT client...")
    await app.start()

    print("Starting USER client...")
    await user_app.start()

    me = await user_app.get_me()

    print(
        f"VC USER: {me.first_name} "
        f"(@{me.username if me.username else 'no_username'})"
    )

    print("Starting PyTgCalls...")
    await call_py.start()

    print("PyTgCalls started successfully.")
    print(f"GROUP_ID: {GROUP_ID}")
    print(f"AUTHORIZED USERS: {len(authorized_users)}")

    print("=" * 60)
    print("BOT IS ONLINE")
    print("=" * 60)


async def shutdown():

    print("Stopping bot...")

    try:
        await call_py.leave_call(GROUP_ID)
    except Exception:
        pass

    try:
        await call_py.stop()
    except Exception:
        pass

    try:
        await user_app.stop()
    except Exception:
        pass

    try:
        await app.stop()
    except Exception:
        pass

    print("Bot stopped.")


# ============================================================
# MAIN
# ============================================================

async def main():

    await startup()

    try:
        # Keep process alive.
        await asyncio.Event().wait()

    except KeyboardInterrupt:
        pass

    finally:
        await shutdown()


if __name__ == "__main__":
    asyncio.run(main())
