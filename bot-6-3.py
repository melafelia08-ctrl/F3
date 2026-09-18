#!/usr/bin/env python3
"""
bot.py — Instagram DM Bot
Login: cookie / session.json (no Meta Developer account needed)
Run: python bot.py
"""

# ── AUTO PIP INSTALL ──────────────────────────────────────────────────────────
import subprocess, sys

_PKGS = ["instagrapi", "sqlalchemy", "python-dotenv"]

print("[setup] pip packages check kar raha hun...")
for _p in _PKGS:
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", _p, "-q",
         "--disable-pip-version-check"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
print("[setup] Sab packages ready.\n")
# ─────────────────────────────────────────────────────────────────────────────

import os, re, time, shutil, logging, threading
from urllib.parse import unquote
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, String, Text,
    UniqueConstraint, create_engine, delete, func, select,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from instagrapi import Client as IGClient

# ── LOGGING ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("bot")

# ── CONFIG (.env ya defaults) ─────────────────────────────────────────────────

IG_USERNAME       = os.getenv("IG_USERNAME",       "sodohu_098")
SESSION_FILE      = os.getenv("SESSION_FILE", "session.json")
OWNER_IGSID       = os.getenv("OWNER_IGSID",       "33328684279")
POLL_INTERVAL     = int(os.getenv("POLL_INTERVAL", "5"))
WARN_THRESHOLD    = int(os.getenv("WARN_THRESHOLD", "3"))
WARN_EXPIRY_DAYS  = int(os.getenv("WARN_EXPIRY_DAYS", "30"))
BACKUP_PATH       = os.getenv("BACKUP_PATH", "backup/ig_gc_bot.db")
BACKUP_HOURS      = int(os.getenv("BACKUP_INTERVAL_HOURS", "24"))
DB_URL            = os.getenv("DATABASE_URL", "sqlite:///ig_gc_bot.db")

# cookies (.env mein optional — pehli baar setup ke liye)
IG_SESSIONID  = os.getenv("IG_SESSIONID",  "33328684279%3AFS6iED3oZGeOsk%3A1%3AAYlzvPYN5hmV_kY7nhKCyhjDuwSYRFKHkkcJszHstg")
IG_CSRFTOKEN  = os.getenv("IG_CSRFTOKEN",  "2moLGkNHOyRN2r3d7CYMK8wOKf6zrKFE")
IG_DS_USER_ID = os.getenv("IG_DS_USER_ID", "33328684279")
IG_MID        = os.getenv("IG_MID",        "aqy4SAABAAF7Z7FoNKpXqNDfwmJf")

# ── DATABASE ──────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass

engine = create_engine(DB_URL, echo=False, connect_args={"check_same_thread": False})
DBSession = sessionmaker(bind=engine, autoflush=False)


def _now():
    return datetime.now(timezone.utc)

# ── MODELS ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    id           = Column(Integer, primary_key=True)
    igsid        = Column(String(128), unique=True, index=True, nullable=False)
    username     = Column(String(255))
    created_at   = Column(DateTime(timezone=True), default=_now)
    last_seen_at = Column(DateTime(timezone=True), default=_now)

class Admin(Base):
    __tablename__ = "admins"
    id         = Column(Integer, primary_key=True)
    igsid      = Column(String(128), unique=True, index=True, nullable=False)
    role       = Column(String(20), default="MODERATOR")
    active     = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)

class Setting(Base):
    __tablename__ = "settings"
    id         = Column(Integer, primary_key=True)
    key        = Column(String(100), unique=True, nullable=False)
    value      = Column(Text, default="")
    updated_at = Column(DateTime(timezone=True), default=_now)

class Warning(Base):
    __tablename__ = "warnings"
    id         = Column(Integer, primary_key=True)
    user_igsid = Column(String(128), index=True, nullable=False)
    issued_by  = Column(String(128))
    reason     = Column(Text, default="")
    active     = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)

class CustomCommand(Base):
    __tablename__ = "custom_commands"
    id         = Column(Integer, primary_key=True)
    name       = Column(String(50), unique=True)
    response   = Column(Text)
    created_by = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=_now)

class ModerationLog(Base):
    __tablename__ = "moderation_logs"
    id            = Column(Integer, primary_key=True)
    actor_igsid   = Column(String(128))
    target_igsid  = Column(String(128))
    action        = Column(String(50))
    details       = Column(Text, default="")
    created_at    = Column(DateTime(timezone=True), default=_now)

class BlockedWord(Base):
    __tablename__ = "blocked_words"
    id         = Column(Integer, primary_key=True)
    word       = Column(String(100), unique=True, index=True)
    added_by   = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=_now)

class UserNote(Base):
    __tablename__ = "user_notes"
    id         = Column(Integer, primary_key=True)
    user_igsid = Column(String(128), index=True)
    note       = Column(Text)
    added_by   = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=_now)

class ProcessedMsg(Base):
    __tablename__ = "processed_msgs"
    id           = Column(Integer, primary_key=True)
    mid          = Column(String(256), unique=True, index=True)
    sender_igsid = Column(String(128))
    text         = Column(Text, default="")
    received_at  = Column(DateTime(timezone=True), default=_now)

class SessionLog(Base):
    """
    Har login/restore/expire event ka record.
    fingerprint = masked sessionid — safe to store, useful to correlate.
    """
    __tablename__ = "session_logs"
    id           = Column(Integer, primary_key=True)
    event        = Column(String(30))          # RESTORED | COOKIE_LOGIN | EXPIRED | ERROR
    fingerprint  = Column(String(32))          # sid[:4]***sid[-4:]
    user_id      = Column(String(64))          # bot ka IG numeric ID
    source       = Column(String(20))          # session_file | env | interactive
    detail       = Column(Text, default="")   # error message ya extra info
    created_at   = Column(DateTime(timezone=True), default=_now)


Base.metadata.create_all(engine)

# ── AUTH ──────────────────────────────────────────────────────────────────────

ROLE_RANK = {"MODERATOR": 1, "ADMIN": 2, "OWNER": 3}

def ensure_owner(s: Session):
    if not OWNER_IGSID:
        return
    existing = s.execute(select(Admin).where(Admin.igsid == OWNER_IGSID)).scalar()
    if not existing:
        s.add(Admin(igsid=OWNER_IGSID, role="OWNER"))
        s.commit()

def role_for(s: Session, igsid: str):
    row = s.execute(select(Admin).where(Admin.igsid == igsid, Admin.active == True)).scalar()
    return row.role if row else None

def has_role(s: Session, igsid: str, minimum: str) -> bool:
    r = role_for(s, igsid)
    return r is not None and ROLE_RANK.get(r, 0) >= ROLE_RANK[minimum]

# ── SESSION HELPERS ───────────────────────────────────────────────────────────

def _mask(sid: str) -> str:
    """
    Logs mein full sessionid kabhi nahi — safe fingerprint return karta hai.
    e.g. "12345678901234567890" → "1234***7890"
    """
    s = str(sid).strip()
    if len(s) > 8:
        return s[:4] + "***" + s[-4:]
    return "****"

def _log_session(event: str, cl: IGClient, source: str, detail: str = ""):
    """DB mein session event record karo + console log."""
    sid = cl.cookie_dict.get("sessionid", "") if cl else ""
    uid = str(cl.user_id) if cl and cl.user_id else "unknown"
    fp  = _mask(sid)

    # console
    log.info(
        "SESSION %-14s | user_id=%-20s | sid=%-14s | src=%s%s",
        event, uid, fp, source,
        f" | {detail}" if detail else ""
    )

    # DB
    try:
        with DBSession() as s:
            s.add(SessionLog(
                event=event, fingerprint=fp,
                user_id=uid, source=source, detail=detail
            ))
            s.commit()
    except Exception as e:
        log.warning("session_log DB write fail: %s", e)

# ── RATE LIMITER ──────────────────────────────────────────────────────────────

_RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
_buckets: dict = {}
_bucket_lock = threading.Lock()

def rate_allow(igsid: str) -> bool:
    with _bucket_lock:
        now = time.monotonic()
        if igsid not in _buckets:
            _buckets[igsid] = {"tokens": float(_RATE_LIMIT), "last": now}
        b = _buckets[igsid]
        elapsed = now - b["last"]
        b["tokens"] = min(float(_RATE_LIMIT), b["tokens"] + elapsed * (_RATE_LIMIT / 60.0))
        b["last"] = now
        if b["tokens"] >= 1.0:
            b["tokens"] -= 1.0
            return True
        return False

# ── ANTI-FLOOD ────────────────────────────────────────────────────────────────

_flood: dict = {}
_flood_lock = threading.Lock()

def flood_check(igsid: str) -> bool:
    with _flood_lock:
        now = time.monotonic()
        if igsid not in _flood:
            _flood[igsid] = deque()
        dq = _flood[igsid]
        while dq and now - dq[0] > 5:
            dq.popleft()
        dq.append(now)
        return len(dq) > 5

# ── MODERATION ────────────────────────────────────────────────────────────────

URL_RE     = re.compile(r"https?://\S+|www\.\S+", re.I)
MENTION_RE = re.compile(r"@\w+")

def get_setting(s: Session, key: str, default: str = "off") -> str:
    row = s.execute(select(Setting).where(Setting.key == key)).scalar()
    return row.value if row else default

def set_setting(s: Session, key: str, value: str):
    row = s.execute(select(Setting).where(Setting.key == key)).scalar()
    if row:
        row.value = value
    else:
        s.add(Setting(key=key, value=value))
    s.commit()

def inspect_message(s: Session, sender: str, text: str):
    if get_setting(s, "bot_enabled", "on") == "off":
        return None
    if get_setting(s, "linkfilter", "off") == "on" and URL_RE.search(text):
        return "link"
    if get_setting(s, "mentionfilter", "off") == "on" and MENTION_RE.search(text):
        return "mention"
    words = s.execute(select(BlockedWord)).scalars().all()
    tl = text.lower()
    for bw in words:
        if bw.word.lower() in tl:
            return "word"
    return None

def add_warning(s: Session, actor: str, target: str, reason: str) -> int:
    s.add(Warning(user_igsid=target, issued_by=actor, reason=reason))
    s.add(ModerationLog(actor_igsid=actor, target_igsid=target, action="warn", details=reason))
    s.commit()
    count = s.execute(
        select(func.count(Warning.id)).where(Warning.user_igsid == target, Warning.active == True)
    ).scalar() or 0
    if count >= WARN_THRESHOLD and OWNER_IGSID:
        _send(OWNER_IGSID, f"[ALERT] User {target} ke {count} active warnings hain (threshold: {WARN_THRESHOLD})")
    return count

def warn_count(s: Session, target: str) -> int:
    return s.execute(
        select(func.count(Warning.id)).where(Warning.user_igsid == target, Warning.active == True)
    ).scalar() or 0

def clear_warnings(s: Session, target: str) -> int:
    rows = s.execute(
        select(Warning).where(Warning.user_igsid == target, Warning.active == True)
    ).scalars().all()
    for w in rows:
        w.active = False
    s.commit()
    return len(rows)

# ── INSTAGRAM SEND HELPER ─────────────────────────────────────────────────────

_cl: IGClient | None = None  # set after login

def _send(user_igsid: str, text: str):
    if not _cl:
        return
    try:
        _cl.direct_send(text[:990], user_ids=[int(user_igsid)])
    except Exception as e:
        log.warning("send to %s failed: %s", user_igsid, e)

# ── COMMANDS ──────────────────────────────────────────────────────────────────

def dispatch(sender: str, text: str, s: Session, thread_id: str = "") -> str:
    raw = text.strip()

    if not raw.startswith("/"):
        custom = s.execute(
            select(CustomCommand).where(CustomCommand.name == raw.lower())
        ).scalar()
        return custom.response.replace("{user}", sender) if custom else ""

    parts = raw.split(maxsplit=2)
    cmd   = parts[0].lower()
    args  = parts[1:]
    role  = role_for(s, sender)

    # ── public ───────────────────────────────────────────────────────────────

    if cmd in ("/start", "/help"):
        return (
            "Commands:\n"
            "Member: /id /status /warnings /getwelcome /listcmd\n"
            "Mod: /warn /clearwarn /userinfo /note /getnote /logs /stats\n"
            "Admin: /panel /admins /addadmin /deladmin\n"
            "  /automod /antispam /antiflood /linkfilter\n"
            "  /wordfilter /mentionfilter /welcome /setwelcome\n"
            "  /addword /delword /listwords /listsettings\n"
            "  /addcmd /delcmd\n"
            "Owner: /broadcast MESSAGE | /sessionlogs"
        )

    if cmd == "/id":
        return f"Tera IGSID: {sender}"

    if cmd == "/status":
        return f"Bot: {get_setting(s, 'bot_enabled', 'on')} | Role: {role or 'MEMBER'}"

    if cmd == "/warnings":
        t = args[0] if args else sender
        return f"Active warnings for {t}: {warn_count(s, t)}"

    if cmd == "/getwelcome":
        return get_setting(s, "welcome_message", "Welcome, {user}!")

    if cmd == "/listcmd":
        rows = s.execute(select(CustomCommand)).scalars().all()
        return "\n".join("/" + r.name for r in rows) or "Koi custom command nahi."

    # ── session logs (owner only) ─────────────────────────────────────────────

    if cmd == "/sessionlogs":
        if not has_role(s, sender, "OWNER"):
            return "Permission denied. OWNER chahiye."
        n    = min(int(args[0]) if args and args[0].isdigit() else 5, 20)
        rows = s.execute(
            select(SessionLog).order_by(SessionLog.created_at.desc()).limit(n)
        ).scalars().all()
        if not rows:
            return "Koi session log nahi abhi tak."
        lines = [f"Last {n} session events:"]
        for r in rows:
            ts = r.created_at.strftime("%m-%d %H:%M")
            lines.append(f"[{ts}] {r.event} | uid={r.user_id} | sid={r.fingerprint} | src={r.source}")
        return "\n".join(lines)

    # ── panel ─────────────────────────────────────────────────────────────────

    if cmd == "/panel":
        if not role:
            return "Permission denied."
        return (
            f"ADMIN PANEL — role: {role}\n"
            "Mod: /warn /clearwarn /userinfo /note /getnote\n"
            "Filters: /linkfilter /wordfilter /mentionfilter /antiflood /automod\n"
            "Words: /addword /delword /listwords\n"
            "Welcome: /welcome /setwelcome\n"
            "Commands: /addcmd /delcmd\n"
            "Stats: /stats /logs /listsettings\n"
            "Admins: /admins /addadmin /deladmin\n"
            "Broadcast: /broadcast MESSAGE\n"
            "Session: /sessionlogs [N]"
        )

    # ── admin management ──────────────────────────────────────────────────────

    if cmd in ("/admins", "/admin"):
        if not has_role(s, sender, "ADMIN"):
            return "Permission denied."
        rows = s.execute(select(Admin).where(Admin.active == True)).scalars().all()
        return "Admins:\n" + "\n".join(f"- {r.igsid}: {r.role}" for r in rows) if rows else "Koi admin nahi."

    if cmd == "/addadmin":
        if not has_role(s, sender, "OWNER"):
            return "Permission denied. OWNER chahiye."
        if not args:
            return "Usage: /addadmin IGSID [ROLE]"
        target   = args[0]
        new_role = args[1].upper() if len(args) > 1 else "MODERATOR"
        if new_role not in ("ADMIN", "MODERATOR"):
            return "Role sirf ADMIN ya MODERATOR ho sakta hai."
        existing = s.execute(select(Admin).where(Admin.igsid == target)).scalar()
        if existing:
            existing.role   = new_role
            existing.active = True
        else:
            s.add(Admin(igsid=target, role=new_role))
        s.commit()
        return f"{target} ko {new_role} banaya."

    if cmd == "/deladmin":
        if not has_role(s, sender, "OWNER"):
            return "Permission denied. OWNER chahiye."
        if not args:
            return "Usage: /deladmin IGSID"
        existing = s.execute(select(Admin).where(Admin.igsid == args[0])).scalar()
        if existing:
            existing.active = False
            s.commit()
        return "Admin remove kar diya."

    # ── moderation ────────────────────────────────────────────────────────────

    if cmd == "/warn":
        if not has_role(s, sender, "MODERATOR"):
            return "Permission denied."
        if not args:
            return "Usage: /warn IGSID [reason]"
        target = args[0]
        reason = " ".join(args[1:]) or "No reason"
        count  = add_warning(s, sender, target, reason)
        return f"Warning add ho gaya {target} ko. Total: {count}"

    if cmd == "/clearwarn":
        if not has_role(s, sender, "MODERATOR"):
            return "Permission denied."
        if not args:
            return "Usage: /clearwarn IGSID"
        n = clear_warnings(s, args[0])
        return f"{args[0]} ki {n} warning(s) clear."

    # ── user info / notes ─────────────────────────────────────────────────────

    if cmd == "/userinfo":
        if not has_role(s, sender, "MODERATOR"):
            return "Permission denied."
        if not args:
            return "Usage: /userinfo IGSID"
        target = args[0]
        user   = s.execute(select(User).where(User.igsid == target)).scalar()
        count  = warn_count(s, target)
        if not user:
            return f"{target}: abhi tak nahi aaya. Warnings: {count}"
        return (
            f"User: {target}\n"
            f"First seen: {user.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Last seen:  {user.last_seen_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Warnings:   {count}"
        )

    if cmd == "/note":
        if not has_role(s, sender, "MODERATOR"):
            return "Permission denied."
        if len(args) < 2:
            return "Usage: /note IGSID text"
        s.add(UserNote(user_igsid=args[0], note=" ".join(args[1:]), added_by=sender))
        s.commit()
        return f"Note save ho gaya {args[0]} ke liye."

    if cmd == "/getnote":
        if not has_role(s, sender, "MODERATOR"):
            return "Permission denied."
        if not args:
            return "Usage: /getnote IGSID"
        notes = s.execute(
            select(UserNote).where(UserNote.user_igsid == args[0])
            .order_by(UserNote.created_at.desc()).limit(5)
        ).scalars().all()
        if not notes:
            return f"{args[0]} ka koi note nahi."
        lines = [f"Notes ({args[0]}):"]
        for n in reversed(notes):
            lines.append(f"[{n.created_at.strftime('%m-%d')}] {n.note}")
        return "\n".join(lines)

    # ── broadcast ─────────────────────────────────────────────────────────────

    if cmd == "/broadcast":
        if not has_role(s, sender, "OWNER"):
            return "Permission denied. OWNER chahiye."
        if not args:
            return "Usage: /broadcast MESSAGE"
        msg = " ".join(args)
        threading.Thread(target=_broadcast, args=(msg,), daemon=True).start()
        return "Broadcast shuru ho gaya (background mein)."

    # ── stats / logs / settings ───────────────────────────────────────────────

    if cmd == "/stats":
        if not has_role(s, sender, "MODERATOR"):
            return "Permission denied."
        uc = s.execute(select(func.count(User.id))).scalar() or 0
        wc = s.execute(select(func.count(Warning.id)).where(Warning.active == True)).scalar() or 0
        cc = s.execute(select(func.count(CustomCommand.id))).scalar() or 0
        mc = s.execute(select(func.count(ProcessedMsg.id))).scalar() or 0
        return f"Stats:\n  Users: {uc}\n  Active warnings: {wc}\n  Custom cmds: {cc}\n  Messages processed: {mc}"

    if cmd == "/logs":
        if not has_role(s, sender, "MODERATOR"):
            return "Permission denied."
        n    = min(int(args[0]) if args and args[0].isdigit() else 5, 20)
        rows = s.execute(
            select(ModerationLog).order_by(ModerationLog.created_at.desc()).limit(n)
        ).scalars().all()
        if not rows:
            return "Koi mod log nahi abhi tak."
        lines = [f"Last {n} actions:"]
        for r in rows:
            ts  = r.created_at.strftime("%m-%d %H:%M")
            tgt = (r.target_igsid or "-")[:14]
            lines.append(f"[{ts}] {r.action} → {tgt}")
        return "\n".join(lines)

    if cmd == "/listsettings":
        if not has_role(s, sender, "ADMIN"):
            return "Permission denied."
        rows = s.execute(select(Setting)).scalars().all()
        return "Settings:\n" + "\n".join(f"  {r.key}: {r.value}" for r in rows) if rows else "Koi setting nahi."

    # ── word filter management ────────────────────────────────────────────────

    if cmd == "/addword":
        if not has_role(s, sender, "ADMIN"):
            return "Permission denied."
        if not args:
            return "Usage: /addword WORD"
        word = args[0].lower()
        if s.execute(select(BlockedWord).where(BlockedWord.word == word)).scalar():
            return f'"{word}" pehle se blocked hai.'
        s.add(BlockedWord(word=word, added_by=sender))
        s.commit()
        return f'"{word}" blocked words mein add.'

    if cmd == "/delword":
        if not has_role(s, sender, "ADMIN"):
            return "Permission denied."
        if not args:
            return "Usage: /delword WORD"
        s.execute(delete(BlockedWord).where(BlockedWord.word == args[0].lower()))
        s.commit()
        return f'"{args[0]}" remove ho gaya.'

    if cmd == "/listwords":
        if not has_role(s, sender, "ADMIN"):
            return "Permission denied."
        words = s.execute(select(BlockedWord)).scalars().all()
        return "Blocked words:\n" + "\n".join(f"  - {w.word}" for w in words) if words else "Koi blocked word nahi."

    # ── filter toggles ────────────────────────────────────────────────────────

    if cmd in ("/automod", "/antispam", "/antiflood", "/linkfilter",
               "/wordfilter", "/mentionfilter", "/welcome"):
        if not has_role(s, sender, "ADMIN"):
            return "Permission denied."
        if not args or args[0].lower() not in ("on", "off"):
            return f"Usage: {cmd} on|off"
        set_setting(s, cmd.lstrip("/"), args[0].lower())
        return f"{cmd} → {args[0].lower()}"

    if cmd == "/setwelcome":
        if not has_role(s, sender, "ADMIN"):
            return "Permission denied."
        if not args:
            return "Usage: /setwelcome MESSAGE ({user} = sender ka ID)"
        set_setting(s, "welcome_message", " ".join(args))
        return "Welcome message save ho gaya."

    # ── custom commands ───────────────────────────────────────────────────────

    if cmd == "/addcmd":
        if not has_role(s, sender, "ADMIN"):
            return "Permission denied."
        if len(args) < 2:
            return "Usage: /addcmd NAME RESPONSE"
        s.add(CustomCommand(name=args[0].lower().lstrip("/"), response=" ".join(args[1:]), created_by=sender))
        s.commit()
        return "Custom command save ho gaya."

    if cmd == "/delcmd":
        if not has_role(s, sender, "ADMIN"):
            return "Permission denied."
        if not args:
            return "Usage: /delcmd NAME"
        s.execute(delete(CustomCommand).where(CustomCommand.name == args[0].lower().lstrip("/")))
        s.commit()
        return "Custom command delete ho gaya."

    return "Unknown command. /help dekho."

# ── BROADCAST ─────────────────────────────────────────────────────────────────

def _broadcast(text: str):
    with DBSession() as s:
        users = s.execute(select(User)).scalars().all()
    sent = failed = 0
    for u in users:
        try:
            if _cl:
                _cl.direct_send(text[:990], user_ids=[int(u.igsid)])
            sent += 1
        except Exception:
            failed += 1
        time.sleep(1.0)
    log.info("broadcast done — sent: %d, failed: %d", sent, failed)

# ── INSTAGRAM LOGIN ───────────────────────────────────────────────────────────

def _get_cookies_interactive() -> dict:
    print("\n── Cookie Login Setup ──────────────────────────────")
    print("Instagram.com kholo → F12 → Application → Cookies")
    print("Yeh 4 values copy karo aur yahan paste karo:\n")
    return {
        "sessionid":  input("sessionid  : ").strip(),
        "csrftoken":  input("csrftoken  : ").strip(),
        "ds_user_id": input("ds_user_id : ").strip(),
        "mid":        input("mid        : ").strip(),
    }

def login() -> IGClient:
    """
    Session-only login — password kabhi nahi.
    Priority:
      1. session.json  → restore, no password
      2. sessionid     → login_by_sessionid(), decoded, no password
      3. full cookies  → set_settings inject, no password
    Har event SessionLog table + console mein.
    """
    cl = IGClient()
    cl.delay_range = [1, 3]
    sf = Path(SESSION_FILE)

    # ── 1. session.json se restore ────────────────────────────────────────────
    if sf.exists():
        try:
            cl.load_settings(str(sf))
            cl.get_timeline_feed()
            _log_session("RESTORED", cl, source="session_file")
            return cl
        except Exception as e:
            _log_session("EXPIRED", cl, source="session_file", detail=str(e))
            log.warning("session.json dead — cookies se login karta hun")
            sf.unlink(missing_ok=True)

    # ── 2. sessionid se direct login ──────────────────────────────────────────
    if not IG_SESSIONID:
        raw_sid = _get_cookies_interactive().get("sessionid", "")
        source  = "interactive"
    else:
        raw_sid = IG_SESSIONID
        source  = "env"

    # URL-encoded colons decode karo: %3A → :
    sid_clean = unquote(raw_sid)

    try:
        cl.login_by_sessionid(sid_clean)

        # baaki cookies bhi set karo agar hain
        if IG_CSRFTOKEN:
            existing = cl.get_settings()
            existing.setdefault("cookies", {}).update({
                "csrftoken":  IG_CSRFTOKEN,
                "ds_user_id": IG_DS_USER_ID,
                "mid":        IG_MID,
            })
            cl.set_settings(existing)

        _log_session("SESSION_LOGIN", cl, source=source,
                     detail=f"sid={sid_clean[:4]}***{sid_clean[-4:]}")
        cl.dump_settings(str(sf))
        log.info("SESSION SAVED     | %s", SESSION_FILE)
        return cl

    except Exception as e:
        _log_session("ERROR", cl, source=source, detail=str(e))
        log.error("login_by_sessionid fail: %s", e)
        raise RuntimeError(
            f"Session login fail: {e}\n"
            "Sessionid expire ho gayi hogi — browser se fresh sessionid lo."
        ) from e

# ── BACKGROUND THREADS ────────────────────────────────────────────────────────

def _bg_expire_warnings():
    while True:
        time.sleep(6 * 3600)
        cutoff = datetime.now(timezone.utc) - timedelta(days=WARN_EXPIRY_DAYS)
        with DBSession() as s:
            rows = s.execute(
                select(Warning).where(Warning.active == True, Warning.created_at < cutoff)
            ).scalars().all()
            for w in rows:
                w.active = False
            s.commit()
        if rows:
            log.info("auto-expire: %d warning(s) deactivate ki", len(rows))

def _bg_backup():
    while True:
        time.sleep(BACKUP_HOURS * 3600)
        try:
            src = DB_URL.split("///")[-1]
            Path(BACKUP_PATH).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, BACKUP_PATH)
            log.info("DB backup → %s", BACKUP_PATH)
        except Exception as e:
            log.warning("backup error: %s", e)

# ── POLL LOOP ─────────────────────────────────────────────────────────────────

_seen_mids: set[str] = set()  # in-memory fast dedup

def _poll():
    log.info("Polling shuru — har %ds messages check karunga", POLL_INTERVAL)
    while True:
        try:
            threads = _cl.direct_threads(amount=20, thread_message_limit=5)
            for thread in threads:
                for msg in reversed(thread.messages):
                    if not getattr(msg, "text", None):
                        continue
                    if str(msg.user_id) == str(_cl.user_id):
                        continue

                    mid    = str(msg.id)
                    sender = str(msg.user_id)
                    text   = msg.text

                    if mid in _seen_mids:
                        continue
                    _seen_mids.add(mid)

                    with DBSession() as s:
                        if s.execute(select(ProcessedMsg).where(ProcessedMsg.mid == mid)).scalar():
                            continue
                        s.add(ProcessedMsg(mid=mid, sender_igsid=sender, text=text))
                        s.commit()

                    if not rate_allow(sender):
                        log.debug("rate-limited: %s", sender)
                        continue
                    if flood_check(sender):
                        log.debug("flood-blocked: %s", sender)
                        continue

                    with DBSession() as s:
                        user = s.execute(select(User).where(User.igsid == sender)).scalar()
                        if not user:
                            user = User(igsid=sender)
                            s.add(user)
                            s.flush()
                            if get_setting(s, "welcome", "off") == "on":
                                tmpl = get_setting(s, "welcome_message", "Welcome, {user}!")
                                _send(sender, tmpl.replace("{user}", sender))
                        else:
                            user.last_seen_at = _now()

                        if inspect_message(s, sender, text):
                            s.commit()
                            continue

                        reply = dispatch(sender, text, s, thread_id=str(thread.id))
                        s.commit()

                    if reply:
                        try:
                            _cl.direct_send(reply[:990], thread_ids=[thread.id])
                        except Exception as e:
                            log.warning("reply failed: %s", e)

        except Exception as e:
            log.warning("poll error: %s", e)

        time.sleep(POLL_INTERVAL)

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    global _cl

    print("=" * 55)
    print("  Instagram DM Bot")
    print("=" * 55)

    # login — session-only, no password after first run
    _cl = login()

    # ── OWNER IGSID finder ────────────────────────────────────────────────────
    #
    # Apna OWNER_IGSID nahi pata?  Yahan console mein print ho jaayega:
    #
    bot_uid = str(_cl.user_id)
    print("\n" + "─" * 55)
    print(f"  Bot user_id (bot ka apna IGSID): {bot_uid}")
    if not OWNER_IGSID:
        print()
        print("  OWNER_IGSID .env mein set nahi hai.")
        print("  Apna IGSID jaanne ke liye:")
        print("  → Bot shuru hone ke baad apne IG se /id bhejo")
        print("  → Jo number aaye, woh .env mein OWNER_IGSID=<number> daalo")
        print("─" * 55 + "\n")
    else:
        print(f"  OWNER_IGSID    : {OWNER_IGSID}")
        print("─" * 55 + "\n")

    # owner ensure
    with DBSession() as s:
        ensure_owner(s)

    if not OWNER_IGSID:
        log.warning("OWNER_IGSID set nahi hai — .env mein apna IGSID daalo")

    # background threads
    threading.Thread(target=_bg_expire_warnings, daemon=True).start()
    threading.Thread(target=_bg_backup, daemon=True).start()

    log.info("Bot ready! Apne IG account se message bhejo: /help")

    # polling (blocking)
    _poll()

if __name__ == "__main__":
    main()
