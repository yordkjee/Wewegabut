import sqlite3
import random
import hashlib
from datetime import datetime
from typing import Dict, Tuple, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes
)

BOT_TOKEN = "8744113633:AAH5nHMtaqnPUJgkoWQkvMMdxAugg9Agiaw"
MIN_PLAYERS = 5
MAX_PLAYERS = 20
DATABASE_FILE = "werewolf_game.db"
PHASE_DURATION = 60
PAUSE_DURATION = 20
MAX_PAUSES = 2

ROLES = {
    'werewolf': {'name': '🐺 Werewolf', 'team': 'evil'},
    'villager': {'name': '👨 Villager', 'team': 'good'},
    'guard': {'name': '🛡️ Guard', 'team': 'good'},
    'doctor': {'name': '👨‍⚕️ Doctor', 'team': 'good'},
    'detective': {'name': '🔍 Detective', 'team': 'good'},
    'mafia_boss': {'name': '👑 Mafia Boss', 'team': 'evil'},
    'cupid': {'name': '💘 Cupid', 'team': 'good'},
    'illusionist': {'name': '🎭 Illusionist', 'team': 'good'},
    'spy': {'name': '🕵️ Spy', 'team': 'good'},
    'judge': {'name': '⚖️ Judge', 'team': 'good'},
    'witch': {'name': '🧙 Witch', 'team': 'good'},
    'hunter': {'name': '🏹 Hunter', 'team': 'good'},
    'priest': {'name': '🙏 Priest', 'team': 'good'},
    'hacker': {'name': '💻 Hacker', 'team': 'good'},
    'puppeteer': {'name': '🎪 Puppeteer', 'team': 'evil'},
    'time_traveler': {'name': '⏰ Time Traveler', 'team': 'good'},
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
        self.paused = False
        self.pause_count = {}
        self.phase = None
        self.day_count = 0
        self.night_count = 0
        self.votes = {}
        self.dead_players = []
    
    def add_player(self, user_id, name):
        if len(self.players) >= self.max_players:
            return False, "❌ Room penuh!"
        if user_id in self.players:
            return False, "❌ Sudah join!"
        self.players[user_id] = {'name': name, 'role': None, 'alive': True}
        self.pause_count[user_id] = MAX_PAUSES
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
phase_jobs = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logged_in = user_id in user_sessions
    keyboard = [[InlineKeyboardButton("🔐 Login", callback_data="login"), InlineKeyboardButton("✍️ Register", callback_data="register")], [InlineKeyboardButton("ℹ️ Bantuan", callback_data="help")]]
    status = "✅ Logged in" if logged_in else "❌ Not logged in"
    await update.message.reply_text("🐺 *WEREWOLF GAME* 🐺\n\n" + f"📊 Status: {status}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if query.data == "login":
        pending_auth[user_id] = {'step': 'login_user'}
        await query.edit_message_text("🔐 Username:")
    elif query.data == "register":
        pending_auth[user_id] = {'step': 'reg_user'}
        await query.edit_message_text("✍️ Username (min 3):")
    elif query.data == "help":
        await query.edit_message_text("🐺 *WEREWOLF*\n/rooms /status /start_game /pause", parse_mode="Markdown")
    elif query.data == "create":
        if user_id not in user_sessions:
            await query.answer("❌ Login!", show_alert=True)
            return
        pending_auth[user_id] = {'step': 'room_name'}
        await query.edit_message_text("📝 Room name:")
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
        await update.effective_chat.send_message(f"✅ *{room.name}* created!", parse_mode="Markdown")
    elif query.data == "yes_pass":
        pending_auth[user_id]['has_password'] = True
        await query.edit_message_text("🔐 Password:")
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
        await update.message.reply_text("🔐 Password:")
    elif step == 'login_pass':
        login_id = login_user(auth['username'], text)
        if login_id:
            user_sessions[user_id] = {'username': auth['username'], 'room': None}
            del pending_auth[user_id]
            await update.message.reply_text(f"✅ Welcome {auth['username']}!")
        else:
            await update.message.reply_text("❌ Wrong!")
    elif step == 'reg_user':
        if len(text) < 3:
            await update.message.reply_text("❌ Min 3!")
            return
        auth['username'] = text
        auth['step'] = 'reg_pass'
        await update.message.reply_text("✍️ Password (min 4):")
    elif step == 'reg_pass':
        if len(text) < 4:
            await update.message.reply_text("❌ Min 4!")
            return
        if register_user(auth['username'], text):
            user_sessions[user_id] = {'username': auth['username'], 'room': None}
            del pending_auth[user_id]
            await update.message.reply_text(f"✅ Welcome {auth['username']}!")
        else:
            await update.message.reply_text("❌ Used!")
    elif step == 'room_name':
        if len(text) < 3:
            await update.message.reply_text("❌ Min 3!")
            return
        auth['room_name'] = text
        auth['step'] = 'room_pass'
        keyboard = [[InlineKeyboardButton("🔓 No password", callback_data="no_pass")], [InlineKeyboardButton("🔐 With password", callback_data="yes_pass")]]
        await update.message.reply_text("🔐 Add password?", reply_markup=InlineKeyboardMarkup(keyboard))
    elif step == 'room_password':
        global room_id_counter
        password = text
        room_id_counter += 1
        room = GameRoom(room_id_counter, auth['room_name'], password, user_id, MAX_PLAYERS)
        room.add_player(user_id, update.effective_user.first_name)
        rooms[room_id_counter] = room
        user_sessions[user_id]['room'] = room_id_counter
        del pending_auth[user_id]
        await update.message.reply_text(f"✅ *{room.name}* created!")

async def rooms_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text("❌ Login! /start")
        return
    if not rooms:
        await update.message.reply_text("📭 No room.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Create", callback_data="create")]]))
        return
    msg = "📋 *ROOMS:*\n\n"
    keyboard = []
    for rid, room in rooms.items():
        if not room.game_active:
            pct = len(room.players)
            stat = "✅" if pct >= 5 else "⏳"
            msg += f"{stat} {room.name}\n👥 {pct}/{room.max_players}\n\n"
            keyboard.append([InlineKeyboardButton(f"{room.name} ({pct}/{room.max_players})", callback_data=f"join:{rid}")])
    keyboard.append([InlineKeyboardButton("➕ Create New", callback_data="create")])
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def create_room_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text("❌ Login!")
        return
    pending_auth[user_id] = {'step': 'room_name'}
    await update.message.reply_text("📝 Room name:")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions or user_sessions[user_id]['room'] is None:
        await update.message.reply_text("❌ No room!")
        return
    room_id = user_sessions[user_id]['room']
    if room_id not in rooms:
        await update.message.reply_text("❌ Room not found!")
        return
    room = rooms[room_id]
    status = room.get_status()
    user_pause = room.pause_count.get(user_id, 0)
    msg = f"📊 *{room.name}*\n👥 {len(room.players)}/{room.max_players}\n🟢 Good: {status['good']} | ⚫ Evil: {status['evil']}\n⏸️ Pause: {user_pause}/{MAX_PAUSES}\n"
    if room.paused:
        msg += "\n⏸️ *GAME PAUSED*"
    if room.game_active:
        msg += f"\n🎮 Phase: {room.phase.upper()}\nDay: {room.day_count}"
    else:
        need = MIN_PLAYERS - len(room.players)
        if need > 0:
            msg += f"\n⏳ Wait {need} more!"
        else:
            msg += f"\n✅ Ready! /start_game"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions or user_sessions[user_id]['room'] is None:
        await update.message.reply_text("❌ No room!")
        return
    room_id = user_sessions[user_id]['room']
    room = rooms[room_id]
    if not room.game_active:
        await update.message.reply_text("❌ Game not active!")
        return
    if user_id not in room.players:
        await update.message.reply_text("❌ Not a player!")
        return
    if not room.players[user_id]['alive']:
        await update.message.reply_text("❌ Dead can't pause!")
        return
    if room.pause_count[user_id] <= 0:
        await update.message.reply_text(f"❌ Pause used! (0/{MAX_PAUSES})")
        return
    
    room.paused = True
    room.pause_count[user_id] -= 1
    remaining = room.pause_count[user_id]
    msg = f"⏸️ *PAUSED by {room.players[user_id]['name']}*\nRemaining: {remaining}/{MAX_PAUSES}"
    await context.bot.send_message(chat_id=room.chat_id, text=msg, parse_mode="Markdown")
    
    if room_id in phase_jobs:
        phase_jobs[room_id].schedule_removal()
    
    context.job_queue.run_once(lambda ctx: auto_resume_game(ctx, room_id, context.bot), when=PAUSE_DURATION)

async def auto_resume_game(context, room_id, bot):
    if room_id not in rooms:
        return
    room = rooms[room_id]
    room.paused = False
    msg = "▶️ *GAME RESUMED*"
    await bot.send_message(chat_id=room.chat_id, text=msg, parse_mode="Markdown")

async def start_game_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions or user_sessions[user_id]['room'] is None:
        await update.message.reply_text("❌ No room!")
        return
    room_id = user_sessions[user_id]['room']
    room = rooms[room_id]
    if len(room.players) < MIN_PLAYERS:
        await update.message.reply_text(f"❌ Min {MIN_PLAYERS}!")
        return
    if room.game_active:
        await update.message.reply_text("❌ Already running!")
        return
    room.game_active = True
    room.chat_id = update.effective_chat.id
    room.assign_roles()
    msg = f"🎮 *GAME START* 🎮\n👥 {len(room.players)} players\n🐺 WW: {sum(1 for p in room.players.values() if p['role'] in ['werewolf', 'mafia_boss'])}\n\nRoles sent to DM!\n/pause - Pause game"
    await update.effective_chat.send_message(msg, parse_mode="Markdown")
    for player_id, player in room.players.items():
        try:
            role_info = ROLES[player['role']]
            role_msg = f"🎭 Role: {role_info['name']}\n\n⏸️ Pause: 2x (20s each)\n/pause - Pause"
            await context.bot.send_message(chat_id=player_id, text=role_msg, parse_mode="Markdown")
        except:
            pass
    context.job_queue.run_once(lambda ctx: night_phase(ctx, room_id, context.bot), when=5)

async def night_phase(context, room_id, bot):
    if room_id not in rooms:
        return
    room = rooms[room_id]
    room.night_count += 1
    room.phase = 'night'
    msg = f"🌙 *NIGHT {room.night_count}* 🌙\n\nRole action! 60s\n/pause"
    await bot.send_message(chat_id=room.chat_id, text=msg, parse_mode="Markdown")
    job = context.job_queue.run_once(lambda ctx: day_phase(ctx, room_id, context.bot), when=PHASE_DURATION)
    phase_jobs[room_id] = job

async def day_phase(context, room_id, bot):
    if room_id not in rooms:
        return
    room = rooms[room_id]
    if room.paused:
        job = context.job_queue.run_once(lambda ctx: day_phase(ctx, room_id, context.bot), when=PHASE_DURATION)
        phase_jobs[room_id] = job
        return
    room.day_count += 1
    room.phase = 'day'
    status = room.get_status()
    msg = f"☀️ *DAY {room.day_count}* ☀️\n🟢 Alive: {len(status['alive'])}\n💀 Dead: {len(room.dead_players)}\n\nDiscuss! 60s\n/pause"
    keyboard = []
    for i, (pid, player) in enumerate(status['alive'].items(), 1):
        keyboard.append([InlineKeyboardButton(f"{i}. {player['name']}", callback_data=f"vote:{pid}")])
    await bot.send_message(chat_id=room.chat_id, text=msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    job = context.job_queue.run_once(lambda ctx: voting_phase(ctx, room_id, context.bot), when=PHASE_DURATION)
    phase_jobs[room_id] = job

async def voting_phase(context, room_id, bot):
    if room_id not in rooms:
        return
    room = rooms[room_id]
    if room.paused:
        job = context.job_queue.run_once(lambda ctx: voting_phase(ctx, room_id, context.bot), when=PHASE_DURATION)
        phase_jobs[room_id] = job
        return
    room.phase = 'voting'
    status = room.get_status()
    alive_list = list(status['alive'].keys())
    msg = f"🗳️ *VOTING* 🗳️\n60s! /pause"
    keyboard = []
    for i, pid in enumerate(alive_list, 1):
        player = room.players[pid]
        keyboard.append([InlineKeyboardButton(f"{i}. {player['name']}", callback_data=f"final_vote:{pid}")])
    await bot.send_message(chat_id=room.chat_id, text=msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    job = context.job_queue.run_once(lambda ctx: count_votes(ctx, room_id, context.bot), when=PHASE_DURATION)
    phase_jobs[room_id] = job

async def count_votes(context, room_id, bot):
    if room_id not in rooms:
        return
    room = rooms[room_id]
    if room.paused:
        job = context.job_queue.run_once(lambda ctx: count_votes(ctx, room_id, context.bot), when=PHASE_DURATION)
        phase_jobs[room_id] = job
        return
    if not room.votes:
        await bot.send_message(chat_id=room.chat_id, text="⚖️ No votes!")
        context.job_queue.run_once(lambda ctx: check_win(ctx, room_id, context.bot), when=2)
        return
    vote_count = {}
    for voter, target in room.votes.items():
        vote_count[target] = vote_count.get(target, 0) + 1
    max_votes = max(vote_count.values())
    most_voted = [pid for pid, votes in vote_count.items() if votes == max_votes]
    if len(most_voted) > 1:
        msg = "⚖️ TIE! Re-voting..."
        await bot.send_message(chat_id=room.chat_id, text=msg)
        room.votes = {}
        context.job_queue.run_once(lambda ctx: voting_phase(ctx, room_id, context.bot), when=2)
        return
    eliminated_id = most_voted[0]
    room.players[eliminated_id]['alive'] = False
    room.dead_players.append(eliminated_id)
    eliminated = room.players[eliminated_id]
    msg = f"💀 *{eliminated['name']}* ELIMINATED!\nRole: {ROLES[eliminated['role']]['name']}"
    await bot.send_message(chat_id=room.chat_id, text=msg, parse_mode="Markdown")
    context.job_queue.run_once(lambda ctx: check_win(ctx, room_id, context.bot), when=2)

async def check_win(context, room_id, bot):
    if room_id not in rooms:
        return
    room = rooms[room_id]
    winner = room.check_win()
    if winner:
        status = room.get_status()
        msg = f"🏆 *GAME END* 🏆\n\n"
        if winner == 'good':
            msg += "✅ *VILLAGERS WIN!*"
        else:
            msg += "⚫ *WEREWOLVES WIN!*"
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
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    print("✅ Bot Online! 🐺")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
