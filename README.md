# So you wanna Self-Host an APOD Bot?

Well.. here you go!

Welcome to the Lunar.bot APOD project. This is a robust, caching, and media-compressing Discord bot that brings the cosmos directly to your servers and DMs using the official NASA API. It is fully modernized to support NASA's recent WordPress backend migration and handles unexpected API outages gracefully.

## Features

* **Smart Local Caching:** Prevents hammering the NASA API. The bot downloads and caches the daily drop locally to save bandwidth and rate limits.
* **Built-in Media Compression:** Automatically compresses large files (like high-res images or massive MP4s) using `ffmpeg` and `Pillow` to fit within Discord's strict 24MB upload limit.
* **User App Support:** Configured to be installed directly to a user's Discord profile, allowing the `/apod` command to be used in any DM or server.
* **Graceful API & Media Fallbacks:** If the NASA API is undergoing maintenance, or a file is absolutely too large even after aggressive compression (or hosted externally like YouTube), the bot safely falls back to providing user-friendly error embeds or direct source URLs without crashing.
* **Automated Cleanup & Self-Healing:** Keeps your host storage clean by automatically deleting cache files older than 30 days. It also auto-prunes dead or permission-locked Discord channels from its dispatch registry to prevent 403 errors.
* **Multi-Message Rendering:** Splitting text embeds and native video files to ensure Discord renders the layout perfectly every time.

## Prerequisites

Before you start, make sure your host machine has the following installed:

* **Python 3.11+**
* **FFmpeg:** Required for heavy video compression.
* Debian/Ubuntu: `sudo apt update && sudo apt install ffmpeg`
* Arch Linux: `sudo pacman -S ffmpeg`


* A **Discord Bot Token** (Obtained from the Discord Developer Portal)
* A **NASA API Key** (Obtained for free at api.nasa.gov. While the new WordPress endpoint doesn't strictly require this, it is highly recommended for legacy routing stability).

## Installation

1. **Prepare your environment:**
Ensure `main.py` and `requirements.txt` are in your project directory.
2. **Install Python dependencies:**
```bash
pip install -r requirements.txt

```


3. **Configure your environment variables:**
Create a file named `.env` in the root directory and add your keys:
```env
DISCORD_TOKEN=your_discord_bot_token_here
NASA_API_KEY=your_nasa_api_key_here

```


4. **Run the bot:**
```bash
python main.py

```


*Note: The bot will automatically generate the `config.json`, `cache.json`, and the `cached/` directory on its first run.*

## Available Commands

### Public Commands

* `/apod` - Fetches the current Astronomy Picture of the Day.
* `/random` - Pulls a random APOD from the NASA archives.
* `/date` - Fetches the APOD for a specific custom date (Format: DD/MM/YYYY).

### Admin & Owner Commands

* `/apod_setup` - (Requires Manage Channels) Sets the current channel for the automated daily 8:00 AM (Europe/Berlin) drop.
* `/status` - (Owner only) Displays detailed infrastructure and bot diagnostics, including ping, CPU/RAM usage, host system metrics, and cache footprint.
* `/sync` - (Owner only) Cleans, re-registers, and syncs all application slash commands globally and across all guilds. Ideal for cleaning up ghost commands from previous bot iterations.

## Important Developer Notes

* **Privileged Intents:** Because this bot uses 100% Slash Commands, you do **not** need the Privileged Message Content Intent enabled in the Developer Portal. This bypasses Discord's 100-server verification lock.
* **Dev Server Hardcoding:** The `/sync` command is registered exclusively to a Developer Server to bypass rate limits. Make sure to update the `DEV_GUILD_ID` and `OWNER_ID` variables at the top of `main.py` to match your Discord IDs.
* **User App Installation:** To allow users to install this bot directly to their profiles, ensure you enable "User Install" in the Discord Developer Portal under the "Installation" tab.

---

*Diagnostics and Bot Architecture by Lun4r.sh*
