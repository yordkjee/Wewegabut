import os, sqlite3, hashlib, random, string, threading, time
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, join_room as sio_join, leave_room as sio_leave, emit
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8744113633:AAH5nHMtaqnPUJgkoWQkvMMdxAugg9Agiaw")
WEBAPP_URL = os.environ.get("TELEGRAM_WEBAPP_URL", "https://wewegabut.yordkjee.repl.co")
DB_FILE = "werewolf.db"
MIN_PLAYERS = 5
MAX_PLAYERS = 20
PHASE_SECONDS = {"night": 45, "day": 60, "voting": 30}
PAUSE_DURATION = 20
MAX_PAUSES = 2

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["SECRET_KEY"] = "werewolf-secret-key"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ---------------- ROLE DEFINITIONS ----------------
ROLES = {
    "werewolf":      {"name": "🐺 Werewolf", "team": "evil", "core": True,  "desc": "Bunuh 1 pemain tiap malam bersama werewolf lain"},
    "villager":      {"name": "👨 Villager", "team": "good", "core": True,  "desc": "Cari dan vote werewolf saat siang"},
    "mafia_boss":    {"name": "👑 Mafia Boss", "team": "evil", "core": False, "desc": "Pemimpin evil, kebal dari deteksi Detective"},
    "guard":         {"name": "🛡️ Guard", "team": "good", "core": False, "desc": "Lindungi 1 pemain dari kematian tiap malam"},
    "doctor":        {"name": "👨‍⚕️ Doctor", "team": "good", "core": False, "desc": "Sembuhkan korban serangan werewolf"},
    "detective":     {"name": "🔍 Detective", "team": "good", "core": False, "desc": "Cek tim (good/evil) 1 pemain tiap malam"},
    "witch":         {"name": "🧙 Witch", "team": "good", "core": False, "desc": "Punya 1x racun & 1x penawar sepanjang game"},
    "hunter":        {"name": "🏹 Hunter", "team": "good", "core": False, "desc": "Saat mati, tembak mati 1 musuh secara acak"},
    "cupid":         {"name": "💘 Cupid", "team": "good", "core": False, "desc": "Pasangkan 2 pemain di malam pertama, mereka mati bersama"},
    "spy":           {"name": "🕵️ Spy", "team": "good", "core": False, "desc": "Melihat sedikit aktivitas evil tiap malam"},
    "judge":         {"name": "⚖️ Judge", "team": "good", "core": False, "desc": "Bisa veto 1x hasil voting siang"},
    "priest":        {"name": "🙏 Priest", "team": "good", "core": False, "desc": "Beri imun 1 malam ke 1 pemain"},
    "hacker":        {"name": "💻 Hacker", "team": "good", "core": False, "desc": "Intip role 1 pemain, 1x pakai"},
    "illusionist":   {"name": "🎭 Illusionist", "team": "good", "core": False, "desc": "Alihkan 1 vote di siang hari"},
    "puppeteer":     {"name": "🎪 Puppeteer", "team": "evil", "core": False, "desc": "Paksa vote 1 pemain, 1x pakai"},
    "time_traveler": {"name": "⏰ Time Traveler", "team": "good", "core": False, "desc": "Lihat role pemain yang sudah mati"},
}
OPTIONAL_ROLES = [r for r, v in ROLES.items() if not v["core"]]
DEFAULT_ENABLED = ["guard", "doctor", "detective", "witch", "hunter", "cupid", "mafia_boss"]

# ---------------- DB ----------------
def db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db(); c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY, display_name TEXT, avatar TEXT,
        wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, created_at TEXT)""")
    conn.commit(); conn.close()

init_db()

DEFAULT_AVATARS = ["🐺","🦊","🐻","🦁","🐯","🐼","🦝","🦉","🐸","🐵","🐲","🦄"]

def get_user(telegram_id):
    conn = db(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
    row = c.fetchone(); conn.close()
    return dict(row) if row else None

def upsert_user(telegram_id, display_name=None, avatar=None):
    existing = get_user(telegram_id)
    conn = db(); c = conn.cursor()
    if existing:
        name = display_name if display_name is not None else existing["display_name"]
        av = avatar if avatar is not None else existing["avatar"]
        c.execute("UPDATE users SET display_name=?, avatar=? WHERE telegram_id=?", (name, av, telegram_id))
    else:
        name = display_name or f"Player{str(telegram_id)[-4:]}"
        av = avatar or random.choice(DEFAULT_AVATARS)
        c.execute("INSERT INTO users (telegram_id,display_name,avatar,created_at) VALUES (?,?,?,?)",
                   (telegram_id, name, av, datetime.now().isoformat()))
    conn.commit(); conn.close()
    return get_user(telegram_id)

# ---------------- IN-MEMORY GAME STATE ----------------
rooms = {}          # room_id -> room dict
sid_to_user = {}     # socket id -> {user_id, username, room_id}
user_sid = {}        # user_id -> sid

def gen_room_code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=5))

def new_room(name, password, max_players, enabled_roles, allow_dup, host_id, host_name, host_avatar="🐺"):
    rid = gen_room_code()
    while rid in rooms:
        rid = gen_room_code()
    rooms[rid] = {
        "id": rid, "name": name, "password": password, "max_players": max_players,
        "enabled_roles": enabled_roles, "allow_dup": allow_dup,
        "host_id": host_id, "players": {}, "order": [], "active": False, "paused": False,
        "phase": None, "day_count": 0, "night_count": 0, "dead_players": [],
        "votes": {}, "night_actions": {}, "lovers": None, "chat": [],
        "created_at": datetime.now().isoformat(),
    }
    add_player(rooms[rid], host_id, host_name, host_avatar)
    return rooms[rid]

def add_player(room, user_id, name, avatar="🐺"):
    if user_id in room["players"]:
        return True
    if len(room["players"]) >= room["max_players"]:
        return False
    room["players"][user_id] = {"name": name, "avatar": avatar, "role": None, "alive": True, "pauses": MAX_PAUSES, "charges": {}}
    room["order"].append(user_id)
    return True

def public_room_state(room):
    return {
        "id": room["id"], "name": room["name"], "has_password": bool(room["password"]),
        "max_players": room["max_players"], "player_count": len(room["players"]),
        "active": room["active"],
        "players": [{"id": pid, "name": p["name"], "avatar": p.get("avatar","🐺"), "alive": p["alive"], "host": pid == room["host_id"]}
                    for pid, p in room["players"].items()],
        "enabled_roles": room["enabled_roles"], "allow_dup": room["allow_dup"],
        "phase": room["phase"], "day_count": room["day_count"], "night_count": room["night_count"],
        "paused": room["paused"],
    }

def lobby_list():
    return [public_room_state(r) for r in rooms.values() if not r["active"]]

# ---------------- SMART BALANCED ROLE ASSIGNMENT ----------------
def calculate_roles(num_players, enabled_roles, allow_dup):
    evil_count = max(1, round(num_players / 4))
    if evil_count >= num_players / 2:
        evil_count = max(1, num_players // 2 - 1)

    mafia_boss_count = 0
    if "mafia_boss" in enabled_roles and evil_count >= 4:
        mafia_boss_count = max(1, evil_count // 4)
    werewolf_count = max(1, evil_count - mafia_boss_count)

    good_count = num_players - (werewolf_count + mafia_boss_count)
    special_pool = [r for r in enabled_roles if r not in ("werewolf", "mafia_boss", "villager")]

    special_assign = []
    if special_pool and good_count > 0:
        max_specials = max(0, good_count - 1)
        if allow_dup:
            target_specials = min(max_specials, max(len(special_pool), round(good_count * 0.6)))
            cycle = special_pool.copy()
            while len(special_assign) < target_specials:
                random.shuffle(cycle)
                for r in cycle:
                    if len(special_assign) >= target_specials:
                        break
                    special_assign.append(r)
        else:
            target_specials = min(max_specials, len(special_pool))
            special_assign = random.sample(special_pool, target_specials)

    villager_count = good_count - len(special_assign)
    roles = ["werewolf"] * werewolf_count + ["mafia_boss"] * mafia_boss_count + special_assign + ["villager"] * max(0, villager_count)
    random.shuffle(roles)
    return roles

def assign_roles(room):
    ids = room["order"].copy()
    random.shuffle(ids)
    roles = calculate_roles(len(ids), room["enabled_roles"], room["allow_dup"])
    for i, pid in enumerate(ids):
        role = roles[i]
        room["players"][pid]["role"] = role
        charges = {}
        if role == "witch":
            charges = {"heal": 1, "poison": 1}
        elif role == "cupid":
            charges = {"pair": 1}
        elif role == "puppeteer":
            charges = {"force": 1}
        elif role == "hacker":
            charges = {"peek": 1}
        elif role == "judge":
            charges = {"veto": 1}
        room["players"][pid]["charges"] = charges

def get_alive(room):
    return {pid: p for pid, p in room["players"].items() if p["alive"]}

def team_counts(room):
    alive = get_alive(room)
    good = sum(1 for p in alive.values() if ROLES[p["role"]]["team"] == "good")
    evil = sum(1 for p in alive.values() if ROLES[p["role"]]["team"] == "evil")
    return good, evil

def check_win(room):
    good, evil = team_counts(room)
    if evil == 0:
        return "good"
    if evil >= good:
        return "evil"
    return None

# ---------------- SOCKET EVENTS ----------------
@socketio.on("connect")
def on_connect():
    emit("lobby_update", lobby_list())

@socketio.on("disconnect")
def on_disconnect():
    info = sid_to_user.pop(request.sid, None)
    if info:
        user_sid.pop(info["user_id"], None)

@socketio.on("telegram_auth")
def on_telegram_auth(data):
    tid = data.get("telegram_id")
    if not tid:
        emit("auth_error", {"message": "Tidak bisa deteksi akun Telegram"}); return
    tid = int(tid)
    existing = get_user(tid)
    suggested_name = (data.get("first_name") or "Player").strip()
    photo = data.get("photo_url")
    if existing:
        sid_to_user[request.sid] = {"user_id": tid, "username": existing["display_name"], "avatar": existing["avatar"], "room_id": None}
        user_sid[tid] = request.sid
        emit("auth_success", {"user_id": tid, "username": existing["display_name"], "avatar": existing["avatar"], "needs_nickname": False})
    else:
        sid_to_user[request.sid] = {"user_id": tid, "username": suggested_name, "avatar": photo or random.choice(DEFAULT_AVATARS), "room_id": None}
        user_sid[tid] = request.sid
        emit("auth_success", {"user_id": tid, "username": suggested_name, "avatar": photo or "🐺", "needs_nickname": True, "suggested_name": suggested_name, "photo_url": photo})

@socketio.on("set_profile")
def on_set_profile(data):
    info = sid_to_user.get(request.sid)
    if not info:
        emit("error_msg", {"message": "Sesi tidak valid, buka ulang app"}); return
    name = (data.get("display_name") or "").strip()[:20] or info["username"]
    avatar = data.get("avatar") or info.get("avatar") or "🐺"
    user = upsert_user(info["user_id"], name, avatar)
    info["username"] = user["display_name"]; info["avatar"] = user["avatar"]
    emit("profile_updated", {"username": user["display_name"], "avatar": user["avatar"]})

@socketio.on("get_lobby")
def on_get_lobby():
    emit("lobby_update", lobby_list())

@socketio.on("create_room")
def on_create_room(data):
    info = sid_to_user.get(request.sid)
    if not info:
        emit("error_msg", {"message": "Login dulu"}); return
    enabled = data.get("enabled_roles") or DEFAULT_ENABLED
    enabled = [r for r in enabled if r in OPTIONAL_ROLES]
    room = new_room(
        data.get("name", "Room")[:24], data.get("password") or None,
        max(MIN_PLAYERS, min(MAX_PLAYERS, int(data.get("max_players", 12)))),
        enabled, bool(data.get("allow_dup", True)), info["user_id"], info["username"],
        info.get("avatar", "🐺"),
    )
    info["room_id"] = room["id"]
    sio_join(room["id"])
    emit("room_joined", public_room_state(room))
    socketio.emit("lobby_update", lobby_list())

@socketio.on("join_room_ev")
def on_join_room(data):
    info = sid_to_user.get(request.sid)
    if not info:
        emit("error_msg", {"message": "Login dulu"}); return
    rid = data.get("room_id")
    room = rooms.get(rid)
    if not room:
        emit("error_msg", {"message": "Room tidak ditemukan"}); return
    if room["password"] and room["password"] != data.get("password"):
        emit("error_msg", {"message": "Password salah"}); return
    if room["active"]:
        emit("error_msg", {"message": "Game sudah dimulai"}); return
    if not add_player(room, info["user_id"], info["username"], info.get("avatar", "🐺")):
        emit("error_msg", {"message": "Room penuh"}); return
    info["room_id"] = rid
    sio_join(rid)
    socketio.emit("room_state", public_room_state(room), room=rid)
    emit("room_joined", public_room_state(room))
    socketio.emit("lobby_update", lobby_list())

@socketio.on("refresh_room")
def on_refresh_room(data=None):
    info = sid_to_user.get(request.sid)
    if not info or not info.get("room_id"): return
    room = rooms.get(info["room_id"])
    if not room: return
    emit("room_state", public_room_state(room))

@socketio.on("leave_room_ev")
def on_leave_room(data):
    info = sid_to_user.get(request.sid)
    if not info or not info.get("room_id"): return
    rid = info["room_id"]; room = rooms.get(rid)
    if room and not room["active"]:
        room["players"].pop(info["user_id"], None)
        room["order"] = [i for i in room["order"] if i != info["user_id"]]
        if not room["players"]:
            rooms.pop(rid, None)
        else:
            if room["host_id"] == info["user_id"]:
                room["host_id"] = room["order"][0]
            socketio.emit("room_state", public_room_state(room), room=rid)
    sio_leave(rid)
    info["room_id"] = None
    socketio.emit("lobby_update", lobby_list())

@socketio.on("send_chat")
def on_chat(data):
    info = sid_to_user.get(request.sid)
    if not info or not info.get("room_id"): return
    msg = {"user": info["username"], "text": str(data.get("text", ""))[:300], "t": datetime.now().strftime("%H:%M")}
    socketio.emit("chat_message", msg, room=info["room_id"])

@socketio.on("start_game")
def on_start_game(data):
    info = sid_to_user.get(request.sid)
    if not info or not info.get("room_id"): return
    room = rooms.get(info["room_id"])
    if not room or room["host_id"] != info["user_id"]: return
    if len(room["players"]) < MIN_PLAYERS:
        emit("error_msg", {"message": f"Minimal {MIN_PLAYERS} pemain"}); return
    if room["active"]: return
    room["active"] = True
    assign_roles(room)
    for pid, p in room["players"].items():
        sid = user_sid.get(pid)
        if sid:
            socketio.emit("your_role", {"role": p["role"], "info": ROLES[p["role"]]}, room=sid)
    socketio.emit("game_started", public_room_state(room), room=room["id"])
    socketio.emit("lobby_update", lobby_list())
    socketio.start_background_task(game_loop, room["id"])

@socketio.on("pause_game")
def on_pause(data):
    info = sid_to_user.get(request.sid)
    if not info or not info.get("room_id"): return
    room = rooms.get(info["room_id"])
    if not room or not room["active"]: return
    p = room["players"].get(info["user_id"])
    if not p or not p["alive"]:
        emit("error_msg", {"message": "Dead player tidak bisa pause"}); return
    if p["pauses"] <= 0:
        emit("error_msg", {"message": "Pause sudah habis"}); return
    p["pauses"] -= 1
    room["paused"] = True
    socketio.emit("game_paused", {"by": p["name"], "remaining": p["pauses"]}, room=room["id"])
    socketio.start_background_task(resume_after, room["id"])

def resume_after(rid):
    socketio.sleep(PAUSE_DURATION)
    room = rooms.get(rid)
    if room:
        room["paused"] = False
        socketio.emit("game_resumed", {}, room=rid)

@socketio.on("night_action")
def on_night_action(data):
    info = sid_to_user.get(request.sid)
    if not info or not info.get("room_id"): return
    room = rooms.get(info["room_id"])
    if not room or room["phase"] != "night": return
    uid = info["user_id"]; p = room["players"].get(uid)
    if not p or not p["alive"]: return
    role, action, target = p["role"], data.get("action"), data.get("target")
    na = room["night_actions"]
    if action == "werewolf_kill" and role in ("werewolf", "mafia_boss"):
        na.setdefault("werewolf_votes", {})[uid] = target
    elif action == "guard_protect" and role == "guard":
        na["guard_target"] = target
    elif action == "doctor_heal" and role == "doctor":
        na["doctor_target"] = target
    elif action == "detective_inspect" and role == "detective":
        na["detective_target"] = target; na["detective_actor"] = uid
    elif action == "witch_heal" and role == "witch" and p["charges"].get("heal", 0) > 0:
        na["witch_heal"] = target; p["charges"]["heal"] = 0
    elif action == "witch_poison" and role == "witch" and p["charges"].get("poison", 0) > 0:
        na["witch_poison"] = target; p["charges"]["poison"] = 0
    elif action == "cupid_pair" and role == "cupid" and p["charges"].get("pair", 0) > 0 and room["night_count"] == 1:
        t2 = data.get("target2")
        if target and t2 and target != t2:
            room["lovers"] = (target, t2)
            p["charges"]["pair"] = 0
            for lid in (target, t2):
                sid = user_sid.get(lid)
                if sid: socketio.emit("lover_info", {"partner": room["players"][t2 if lid == target else target]["name"]}, room=sid)
    elif action == "puppeteer_force" and role == "puppeteer" and p["charges"].get("force", 0) > 0:
        na["puppeteer_forced"] = target; na["puppeteer_target_actor"] = data.get("target2")
        p["charges"]["force"] = 0
    emit("action_ack", {"action": action})

@socketio.on("cast_vote")
def on_vote(data):
    info = sid_to_user.get(request.sid)
    if not info or not info.get("room_id"): return
    room = rooms.get(info["room_id"])
    if not room or room["phase"] != "voting": return
    uid = info["user_id"]; p = room["players"].get(uid)
    if not p or not p["alive"]: return
    room["votes"][uid] = data.get("target")
    socketio.emit("vote_update", {"count": len(room["votes"]), "alive": len(get_alive(room))}, room=room["id"])

# ---------------- GAME LOOP ----------------
def wait_phase(rid, seconds):
    remaining = seconds
    while remaining > 0:
        room = rooms.get(rid)
        if not room or not room["active"]:
            return False
        if room["paused"]:
            socketio.sleep(1); continue
        socketio.sleep(1); remaining -= 1
    return True

def game_loop(rid):
    room = rooms.get(rid)
    socketio.sleep(3)
    while room and room["active"]:
        if not run_night(rid): return
        if not run_day(rid): return
        if not run_voting(rid): return
        winner = check_win(room)
        if winner:
            end_game(rid, winner); return
        room["votes"] = {}

def run_night(rid):
    room = rooms.get(rid)
    if not room: return False
    room["night_count"] += 1
    room["phase"] = "night"
    room["night_actions"] = {}
    socketio.emit("phase_change", {"phase": "night", "n": room["night_count"], "duration": PHASE_SECONDS["night"]}, room=rid)
    if not wait_phase(rid, PHASE_SECONDS["night"]): return False
    resolve_night(room)
    return True

def resolve_night(room):
    na = room["night_actions"]
    wolf_votes = list(na.get("werewolf_votes", {}).values())
    kill_target = None
    if wolf_votes:
        counts = {}
        for t in wolf_votes: counts[t] = counts.get(t, 0) + 1
        mx = max(counts.values())
        kill_target = random.choice([t for t, c in counts.items() if c == mx])

    saved = kill_target and (na.get("guard_target") == kill_target or na.get("doctor_target") == kill_target or na.get("witch_heal") == kill_target)
    deaths = []
    if kill_target and not saved and room["players"].get(kill_target, {}).get("alive"):
        deaths.append(kill_target)
    poison = na.get("witch_poison")
    if poison and room["players"].get(poison, {}).get("alive") and poison not in deaths:
        deaths.append(poison)

    lovers = room.get("lovers")
    for d in list(deaths):
        if lovers and d in lovers:
            other = lovers[0] if lovers[1] == d else lovers[1]
            if room["players"].get(other, {}).get("alive") and other not in deaths:
                deaths.append(other)

    final_deaths = []
    for d in deaths:
        if room["players"][d]["alive"]:
            room["players"][d]["alive"] = False
            room["dead_players"].append(d)
            final_deaths.append(d)
            if room["players"][d]["role"] == "hunter":
                hunter_team = ROLES["hunter"]["team"]
                opp = [pid for pid, p in room["players"].items() if p["alive"] and ROLES[p["role"]]["team"] != hunter_team]
                if opp:
                    victim = random.choice(opp)
                    room["players"][victim]["alive"] = False
                    room["dead_players"].append(victim)
                    final_deaths.append(victim)

    det_target = na.get("detective_target")
    if det_target and na.get("detective_actor"):
        sid = user_sid.get(na["detective_actor"])
        if sid and det_target in room["players"]:
            team = ROLES[room["players"][det_target]["role"]]["team"]
            socketio.emit("action_result", {"message": f"{room['players'][det_target]['name']} adalah tim {team.upper()}"}, room=sid)

    names = [room["players"][d]["name"] for d in final_deaths]
    socketio.emit("night_result", {"deaths": names, "died_ids": final_deaths}, room=room["id"])

def run_day(rid):
    room = rooms.get(rid)
    if not room: return False
    room["day_count"] += 1
    room["phase"] = "day"
    socketio.emit("phase_change", {
        "phase": "day", "n": room["day_count"], "duration": PHASE_SECONDS["day"],
        "alive": [{"id": pid, "name": p["name"]} for pid, p in get_alive(room).items()],
    }, room=rid)
    return wait_phase(rid, PHASE_SECONDS["day"])

def run_voting(rid):
    room = rooms.get(rid)
    if not room: return False
    room["phase"] = "voting"
    room["votes"] = {}
    socketio.emit("phase_change", {
        "phase": "voting", "duration": PHASE_SECONDS["voting"],
        "alive": [{"id": pid, "name": p["name"]} for pid, p in get_alive(room).items()],
    }, room=rid)
    if not wait_phase(rid, PHASE_SECONDS["voting"]): return False
    resolve_votes(room)
    return True

def resolve_votes(room):
    votes = dict(room["votes"])
    na = room.get("night_actions", {})
    forced_voter, forced_target = na.get("puppeteer_target_actor"), na.get("puppeteer_forced")
    if forced_voter and forced_target and forced_voter in room["players"]:
        votes[forced_voter] = forced_target
    if not votes:
        socketio.emit("vote_result", {"eliminated": None, "message": "Tidak ada suara, tidak ada yang tereliminasi"}, room=room["id"])
        return
    counts = {}
    for t in votes.values(): counts[t] = counts.get(t, 0) + 1
    mx = max(counts.values())
    top = [t for t, c in counts.items() if c == mx]
    if len(top) > 1:
        socketio.emit("vote_result", {"eliminated": None, "message": "Suara seri, tidak ada yang tereliminasi"}, room=room["id"])
        return
    elim = top[0]
    if elim in room["players"] and room["players"][elim]["alive"]:
        room["players"][elim]["alive"] = False
        room["dead_players"].append(elim)
        role = room["players"][elim]["role"]
        socketio.emit("vote_result", {
            "eliminated": elim, "name": room["players"][elim]["name"],
            "role": ROLES[role]["name"], "message": f"{room['players'][elim]['name']} dieliminasi warga!"
        }, room=room["id"])
        if role == "hunter":
            hteam = ROLES["hunter"]["team"]
            opp = [pid for pid, p in room["players"].items() if p["alive"] and ROLES[p["role"]]["team"] != hteam]
            if opp:
                victim = random.choice(opp)
                room["players"][victim]["alive"] = False
                room["dead_players"].append(victim)
                socketio.emit("night_result", {"deaths": [room["players"][victim]["name"]], "died_ids": [victim]}, room=room["id"])

def end_game(rid, winner):
    room = rooms.get(rid)
    if not room: return
    room["active"] = False
    room["phase"] = None
    reveal = [{"name": p["name"], "role": ROLES[p["role"]]["name"], "team": ROLES[p["role"]]["team"], "alive": p["alive"]} for p in room["players"].values()]
    socketio.emit("game_over", {"winner": winner, "reveal": reveal}, room=rid)
    socketio.emit("lobby_update", lobby_list())

# ---------------- STATIC ----------------
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/roles")
def api_roles():
    return jsonify({"roles": ROLES, "optional": OPTIONAL_ROLES, "default_enabled": DEFAULT_ENABLED})

# ---------------- TELEGRAM BOT ----------------
async def tg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🎮 Buka Werewolf Game", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        "🐺 *WEREWOLF GAME* 🐺\n\nTekan tombol di bawah untuk buka aplikasi game — register/login, buat room, dan main langsung di dalam Telegram!",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown",
    )

def run_bot():
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", tg_start))
    application.run_polling()

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
