import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import os
import json
import datetime
import zoneinfo
import psutil
import time
import hashlib
import logging
import sys
import subprocess
import asyncio
import random
import re
import html
import platform
from urllib.parse import urlparse
from PIL import Image
from dotenv import load_dotenv

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("Lunar.Bot")
logging.getLogger("discord").setLevel(logging.ERROR)

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
NASA_API_KEY = os.getenv("NASA_API_KEY")

DEV_GUILD_ID = 1209920360565182506
DEV_GUILD = discord.Object(id=DEV_GUILD_ID)
OWNER_ID = 1390299961500897322
CONFIG_FILE = "config.json"
CACHE_FILE = "cache.json"
CACHE_DIR = "cached"
START_TIME = time.time()

# Limits & Cache Window
TARGET_DISCORD_SIZE = 24 * 1024 * 1024
MAX_DOWNLOAD_SIZE = 150 * 1024 * 1024
CACHE_RETENTION_DAYS = 30  # Expanded cache window allowing significantly more images to persist

NASA_LOGO_URL = "https://cdn.freebiesupply.com/logos/large/2x/nasa-2-logo-png-transparent.png"
SPACE_COLORS = [0x0B3D91, 0x1B1B3A, 0x4B0082, 0x8A2BE2, 0xFF4500, 0xFFD700, 0x00CED1, 0x2F4F4F]

if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)
    logger.info(f"Created cache directory: {CACHE_DIR}")


class APODBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.tree.on_error = self.on_app_command_error
        await self.tree.sync(guild=DEV_GUILD)
        await self.tree.sync()
        logger.info("Global commands synced. Multi-Guild logic active & duplicates resolved.")
        daily_apod_task.start()

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            error_msg = str(error)
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(error_msg, ephemeral=True)
                else:
                    await interaction.followup.send(error_msg, ephemeral=True)
            except discord.HTTPException:
                pass
        else:
            cmd_name = interaction.command.name if interaction.command else 'Unknown'
            logger.error(f"Ignoring exception in command '{cmd_name}': {error}")


bot = APODBot()


# --- Utility Functions ---

def load_json(filename):
    try:
        with open(filename, "r") as f:
            data = json.load(f)
            if "channels" not in data:
                data = {"channels": {}}
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"channels": {}}


def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)


def get_daily_color():
    day_index = datetime.date.today().toordinal() % len(SPACE_COLORS)
    return SPACE_COLORS[day_index]


def get_random_date_str():
    min_date = datetime.date(1995, 6, 16)
    max_date = datetime.date.today()
    random_days = random.randint(0, (max_date - min_date).days)
    random_date = min_date + datetime.timedelta(days=random_days)
    return random_date.strftime("%Y-%m-%d")


def get_cache_metrics():
    total_size = 0
    total_files = 0
    if os.path.exists(CACHE_DIR):
        for entry in os.scandir(CACHE_DIR):
            if entry.is_file():
                total_files += 1
                total_size += entry.stat().st_size
    return total_files, total_size


def cleanup_old_cache():
    now = time.time()
    deleted_count = 0
    retained_window = CACHE_RETENTION_DAYS * 86400
    for filename in os.listdir(CACHE_DIR):
        filepath = os.path.join(CACHE_DIR, filename)
        if os.path.isfile(filepath):
            if os.stat(filepath).st_mtime < now - retained_window:
                try:
                    os.remove(filepath)
                    deleted_count += 1
                    logger.info(f"Deleted expired cache asset: {filename}")
                except Exception as e:
                    logger.error(f"Failed to delete {filename}: {e}")
    if deleted_count > 0:
        logger.info(f"Cache maintenance complete: purged {deleted_count} stale assets older than {CACHE_RETENTION_DAYS} days.")


def compress_media(filepath: str, ext: str) -> str | None:
    compressed_path = filepath.replace(ext, f"_compressed{ext}")

    if os.path.exists(compressed_path) and os.path.getsize(compressed_path) <= TARGET_DISCORD_SIZE:
        return compressed_path

    logger.info(f"File exceeds target size threshold ({TARGET_DISCORD_SIZE / (1024 * 1024):.0f}MB). Initiating compression for {filepath}...")
    try:
        if ext.lower() in ['.jpg', '.jpeg', '.png']:
            img = Image.open(filepath)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            quality = 85
            img.save(compressed_path, "JPEG", quality=quality)
            while os.path.getsize(compressed_path) > TARGET_DISCORD_SIZE and quality > 10:
                quality -= 10
                img.save(compressed_path, "JPEG", quality=quality)
        elif ext.lower() in ['.mp4', '.mov', '.webm']:
            cmd = [
                "ffmpeg", "-y", "-i", filepath,
                "-vf", "scale=-2:480",
                "-r", "24",
                "-vcodec", "libx264",
                "-crf", "35",
                "-preset", "fast",
                "-acodec", "aac",
                "-b:a", "64k",
                compressed_path
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                logger.error(f"FFMPEG Error Output:\n{result.stderr}")
                return None

        if os.path.exists(compressed_path):
            new_size = os.path.getsize(compressed_path)
            if new_size <= TARGET_DISCORD_SIZE:
                logger.info(f"Compression successful. Optimized footprint: {new_size / (1024 * 1024):.2f} MB")
                return compressed_path
            else:
                logger.warning(
                    f"Compression unsuccessful: Resulting file ({new_size / (1024 * 1024):.2f} MB) exceeds limit.")
                os.remove(compressed_path)
                return None
        else:
            logger.warning("FFMPEG execution halted without generating target output.")
            return None
    except Exception as e:
        logger.error(f"Compression pipeline failure: {e}")
        if os.path.exists(compressed_path):
            os.remove(compressed_path)
        return None


async def safe_send(interaction: discord.Interaction = None, channel: discord.TextChannel = None,
                    original_url: str = None, **kwargs):
    try:
        if interaction:
            await interaction.followup.send(**kwargs)
        elif channel:
            kwargs.pop('ephemeral', None)
            await channel.send(**kwargs)
    except discord.errors.Forbidden as e:
        logger.error(f"403 Forbidden: Insufficient dispatch permissions for channel {channel.id if channel else 'Unknown'}.")
        raise e  
    except discord.errors.HTTPException as e:
        if e.status == 413:
            logger.warning("Discord 413 Payload Too Large. Transmitting fallback source hyperlink.")
            if "file" in kwargs:
                del kwargs["file"]
            kwargs["content"] = f"**Today's APOD payload exceeds Discord upload limits even after compression pipeline optimization!**\nDirect high-resolution resource link: {original_url}"
            if interaction:
                await interaction.followup.send(**kwargs)
            elif channel:
                kwargs.pop('ephemeral', None)
                await channel.send(**kwargs)
        else:
            logger.error(f"Discord API transmission failure: {e}")


async def download_media(url: str, date_str: str) -> str | None:
    if not url or "youtube.com" in url or "youtu.be" in url or "vimeo.com" in url:
        return None
    parsed_url = urlparse(url)
    ext = os.path.splitext(parsed_url.path)[1]
    if not ext:
        ext = ".jpg"

    filepath = os.path.join(CACHE_DIR, f"{date_str}{ext}")
    compressed_path = filepath.replace(ext, f"_compressed{ext}")

    if os.path.exists(compressed_path):
        return compressed_path
    if os.path.exists(filepath):
        if os.path.getsize(filepath) <= TARGET_DISCORD_SIZE:
            return filepath
        else:
            compressed_result = compress_media(filepath, ext)
            if compressed_result:
                return compressed_result

    logger.info(f"Retrieving external resource: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    custom_timeout = aiohttp.ClientTimeout(total=1800)

    try:
        async with aiohttp.ClientSession(headers=headers, timeout=custom_timeout) as session:
            async with session.get(url) as response:
                if response.status == 200:
                    total_size = int(response.headers.get('Content-Length', 0))
                    if total_size > MAX_DOWNLOAD_SIZE:
                        logger.warning(
                            f"Resource exceeds safety ceiling ({total_size / 1024 / 1024:.2f} MB). Aborting download.")
                        return None
                    downloaded_size = 0
                    with open(filepath, 'wb') as f:
                        async for chunk in response.content.iter_chunked(1024 * 512):
                            downloaded_size += len(chunk)
                            f.write(chunk)
                            if total_size > 0:
                                percent = (downloaded_size / total_size) * 100
                                bar_length = 40
                                filled = int(bar_length * (downloaded_size / total_size))
                                bar = '#' * filled + '-' * (bar_length - filled)
                                sys.stdout.write(
                                    f"\r[DOWNLOAD] [{bar}] {percent:.1f}% ({downloaded_size / (1024 * 1024):.2f} MB / {total_size / (1024 * 1024):.2f} MB)")
                            else:
                                sys.stdout.write(f"\r[DOWNLOAD] {downloaded_size / (1024 * 1024):.2f} MB downloaded...")
                            sys.stdout.flush()
                    sys.stdout.write("\n")
                    logger.info(f"Asset persisted to disk: {filepath}")

                    if downloaded_size > TARGET_DISCORD_SIZE:
                        compressed_result = compress_media(filepath, ext)
                        return compressed_result if compressed_result else None
                    return filepath
                else:
                    logger.error(f"Resource download rejected. Gateway returned status {response.status}")
    except Exception as e:
        sys.stdout.write("\n")
        logger.error(f"Exception encountered during asset streaming: {type(e).__name__} - {e}")
        if os.path.exists(filepath):
            os.remove(filepath)
    return None


async def fetch_apod_with_cache(params=None):
    is_standard_call = params is None or ("date" not in params and "count" not in params)

    if is_standard_call:
        cache = load_json(CACHE_FILE)
        today = datetime.date.today().isoformat()
        if cache.get("date") == today:
            return cache.get("data")

    base_url = "https://science.nasa.gov/wp-json/wp/v2/apod-basic"
    url = base_url

    if params and "date" in params:
        date_obj = datetime.datetime.strptime(params["date"], "%Y-%m-%d")
        legacy_date = date_obj.strftime("%y%m%d")
        url = f"{base_url}/{legacy_date}"

    logger.info(f"Querying APOD endpoint: {url}")
    
    max_retries = 3
    base_delay = 2
    
    async with aiohttp.ClientSession() as session:
        for attempt in range(max_retries):
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        data_dict = data[0] if isinstance(data, list) and len(data) > 0 else data

                        if is_standard_call:
                            image_url = data_dict.get("hdurl", data_dict.get("url", ""))
                            url_hash = hashlib.md5(image_url.encode()).hexdigest()
                            save_json(CACHE_FILE, {
                                "date": datetime.date.today().isoformat(),
                                "url_hash": url_hash,
                                "data": data_dict
                            })
                        return data_dict
                    elif response.status == 404:
                        logger.error(f"APOD resource 404 on route: {url}")
                        return None
                    elif response.status in [500, 502, 503, 504]:
                        logger.warning(f"NASA upstream returned HTTP {response.status}. Retry attempt {attempt + 1}/{max_retries} engaged.")
                    else:
                        logger.error(f"NASA gateway terminated request with fatal status {response.status}")
                        return None
            except aiohttp.ClientError as e:
                logger.warning(f"Network transport fault encountered: {e}. Attempt {attempt + 1}/{max_retries} queued.")
            
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.info(f"Backoff delay active: holding for {delay} seconds...")
                await asyncio.sleep(delay)
                
        logger.error("All fetch retry vectors exhausted without successful payload response.")
        return None


class APODView(discord.ui.View):
    def __init__(self, hdurl: str = None, video_url: str = None):
        super().__init__()
        if hdurl:
            self.add_item(
                discord.ui.Button(label="View Full Resolution", url=hdurl, style=discord.ButtonStyle.link))
        if video_url:
            self.add_item(
                discord.ui.Button(label="Open Source URL", url=video_url, style=discord.ButtonStyle.link))


async def build_apod_message(data):
    if isinstance(data, list):
        data = data[0]

    title = data.get("title", "Astronomy Picture of the Day")
    raw_desc = data.get("explanation", "")
    
    if not raw_desc:
        desc = ""
    else:
        raw_desc = re.sub(r'<(br|p)\s*/?>', '\n\n', raw_desc, flags=re.IGNORECASE)
        clean_desc = re.sub(r'<[^>]+>', '', raw_desc)
        desc = html.unescape(clean_desc).strip()
        
        desc = re.sub(r'Your Sky Surprise:.*', '', desc, flags=re.IGNORECASE)
        desc = re.sub(r'Tomorrow\'s picture:.*', '', desc, flags=re.IGNORECASE)
        desc = desc.strip()
        
    if not desc or "NASA Science" in title:
        title = "APOD Currently Unavailable"
        desc = (
            "NASA's API is currently undergoing a backend migration and failed to return "
            "valid data for this date.\n\n"
            "You can still view today's Astronomy Picture of the Day directly on their official site:\n"
            "🔗 **[science.nasa.gov/apod](https://science.nasa.gov/apod/)**"
        )
        media_type = "error"
        url = "https://science.nasa.gov/apod/"
        hdurl = None
    else:
        media_type = data.get("media_type")
        url = data.get("url")
        hdurl = data.get("hdurl")

    date = data.get("date", "Unknown Date")

    if len(desc) > 4000:
        desc = desc[:3997] + "..."

    embed = discord.Embed(title=title, description=desc, color=get_daily_color())
    embed.set_author(name="NASA API | APOD", icon_url=NASA_LOGO_URL)
    embed.set_footer(text=f"Date: {date} | Bot by Lun4r.sh")

    download_url = hdurl if media_type == "image" and hdurl else url
    local_filepath = None

    if media_type != "error":
        local_filepath = await download_media(download_url, date)
        
    if local_filepath and media_type == "image":
        embed.set_image(url=f"attachment://{os.path.basename(local_filepath)}")
    elif not local_filepath and media_type == "image":
        logger.info("Serving APOD via direct URL links.")
        embed.set_image(url=download_url)

    return embed, local_filepath, url, media_type, download_url


# --- Custom Checks ---

def is_owner():
    def predicate(interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            raise app_commands.CheckFailure("You do not have permission to execute this administrative directive.")
        return True
    return app_commands.check(predicate)


# --- Commands ---

@bot.tree.command(name="apod", description="Fetches the current Astronomy Picture of the Day.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def get_apod(interaction: discord.Interaction):
    config = load_json(CONFIG_FILE)
    channels = config.get("channels", {})
    guild_id_str = str(interaction.guild_id) if interaction.guild else None
    apod_channel = channels.get(guild_id_str)

    is_ephemeral = interaction.guild is not None and interaction.channel_id != apod_channel
    await interaction.response.defer(ephemeral=is_ephemeral)
    logger.info(f"Command /apod invoked by {interaction.user.name} (Ephemeral: {is_ephemeral})")

    data = await fetch_apod_with_cache()
    if not data:
        await interaction.followup.send("Failed to communicate with NASA APOD endpoints.", ephemeral=is_ephemeral)
        return

    embed, local_filepath, original_url, media_type, hdurl = await build_apod_message(data)
    view = APODView(hdurl=hdurl) if media_type == "image" else APODView(video_url=original_url)
    file = discord.File(local_filepath, filename=os.path.basename(local_filepath)) if local_filepath else None

    if media_type == "video":
        await safe_send(interaction=interaction, embed=embed, view=view, ephemeral=is_ephemeral)
        video_kwargs = {"ephemeral": is_ephemeral}
        if file:
            video_kwargs["file"] = file
            video_kwargs["content"] = "**Today's Featured Media (Video):**"
        else:
            video_kwargs["content"] = f"**Today's Featured Media (Video):**\n{original_url}"
        await safe_send(interaction=interaction, original_url=original_url, **video_kwargs)
    else:
        kwargs = {"embed": embed, "view": view, "ephemeral": is_ephemeral}
        if file:
            kwargs["file"] = file
        await safe_send(interaction=interaction, original_url=original_url, **kwargs)


@bot.tree.command(name="random", description="Fetches a random APOD from the archives.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def random_apod(interaction: discord.Interaction):
    config = load_json(CONFIG_FILE)
    channels = config.get("channels", {})
    guild_id_str = str(interaction.guild_id) if interaction.guild else None
    apod_channel = channels.get(guild_id_str)

    is_ephemeral = interaction.guild is not None and interaction.channel_id != apod_channel
    await interaction.response.defer(ephemeral=is_ephemeral)
    
    date_str = get_random_date_str()
    logger.info(f"Command /random invoked by {interaction.user.name} (Target Date: {date_str})")

    data = await fetch_apod_with_cache(params={"date": date_str})
    if not data:
        await interaction.followup.send("Failed to retrieve archive selection from NASA services.", ephemeral=is_ephemeral)
        return

    embed, local_filepath, original_url, media_type, hdurl = await build_apod_message(data)
    view = APODView(hdurl=hdurl) if media_type == "image" else APODView(video_url=original_url)
    file = discord.File(local_filepath, filename=os.path.basename(local_filepath)) if local_filepath else None

    if media_type == "video":
        await safe_send(interaction=interaction, embed=embed, view=view, ephemeral=is_ephemeral)
        video_kwargs = {"ephemeral": is_ephemeral}
        if file:
            video_kwargs["file"] = file
            video_kwargs["content"] = "**Archived Video Broadcast:**"
        else:
            video_kwargs["content"] = f"**Archived Video Broadcast:**\n{original_url}"
        await safe_send(interaction=interaction, original_url=original_url, **video_kwargs)
    else:
        kwargs = {"embed": embed, "view": view, "ephemeral": is_ephemeral}
        if file:
            kwargs["file"] = file
        await safe_send(interaction=interaction, original_url=original_url, **kwargs)


@bot.tree.command(name="fallback", description="Grabs a random APOD from the archives when today's is unavailable.")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def fallback_apod(interaction: discord.Interaction):
    config = load_json(CONFIG_FILE)
    channels = config.get("channels", {})
    guild_id_str = str(interaction.guild_id) if interaction.guild else None
    apod_channel = channels.get(guild_id_str)

    is_ephemeral = interaction.guild is not None and interaction.channel_id != apod_channel
    await interaction.response.defer(ephemeral=is_ephemeral)
    
    date_str = get_random_date_str()
    logger.info(f"Command /fallback invoked by {interaction.user.name} (Target Date: {date_str})")

    data = await fetch_apod_with_cache(params={"date": date_str})
    if not data:
        await interaction.followup.send("Unable to populate fallback APOD entry from archive repository.", ephemeral=is_ephemeral)
        return

    embed, local_filepath, original_url, media_type, hdurl = await build_apod_message(data)
    view = APODView(hdurl=hdurl) if media_type == "image" else APODView(video_url=original_url)
    file = discord.File(local_filepath, filename=os.path.basename(local_filepath)) if local_filepath else None

    if media_type == "video":
        await safe_send(interaction=interaction, embed=embed, view=view, ephemeral=is_ephemeral)
        video_kwargs = {"ephemeral": is_ephemeral}
        content = "**Primary daily broadcast unreachable. Transmitting historical archive selection:**"
        if not file:
            content += f"\n{original_url}"
        video_kwargs["content"] = content
        if file:
            video_kwargs["file"] = file
        await safe_send(interaction=interaction, original_url=original_url, **video_kwargs)
    else:
        kwargs = {"embed": embed, "view": view, "ephemeral": is_ephemeral}
        kwargs["content"] = "**Primary daily broadcast unreachable. Transmitting historical archive selection:**"
        if file:
            kwargs["file"] = file
        await safe_send(interaction=interaction, original_url=original_url, **kwargs)


@bot.tree.command(name="date", description="Fetches the APOD for a specific date.")
@app_commands.describe(date_str="Format: DD/MM/YYYY (e.g., 16/06/1995)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def date_apod(interaction: discord.Interaction, date_str: str):
    config = load_json(CONFIG_FILE)
    channels = config.get("channels", {})
    guild_id_str = str(interaction.guild_id) if interaction.guild else None
    apod_channel = channels.get(guild_id_str)

    is_ephemeral = interaction.guild is not None and interaction.channel_id != apod_channel
    await interaction.response.defer(ephemeral=is_ephemeral)

    try:
        target_date = datetime.datetime.strptime(date_str, "%d/%m/%Y").date()
    except ValueError:
        await interaction.followup.send("Invalid date format syntax. Please format query as `DD/MM/YYYY` (e.g., 16/06/1995).",
                                        ephemeral=is_ephemeral)
        return

    min_date = datetime.date(1995, 6, 16)
    max_date = datetime.date.today()
    if target_date < min_date or target_date > max_date:
        await interaction.followup.send("Specified timestamp falls outside the accessible historical index (16/06/1995 to present).",
                                        ephemeral=is_ephemeral)
        return

    api_date_str = target_date.strftime("%Y-%m-%d")
    logger.info(f"Command /date invoked by {interaction.user.name} for date parameter: {api_date_str}")

    data = await fetch_apod_with_cache(params={"date": api_date_str})
    if not data:
        await interaction.followup.send(f"Failed to query APOD record for {date_str}.", ephemeral=is_ephemeral)
        return

    embed, local_filepath, original_url, media_type, hdurl = await build_apod_message(data)
    view = APODView(hdurl=hdurl) if media_type == "image" else APODView(video_url=original_url)
    file = discord.File(local_filepath, filename=os.path.basename(local_filepath)) if local_filepath else None

    if media_type == "video":
        await safe_send(interaction=interaction, embed=embed, view=view, ephemeral=is_ephemeral)
        video_kwargs = {"ephemeral": is_ephemeral}
        if file:
            video_kwargs["file"] = file
            video_kwargs["content"] = f"**Recorded Media for {date_str}:**"
        else:
            video_kwargs["content"] = f"**Recorded Media for {date_str}:**\n{original_url}"
        await safe_send(interaction=interaction, original_url=original_url, **video_kwargs)
    else:
        kwargs = {"embed": embed, "view": view, "ephemeral": is_ephemeral}
        if file:
            kwargs["file"] = file
        await safe_send(interaction=interaction, original_url=original_url, **kwargs)


@bot.tree.command(name="apod_setup", description="Sets the daily drop channel (Server only).")
@app_commands.default_permissions(manage_channels=True)
async def setup_apod(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("This command requires execution within an active server guild context.", ephemeral=True)
        return

    config = load_json(CONFIG_FILE)
    channels = config.get("channels", {})
    channels[str(interaction.guild_id)] = interaction.channel_id
    config["channels"] = channels
    save_json(CONFIG_FILE, config)

    logger.info(f"Designated daily dispatch target updated to channel {interaction.channel_id} in guild {interaction.guild.id}")
    await interaction.response.send_message(f"Daily APOD transmissions linked to {interaction.channel.mention}.",
                                            ephemeral=True)


@bot.tree.command(name="status", description="Displays detailed infrastructure and bot diagnostics (Owner only).")
@is_owner()
async def bot_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    # Core process metrics
    proc = psutil.Process(os.getpid())
    proc_memory = proc.memory_info().rss / (1024 ** 2)
    proc_cpu = proc.cpu_percent(interval=0.1)

    # Global host metrics
    total_cpu = psutil.cpu_percent(interval=None)
    cpu_cores = psutil.cpu_count(logical=True)
    virtual_mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')

    # Bot performance & networking
    latency = round(bot.latency * 1000)
    uptime_seconds = int(time.time() - START_TIME)
    uptime_formatted = str(datetime.timedelta(seconds=uptime_seconds))

    # Cache filesystem analysis
    cache_files, cache_bytes = get_cache_metrics()
    cache_mb = cache_bytes / (1024 * 1024)

    # Configuration profile
    config = load_json(CONFIG_FILE)
    active_channels = config.get("channels", {})
    guild_count = len(bot.guilds)
    user_count = sum(guild.member_count or 0 for guild in bot.guilds)

    embed = discord.Embed(
        title="🛰️ Lunar.bot Diagnostic Telemetry",
        color=get_daily_color(),
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_author(name="NASA API | APOD System Diagnostics", icon_url=NASA_LOGO_URL)

    embed.add_field(
        name="⚡ Bot Runtime",
        value=(
            f"**Ping:** `{latency} ms`\n"
            f"**Uptime:** `{uptime_formatted}`\n"
            f"**Guilds:** `{guild_count:,}`\n"
            f"**Total Reach:** `~{user_count:,}` users\n"
            f"**Drop Channels:** `{len(active_channels):,}` configured"
        ),
        inline=True
    )

    embed.add_field(
        name="🖥️ Resource Allocation",
        value=(
            f"**Process RAM:** `{proc_memory:.1f} MB`\n"
            f"**System RAM:** `{virtual_mem.used / (1024 ** 3):.2f}` / `{virtual_mem.total / (1024 ** 3):.2f} GB` ({virtual_mem.percent}%)\n"
            f"**Bot CPU:** `{proc_cpu:.1f}%`\n"
            f"**Host CPU:** `{total_cpu}%` ({cpu_cores} threads)\n"
            f"**Disk Usage:** `{disk.percent}%` free of `{disk.total / (1024 ** 3):.1f} GB`"
        ),
        inline=True
    )

    embed.add_field(
        name="💾 Storage & Cache Engine",
        value=(
            f"**Cached Assets:** `{cache_files:,}` items\n"
            f"**Cache Footprint:** `{cache_mb:.2f} MB`\n"
            f"**Retention Window:** `{CACHE_RETENTION_DAYS}` Days\n"
            f"**Target Size Limit:** `{TARGET_DISCORD_SIZE / (1024 * 1024):.0f} MB`\n"
            f"**Safety Download Ceiling:** `{MAX_DOWNLOAD_SIZE / (1024 * 1024):.0f} MB`"
        ),
        inline=False
    )

    embed.add_field(
        name="⚙️ Environment",
        value=(
            f"**Python:** `v{platform.python_version()}`\n"
            f"**Discord.py:** `v{discord.__version__}`\n"
            f"**OS Platform:** `{platform.system()} {platform.release()}`"
        ),
        inline=True
    )

    embed.set_footer(text="Diagnostics Telemetry | Bot by Lun4r.sh")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="sync", description="Cleans, re-registers, and syncs all application slash commands (Owner only).", guild=DEV_GUILD)
@is_owner()
async def sync_commands(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    logger.info("Executing global and guild synchronization routine...")

    bot.tree.clear_commands(guild=None)
    await bot.tree.sync(guild=None)

    for guild in bot.guilds:
        if guild.id != DEV_GUILD_ID:
            try:
                bot.tree.clear_commands(guild=guild)
                await bot.tree.sync(guild=guild)
            except discord.HTTPException as e:
                logger.warning(f"Failed clearing cached tree for guild {guild.id}: {e}")

    bot.tree.copy_global_to(guild=DEV_GUILD)
    synced_dev = await bot.tree.sync(guild=DEV_GUILD)
    synced_global = await bot.tree.sync()

    logger.info(f"Sync complete: {len(synced_dev)} dev commands, {len(synced_global)} global commands registered.")
    await interaction.followup.send(
        content=(
            f"✅ **Command synchronization finished!**\n"
            f"• Dev Guild Scope: `{len(synced_dev)}` commands synced\n"
            f"• Global Scope: `{len(synced_global)}` commands synced\n"
            f"• Residual trees purged across `{len(bot.guilds)}` guilds."
        ),
        ephemeral=True
    )


# --- Tasks ---

tz = zoneinfo.ZoneInfo("Europe/Berlin")
schedule_time = datetime.time(hour=8, minute=0, tzinfo=tz)


@tasks.loop(time=schedule_time)
async def daily_apod_task():
    logger.info("Initiating scheduled daily APOD broadcast dispatch...")
    cleanup_old_cache()

    config = load_json(CONFIG_FILE)
    channels = config.get("channels", {})

    if not channels:
        logger.warning("No destinations recorded in dispatch registry.")
        return

    data = await fetch_apod_with_cache()
    if not data:
        logger.error("Daily dispatch aborted: Failed to resolve APOD payload.")
        return

    embed, local_filepath, original_url, media_type, hdurl = await build_apod_message(data)
    success_count = 0
    channels_to_remove = []

    for guild_id_str, channel_id in list(channels.items()):
        channel = bot.get_channel(channel_id)
        if not channel: 
            channels_to_remove.append(guild_id_str)
            continue

        view = APODView(hdurl=hdurl) if media_type == "image" else APODView(video_url=original_url)
        file = discord.File(local_filepath, filename=os.path.basename(local_filepath)) if local_filepath else None

        try:
            if media_type == "video":
                await safe_send(channel=channel, embed=embed, view=view)
                video_kwargs = {"content": "**Today's Featured Media (Video):**" if file else f"**Today's Featured Media (Video):**\n{original_url}"}
                if file:
                    video_kwargs["file"] = file
                await safe_send(channel=channel, original_url=original_url, **video_kwargs)
            else:
                kwargs = {"embed": embed, "view": view}
                if file:
                    kwargs["file"] = file
                await safe_send(channel=channel, original_url=original_url, **kwargs)

            success_count += 1
            await asyncio.sleep(0.5)
            
        except discord.errors.Forbidden:
            logger.warning(f"Revoking guild destination {guild_id_str} due to missing permissions (403).")
            channels_to_remove.append(guild_id_str)
        except Exception as e:
            logger.error(f"Transmission error encountered on target channel {channel_id}: {e}")

    if channels_to_remove:
        for gid in channels_to_remove:
            channels.pop(gid, None)
        config["channels"] = channels
        save_json(CONFIG_FILE, config)
        logger.info(f"Pruned {len(channels_to_remove)} unreachable destinations from configuration.")

    logger.info(f"Daily APOD dispatch concluded: Successfully published to {success_count}/{len(channels) + len(channels_to_remove)} designated channels.")


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"Connected to {len(bot.guilds)} guilds.")


bot.run(TOKEN, log_handler=None)