import sqlite3
import random
import hashlib
import asyncio
from datetime import datetime
from typing import Dict, Tuple, Optional, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, JobQueue
)

BOT_TOKEN = "8744113633:AAH5nHMtaqnPUJgkoWQkvMMdxAugg9Agiaw"
MIN_PLAYERS = 5
MAX_PLAYERS = 20
DATABASE_FILE = "werewolf_game.db"
PHASE_DURATION = 60  # 1 menit per phase

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

def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS rooms (room_id INTEGER PRIMARY KEY, room_name TEXT, password TEXT, creator_id INTEGER, max_players INTEGER, created_at TEXT, active INTEGER)''')
    conn.commit()
    conn.close()

init_db()

def hash_pwd(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()

def register_user(username: str, password: str) -> bool:
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute('INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)', (username, hash_pwd(password), datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def login_user(username: str, password: str) -> Optional[int]:
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute('SELECT user_id FROM users WHERE username = ? AND password = ?', (username, hash_pwd(password)))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

class GameRoom:
    def __init__(self, room_id, name, password, creator_id, max_players=12, chat_id=None):
        self.room_id = room_id
        self.name = name
        self.password = password
        self.creator_id = creator_id
        self.max_players = max_players
        self.chat_id = chat_id
        self.players = {}
        self.game_active = False
        self.phase = None
        self.day_count = 0
        self.night_count = 0
        self.votes = {}
        self.actions = {}
        self.dead_players = []
        self.paired_players = []
        self.protected_player = None
        self.created_at = datetime.now()
    
    def add_player(self, user_id, name) -> Tuple[bool, str]:
        if len(self.players) >= self.max_players:
            return False, "❌ Room penuh!"
        if user_id in self.players:
            return False, "❌ Sudah join!"
        self.players[user_id] = {'name': name, 'role': None, 'alive': True}
        return True, f"✅ {name} join!"
    
    def assign_roles(self):
        if len(self.players) < MIN_PLAYERS:
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
        good = sum(1 for p in alive.values() if ROLES.get(p['role'], {}).get('team') == 'good')
        evil = sum(1 for p in alive.values() if ROLES.get(p['role'], {}).get('team') == 'evil')
        return {'alive': alive, 'good': good, 'evil': evil}
    
    def check_win(self):
        status = self.get_status()
        if status['evil'] == 0:
            return 'good'
        if status['evil'] >= status['good']:
            return 'evil'
        return None

rooms: Dict[int, GameRoom] = {}
user_sessions = {}
pending_auth = {}
room_id_counter = 1000
group_chat_ids = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logged_in = user_id in user_sessions
    keyboard = [[InlineKeyboardButton("🔐 Login", callback_data="login"), InlineKeyboardButton("✍️ Register", callback_data="register")], [InlineKeyboardButton("ℹ️ Bantuan", callback_data="help")]]
    status = "✅ Logged in" if logged_in else "❌ Not logged in"
    await update.message.reply_text("🐺 *WEREWOLF GAME ADVANCED* 🐺\n\nSelamat datang! Game Werewolf seru di Telegram.\n\n" + f"📊 Status: {status}\n\nPilih opsi di bawah untuk mulai!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

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
        help_msg = "🐺 *WEREWOLF GAME*\n\n/rooms - Room list\n/create_room - Buat room\n/status - Game status\n/start_game - Mulai game"
        await query.edit_message_text(help_msg, parse_mode="Markdown")
    elif query.data == "create":
        if user_id not in user_sessions:
            await query.answer("❌ Login dulu!", show_alert=True)
            return
        pending_auth[user_id] = {'step': 'room_name'}
        await query.edit_message_text("📝 Nama room? (min 3 karakter)")
    elif query.data == "no_pass":
        pending_auth[user_id]['has_password'] = False
        user_id_val = user_id
        room_name = pending_auth[user_id]['room_name']
        global room_id_counter
        room_id_counter += 1
        room = GameRoom(room_id_counter, room_name, None, user_id_val, MAX_PLAYERS)
        room.add_player(user_id_val, update.effective_user.first_name)
        rooms[room_id_counter] = room
        user_sessions[user_id_val]['room'] = room_id_counter
        del pending_auth[user_id_val]
        msg = f"✅ *ROOM DIBUAT!*\n\n*Nama:* {room.name}\n*Password:* 🔓 Tidak\n👥 {len(room.players)}/{room.max_players}\n\nTunggu {MIN_PLAYERS - len(room.players)} pemain lagi!"
        await update.effective_chat.send_message(msg, parse_mode="Markdown")
    elif query.data == "yes_pass":
        pending_auth[user_id]['has_password'] = True
        await query.edit_message_text("🔐 Ketik password untuk room:")
    await query.answer()

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    if user_id not in pending_auth:
        return
    auth = pending_auth[user_id]
    step = auth.get('step')
    if step == 'login_user':
        auth['username'] = text
        auth['step'] = 'login_pass'
        await update.message.reply_text("🔐 Kirim password:")
    elif step == 'login_pass':
        login_id = login_user(auth['username'], text)
        if login_id:
            user_sessions[user_id] = {'username': auth['username'], 'room': None}
            del pending_auth[user_id]
            await update.message.reply_text(f"✅ *LOGIN BERHASIL!*\n\nWelcome, {auth['username']}!\n\nKetik /rooms untuk mulai!", parse_mode="Markdown")
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
            await update.message.reply_text(f"✅ *REGISTER BERHASIL!*\n\nWelcome, {auth['username']}!\n\nKetik /rooms untuk mulai!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Username sudah digunakan!")
    elif step == 'room_name':
        if len(text) < 3:
            await update.message.reply_text("❌ Room name minimal 3 karakter!")
            return
        auth['room_name'] = text
        auth['step'] = 'room_pass'
        keyboard = [[InlineKeyboardButton("🔓 Tidak ada password", callback_data="no_pass")], [InlineKeyboardButton("🔐 Pakai password", callback_data="yes_pass")]]
        await update.message.reply_text("🔐 Tambah password untuk room?", reply_markup=InlineKeyboardMarkup(keyboard))
    elif step == 'room_password':
        global room_id_counter
        password = text
        room_id_counter += 1
        room = GameRoom(room_id_counter, auth['room_name'], password, user_id, MAX_PLAYERS)
        room.add_player(user_id, update.effective_user.first_name)
        rooms[room_id_counter] = room
        user_sessions[user_id]['room'] = room_id_counter
        del pending_auth[user_id]
        msg = f"✅ *ROOM DIBUAT!*\n\n*Nama:* {room.name}\n*Password:* 🔐 Ada\n👥 {len(room.players)}/{room.max_players}"
        await update.message.reply_text(msg, parse_mode="Markdown")

async def rooms_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text("❌ Login dulu! /start")
        return
    if not rooms:
        await update.message.reply_text("📭 Tidak ada room.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Buat Room", callback_data="create")]]))
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
        await update.message.reply_text("❌ Login dulu! /start")
        return
    pending_auth[user_id] = {'step': 'room_name'}
    await update.message.reply_text("📝 Nama room? (min 3 karakter)")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions or user_sessions[user_id]['room'] is None:
        await update.message.reply_text("❌ Anda tidak di room!")
        return
    room_id = user_sessions[user_id]['room']
    if room_id not in rooms:
        await update.message.reply_text("❌ Room tidak ditemukan!")
        return
    room = rooms[room_id]
    status = room.get_status()
    msg = f"📊 *ROOM: {room.name}*\n\n👥 Pemain: {len(room.players)}/{room.max_players}\n🟢 Good: {status['good']} | ⚫ Evil: {status['evil']}\n"
    if room.game_active:
        msg += f"🎮 GAME AKTIF\nPhase: {room.phase.upper()}\nHari: {room.day_count}"
    else:
        need = MIN_PLAYERS - len(room.players)
        if need > 0:
            msg += f"⏳ Tunggu {need} pemain lagi!"
        else:
            msg += f"✅ Siap dimulai! /start_game"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def start_game_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions or user_sessions[user_id]['room'] is None:
        await update.message.reply_text("❌ Anda tidak di room!")
        return
    room_id = user_sessions[user_id]['room']
    room = rooms[room_id]
    if len(room.players) < MIN_PLAYERS:
        await update.message.reply_text(f"❌ Minimal {MIN_PLAYERS} pemain!")
        return
    if room.game_active:
        await update.message.reply_text("❌ Game sudah berjalan!")
        return
    room.game_active = True
    room.chat_id = update.effective_chat.id
    group_chat_ids[room_id] = update.effective_chat.id
    room.assign_roles()
    msg = f"🎮 *GAME DIMULAI!* 🎮\n\n👥 Total: {len(room.players)} pemain\n🐺 Werewolves: {sum(1 for p in room.players.values() if p['role'] in ['werewolf', 'mafia_boss'])}\n"
    msg += "\nRole sudah dikirim via DM!\n\n🌙 FASE MALAM dimulai dalam 5 detik..."
    await update.effective_chat.send_message(msg, parse_mode="Markdown")
    for player_id, player in room.players.items():
        try:
            role_info = ROLES[player['role']]
            role_msg = f"🎭 *ROLE MU:* {role_info['name']}\n\n{role_info['desc']}"
            await context.bot.send_message(chat_id=player_id, text=role_msg, parse_mode="Markdown")
        except:
            pass
    context.job_queue.run_once(lambda ctx: night_phase(ctx, room_id, context.bot), when=5)

async def night_phase(context, room_id, bot):
    room = rooms[room_id]
    room.night_count += 1
    room.phase = 'night'
    msg = f"🌙 *FASE MALAM KE-{room.night_count}* 🌙\n\n"
    msg += "Werewolf, Guard, Doctor, Detective - ambil aksi kalian!\n\n⏰ Waktu: 60 detik"
    await bot.send_message(chat_id=room.chat_id, text=msg, parse_mode="Markdown")
    context.job_queue.run_once(lambda ctx: day_phase(ctx, room_id, context.bot), when=PHASE_DURATION)

async def day_phase(context, room_id, bot):
    room = rooms[room_id]
    room.day_count += 1
    room.phase = 'day'
    status = room.get_status()
    msg = f"☀️ *PAGI KE-{room.day_count}* ☀️\n\n"
    msg += f"🟢 Alive: {len(status['alive'])}\n💀 Dead: {len(room.dead_players)}\n\n"
    msg += "Diskusi & voting dalam 60 detik!\n\n"
    msg += "Ketik nomor pemain untuk vote:"
    keyboard = []
    for i, (pid, player) in enumerate(status['alive'].items(), 1):
        keyboard.append([InlineKeyboardButton(f"{i}. {player['name']}", callback_data=f"vote:{pid}")])
    await bot.send_message(chat_id=room.chat_id, text=msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    context.job_queue.run_once(lambda ctx: voting_phase(ctx, room_id, context.bot), when=PHASE_DURATION)

async def voting_phase(context, room_id, bot):
    room = rooms[room_id]
    room.phase = 'voting'
    status = room.get_status()
    alive_list = list(status['alive'].keys())
    msg = f"🗳️ *VOTING PHASE* 🗳️\n\n"
    msg += "60 detik terakhir untuk voting!\n\n"
    keyboard = []
    for i, pid in enumerate(alive_list, 1):
        player = room.players[pid]
        keyboard.append([InlineKeyboardButton(f"{i}. {player['name']}", callback_data=f"final_vote:{pid}")])
    await bot.send_message(chat_id=room.chat_id, text=msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    context.job_queue.run_once(lambda ctx: count_votes(ctx, room_id, context.bot), when=PHASE_DURATION)

async def count_votes(context, room_id, bot):
    room = rooms[room_id]
    if not room.votes:
        await bot.send_message(chat_id=room.chat_id, text="⚖️ Tidak ada voting!")
        context.job_queue.run_once(lambda ctx: check_win(ctx, room_id, context.bot), when=2)
        return
    vote_count = {}
    for voter, target in room.votes.items():
        vote_count[target] = vote_count.get(target, 0) + 1
    max_votes = max(vote_count.values())
    most_voted = [pid for pid, votes in vote_count.items() if votes == max_votes]
    if len(most_voted) > 1:
        msg = "⚖️ SERI! Voting ulang..."
        await bot.send_message(chat_id=room.chat_id, text=msg, parse_mode="Markdown")
        room.votes = {}
        context.job_queue.run_once(lambda ctx: voting_phase(ctx, room_id, context.bot), when=2)
        return
    eliminated_id = most_voted[0]
    room.players[eliminated_id]['alive'] = False
    room.dead_players.append(eliminated_id)
    eliminated = room.players[eliminated_id]
    msg = f"💀 *{eliminated['name']}* DIELIMINASI!\n\n"
    msg += f"Role: {ROLES[eliminated['role']]['name']}"
    await bot.send_message(chat_id=room.chat_id, text=msg, parse_mode="Markdown")
    context.job_queue.run_once(lambda ctx: check_win(ctx, room_id, context.bot), when=2)

async def check_win(context, room_id, bot):
    room = rooms[room_id]
    winner = room.check_win()
    if winner:
        status = room.get_status()
        msg = f"🏆 *GAME SELESAI!* 🏆\n\n"
        if winner == 'good':
            msg += "✅ *VILLAGERS MENANG!*\n\nSemua werewolf berhasil dieliminasi!"
        else:
            msg += "⚫ *WEREWOLVES MENANG!*\n\nEvil team seimbang atau lebih!"
        msg += f"\n\n🟢 Good: {status['good']} | ⚫ Evil: {status['evil']}"
        await bot.send_message(chat_id=room.chat_id, text=msg, parse_mode="Markdown")
        room.game_active = False
        return
    room.votes = {}
    context.job_queue.run_once(lambda ctx: night_phase(ctx, room_id, context.bot), when=3)

async def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rooms", rooms_cmd))
    app.add_handler(CommandHandler("create_room", create_room_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("start_game", start_game_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    print("✅ Bot Online! 🐺")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
