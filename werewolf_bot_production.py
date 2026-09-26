import sqlite3
import random
import hashlib
import os
from datetime import datetime
from typing import Dict, Tuple, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes
)

# ============ CONFIG ============
BOT_TOKEN = "8744113633:AAH5nHMtaqnPUJgkoWQkvMMdxAugg9Agiaw"
MIN_PLAYERS = 5
MAX_PLAYERS = 20
DATABASE_FILE = "werewolf_game.db"

# ============ ROLES DATA ============
ROLES = {
    'werewolf': {'name': '🐺 Werewolf', 'team': 'evil', 'desc': 'Bunuh 1 pemain malam'},
    'villager': {'name': '👨 Villager', 'team': 'good', 'desc': 'Vote eliminasi'},
    'guard': {'name': '🛡️ Guard', 'team': 'good', 'desc': 'Protect pemain'},
    'doctor': {'name': '👨‍⚕️ Doctor', 'team': 'good', 'desc': 'Heal korban'},
    'detective': {'name': '🔍 Detective', 'team': 'good', 'desc': 'Cek role pemain'},
    'mafia_boss': {'name': '👑 Mafia Boss', 'team': 'evil', 'desc': 'Leader evil team'},
    'cupid': {'name': '💘 Cupid', 'team': 'good', 'desc': 'Pair 2 pemain'},
    'illusionist': {'name': '🎭 Illusionist', 'team': 'good', 'desc': 'Ubah vote'},
    'spy': {'name': '🕵️ Spy', 'team': 'good', 'desc': 'Lihat chat evil'},
    'judge': {'name': '⚖️ Judge', 'team': 'good', 'desc': 'Veto eliminasi'},
    'witch': {'name': '🧙 Witch', 'team': 'good', 'desc': 'Kill atau heal'},
    'hunter': {'name': '🏹 Hunter', 'team': 'good', 'desc': 'Kill saat mati'},
    'priest': {'name': '🙏 Priest', 'team': 'good', 'desc': 'Blessing immunitas'},
    'hacker': {'name': '💻 Hacker', 'team': 'good', 'desc': 'Hack role'},
    'puppeteer': {'name': '🎪 Puppeteer', 'team': 'evil', 'desc': 'Control voting'},
    'time_traveler': {'name': '⏰ Time Traveler', 'team': 'good', 'desc': 'Lihat dead role'},
}

# ============ DATABASE ============
def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS rooms
                 (room_id INTEGER PRIMARY KEY, room_name TEXT, password TEXT, creator_id INTEGER, 
                 max_players INTEGER, created_at TEXT, active INTEGER)''')
    conn.commit()
    conn.close()

init_db()

# ============ HELPER FUNCTIONS ============
def hash_pwd(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()

def register_user(username: str, password: str) -> bool:
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute('INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)',
                 (username, hash_pwd(password), datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def login_user(username: str, password: str) -> Optional[int]:
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute('SELECT user_id FROM users WHERE username = ? AND password = ?',
             (username, hash_pwd(password)))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

# ============ GAME ROOM ============
class GameRoom:
    def __init__(self, room_id, name, password, creator_id, max_players=12):
        self.room_id = room_id
        self.name = name
        self.password = password
        self.creator_id = creator_id
        self.max_players = max_players
        self.players = {}
        self.game_active = False
        self.phase = None
        self.day_count = 0
        self.votes = {}
        self.created_at = datetime.now()
    
    def add_player(self, user_id, name) -> Tuple[bool, str]:
        if len(self.players) >= self.max_players:
            return False, "❌ Room penuh!"
        if user_id in self.players:
            return False, "❌ Sudah join!"
        self.players[user_id] = {'name': name, 'role': None, 'alive': True}
        return True, f"✅ {name} join!"
    
    def remove_player(self, user_id):
        if user_id in self.players:
            del self.players[user_id]
            return True
        return False
    
    def assign_roles(self):
        if len(self.players) < 5:
            return False
        player_ids = list(self.players.keys())
        random.shuffle(player_ids)
        num = len(player_ids)
        roles = []
        if num <= 7:
            roles = ['werewolf', 'werewolf', 'guard', 'doctor', 'detective'] + ['villager'] * (num - 5)
        elif num <= 12:
            roles = ['werewolf'] * 3 + ['mafia_boss'] + ['guard', 'doctor', 'detective', 'cupid', 'witch'] + ['villager'] * (num - 9)
        elif num <= 18:
            roles = ['werewolf'] * 4 + ['mafia_boss'] + ['guard', 'doctor', 'detective', 'cupid', 'witch', 'spy', 'hunter'] + ['villager'] * (num - 12)
        else:
            roles = ['werewolf'] * 5 + ['mafia_boss'] * 2 + ['guard', 'doctor', 'detective', 'cupid', 'witch', 'spy', 'hunter', 'priest', 'hacker'] + ['villager'] * (num - 17)
        random.shuffle(roles)
        for i, pid in enumerate(player_ids):
            self.players[pid]['role'] = roles[i]
        return True
    
    def get_status(self):
        alive = {uid: p for uid, p in self.players.items() if p['alive']}
        dead = {uid: p for uid, p in self.players.items() if not p['alive']}
        good = sum(1 for p in alive.values() if ROLES.get(p['role'], {}).get('team') == 'good')
        evil = sum(1 for p in alive.values() if ROLES.get(p['role'], {}).get('team') == 'evil')
        return {'alive': alive, 'dead': dead, 'good': good, 'evil': evil}
    
    def check_win(self):
        status = self.get_status()
        if status['evil'] == 0:
            return 'good'
        if status['evil'] >= status['good']:
            return 'evil'
        return None

# ============ GLOBAL STATE ============
rooms: Dict[int, GameRoom] = {}
user_sessions = {}
pending_auth = {}
room_id_counter = 1000

# ============ COMMAND HANDLERS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logged_in = user_id in user_sessions
    
    keyboard = [
        [InlineKeyboardButton("🔐 Login", callback_data="login"),
         InlineKeyboardButton("✍️ Register", callback_data="register")],
        [InlineKeyboardButton("ℹ️ Bantuan", callback_data="help")]
    ]
    
    status = "✅ Logged in" if logged_in else "❌ Not logged in"
    
    await update.message.reply_text(
        "🐺 *WEREWOLF GAME ADVANCED* 🐺\n\n"
        "Selamat datang! Game Werewolf seru di Telegram.\n\n"
        f"📊 Status: {status}\n\n"
        "Pilih opsi di bawah untuk mulai!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    if query.data == "login":
        pending_auth[user_id] = {'step': 'login_user'}
        await query.edit_message_text("🔐 *LOGIN*\n\nKirim username:")
    elif query.data == "register":
        pending_auth[user_id] = {'step': 'reg_user'}
        await query.edit_message_text("✍️ *REGISTER*\n\nBuat username (min 3 char):")
    elif query.data == "help":
        help_msg = "🐺 *WEREWOLF GAME ADVANCED*\n\n*CARA BERMAIN:*\n1. Register/Login\n2. /rooms → Buat atau join room\n3. Tunggu 5+ pemain\n4. Game start otomatis!\n\n*PHASE GAME:*\n🌙 Malam - Role ambil action\n☀️ Siang - Diskusi & vote\n🗳️ Voting - Eliminasi hasil vote"
        await query.edit_message_text(help_msg, parse_mode="Markdown")
    await query.answer()

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    if user_id not in pending_auth:
        return
    auth = pending_auth[user_id]
    step = auth['step']
    if step == 'login_user':
        auth['username'] = text
        auth['step'] = 'login_pass'
        await update.message.reply_text("🔐 Kirim password:")
    elif step == 'login_pass':
        login_id = login_user(auth['username'], text)
        if login_id:
            user_sessions[user_id] = {'username': auth['username'], 'room': None}
            del pending_auth[user_id]
            await update.message.reply_text(f"✅ *LOGIN BERHASIL!*\n\nWelcome, {auth['username']}!\n\nKetik /rooms untuk mulai bermain!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Username/password salah!")
    elif step == 'reg_user':
        if len(text) < 3:
            await update.message.reply_text("❌ Username minimal 3 karakter!")
            return
        auth['username'] = text
        auth['step'] = 'reg_pass'
        await update.message.reply_text("✍️ Buat password (min 4 char):")
    elif step == 'reg_pass':
        if len(text) < 4:
            await update.message.reply_text("❌ Password minimal 4 karakter!")
            return
        if register_user(auth['username'], text):
            user_sessions[user_id] = {'username': auth['username'], 'room': None}
            del pending_auth[user_id]
            await update.message.reply_text(f"✅ *REGISTER BERHASIL!*\n\nWelcome, {auth['username']}!\n\nKetik /rooms untuk mulai bermain!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Username sudah digunakan!")

async def rooms_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text("❌ Anda harus login dulu!\n\nKetik /start")
        return
    if not rooms:
        await update.message.reply_text("📭 Tidak ada room.\n\n/create_room untuk buat!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Buat Room", callback_data="create")]]))
        return
    msg = "📋 *DAFTAR ROOM:*\n\n"
    keyboard = []
    for rid, room in rooms.items():
        if not room.game_active:
            pct = len(room.players)
            stat = "✅" if pct >= 5 else "⏳"
            lock = "🔐" if room.password else "🔓"
            msg += f"{stat} *{room.name}* {lock}\n👥 {pct}/{room.max_players}\n\n"
            keyboard.append([InlineKeyboardButton(f"📍 {room.name} ({pct}/{room.max_players})", callback_data=f"join:{rid}")])
    keyboard.append([InlineKeyboardButton("➕ Buat Room Baru", callback_data="create")])
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def create_room_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text("❌ Anda harus login dulu!")
        return
    pending_auth[user_id] = {'step': 'room_name'}
    await update.message.reply_text("📝 Nama room? (min 3 karakter)")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions or user_sessions[user_id]['room'] is None:
        await update.message.reply_text("❌ Anda tidak di room mana pun!")
        return
    room_id = user_sessions[user_id]['room']
    if room_id not in rooms:
        await update.message.reply_text("❌ Room tidak ditemukan!")
        return
    room = rooms[room_id]
    status = room.get_status()
    msg = f"📊 *ROOM: {room.name}*\n\n👥 Pemain: {len(room.players)}/{room.max_players}\n🟢 Good: {status['good']} | ⚫ Evil: {status['evil']}\n✅ Alive: {len(status['alive'])} | 💀 Dead: {len(status['dead'])}\n\n"
    if room.game_active:
        msg += f"🎮 *GAME AKTIF*\nPhase: {room.phase.upper()}\nHari ke-: {room.day_count}"
    else:
        need = 5 - len(room.players)
        if need > 0:
            msg += f"⏳ Tunggu {need} pemain lagi untuk mulai!"
        else:
            msg += f"✅ SIAP DIMULAI!"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rooms", rooms_cmd))
    app.add_handler(CommandHandler("create_room", create_room_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(login|register|help|create)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    print("✅ Bot Online! 🐺")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
