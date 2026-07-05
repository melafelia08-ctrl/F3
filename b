
# SODOBOT_Advanced.py
import os
import sys
import json
import time
import shutil
import zipfile
import logging
import asyncio
import threading
import subprocess
import re
import platform
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.constants import ParseMode

# ═══════════════════════════════════════════════════════
# 🔧 CONFIGURATION
# ═══════════════════════════════════════════════════════
TOKEN = "8880154979:AAFbR8XG2JO5bbmd7-AdYMn-XqcOsW5mpJk"
OWNER_ID = 5140999748
PASSWORD = "ジェイ"
DATA_FILE = "bot_data.json"
DOWNLOADS_DIR = "downloads"
LOGS_DIR = "logs"
BACKUP_DIR = "backups"
MAX_SCRIPTS_PER_USER = 10
AUTO_RESTART_DEFAULT = True
MAX_LOG_SIZE = 4096

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════
# 📊 DATA STRUCTURES & PERSISTENCE
# ═══════════════════════════════════════════════════════
def load_data():
    default = {
        "approved_users": {},
        "banned_users": [],
        "user_settings": {},
        "script_history": {},
        "broadcast_log": []
    }
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                default.update(data)
        except Exception:
            pass
    return default

def save_data():
    try:
        serializable = {
            "approved_users": bot_data["approved_users"],
            "banned_users": bot_data["banned_users"],
            "user_settings": bot_data["user_settings"],
            "script_history": bot_data.get("script_history", {}),
            "broadcast_log": bot_data.get("broadcast_log", [])[-50:]
        }
        with open(DATA_FILE, 'w') as f:
            json.dump(serializable, f, indent=2)
    except Exception as e:
        logger.error(f"Save error: {e}")

bot_data = load_data()
active_processes = {}

# ═══════════════════════════════════════════════════════
# 🌐 FLASK KEEP-ALIVE
# ═══════════════════════════════════════════════════════
app = Flask(__name__)

@app.route('/')
def home():
    total_procs = sum(len([p for p in procs if p["proc"].poll() is None]) for procs in active_processes.values())
    return f"""<h1>🤖 SODOBOT Advanced</h1>
    <p>Status: <b>Online</b></p>
    <p>Active Scripts: {total_procs}</p>
    <p>Approved Users: {len(bot_data['approved_users'])}</p>
    <p>Uptime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>"""

@app.route('/health')
def health():
    return {"status": "ok", "timestamp": time.time()}, 200

def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

def keep_alive():
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

# ═══════════════════════════════════════════════════════
# 📝 LOGGING
# ═══════════════════════════════════════════════════════
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, 'bot.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# 🔍 UTILITY FUNCTIONS (No PSUTIL needed)
# ═══════════════════════════════════════════════════════
def get_stdlib_modules():
    if sys.version_info >= (3, 10):
        return sys.stdlib_module_names
    else:
        import distutils.sysconfig as sysconfig
        std_lib = sysconfig.get_python_lib(standard_lib=True)
        return set(os.listdir(std_lib))

STDLIB_MODULES = get_stdlib_modules()

def is_authorized(user_id):
    uid = str(user_id)
    return uid == str(OWNER_ID) or uid in bot_data["approved_users"]

def is_owner(user_id):
    return str(user_id) == str(OWNER_ID)

def is_banned(user_id):
    return str(user_id) in bot_data.get("banned_users", [])

def format_uptime(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds/60)}m {int(seconds%60)}s"
    elif seconds < 86400:
        return f"{int(seconds/3600)}h {int((seconds%3600)/60)}m"
    else:
        return f"{int(seconds/86400)}d {int((seconds%86400)/3600)}h"

def get_system_info():
    try:
        cpu_count = os.cpu_count() or 'N/A'
        disk = os.statvfs('.')
        total_disk = disk.f_blocks * disk.f_frsize
        free_disk = disk.f_bavail * disk.f_frsize
        used_disk = total_disk - free_disk
        disk_percent = (used_disk / total_disk) * 100 if total_disk > 0 else 0
        disk_info = f"{disk_percent:.1f}% ({used_disk//(1024**3)}GB/{total_disk//(1024**3)}GB)"
        
        return {
            "cpu_count": cpu_count,
            "disk": disk_info,
            "platform": platform.platform(),
            "python": sys.version.split()[0]
        }
    except:
        return {
            "cpu_count": "N/A",
            "disk": "N/A",
            "platform": platform.platform(),
            "python": sys.version.split()[0]
        }

def scan_python_dependencies(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        imports = re.findall(r'^\s*(?:from|import)\s+([a-zA-Z0-9_]+)', content, re.MULTILINE)
        return list(set(imports))
    except Exception as e:
        logger.error(f"Error scanning python deps: {e}")
        return []

def scan_js_dependencies(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        requires = re.findall(r'require\([\'"](.+?)[\'"]\)', content)
        imports = re.findall(r'from\s+[\'"](.+?)[\'"]', content)
        return list(set(requires + imports))
    except Exception as e:
        logger.error(f"Error scanning js deps: {e}")
        return []

PIP_MAPPINGS = {
    "telegram": "python-telegram-bot",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "selenium": "selenium",
    "requests": "requests",
    "flask": "flask",
    "django": "django",
    "pandas": "pandas",
    "numpy": "numpy",
    "matplotlib": "matplotlib",
    "aiohttp": "aiohttp",
    "discord": "discord.py",
    "pytube": "pytube",
    "yt_dlp": "yt-dlp",
}

async def install_deps(update, msg, deps, pkg_mgr):
    if not deps:
        return
    
    if pkg_mgr == "pip":
        deps = [dep for dep in deps if dep not in STDLIB_MODULES]
    
    if not deps:
        return

    await msg.edit_text(f"📦 Found {len(deps)} dependencies. Installing via {pkg_mgr}...")
    
    installed = []
    failed = []
    
    for dep in deps:
        pkg_name = PIP_MAPPINGS.get(dep, dep) if pkg_mgr == "pip" else dep
        
        try:
            if pkg_mgr == "pip":
                cmd = [sys.executable, "-m", "pip", "install", pkg_name, "--no-cache-dir", "--quiet"]
            else:
                cmd = ["npm", "install", pkg_name, "--silent"]
            
            result = subprocess.run(cmd, capture_output=True, timeout=120, text=True)
            if result.returncode == 0:
                installed.append(pkg_name)
            else:
                failed.append(pkg_name)
        except subprocess.TimeoutExpired:
            failed.append(pkg_name)
        except Exception as e:
            failed.append(pkg_name)
    
    summary = f"📦 Dependencies: ✅ {len(installed)} installed"
    if failed:
        summary += f" | ❌ {len(failed)} failed ({', '.join(failed[:3])})"
    await msg.edit_text(summary)

# ═══════════════════════════════════════════════════════
# 🔄 PROCESS MONITOR (Auto-restart & cleanup)
# ═══════════════════════════════════════════════════════
def process_monitor():
    while True:
        try:
            for user_id, procs in list(active_processes.items()):
                for i, p in enumerate(procs):
                    if p["proc"].poll() is not None:
                        uptime = time.time() - p["start_time"]
                        
                        if p.get("auto_restart", False) and uptime > 5:
                            logger.info(f"Auto-restarting {p['name']} for user {user_id}")
                            try:
                                log_file = open(p["log_path"], "a", encoding="utf-8", errors="ignore")
                                log_file.write(f"\n{'='*50}\n🔄 Auto-restart at {datetime.now()}\n{'='*50}\n")
                                log_file.close()
                                
                                log_file = open(p["log_path"], "a", encoding="utf-8", errors="ignore")
                                proc = subprocess.Popen(
                                    p["run_cmd"],
                                    stdout=log_file,
                                    stderr=log_file,
                                    cwd=p["work_dir"],
                                    env=p["env"],
                                    text=True,
                                    start_new_session=True
                                )
                                procs[i] = {**p, "proc": proc, "start_time": time.time(), "pid": proc.pid}
                            except Exception as e:
                                logger.error(f"Failed to restart {p['name']}: {e}")
            time.sleep(10)
        except Exception as e:
            logger.error(f"Monitor error: {e}")
            time.sleep(30)

def start_monitor():
    t = threading.Thread(target=process_monitor, daemon=True)
    t.start()

# ═══════════════════════════════════════════════════════
# 🎨 KEYBOARDS
# ═══════════════════════════════════════════════════════
def get_main_keyboard(user_id):
    owner_extra = []
    if is_owner(user_id):
        owner_extra = [
            [KeyboardButton("👥 User Manager"), KeyboardButton("📢 Broadcast")],
            [KeyboardButton("⚙️ Bot Settings"), KeyboardButton("📦 Backup")],
        ]
    
    keyboard = [
        [KeyboardButton("📁 Upload Files"), KeyboardButton("📂 My Scripts")],
        [KeyboardButton("⚡ Bot Speed"), KeyboardButton("📊 Statistics")],
        [KeyboardButton("📩 View Logs"), KeyboardButton("📞 Contact Owner")],
        [KeyboardButton("🛑 Stop Script"), KeyboardButton("🔄 Restart Script")],
        [KeyboardButton("🖥️ System Info"), KeyboardButton("🗑️ Delete Script")],
    ] + owner_extra + [[KeyboardButton("❌ Close Menu")]]
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_inline_main():
    keyboard = [
        [InlineKeyboardButton("📁 Upload", callback_data="menu_upload"),
         InlineKeyboardButton("📂 Scripts", callback_data="menu_scripts")],
        [InlineKeyboardButton("⚡ Speed", callback_data="menu_speed"),
         InlineKeyboardButton("📊 Stats", callback_data="menu_stats")],
        [InlineKeyboardButton("🖥️ System", callback_data="menu_system"),
         InlineKeyboardButton("📞 Contact", callback_data="menu_contact")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_scripts_keyboard(user_id, action="stop"):
    procs = active_processes.get(str(user_id), [])
    keyboard = []
    for i, p in enumerate(procs):
        status = "🟢" if p["proc"].poll() is None else "🔴"
        emoji = "🛑" if action == "stop" else "🔄" if action == "restart" else "📩" if action == "logs" else "🗑️"
        keyboard.append([InlineKeyboardButton(
            f"{emoji} {status} {p['name']}", 
            callback_data=f"{action}_{i}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_main")])
    return InlineKeyboardMarkup(keyboard)

def get_user_management_keyboard():
    keyboard = []
    for uid, info in bot_data["approved_users"].items():
        if isinstance(info, dict):
            name = info.get("name", "Unknown")
        else:
            name = "User"
        keyboard.append([InlineKeyboardButton(
            f"👤 {name} ({uid})", callback_data=f"userinfo_{uid}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_main")])
    return InlineKeyboardMarkup(keyboard)

# ═══════════════════════════════════════════════════════
# 🤖 BOT HANDLERS
# ═══════════════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)
    
    if is_banned(user_id):
        await update.message.reply_text("🚫 You have been banned from using this bot.")
        return
    
    if not is_authorized(user_id):
        await update.message.reply_text(
            "🔐 *Access Restricted*\n\n"
            "Please enter the password to use this bot.\n"
            "Type the password below:",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    bot_data["approved_users"][user_id] = {
        "name": user.first_name,
        "username": user.username,
        "joined": datetime.now().isoformat()
    }
    save_data()
    
    proc_count = len([p for p in active_processes.get(user_id, []) if p["proc"].poll() is None])
    
    welcome_text = (
        f"〽️ *Welcome, {user.first_name}!* 💞\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 *User ID:* `{user.id}`\n"
        f"✳️ *Username:* @{user.username if user.username else 'Not set'}\n"
        f"🔰 *Status:* {'👑 Owner' if is_owner(user_id) else '✅ Approved'}\n"
        f"📁 *Active Scripts:* {proc_count}/{MAX_SCRIPTS_PER_USER}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 *Host & Run Python/JS Scripts 24/7*\n\n"
        f"📥 Upload `.py`, `.js`, or `.zip` files\n"
        f"🔧 Auto dependency installation\n"
        f"🔄 Auto-restart on crash\n"
        f"👇 *Use the menu below or type commands*"
    )
    
    inline_keyboard = [
        [InlineKeyboardButton("📢 Updates Channel", url="https://t.me/chutxmm"),
         InlineKeyboardButton("📞 Support", url="https://t.me/S0DOHU")],
    ]
    
    await update.message.reply_text(
        welcome_text, 
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard(user_id)
    )
    await update.message.reply_text(
        "🎯 *Quick Actions:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_inline_main()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    data = query.data
    
    if not is_authorized(user_id):
        await query.edit_message_text("🔐 Access Restricted.")
        return
    
    if data == "back_main":
        await query.edit_message_text(
            "🎯 *Quick Actions:*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_inline_main()
        )
        return
    
    if data == "menu_upload":
        await query.edit_message_text("📤 Send your `.py`, `.js`, or `.zip` file to upload and run.")
        return
    
    if data == "menu_scripts":
        procs = active_processes.get(user_id, [])
        if not procs:
            await query.edit_message_text("📂 You have no scripts. Upload a file to get started!")
            return
        msg = "📂 *Your Scripts:*\n\n"
        for p in procs:
            status = "🟢 Running" if p["proc"].poll() is None else "🔴 Stopped"
            uptime = format_uptime(time.time() - p["start_time"]) if p["proc"].poll() is None else "N/A"
            msg += f"• `{p['name']}`\n  Status: {status} | Uptime: {uptime}\n"
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
        return
    
    if data == "menu_speed":
        start_time = time.time()
        await query.edit_message_text("⚡ Checking speed...")
        latency = round((time.time() - start_time) * 1000, 2)
        await query.edit_message_text(f"⚡ *Bot Latency:* `{latency}ms`\n📡 Status: Excellent", parse_mode=ParseMode.MARKDOWN)
        return
    
    if data == "menu_stats":
        total_active = sum(len([p for p in procs if p["proc"].poll() is None]) for procs in active_processes.values())
        total_users = len(bot_data["approved_users"])
        msg = (
            "📊 *Bot Statistics*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Active Scripts: `{total_active}`\n"
            f"👥 Total Users: `{total_users}`\n"
            f"📦 Scripts Run (Total): `{len(bot_data.get('script_history', {}))}`\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
        return
    
    if data == "menu_system":
        sys_info = get_system_info()
        msg = (
            "🖥️ *System Information*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💾 CPU Cores: `{sys_info['cpu_count']}`\n"
            f"💿 Disk Usage: `{sys_info['disk']}`\n"
            f"🐍 Python: `{sys_info['python']}`\n"
            f"🌐 Platform: `{sys_info['platform']}`\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
        return
    
    if data == "menu_contact":
        await query.edit_message_text(
            "📞 *Contact Owner*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "👤 Telegram: @S0DOHU\n"
            "📢 Channel: @chutxmm\n"
            "━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if data.startswith("stop_"):
        index = int(data.split("_")[1])
        procs = active_processes.get(user_id, [])
        if 0 <= index < len(procs):
            p = procs[index]
            if p["proc"].poll() is None:
                try:
                    p["proc"].terminate()
                    try:
                        p["proc"].wait(timeout=5)
                    except:
                        p["proc"].kill()
                    await query.edit_message_text(f"✅ Stopped `{p['name']}`", parse_mode=ParseMode.MARKDOWN)
                except Exception as e:
                    await query.edit_message_text(f"❌ Error: {str(e)}")
            else:
                await query.edit_message_text(f"ℹ️ `{p['name']}` already stopped.", parse_mode=ParseMode.MARKDOWN)
            procs.pop(index)
        return
    
    if data.startswith("restart_"):
        index = int(data.split("_")[1])
        procs = active_processes.get(user_id, [])
        if 0 <= index < len(procs):
            p = procs[index]
            if p["proc"].poll() is None:
                p["proc"].terminate()
                try:
                    p["proc"].wait(timeout=5)
                except:
                    p["proc"].kill()
            
            try:
                log_file = open(p["log_path"], "a", encoding="utf-8", errors="ignore")
                log_file.write(f"\n{'='*50}\n🔄 Manual restart at {datetime.now()}\n{'='*50}\n")
                proc = subprocess.Popen(
                    p["run_cmd"],
                    stdout=log_file,
                    stderr=log_file,
                    cwd=p["work_dir"],
                    env=p["env"],
                    text=True,
                    start_new_session=True
                )
                procs[index] = {**p, "proc": proc, "start_time": time.time(), "pid": proc.pid}
                success_msg = f"✅ Restarted `{p['name']}` (PID: {proc.pid})"
                await query.edit_message_text(success_msg, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                await query.edit_message_text(f"❌ Restart error: {str(e)}")
        return
    
    if data.startswith("logs_"):
        index = int(data.split("_")[1])
        procs = active_processes.get(user_id, [])
        if 0 <= index < len(procs):
            p = procs[inde