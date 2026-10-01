import os
import json
import time
import asyncio
from pathlib import Path
from collections import deque

import yt_dlp
from PIL import Image, ImageDraw, ImageFont

try:
    import turtle
    TURTLE_AVAILABLE = True
except Exception:
    TURTLE_AVAILABLE = False

from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.enums import ChatMemberStatus

# py-tgcalls 2.0+ API
from pytgcalls import PyTgCalls
from pytgcalls.types import AudioPiped, VideoFileStream


# ============================================================
# CONFIG
# ============================================================

API_ID = int(os.getenv("API_ID", "12345678"))
API_HASH = os.getenv("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")

GROUP_ID = int(
    os.getenv("GROUP_ID", "-1001234567890")
)

BRAND = "@epic_india"

DATA_DIR = Path("epic_bot_data")
DATA_DIR.mkdir(exist_ok=True)

DOWNLOAD_DIR = DATA_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

THUMB_DIR = DATA_DIR / "thumbnails"
THUMB_DIR.mkdir(exist_ok=True)

LOADING_DIR = DATA_DIR / "loading"
LOADING_DIR.mkdir(exist_ok=True)

AUTH_FILE = DATA_DIR / "authorized.json"


# ============================================================
# PYROGRAM + PYTGCALLS
# ============================================================

app = Client(
    "epic_india_music_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

call_py = PyTgCalls(app)


# ============================================================
# STATE
# ============================================================

queue = deque()

current_track = None
is_playing = False

authorized_users = set()
admin_cache = set()

play_lock = asyncio.Lock()


# ============================================================
# AUTH STORAGE
# ============================================================

def load_auth():
    global authorized_users

    if not AUTH_FILE.exists():
        authorized_users = set()
        save_auth()
        return

    try:
        data = json.loads(
            AUTH_FILE.read_text(
                encoding="utf-8"
            )
        )

        authorized_users = {
            int(x) for x in data
        }

    except Exception:
        authorized_users = set()


def save_auth():
    AUTH_FILE.write_text(
        json.dumps(
            sorted(authorized_users),
            indent=2
        ),
        encoding="utf-8",
    )


load_auth()


# ============================================================
# HELPERS
# ============================================================

def allowed_group(message: Message):
    return (
        message.chat
        and message.chat.id == GROUP_ID
    )


def user_name(user):
    if not user:
        return "Unknown"

    name = " ".join(
        x for x in [
            user.first_name,
            user.last_name
        ]
        if x
    ).strip()

    return (
        name
        or user.username
        or str(user.id)
    )


def user_tag(user):
    if user.username:
        return f"@{user.username}"

    return str(user.id)


async def refresh_admins():
    global admin_cache

    admin_cache = set()

    try:
        async for member in app.get_chat_members(
            GROUP_ID
        ):
            if member.status in (
                ChatMemberStatus.OWNER,
                ChatMemberStatus.ADMINISTRATOR,
            ):
                admin_cache.add(
                    member.user.id
                )

    except Exception as e:
        print(
            "Admin refresh error:",
            e
        )

    return admin_cache


async def is_admin(user_id: int):
    if user_id in admin_cache:
        return True

    try:
        member = await app.get_chat_member(
            GROUP_ID,
            user_id
        )

        if member.status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
        ):
            admin_cache.add(user_id)
            return True

    except Exception:
        pass

    return False


async def is_authorized(user_id: int):
    if await is_admin(user_id):
        return True

    return user_id in authorized_users


async def check_auth(message: Message):
    if not message.from_user:
        return False

    if await is_authorized(
        message.from_user.id
    ):
        return True

    await message.reply_text(
        "❌ You are not authorized.\n\n"
        f"{BRAND}"
    )

    return False


async def resolve_user(value):
    value = value.strip()

    if value.startswith("@"):
        value = value[1:]

    if value.isdigit():
        try:
            return await app.get_users(
                int(value)
            )
        except Exception:
            return None

    try:
        return await app.get_users(value)
    except Exception:
        return None


# ============================================================
# FONT
# ============================================================

def get_font(size):
    fonts = [
        "/usr/share/fonts/truetype/dejavu/"
        "DejaVuSans-Bold.ttf",

        "/usr/share/fonts/truetype/liberation2/"
        "LiberationSans-Bold.ttf",

        "arial.ttf",
    ]

    for font in fonts:
        try:
            return ImageFont.truetype(
                font,
                size
            )
        except Exception:
            continue

    return ImageFont.load_default()


# ============================================================
# TURTLE / PIL LOADING GRAPHIC
# ============================================================

def create_pil_loading(path):
    width = 900
    height = 500

    img = Image.new(
        "RGB",
        (width, height),
        "#070711"
    )

    draw = ImageDraw.Draw(img)

    for y in range(height):
        r = int(
            7 + (y / height) * 20
        )
        g = 7
        b = int(
            17 + (y / height) * 35
        )

        draw.line(
            [(0, y), (width, y)],
            fill=(r, g, b)
        )

    for radius, color in [
        (190, "#8A2BE2"),
        (145, "#FF1493"),
        (100, "#00D9FF"),
    ]:
        draw.ellipse(
            (
                450 - radius,
                250 - radius,
                450 + radius,
                250 + radius
            ),
            outline=color,
            width=5
        )

    big = get_font(55)
    mid = get_font(35)
    small = get_font(18)

    def center(text, y, font, color):
        box = draw.textbbox(
            (0, 0),
            text,
            font=font
        )

        w = box[2] - box[0]

        draw.text(
            ((width - w) / 2, y),
            text,
            font=font,
            fill=color
        )

    center(
        "EPIC",
        175,
        big,
        "#FFFFFF"
    )

    center(
        "INDIA",
        245,
        mid,
        "#FF1493"
    )

    center(
        "PREPARING YOUR REQUEST...",
        315,
        small,
        "#00D9FF"
    )

    img.save(path)

    return path


def create_turtle_graphic(path):
    """
    Turtle attempt.
    Falls back to PIL automatically on headless VPS.
    """

    if not TURTLE_AVAILABLE:
        return create_pil_loading(path)

    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()

        canvas = tk.Canvas(
            root,
            width=900,
            height=500,
            bg="#070711",
            highlightthickness=0
        )

        canvas.pack()

        screen = turtle.TurtleScreen(
            canvas
        )

        screen.bgcolor("#070711")

        t = turtle.RawTurtle(
            screen
        )

        t.hideturtle()
        t.speed(0)
        t.penup()

        for radius, color in [
            (190, "#8A2BE2"),
            (145, "#FF1493"),
            (100, "#00D9FF"),
        ]:
            t.goto(
                0,
                -radius
            )

            t.pendown()
            t.pencolor(color)
            t.circle(radius)
            t.penup()

        t.goto(0, 35)
        t.color("#FFFFFF")

        t.write(
            "EPIC",
            align="center",
            font=(
                "Arial",
                48,
                "bold"
            )
        )

        t.goto(0, -30)
        t.color("#FF1493")

        t.write(
            "INDIA",
            align="center",
            font=(
                "Arial",
                34,
                "bold"
            )
        )

        t.goto(0, -90)
        t.color("#00D9FF")

        t.write(
            "PREPARING YOUR REQUEST...",
            align="center",
            font=(
                "Arial",
                14,
                "bold"
            )
        )

        root.update()

        ps = canvas.postscript(
            colormode="color"
        )

        ps_file = path.with_suffix(
            ".ps"
        )

        ps_file.write_text(
            ps,
            encoding="latin-1"
        )

        root.destroy()

        try:
            img = Image.open(ps_file)
            img.save(path)
            ps_file.unlink(
                missing_ok=True
            )

            return path

        except Exception:
            ps_file.unlink(
                missing_ok=True
            )

            return create_pil_loading(
                path
            )

    except Exception as e:
        print(
            "Turtle unavailable:",
            e
        )

        return create_pil_loading(
            path
        )


LOADING_IMAGE = (
    LOADING_DIR /
    "loading.png"
)

if not LOADING_IMAGE.exists():
    create_turtle_graphic(
        LOADING_IMAGE
    )


# ============================================================
# REQUEST THUMBNAIL
# ============================================================

def make_thumbnail(
    title,
    requester,
    name
):
    width = 1280
    height = 720

    img = Image.new(
        "RGB",
        (width, height),
        "#08080F"
    )

    draw = ImageDraw.Draw(img)

    # Background
    for y in range(height):
        ratio = y / height

        r = int(
            8 + 25 * ratio
        )

        g = int(
            8 + 4 * ratio
        )

        b = int(
            20 + 30 * ratio
        )

        draw.line(
            [(0, y), (width, y)],
            fill=(r, g, b)
        )

    # Neon circles
    draw.ellipse(
        (-150, -150, 450, 450),
        fill="#17102E",
        outline="#8A2BE2",
        width=8
    )

    draw.ellipse(
        (850, 330, 1450, 930),
        fill="#211025",
        outline="#FF1493",
        width=8
    )

    logo_font = get_font(90)
    title_font = get_font(46)
    user_font = get_font(32)
    small_font = get_font(26)

    def center(
        text,
        y,
        font,
        color
    ):
        box = draw.textbbox(
            (0, 0),
            text,
            font=font
        )

        w = box[2] - box[0]

        draw.text(
            (
                (width - w) / 2,
                y
            ),
            text,
            font=font,
            fill=color
        )

    center(
        BRAND,
        80,
        logo_font,
        "#FFFFFF"
    )

    draw.rounded_rectangle(
        (
            120,
            220,
            1160,
            228
        ),
        radius=4,
        fill="#00D9FF"
    )

    center(
        title[:65],
        280,
        title_font,
        "#FFFFFF"
    )

    center(
        f"Requested by {requester[:45]}",
        385,
        user_font,
        "#00D9FF"
    )

    center(
        name[:45],
        445,
        small_font,
        "#C9C9D8"
    )

    center(
        "MUSIC REQUEST",
        555,
        small_font,
        "#FF1493"
    )

    output = (
        THUMB_DIR /
        f"request_{int(time.time() * 1000)}.jpg"
    )

    img.save(
        output,
        "JPEG",
        quality=94
    )

    return output


# ============================================================
# YOUTUBE SEARCH
# ============================================================

def youtube_search(query):
    if (
        "youtube.com" in query
        or "youtu.be" in query
    ):
        search = query
    else:
        search = (
            f"ytsearch1:{query}"
        )

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        info = ydl.extract_info(
            search,
            download=False
        )

    if not info:
        return None

    if "entries" in info:
        entries = list(
            info["entries"]
        )

        if not entries:
            return None

        info = entries[0]

    return {
        "title": info.get(
            "title",
            query
        ),
        "url": info.get(
            "webpage_url"
        ),
    }


# ============================================================
# DOWNLOAD
# ============================================================

def download_media(
    url,
    video=False
):
    if video:
        output = str(
            DOWNLOAD_DIR /
            "%(id)s.%(ext)s"
        )

        options = {
            "format": (
                "bestvideo[height<=720]"
                "+"
                "bestaudio/"
                "best[height<=720]/best"
            ),
            "outtmpl": output,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
        }

    else:
        output = str(
            DOWNLOAD_DIR /
            "%(id)s.%(ext)s"
        )

        options = {
            "format": "bestaudio/best",
            "outtmpl": output,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=True
        )

        prepared = Path(
            ydl.prepare_filename(info)
        )

    if video:
        candidates = [
            prepared.with_suffix(".mp4"),
            prepared,
            prepared.with_suffix(".mkv"),
            prepared.with_suffix(".webm"),
        ]
    else:
        candidates = [
            prepared.with_suffix(".mp3"),
            prepared.with_suffix(".m4a"),
            prepared.with_suffix(".webm"),
            prepared,
        ]

    for file in candidates:
        if file.exists():
            return str(file)

    stem = prepared.stem

    for file in DOWNLOAD_DIR.glob(
        f"{stem}*"
    ):
        if file.is_file():
            return str(file)

    raise FileNotFoundError(
        "Downloaded file not found."
    )


# ============================================================
# LOADING
# ============================================================

async def loading_animation(text):
    try:
        msg = await app.send_photo(
            GROUP_ID,
            str(LOADING_IMAGE),
            caption=(
                f"⏳ **{text}**\n\n"
                f"{BRAND}"
            )
        )

        await asyncio.sleep(3)

        try:
            await msg.delete()
        except Exception:
            pass

    except Exception as e:
        print(
            "Loading error:",
            e
        )


# ============================================================
# TRACK PLAY
# ============================================================

async def play_track(track):
    global current_track
    global is_playing

    current_track = track
    is_playing = True

    try:
        media = await asyncio.to_thread(
            download_media,
            track["url"],
            track["video"]
        )

        track["file"] = media

        # py-tgcalls 2.0+ API
        if track["video"]:
            stream = VideoFileStream(media)
        else:
            stream = AudioPiped(media)

        # py-tgcalls 2.0+ play API
        await call_py.play(
            GROUP_ID,
            stream
        )

        print(
            "Playing:",
            track["title"]
        )

    except Exception as e:
        print(
            "Playback error:",
            repr(e)
        )

        is_playing = False
        current_track = None

        await cleanup_track(
            track
        )

        try:
            await app.send_message(
                GROUP_ID,
                (
                    "❌ **Playback failed.**\n\n"
                    f"🎵 `{track['title']}`\n\n"
                    f"{BRAND}"
                )
            )
        except Exception:
            pass

        await play_next()


async def play_next():
    global is_playing
    global current_track

    async with play_lock:
        if is_playing:
            return

        if not queue:
            current_track = None
            return

        track = queue.popleft()

        await play_track(
            track
        )


# ============================================================
# CLEANUP
# ============================================================

async def cleanup_track(track):
    file = track.get("file")

    if not file:
        return

    try:
        path = Path(file)

        if path.exists():
            path.unlink()

    except Exception as e:
        print(
            "Cleanup error:",
            e
        )


# ============================================================
# STREAM END HANDLER (py-tgcalls 2.0+)
# ============================================================

@call_py.on_stream_end()
async def on_stream_end(client, stream):
    global current_track
    global is_playing

    try:
        if stream.chat_id != GROUP_ID:
            return
    except Exception:
        pass

    old = current_track

    current_track = None
    is_playing = False

    if old:
        await cleanup_track(
            old
        )

    await play_next()


# ============================================================
# REQUEST CARD
# ============================================================

async def send_request_card(
    track
):
    thumb = make_thumbnail(
        track["title"],
        track["username"],
        track["name"]
    )

    if track["video"]:
        icon = "🎬"
    else:
        icon = "🎵"

    caption = (
        f"{icon} **{track['title']}**\n\n"
        f"👤 Requested by: "
        f"{track['username']}\n"
        f"📝 Name: {track['name']}\n\n"
        f"{BRAND}"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⏭ Skip",
                    callback_data="music_skip"
                ),
                InlineKeyboardButton(
                    "⏹ Stop",
                    callback_data="music_stop"
                ),
            ]
        ]
    )

    msg = await app.send_photo(
        GROUP_ID,
        str(thumb),
        caption=caption,
        reply_markup=keyboard
    )

    track["reply_message_id"] = msg.id

    return msg


# ============================================================
# /PLAY
# ============================================================

@app.on_message(
    filters.command(
        "play",
        prefixes="/"
    )
)
async def play_command(
    client,
    message: Message
):
    if not allowed_group(message):
        return

    if not await check_auth(
        message
    ):
        return

    parts = message.text.split(
        maxsplit=1
    )

    if len(parts) < 2:
        await message.reply_text(
            "Usage: `/play song name`\n\n"
            f"{BRAND}"
        )
        return

    query = parts[1].strip()

    # Delete original /play command.
    try:
        await message.delete()
    except Exception:
        pass

    # 3-second graphic.
    await loading_animation(
        "Preparing your song..."
    )

    try:
        result = await asyncio.to_thread(
            youtube_search,
            query
        )

        if not result:
            await client.send_message(
                GROUP_ID,
                (
                    "❌ Song not found.\n\n"
                    f"{BRAND}"
                )
            )
            return

        user = message.from_user

        track = {
            "title": result["title"],
            "url": result["url"],
            "video": False,
            "user_id": user.id,
            "username": user_tag(user),
            "name": user_name(user),
            "request_message_id": message.id,
            "reply_message_id": None,
            "file": None,
        }

        await send_request_card(
            track
        )

        queue.append(track)

        if not is_playing:
            await play_next()

    except Exception as e:
        print(
            "/play error:",
            repr(e)
        )

        await client.send_message(
            GROUP_ID,
            (
                "❌ **Request failed.**\n\n"
                f"`{e}`\n\n"
                f"{BRAND}"
            )
        )


# ============================================================
# /VPLAY
# ============================================================

@app.on_message(
    filters.command(
        "vplay",
        prefixes="/"
    )
)
async def vplay_command(
    client,
    message: Message
):
    if not allowed_group(message):
        return

    if not await check_auth(
        message
    ):
        return

    parts = message.text.split(
        maxsplit=1
    )

    if len(parts) < 2:
        await message.reply_text(
            "Usage: `/vplay video name`\n\n"
            f"{BRAND}"
        )
        return

    query = parts[1].strip()

    # Delete original /vplay command.
    try:
        await message.delete()
    except Exception:
        pass

    await loading_animation(
        "Preparing your video..."
    )

    try:
        result = await asyncio.to_thread(
            youtube_search,
            query
        )

        if not result:
            await client.send_message(
                GROUP_ID,
                (
                    "❌ Video not found.\n\n"
                    f"{BRAND}"
                )
            )
            return

        user = message.from_user

        track = {
            "title": result["title"],
            "url": result["url"],
            "video": True,
            "user_id": user.id,
            "username": user_tag(user),
            "name": user_name(user),
            "request_message_id": message.id,
            "reply_message_id": None,
            "file": None,
        }

        await send_request_card(
            track
        )

        queue.append(track)

        if not is_playing:
            await play_next()

    except Exception as e:
        print(
            "/vplay error:",
            repr(e)
        )

        await client.send_message(
            GROUP_ID,
            (
                "❌ **Video request failed.**\n\n"
                f"`{e}`\n\n"
                f"{BRAND}"
            )
        )


# ============================================================
# START
# ============================================================

async def main():
    async with app:
        print("🎵 Epic India Music Bot started!")
        await idle()


if __name__ == "__main__":
    asyncio.run(main())

