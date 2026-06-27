
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
            p = procs[index]
            if os.path.exists(p["log_path"]):
                with open(p["log_path"], "r", encoding="utf-8", errors="ignore") as f:
                    log_content = f.read()[-MAX_LOG_SIZE:]
                keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
                await query.edit_message_text(
                    f"📝 *Logs for* `{p['name']}`:\n```\n{log_content}\n```",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await query.edit_message_text("❌ No log file found.")
        return
    
    if data.startswith("delete_"):
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
            if os.path.exists(p.get("work_dir", "")) and p["work_dir"] != DOWNLOADS_DIR:
                shutil.rmtree(p["work_dir"], ignore_errors=True)
            if os.path.exists(p["log_path"]):
                os.remove(p["log_path"])
            procs.pop(index)
            await query.edit_message_text(f"🗑️ Deleted `{p['name']}` and cleaned up.", parse_mode=ParseMode.MARKDOWN)
        return
    
    if data.startswith("userinfo_") and is_owner(user_id):
        target_uid = data.split("_")[1]
        info = bot_data["approved_users"].get(target_uid, {})
        procs = active_processes.get(target_uid, [])
        active = len([p for p in procs if p["proc"].poll() is None])
        
        keyboard = [
            [InlineKeyboardButton("🚫 Ban User", callback_data=f"ban_{target_uid}"),
             InlineKeyboardButton("❌ Remove Access", callback_data=f"remove_{target_uid}")],
            [InlineKeyboardButton("🛑 Stop All Scripts", callback_data=f"stopall_{target_uid}")],
            [InlineKeyboardButton("🔙 Back", callback_data="menu_users")]
        ]
        
        msg = (
            f"👤 *User Info*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 ID: `{target_uid}`\n"
            f"📝 Name: {info.get('name', 'Unknown')}\n"
            f"📎 Username: @{info.get('username', 'N/A')}\n"
            f"📅 Joined: {info.get('joined', 'N/A')}\n"
            f"🟢 Active Scripts: {active}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    if data == "menu_users" and is_owner(user_id):
        await query.edit_message_text(
            "👥 *User Management*\nSelect a user:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_user_management_keyboard()
        )
        return
    
    if data.startswith("ban_") and is_owner(user_id):
        target_uid = data.split("_")[1]
        bot_data["banned_users"].append(target_uid)
        if target_uid in bot_data["approved_users"]:
            del bot_data["approved_users"][target_uid]
        if target_uid in active_processes:
            for p in active_processes[target_uid]:
                if p["proc"].poll() is None:
                    p["proc"].terminate()
            active_processes[target_uid] = []
        save_data()
        await query.edit_message_text(f"🚫 User `{target_uid}` has been banned.", parse_mode=ParseMode.MARKDOWN)
        return
    
    if data.startswith("remove_") and is_owner(user_id):
        target_uid = data.split("_")[1]
        if target_uid in bot_data["approved_users"]:
            del bot_data["approved_users"][target_uid]
        save_data()
        await query.edit_message_text(f"❌ Access removed for `{target_uid}`.", parse_mode=ParseMode.MARKDOWN)
        return
    
    if data.startswith("stopall_") and is_owner(user_id):
        target_uid = data.split("_")[1]
        if target_uid in active_processes:
            for p in active_processes[target_uid]:
                if p["proc"].poll() is None:
                    p["proc"].terminate()
            active_processes[target_uid] = []
        await query.edit_message_text(f"🛑 All scripts stopped for `{target_uid}`.", parse_mode=ParseMode.MARKDOWN)
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = str(update.effective_user.id)
    user = update.effective_user
    
    if is_banned(user_id):
        await update.message.reply_text("🚫 You are banned.")
        return
    
    if not is_authorized(user_id):
        if text == PASSWORD:
            bot_data["approved_users"][user_id] = {
                "name": user.first_name,
                "username": user.username,
                "joined": datetime.now().isoformat()
            }
            save_data()
            await update.message.reply_text(
                "✅ *Password correct!*\nYou are now approved.\nSend /start to begin.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            if text and not update.message.document:
                await update.message.reply_text("🔐 Access Restricted. Enter the correct password.")
        return
    
    if text == "📁 Upload Files":
        await update.message.reply_text(
            "📤 *Upload Files*\n\n"
            "Send your file directly here:\n"
            "• `.py` - Python script\n"
            "• `.js` - JavaScript file\n"
            "• `.zip` - Archive with `main.py` or `index.js`\n\n"
            "🔧 Dependencies will be auto-installed!",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif text == "📂 My Scripts":
        procs = active_processes.get(user_id, [])
        if not procs:
            await update.message.reply_text("📂 You have no scripts. Upload a file to get started!")
        else:
            msg = "📂 *Your Scripts:*\n\n"
            for i, p in enumerate(procs):
                status = "🟢 Running" if p["proc"].poll() is None else "🔴 Stopped"
                uptime = format_uptime(time.time() - p["start_time"]) if p["proc"].poll() is None else "N/A"
                msg += f"*{i+1}. {p['name']}*\n"
                msg += f"   Status: {status}\n"
                msg += f"   Uptime: {uptime}\n"
                msg += f"   Auto-restart: {'✅' if p.get('auto_restart') else '❌'}\n\n"
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    
    elif text == "⚡ Bot Speed":
        start_time = time.time()
        msg = await update.message.reply_text("⚡ Checking speed...")
        latency = round((time.time() - start_time) * 1000, 2)
        await msg.edit_text(
            f"⚡ *Bot Latency:* `{latency}ms`\n"
            f"📡 Status: {'Excellent' if latency < 100 else 'Good' if latency < 300 else 'Slow'}",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif text == "📊 Statistics":
        total_active = sum(len([p for p in procs if p["proc"].poll() is None]) for procs in active_processes.values())
        total_users = len(bot_data["approved_users"])
        total_history = len(bot_data.get("script_history", {}))
        
        msg = (
            "📊 *Bot Statistics*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Active Scripts: `{total_active}`\n"
            f"👥 Approved Users: `{total_users}`\n"
            f"📦 Total Scripts Run: `{total_history}`\n"
            f"🚫 Banned Users: `{len(bot_data.get('banned_users', []))}`\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    
    elif text == "📩 View Logs":
        procs = active_processes.get(user_id, [])
        if not procs:
            await update.message.reply_text("❌ No scripts running.")
            return
        await update.message.reply_text(
            "📩 Select a script to view logs:",
            reply_markup=get_scripts_keyboard(user_id, "logs")
        )
    
    elif text == "📞 Contact Owner":
        await update.message.reply_text(
            "📞 *Contact Owner*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "👤 Telegram: @S0DOHU\n"
            "📢 Channel: @chutxmm\n"
            "━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif text == "🛑 Stop Script":
        procs = active_processes.get(user_id, [])
        if not procs:
            await update.message.reply_text("❌ You have no scripts running.")
            return
        await update.message.reply_text(
            "🛑 Select a script to stop:",
            reply_markup=get_scripts_keyboard(user_id, "stop")
        )
    
    elif text == "🔄 Restart Script":
        procs = active_processes.get(user_id, [])
        if not procs:
            await update.message.reply_text("❌ You have no scripts to restart.")
            return
        await update.message.reply_text(
            "🔄 Select a script to restart:",
            reply_markup=get_scripts_keyboard(user_id, "restart")
        )
    
    elif text == "🖥️ System Info":
        sys_info = get_system_info()
        msg = (
            "🖥️ *System Information*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💾 CPU Cores: `{sys_info['cpu_count']}`\n"
            f"💿 Disk: `{sys_info['disk']}`\n"
            f"🐍 Python: `{sys_info['python']}`\n"
            f"🌐 Platform: `{sys_info['platform']}`\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    
    elif text == "🗑️ Delete Script":
        procs = active_processes.get(user_id, [])
        if not procs:
            await update.message.reply_text("❌ You have no scripts to delete.")
            return
        await update.message.reply_text(
            "🗑️ Select a script to delete (files will be removed):",
            reply_markup=get_scripts_keyboard(user_id, "delete")
        )
    
    elif text == "👥 User Manager" and is_owner(user_id):
        await update.message.reply_text(
            "👥 *User Management*\nSelect a user to manage:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_user_management_keyboard()
        )
    
    elif text == "📢 Broadcast" and is_owner(user_id):
        await update.message.reply_text(
            "📢 *Broadcast Message*\n\nSend the message you want to broadcast to all approved users.",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data["awaiting_broadcast"] = True
    
    elif text == "⚙️ Bot Settings" and is_owner(user_id):
        msg = (
            "⚙️ *Bot Settings*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 Max Scripts/User: `{MAX_SCRIPTS_PER_USER}`\n"
            f"🔄 Auto-restart: `{'ON' if AUTO_RESTART_DEFAULT else 'OFF'}`\n"
            f"📝 Log Size Limit: `{MAX_LOG_SIZE} chars`\n"
            f"👥 Approved Users: `{len(bot_data['approved_users'])}`\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    
    elif text == "📦 Backup" and is_owner(user_id):
        backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        backup_path = os.path.join(BACKUP_DIR, backup_name)
        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            if os.path.exists(DATA_FILE):
                zf.write(DATA_FILE)
        await update.message.reply_document(
            document=InputFile(backup_path),
            caption=f"📦 Backup created: `{backup_name}`",
            parse_mode=ParseMode.MARKDOWN
        )
        os.remove(backup_path)
    
    elif text == "❌ Close Menu":
        await update.message.reply_text(
            "✅ Menu closed. Send /start to reopen.",
            reply_markup=ReplyKeyboardMarkup([[]], resize_keyboard=True)
        )
    
    elif context.user_data.get("awaiting_broadcast") and is_owner(user_id):
        context.user_data["awaiting_broadcast"] = False
        sent = 0
        failed = 0
        for uid in bot_data["approved_users"]:
            try:
                await context.bot.send_message(uid, f"📢 *Broadcast:*\n\n{text}", parse_mode=ParseMode.MARKDOWN)
                sent += 1
                await asyncio.sleep(0.5)
            except:
                failed += 1
        await update.message.reply_text(
            f"📢 *Broadcast Complete*\n✅ Sent: {sent}\n❌ Failed: {failed}",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif update.message.document:
        await handle_file_upload(update, context)
    
    elif text and (text.endswith('.py') or text.endswith('.js') or text.endswith('.zip')):
        await update.message.reply_text(
            "⚠️ *File name detected in text!*\n\n"
            "📁 Please send the actual **FILE** (as a document/attachment), not just the file name as text.\n\n"
            "👉 Tap the 📎 clip icon → select your `.py`/`.js`/`.zip` file → Send",
            parse_mode=ParseMode.MARKDOWN
        )

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    doc = update.message.document
    file_name = doc.file_name
    user_folder = os.path.join(DOWNLOADS_DIR, user_id)
    os.makedirs(user_folder, exist_ok=True)
    file_path = os.path.join(user_folder, file_name)
    
    procs = active_processes.get(user_id, [])
    active_count = len([p for p in procs if p["proc"].poll() is None])
    if active_count >= MAX_SCRIPTS_PER_USER:
        await update.message.reply_text(
            f"❌ *Script limit reached!*\nMax: {MAX_SCRIPTS_PER_USER}\nActive: {active_count}\n\nStop a script first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    msg = await update.message.reply_text(f"⏳ Processing `{file_name}`...", parse_mode=ParseMode.MARKDOWN)
    
    try:
        new_file = await context.bot.get_file(doc.file_id)
        
        # ══════════════════════════════════════════
        # 🔧 STRONG DOWNLOAD - 4 methods with verify
        # ══════════════════════════════════════════
        download_ok = False
        
        try:
            await new_file.download_to_drive(file_path)
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                download_ok = True
        except Exception:
            pass
        
        if not download_ok:
            try:
                await new_file.download(custom_path=file_path)
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    download_ok = True
            except Exception:
                pass
        
        if not download_ok:
            try:
                byte_content = await new_file.download_as_bytearray()
                with open(file_path, 'wb') as f:
                    f.write(byte_content)
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    download_ok = True
            except Exception:
                pass
        
        if not download_ok:
            try:
                import urllib.request
                file_url = new_file.file_path
                if file_url and file_url.startswith('http'):
                    urllib.request.urlretrieve(file_url, file_path)
                    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        download_ok = True
            except Exception:
                pass
        
        if not download_ok:
            await msg.edit_text(
                f"❌ *Download failed!*\n\n"
                f"File `{file_name}` could not be saved.\n"
                f"Please try again.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # ══════════════════════════════════════════
        # 🔧 FIX: Absolute paths - no more double path
        # ══════════════════════════════════════════
        abs_file_path = os.path.abspath(file_path)
        abs_user_folder = os.path.abspath(user_folder)
        
        await msg.edit_text(f"✅ Downloaded `{file_name}` ({os.path.getsize(abs_file_path)} bytes)", parse_mode=ParseMode.MARKDOWN)
        
        run_cmd = None
        work_dir = abs_user_folder
        
        if file_name.endswith('.py'):
            await msg.edit_text("📥 Scanning dependencies...")
            deps = scan_python_dependencies(abs_file_path)
            await install_deps(update, msg, deps, "pip")
            run_cmd = [sys.executable, abs_file_path]
        
        elif file_name.endswith('.js'):
            await msg.edit_text("📥 Scanning dependencies...")
            deps = scan_js_dependencies(abs_file_path)
            await install_deps(update, msg, deps, "npm")
            run_cmd = ["node", abs_file_path]
        
        elif file_name.endswith('.zip'):
            await msg.edit_text("📦 Extracting ZIP archive...")
            extract_path = os.path.join(abs_user_folder, file_name.replace('.zip', ''))
            if os.path.exists(extract_path):
                shutil.rmtree(extract_path)
            os.makedirs(extract_path, exist_ok=True)
            with zipfile.ZipFile(abs_file_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
            
            work_dir = extract_path
            main_py = os.path.join(extract_path, 'main.py')
            index_js = os.path.join(extract_path, 'index.js')
            
            if os.path.exists(main_py):
                await msg.edit_text("📦 Found main.py. Checking requirements.txt...")
                run_cmd = [sys.executable, main_py]
                req_path = os.path.join(extract_path, 'requirements.txt')
                if os.path.exists(req_path):
                    await msg.edit_text("📦 Installing from requirements.txt...")
                    subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_path, "--quiet"], capture_output=True)
            elif os.path.exists(index_js):
                await msg.edit_text("📦 Found index.js. Checking package.json...")
                run_cmd = ["node", index_js]
                if os.path.exists(os.path.join(extract_path, 'package.json')):
                    await msg.edit_text("📦 Running npm install...")
                    subprocess.run(["npm", "install"], cwd=extract_path, capture_output=True)
            else:
                await msg.edit_text("❌ Could not find `main.py` or `index.js` in ZIP.", parse_mode=ParseMode.MARKDOWN)
                return
        else:
            await msg.edit_text("❌ Unsupported file type. Use `.py`, `.js`, or `.zip`.")
            return
        
        if run_cmd:
            await msg.edit_text(f"🚀 Launching `{file_name}`...", parse_mode=ParseMode.MARKDOWN)
            log_path = os.path.abspath(os.path.join(LOGS_DIR, f"{user_id}_{file_name}_{int(time.time())}.log"))
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            
            log_file = open(log_path, "w", encoding="utf-8", errors="ignore")
            proc = subprocess.Popen(
                run_cmd,
                stdout=log_file,
                stderr=log_file,
                cwd=work_dir,
                env=env,
                text=True,
                start_new_session=True
            )
            
            if user_id not in active_processes:
                active_processes[user_id] = []
            
            active_processes[user_id].append({
                "name": file_name,
                "proc": proc,
                "log_path": log_path,
                "start_time": time.time(),
                "auto_restart": AUTO_RESTART_DEFAULT,
                "pid": proc.pid,
                "work_dir": work_dir,
                "run_cmd": run_cmd,
                "env": env
            })
            
            await asyncio.sleep(3)
            if proc.poll() is not None:
                log_file.close()
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    error_log = f.read()[-1000:]
                await msg.edit_text(
                    f"❌ `{file_name}` crashed!\n\n📝 *Error Log:*\n```\n{error_log}\n```",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await msg.edit_text(
                    f"✅ `{file_name}` is running!\n\n"
                    f"🆔 PID: `{proc.pid}`\n"
                    f"📂 Path: `{abs_file_path}`\n"
                    f"🔄 Auto-restart: `{'ON' if AUTO_RESTART_DEFAULT else 'OFF'}`",
                    parse_mode=ParseMode.MARKDOWN
                )
    
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{str(e)}`", parse_mode=ParseMode.MARKDOWN)

# ═══════════════════════════════════════════════════════
# 📋 COMMAND HANDLERS
# ═══════════════════════════════════════════════════════
async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/approve <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    uid = context.args[0]
    bot_data["approved_users"][uid] = {"name": "Manual approval", "joined": datetime.now().isoformat()}
    save_data()
    await update.message.reply_text(f"✅ User `{uid}` approved.", parse_mode=ParseMode.MARKDOWN)

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/ban <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    uid = context.args[0]
    bot_data["banned_users"].append(uid)
    if uid in bot_data["approved_users"]:
        del bot_data["approved_users"][uid]
    if uid in active_processes:
        for p in active_processes[uid]:
            if p["proc"].poll() is None:
                p["proc"].terminate()
        active_processes[uid] = []
    save_data()
    await update.message.reply_text(f"🚫 User `{uid}` banned.", parse_mode=ParseMode.MARKDOWN)

async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/unban <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    uid = context.args[0]
    if uid in bot_data["banned_users"]:
        bot_data["banned_users"].remove(uid)
        save_data()
        await update.message.reply_text(f"✅ User `{uid}` unbanned.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("User is not banned.")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    msg = (
        "🤖 *SODOBOT Advanced - Help*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📤 Upload `.py`, `.js`, or `.zip` files to run\n"
        "📂 View and manage your running scripts\n"
        "🔄 Auto-restart on crash (enabled by default)\n"
        "📝 View real-time logs\n\n"
        "*Commands:*\n"
        "• /start - Open main menu\n"
        "• /stop - Stop a script\n"
        "• /restart - Restart a script\n"
        "• /logs - View script logs\n"
        "• /help - This message\n"
    )
    if is_owner(update.effective_user.id):
        msg += (
            "\n*Owner Commands:*\n"
            "• /approve <id> - Approve user\n"
            "• /ban <id> - Ban user\n"
            "• /unban <id> - Unban user\n"
            "• /users - List all users\n"
        )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    msg = "👥 *Approved Users:*\n\n"
    for uid, info in bot_data["approved_users"].items():
        name = info.get("name", "Unknown") if isinstance(info, dict) else "User"
        procs = active_processes.get(uid, [])
        active = len([p for p in procs if p["proc"].poll() is None])
        msg += f"• `{uid}` - {name} ({active} active)\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# ═══════════════════════════════════════════════════════
# 🚀 MAIN
# ═══════════════════════════════════════════════════════
if __name__ == '__main__':
    keep_alive()
    start_monitor()
    
    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .connect_timeout(60)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(60)
        .build()
    )
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('stop', lambda u, c: handle_message(u, c)))
    application.add_handler(CommandHandler('restart', lambda u, c: handle_message(u, c)))
    application.add_handler(CommandHandler('logs', lambda u, c: handle_message(u, c)))
    application.add_handler(CommandHandler('help', cmd_help))
    application.add_handler(CommandHandler('approve', cmd_approve))
    application.add_handler(CommandHandler('ban', cmd_ban))
    application.add_handler(CommandHandler('unban', cmd_unban))
    application.add_handler(CommandHandler('users', cmd_users))
    
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(
        filters.Document.ALL | (filters.TEXT & ~filters.COMMAND),
        handle_message
    ))
    
    print("🤖 SODOBOT Advanced is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)