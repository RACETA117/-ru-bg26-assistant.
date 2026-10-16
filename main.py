from __future__ import annotations

import asyncio
import base64
import html
import json
import os
import random
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "RU_BG26_bot").strip().lstrip("@")
DB_PATH = Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3"))
MASTER_IDS = {int(x.strip()) for x in os.getenv("MASTER_IDS", "").split(",") if x.strip().isdigit()}
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()

if not BOT_TOKEN:
    raise RuntimeError("Не указан BOT_TOKEN. Скопируй .env.example в .env и заполни BOT_TOKEN.")

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
router = Router()

# Сеансы интерфейса в памяти. Игровые данные (заметки, участники боя, привязки) хранятся в SQLite.
dice_configs: dict[int, dict[str, int]] = {}
encounter_configs: dict[int, dict[str, Any]] = {}
loot_configs: dict[int, dict[str, Any]] = {}
pending_queries: dict[int, str] = {}
recent_rolls: dict[str, dict[str, Any]] = {}

DND_LINKS = {
    "bestiary14": "https://www.dnd.su/bestiary/",
    "bestiary24": "https://next.dnd.su/bestiary/",
    "spells14": "https://www.dnd.su/spells/",
    "spells24": "https://next.dnd.su/spells/",
    "items14": "https://www.dnd.su/items/",
    "items24": "https://next.dnd.su/items/",
    "loot14": "https://www.dnd.su/loot/",
    "enc14": "https://www.dnd.su/encounter_difficulty/",
    "enc24": "https://next.dnd.su/encounter-planner/",
}

# Мини-индекс используется только для ориентировочной оценки столкновений (CR/XP),
# а не как копия статблоков D&D.su. Полный профиль открывается в справочнике.
MONSTERS: list[dict[str, Any]] = [
    {"name": "Кобольд", "key": "kobold", "aliases": ["кобольд", "kobold"], "cr": "1/8", "xp": 25, "env": ["пещера", "подземелье", "подземье"]},
    {"name": "Бандит", "key": "bandit", "aliases": ["бандит", "bandit"], "cr": "1/8", "xp": 25, "env": ["дорога", "город", "лес"]},
    {"name": "Гоблин", "key": "goblin", "aliases": ["гоблин", "goblin"], "cr": "1/4", "xp": 50, "env": ["лес", "пещера", "подземелье", "болото"]},
    {"name": "Скелет", "key": "skeleton", "aliases": ["скелет", "skeleton"], "cr": "1/4", "xp": 50, "env": ["подземелье", "руины", "кладбище"]},
    {"name": "Волк", "key": "wolf", "aliases": ["волк", "wolf"], "cr": "1/4", "xp": 50, "env": ["лес", "горы", "равнина"]},
    {"name": "Зомби", "key": "zombie", "aliases": ["зомби", "zombie"], "cr": "1/4", "xp": 50, "env": ["руины", "кладбище", "подземелье"]},
    {"name": "Орк", "key": "orc", "aliases": ["орк", "orc"], "cr": "1/2", "xp": 100, "env": ["горы", "пещера", "лес"]},
    {"name": "Упырь", "key": "ghoul", "aliases": ["упырь", "ghoul"], "cr": "1", "xp": 200, "env": ["кладбище", "руины", "подземелье"]},
    {"name": "Багбир", "key": "bugbear", "aliases": ["багбир", "bugbear"], "cr": "1", "xp": 200, "env": ["лес", "пещера", "подземелье"]},
    {"name": "Огр", "key": "ogre", "aliases": ["огр", "ogre"], "cr": "2", "xp": 450, "env": ["горы", "лес", "пещера"]},
    {"name": "Мимик", "key": "mimic", "aliases": ["мимик", "mimic"], "cr": "2", "xp": 450, "env": ["подземелье", "руины"]},
    {"name": "Совомедведь", "key": "owlbear", "aliases": ["совомедведь", "owlbear"], "cr": "3", "xp": 700, "env": ["лес", "горы"]},
    {"name": "Минотавр", "key": "minotaur", "aliases": ["минотавр", "minotaur"], "cr": "3", "xp": 700, "env": ["лабиринт", "подземелье", "руины"]},
    {"name": "Василиск", "key": "basilisk", "aliases": ["василиск", "basilisk"], "cr": "3", "xp": 700, "env": ["пещера", "руины"]},
    {"name": "Тролль", "key": "troll", "aliases": ["тролль", "troll"], "cr": "5", "xp": 1800, "env": ["болото", "пещера", "лес"]},
    {"name": "Виверна", "key": "wyvern", "aliases": ["виверна", "wyvern"], "cr": "6", "xp": 2300, "env": ["горы", "равнина"]},
    {"name": "Медуза", "key": "medusa", "aliases": ["медуза", "medusa"], "cr": "6", "xp": 2300, "env": ["руины", "подземелье"]},
    {"name": "Молодой красный дракон", "key": "young-red-dragon", "aliases": ["молодой красный дракон", "young red dragon"], "cr": "10", "xp": 5900, "env": ["горы", "пещера"]},
]

# Пороги XP на персонажа по таблице D&D 5e 2014. Генератор отмечает оценку как приблизительную.
XP_THRESHOLDS = {
    1: (25, 50, 75, 100), 2: (50, 100, 150, 200), 3: (75, 150, 225, 400),
    4: (125, 250, 375, 500), 5: (250, 500, 750, 1100), 6: (300, 600, 900, 1400),
    7: (350, 750, 1100, 1700), 8: (450, 900, 1400, 2100), 9: (550, 1100, 1600, 2400),
    10: (600, 1200, 1900, 2800), 11: (800, 1600, 2400, 3600), 12: (1000, 2000, 3000, 4500),
    13: (1100, 2200, 3400, 5100), 14: (1250, 2500, 3800, 5700), 15: (1400, 2800, 4300, 6400),
    16: (1600, 3200, 4800, 7200), 17: (2000, 3900, 5900, 8800), 18: (2100, 4200, 6300, 9500),
    19: (2400, 4900, 7300, 10900), 20: (2800, 5700, 8500, 12700),
}
DIFFICULTIES = {"Легко": 0, "Средне": 1, "Сложно": 2, "Смертельно": 3}
DIFFICULTY_KEYS = {"easy": "Легко", "medium": "Средне", "hard": "Сложно", "deadly": "Смертельно"}
ENVIRONMENTS = ["подземелье", "лес", "горы", "болото", "руины", "дорога"]

NPC_NAMES = ["Вельма Тихая", "Оррин Медноклык", "Севера Лунная", "Бранн Каменный Пояс", "Мираль Воронье Перо", "Торвен Серый", "Иллианна Вьюга", "Кестрел Пепельный"]
NPC_MANNERISMS = ["говорит шёпотом даже в шумной комнате", "постоянно вертит в пальцах монету", "избегает смотреть на двери", "помнит лица, но путает имена", "вставляет поговорки не к месту", "никогда не отвечает на вопрос сразу"]
NPC_GOALS = ["ищет пропавшего родственника", "хочет выкупить семейную реликвию", "пытается скрыть старую ошибку", "собирает сведения о тайном культе", "ищет надёжных телохранителей", "хочет отомстить бывшему покровителю"]
RANDOM_EVENTS = ["На дороге обнаружена свежая повозка без лошадей.", "Из ближайшего колодца доносится тихий звон.", "Незнакомец узнаёт одного из героев, хотя тот его не помнит.", "Ветер приносит обрывок карты с отмеченной точкой.", "Все животные в округе внезапно замолкают.", "На двери заброшенного дома появляется новый знак.", "Караван просит помощи, но его охрана что-то скрывает."]


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS group_invites (
            token TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS group_links (
            user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, title TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS campaign_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            note TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS combatants (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, name TEXT NOT NULL,
            initiative INTEGER NOT NULL, hp INTEGER NOT NULL, max_hp INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS combat_state (
            chat_id INTEGER PRIMARY KEY, current_id INTEGER
        );
        """)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def e(value: Any) -> str:
    return html.escape(str(value), quote=False)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Кубики", callback_data="menu:dice"), InlineKeyboardButton(text="🐉 Бестиарий", callback_data="menu:bestiary")],
        [InlineKeyboardButton(text="✨ Заклинания", callback_data="menu:spells"), InlineKeyboardButton(text="💎 Предметы", callback_data="menu:items")],
        [InlineKeyboardButton(text="🧰 Инструменты мастера", callback_data="menu:master"), InlineKeyboardButton(text="📚 Правила и лор", callback_data="menu:rules")],
        [InlineKeyboardButton(text="🔗 Связать с группой", callback_data="menu:linkhelp"), InlineKeyboardButton(text="❔ Помощь", callback_data="menu:help")],
    ])


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:home")]])


def source_keyboard(kind: str) -> InlineKeyboardMarkup:
    if kind == "bestiary":
        rows = [[InlineKeyboardButton(text="Бестиарий — правила 2014", url=DND_LINKS["bestiary14"])],
                [InlineKeyboardButton(text="Бестиарий — правила 2024", url=DND_LINKS["bestiary24"])]]
    elif kind == "spells":
        rows = [[InlineKeyboardButton(text="Заклинания — правила 2014", url=DND_LINKS["spells14"])],
                [InlineKeyboardButton(text="Заклинания — правила 2024", url=DND_LINKS["spells24"])]]
    else:
        rows = [[InlineKeyboardButton(text="Предметы — правила 2014", url=DND_LINKS["items14"])],
                [InlineKeyboardButton(text="Предметы — правила 2024", url=DND_LINKS["items24"])]]
    rows.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dice_keyboard(user_id: int) -> InlineKeyboardMarkup:
    cfg = dice_configs.setdefault(user_id, {"faces": 20, "count": 1, "bonus": 0})
    faces, count, bonus = cfg["faces"], cfg["count"], cfg["bonus"]
    btext = f"{bonus:+d}" if bonus else "0"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"d4{' ✓' if faces == 4 else ''}", callback_data="dice:face:4"), InlineKeyboardButton(text=f"d6{' ✓' if faces == 6 else ''}", callback_data="dice:face:6"), InlineKeyboardButton(text=f"d8{' ✓' if faces == 8 else ''}", callback_data="dice:face:8"), InlineKeyboardButton(text=f"d10{' ✓' if faces == 10 else ''}", callback_data="dice:face:10")],
        [InlineKeyboardButton(text=f"d12{' ✓' if faces == 12 else ''}", callback_data="dice:face:12"), InlineKeyboardButton(text=f"d20{' ✓' if faces == 20 else ''}", callback_data="dice:face:20"), InlineKeyboardButton(text=f"d100{' ✓' if faces == 100 else ''}", callback_data="dice:face:100")],
        [InlineKeyboardButton(text=f"1 кубик{' ✓' if count == 1 else ''}", callback_data="dice:count:1"), InlineKeyboardButton(text=f"2 кубика{' ✓' if count == 2 else ''}", callback_data="dice:count:2"), InlineKeyboardButton(text=f"5 кубиков{' ✓' if count == 5 else ''}", callback_data="dice:count:5")],
        [InlineKeyboardButton(text="−3", callback_data="dice:bonus:-3"), InlineKeyboardButton(text="−1", callback_data="dice:bonus:-1"), InlineKeyboardButton(text=f"Бонус {btext}", callback_data="dice:bonus:0"), InlineKeyboardButton(text="+1", callback_data="dice:bonus:1"), InlineKeyboardButton(text="+3", callback_data="dice:bonus:3"), InlineKeyboardButton(text="+5", callback_data="dice:bonus:5")],
        [InlineKeyboardButton(text="🎲 Бросить", callback_data="dice:do")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:home")],
    ])


def loot_keyboard(user_id: int) -> InlineKeyboardMarkup:
    cfg = loot_configs.setdefault(user_id, {"level": 1, "kind": "all"})
    level = cfg["level"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Ур. 1–4{' ✓' if level == 1 else ''}", callback_data="loot:level:1"), InlineKeyboardButton(text=f"Ур. 5–10{' ✓' if level == 5 else ''}", callback_data="loot:level:5")],
        [InlineKeyboardButton(text=f"Ур. 11–16{' ✓' if level == 11 else ''}", callback_data="loot:level:11"), InlineKeyboardButton(text=f"Ур. 17–20{' ✓' if level == 17 else ''}", callback_data="loot:level:17")],
        [InlineKeyboardButton(text="Монеты", callback_data="loot:kind:coins"), InlineKeyboardButton(text="Сокровища", callback_data="loot:kind:treasure"), InlineKeyboardButton(text="Магпредмет", callback_data="loot:kind:magic")],
        [InlineKeyboardButton(text="🎁 Сгенерировать", callback_data="loot:generate")],
        [InlineKeyboardButton(text="Открыть генератор D&D.su", url=DND_LINKS["loot14"])],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:home")],
    ])


def encounter_keyboard(user_id: int) -> InlineKeyboardMarkup:
    cfg = encounter_configs.setdefault(user_id, {"party": 4, "level": 3, "difficulty": "Средне", "env": "подземелье"})
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Партия: {cfg['party']}", callback_data="enc:noop"), *[InlineKeyboardButton(text=str(n), callback_data=f"enc:party:{n}") for n in (3, 4, 5, 6)]],
        [InlineKeyboardButton(text=f"Уровень: {cfg['level']}", callback_data="enc:noop"), *[InlineKeyboardButton(text=str(n), callback_data=f"enc:level:{n}") for n in (1, 3, 5, 8, 10, 15)]],
        [InlineKeyboardButton(text=f"Сложность: {cfg['difficulty']}", callback_data="enc:noop")],
        [InlineKeyboardButton(text=x, callback_data=f"enc:difficulty:{x}") for x in ("Легко", "Средне", "Сложно", "Смертельно")],
        [InlineKeyboardButton(text=f"Среда: {cfg['env']}", callback_data="enc:noop")],
        [InlineKeyboardButton(text=x, callback_data=f"enc:env:{x}") for x in ENVIRONMENTS],
        [InlineKeyboardButton(text="⚔️ Сгенерировать встречу", callback_data="enc:generate")],
        [InlineKeyboardButton(text="Планировщик D&D.su 2014", url=DND_LINKS["enc14"]), InlineKeyboardButton(text="Планировщик D&D.su 2024", url=DND_LINKS["enc24"])],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:home")],
    ])


def roll_parse(text: str) -> tuple[int, int, int] | None:
    raw = text.strip()
    # /roll 5d8+3 and /d20+5, /5d8 or 5d8+3 in a private chat
    if raw.startswith("/"):
        raw = raw[1:]
        if "@" in raw:
            raw = raw.split("@", 1)[0]
        if raw.lower().startswith("roll "):
            raw = raw[5:].strip()
    m = re.fullmatch(r"(?i)(?:(\d{1,3})?d(\d{1,5}))(?:([+-]\d{1,6}))?", raw)
    if not m:
        return None
    count = int(m.group(1) or 1)
    sides = int(m.group(2))
    bonus = int(m.group(3) or 0)
    if count < 1 or count > 100 or sides < 2 or sides > 10000 or abs(bonus) > 100000:
        return None
    return count, sides, bonus


def roll_values(count: int, sides: int, bonus: int) -> dict[str, Any]:
    values = [random.randint(1, sides) for _ in range(count)]
    subtotal = sum(values)
    total = subtotal + bonus
    return {"count": count, "sides": sides, "bonus": bonus, "values": values, "subtotal": subtotal, "total": total}


def roll_text(result: dict[str, Any], who: str | None = None) -> str:
    c, s, b = result["count"], result["sides"], result["bonus"]
    expr = f"{c}d{s}" if c != 1 else f"d{s}"
    if b:
        expr += f" {b:+d}"
    title = f"🎲 <b>{e(who) + ': ' if who else ''}Бросок {expr}</b>"
    vals = ", ".join(str(v) for v in result["values"])
    bonus_line = f"Бонус: <b>{b:+d}</b>\n" if b else "Бонус: <b>0</b>\n"
    return f"{title}\nКубики: <code>{vals}</code>\nСумма кубиков: <b>{result['subtotal']}</b>\n{bonus_line}━━━━━━━━━━━━\nИтог: <b>{result['total']}</b>"


def roll_result_keyboard(roll_id: str, is_private: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🎲 Бросить ещё раз", callback_data=f"roll:again:{roll_id}")]]
    if is_private:
        rows.insert(0, [InlineKeyboardButton(text="📣 Отправить в беседу", callback_data=f"roll:share:{roll_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def is_private(message: Message) -> bool:
    return bool(message.chat and message.chat.type == ChatType.PRIVATE)

async def try_roll_message(message: Message, text: str | None = None) -> bool:
    raw = (text if text is not None else (message.text or "")).strip()
    parsed = roll_parse(raw)
    if parsed is None:
        return False
    count, sides, bonus = parsed
    result = roll_values(count, sides, bonus)
    roll_id = secrets.token_urlsafe(7).replace("-", "a").replace("_", "b")[:12]
    recent_rolls[roll_id] = {"result": result, "owner": message.from_user.id if message.from_user else 0,
                             "name": message.from_user.full_name if message.from_user else "Игрок"}
    is_dm = is_private(message)
    await message.answer(roll_text(result, message.from_user.full_name if not is_dm and message.from_user else None),
                         reply_markup=roll_result_keyboard(roll_id, is_dm))
    return True


def bestiary_keyboard() -> InlineKeyboardMarkup:
    return source_keyboard("bestiary")

async def is_master(message: Message, bot: Bot) -> bool:
    uid = message.from_user.id if message.from_user else 0
    if uid in MASTER_IDS:
        return True
    if message.chat.type == ChatType.PRIVATE:
        return False
    try:
        member = await bot.get_chat_member(message.chat.id, uid)
        return str(member.status) in {"creator", "administrator"}
    except Exception:
        return False


async def callback_is_master(call: CallbackQuery, bot: Bot) -> bool:
    if call.from_user.id in MASTER_IDS:
        return True
    if not call.message or call.message.chat.type == ChatType.PRIVATE:
        return False
    try:
        member = await bot.get_chat_member(call.message.chat.id, call.from_user.id)
        return str(member.status) in {"creator", "administrator"}
    except Exception:
        return False


def xp_multiplier(monster_count: int) -> float:
    if monster_count <= 1:
        return 1.0
    if monster_count == 2:
        return 1.5
    if monster_count <= 6:
        return 2.0
    if monster_count <= 10:
        return 2.5
    if monster_count <= 14:
        return 3.0
    return 4.0


def generate_encounter(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    party = int(cfg["party"])
    level = max(1, min(20, int(cfg["level"])))
    difficulty = DIFFICULTIES.get(cfg["difficulty"], 1)
    target = XP_THRESHOLDS[level][difficulty] * party
    env = str(cfg["env"])
    pool = [m for m in MONSTERS if env in m["env"]]
    if not pool:
        pool = MONSTERS[:]
    chosen: list[dict[str, Any]] = []
    for _ in range(10):
        adj = sum(m["xp"] for m in chosen) * xp_multiplier(len(chosen)) if chosen else 0
        if chosen and adj >= target * 0.82:
            break
        potential = []
        for mon in pool:
            n = len(chosen) + 1
            new_adj = (sum(m["xp"] for m in chosen) + mon["xp"]) * xp_multiplier(n)
            if new_adj <= target * 1.12 or not chosen:
                potential.append(mon)
        if not potential:
            break
        # На низких уровнях предпочитаем не брать противника, который один превышает весь бюджет.
        lower = [m for m in potential if m["xp"] <= max(25, target)]
        if lower:
            potential = lower
        chosen.append(random.choice(potential))
    base = sum(m["xp"] for m in chosen)
    adjusted = int(round(base * xp_multiplier(len(chosen))))
    return chosen, target, adjusted


def generate_loot(cfg: dict[str, Any]) -> str:
    level = int(cfg.get("level", 1))
    kind = cfg.get("kind", "all")
    bands = {1: (5, 40), 5: (30, 180), 11: (120, 700), 17: (500, 2500)}
    band = max(k for k in bands if k <= level)
    low, high = bands[band]
    pieces: list[str] = []
    if kind in ("all", "coins"):
        gold = random.randint(low, high)
        silver = random.randint(0, 9) * 10
        pieces.append(f"💰 Монеты: {gold} зм. и {silver} см.")
    if kind in ("all", "treasure"):
        curiosities = ["старинный серебряный медальон", "карта с выцветшей печатью", "мешочек полированных агатов", "миниатюрная статуэтка неизвестного святого", "дневник с вырванными страницами", "шкатулка с тремя неизвестными ключами"]
        pieces.append(f"💎 Находка: {random.choice(curiosities)}")
    if kind in ("all", "magic"):
        rarity = ("обычный", "необычный") if level < 5 else ("необычный", "редкий") if level < 11 else ("редкий", "очень редкий") if level < 17 else ("очень редкий", "легендарный")
        items = ["оружие с рунической гравировкой", "плащ, пахнущий грозой", "перчатки с серебряными швами", "компас, который указывает на ближайшую тайну", "кольцо с треснувшим камнем", "складной фонарь с голубым пламенем"]
        pieces.append(f"✨ Магический предмет (на усмотрение мастера): {random.choice(items)} — редкость {random.choice(rarity)}")
    pieces.append("\nЭто авторская случайная заготовка, а не дословная таблица D&D.su. Проверь баланс и совместимость с редакцией кампании.")
    return "\n".join(pieces)


def monster_match(query: str) -> dict[str, Any] | None:
    q = query.casefold().strip()
    if not q:
        return None
    matches = []
    for m in MONSTERS:
        if any(q == alias.casefold() or q in alias.casefold() for alias in m["aliases"]):
            matches.append(m)
    return max(matches, key=lambda x: len(x["name"])) if matches else None


@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject, bot: Bot) -> None:
    args = (command.args or "").strip()
    if args.startswith("link_"):
        token = args[5:]
        with db() as con:
            inv = con.execute("SELECT * FROM group_invites WHERE token=?", (token,)).fetchone()
            if inv:
                con.execute("INSERT OR REPLACE INTO group_links(user_id,chat_id,title,created_at) VALUES(?,?,?,?)",
                            (message.from_user.id, inv["chat_id"], inv["title"], now_iso()))
                await message.answer(f"✅ Кабинет связан с беседой <b>{e(inv['title'])}</b>. Теперь результаты кубиков можно отправлять туда.", reply_markup=main_menu())
                return
        await message.answer("Эта ссылка для привязки устарела. Вызови /link в нужной беседе и открой новую ссылку.", reply_markup=main_menu())
        return
    await message.answer(
        "<b>РУ • D&D-компаньон</b>\n\nВыбери действие кнопками. В личном кабинете можно бросать кубики, открывать справочники и пользоваться инструментами мастера. В беседе доступны команды.",
        reply_markup=main_menu())


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await message.answer("<b>Меню D&D-компаньона</b>", reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Команды</b>\n"
        "🎲 Кубики: <code>/d20</code>, <code>/d20+5</code>, <code>/roll 5d8+3</code>.\n"
        "В личке также можно отправить <code>/5d8</code>. В группах для формата, начинающегося с цифры, может потребоваться отключить Privacy Mode у BotFather; альтернатива — <code>/roll 5d8</code>.\n\n"
        "🐉 <code>/monster goblin</code> — быстрый индекс CR/XP и ссылки на бестиарий.\n"
        "✨ <code>/spell fireball</code>, 💎 <code>/item плащ защиты</code> — справочники по редакциям.\n"
        "🧰 <code>/loot</code>, <code>/encounter</code>, <code>/npc</code>, <code>/event</code> — генераторы мастера.\n"
        "⚔️ <code>/init</code>, <code>/init add Гоблин 12 7</code>, <code>/init next</code>, <code>/init hp Гоблин -3</code>.\n"
        "📝 <code>/note текст</code>, <code>/notes</code> — заметки кампании. <code>/ask вопрос</code> — ИИ-помощник (нужен API-ключ).\n"
        "🔗 <code>/link</code> в группе — привязать личный кабинет к беседе. <code>/myid</code> — узнать числовой Telegram ID.",
        reply_markup=main_menu())


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>\nДобавь его в MASTER_IDS в файле .env, если это ID мастера.")


@router.message(Command("link"))
async def cmd_link(message: Message, bot: Bot) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Чтобы связать кабинет с группой, добавь бота в беседу, вызови там /link и открой полученную ссылку здесь.")
        return
    token = secrets.token_urlsafe(12).replace("=", "")
    title = message.chat.title or "D&D-кампания"
    with db() as con:
        con.execute("INSERT INTO group_invites(token,chat_id,title,created_at) VALUES(?,?,?,?)", (token, message.chat.id, title, now_iso()))
    me = await bot.get_me()
    username = me.username or BOT_USERNAME
    url = f"https://t.me/{username}?start=link_{token}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 Связать мой кабинет с этой беседой", url=url)]])
    await message.answer("Чтобы отправлять броски из личного меню в эту группу, каждый игрок должен один раз нажать кнопку и запустить бота в личке.", reply_markup=kb)


@router.message(Command("roll"))
async def cmd_roll(message: Message, command: CommandObject) -> None:
    expr = (command.args or "").strip()
    if not expr:
        await message.answer("Формат: <code>/roll 5d8+3</code> или <code>/roll d20-1</code>.")
        return
    await try_roll_message(message, expr)


@router.message(Command("monster"))
async def cmd_monster(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Напиши название, например <code>/monster гоблин</code>.", reply_markup=bestiary_keyboard())
        return
    m = monster_match(query)
    if m:
        text = f"🐉 <b>{e(m['name'])}</b>\nОриентировочный индекс: ПО {e(m['cr'])}, {m['xp']} XP.\nПолный статблок и особенности открой на D&D.su в выбранной редакции."
    else:
        text = f"🔎 Запрос: <b>{e(query)}</b>\nВстроенный стартовый индекс не содержит этой записи. Открой каталог и введи название в поле «Поиск по разделу»."
    await message.answer(text, reply_markup=bestiary_keyboard())


@router.message(Command("spell"))
async def cmd_spell(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    text = f"✨ Поиск заклинания: <b>{e(query)}</b>\nОткрой нужную редакцию и введи название в поиске по разделу." if query else "✨ Выбери справочник заклинаний нужной редакции."
    await message.answer(text, reply_markup=source_keyboard("spells"))


@router.message(Command("item"))
async def cmd_item(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    text = f"💎 Поиск предмета: <b>{e(query)}</b>\nОткрой нужную редакцию и введи название в поиске по разделу." if query else "💎 Выбери справочник предметов нужной редакции."
    await message.answer(text, reply_markup=source_keyboard("items"))


@router.message(Command("loot"))
async def cmd_loot(message: Message, bot: Bot) -> None:
    if not await is_master(message, bot):
        await message.answer("Инструменты генерации добычи доступны мастеру или администраторам группы.")
        return
    uid = message.from_user.id
    loot_configs.setdefault(uid, {"level": 1, "kind": "all"})
    await message.answer("<b>🎁 Генератор добычи</b>\nВыбери диапазон уровня и тип награды.", reply_markup=loot_keyboard(uid))


@router.message(Command("encounter"))
async def cmd_encounter(message: Message, bot: Bot) -> None:
    if not await is_master(message, bot):
        await message.answer("Генератор столкновений доступен мастеру или администраторам группы.")
        return
    uid = message.from_user.id
    encounter_configs.setdefault(uid, {"party": 4, "level": 3, "difficulty": "Средне", "env": "подземелье"})
    await message.answer("<b>⚔️ Генератор столкновения</b>\nНастрой партию, уровень, сложность и окружение.", reply_markup=encounter_keyboard(uid))


@router.message(Command("npc"))
async def cmd_npc(message: Message, bot: Bot) -> None:
    if not await is_master(message, bot):
        await message.answer("Генератор NPC доступен мастеру или администраторам группы.")
        return
    await message.answer(
        f"<b>🧙 Случайный NPC</b>\nИмя: <b>{e(random.choice(NPC_NAMES))}</b>\nМанера: {e(random.choice(NPC_MANNERISMS))}.\nЦель: {e(random.choice(NPC_GOALS))}.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Ещё NPC", callback_data="random:npc")], [InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:home")]]))


@router.message(Command("event"))
async def cmd_event(message: Message, bot: Bot) -> None:
    if not await is_master(message, bot):
        await message.answer("Генератор событий доступен мастеру или администраторам группы.")
        return
    await message.answer(f"<b>🎲 Случайное событие</b>\n{e(random.choice(RANDOM_EVENTS))}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Ещё событие", callback_data="random:event")], [InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:home")]]))


@router.message(Command("note"))
async def cmd_note(message: Message, command: CommandObject, bot: Bot) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Заметки кампании сохраняются из общей группы: используй там <code>/note текст заметки</code>.")
        return
    if not await is_master(message, bot):
        await message.answer("Добавлять заметки может мастер или администратор группы.")
        return
    note = (command.args or "").strip()
    if not note:
        await message.answer("Формат: <code>/note Советник признался, что видел герб на севере</code>")
        return
    with db() as con:
        con.execute("INSERT INTO campaign_notes(chat_id,user_id,note,created_at) VALUES(?,?,?,?)", (message.chat.id, message.from_user.id, note[:3000], now_iso()))
    await message.answer("📝 Заметка добавлена в журнал кампании.")


@router.message(Command("notes"))
async def cmd_notes(message: Message) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Журнал открывается в группе, где сохранены заметки.")
        return
    with db() as con:
        rows = con.execute("SELECT note,created_at FROM campaign_notes WHERE chat_id=? ORDER BY id DESC LIMIT 10", (message.chat.id,)).fetchall()
    if not rows:
        await message.answer("Пока нет заметок. Мастер может добавить первую командой /note текст.")
        return
    body = "<b>📝 Последние заметки кампании</b>\n\n" + "\n\n".join(f"• {e(r['note'])}\n<code>{e(r['created_at'][:16].replace('T',' '))} UTC</code>" for r in rows)
    await message.answer(body[:4000])


@router.message(Command("init"))
async def cmd_init(message: Message, command: CommandObject, bot: Bot) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Трекер инициативы работает в общей группе. Добавь бота туда и используй /init.")
        return
    args = (command.args or "").strip()
    if not args:
        with db() as con:
            rows = con.execute("SELECT id,name,initiative,hp,max_hp FROM combatants WHERE chat_id=? ORDER BY initiative DESC,id", (message.chat.id,)).fetchall()
            state = con.execute("SELECT current_id FROM combat_state WHERE chat_id=?", (message.chat.id,)).fetchone()
        if not rows:
            await message.answer("⚔️ Инициатива пуста. Добавь участника: <code>/init add Гоблин 12 7</code>\nЗатем используй <code>/init next</code>.")
            return
        current_id = state["current_id"] if state else None
        lines = ["<b>⚔️ Порядок инициативы</b>"]
        for i, r in enumerate(rows, 1):
            marker = "▶️ " if current_id == r["id"] else ""
            lines.append(f"{marker}{i}. <b>{e(r['name'])}</b> — инициатива {r['initiative']}, HP {r['hp']}/{r['max_hp']}")
        lines.append("\nКоманды: <code>/init add Имя Иниц HP</code>, <code>/init next</code>, <code>/init hp Имя -3</code>, <code>/init clear</code>.")
        await message.answer("\n".join(lines))
        return
    if not await is_master(message, bot):
        await message.answer("Менять трекер боя может мастер или администратор группы.")
        return
    parts = args.split()
    action = parts[0].lower()
    with db() as con:
        if action == "add" and len(parts) >= 4:
            name = " ".join(parts[1:-2])[:80]
            try:
                initiative, hp = int(parts[-2]), int(parts[-1])
            except ValueError:
                await message.answer("Формат: <code>/init add Гоблин 12 7</code>")
                return
            if not name or hp < 0 or hp > 99999:
                await message.answer("Имя или HP указаны некорректно.")
                return
            cur = con.execute("INSERT INTO combatants(chat_id,name,initiative,hp,max_hp) VALUES(?,?,?,?,?)", (message.chat.id, name, initiative, hp, hp))
            con.execute("INSERT OR IGNORE INTO combat_state(chat_id,current_id) VALUES(?,?)", (message.chat.id, cur.lastrowid))
            await message.answer(f"✅ Добавлен: <b>{e(name)}</b>, инициатива {initiative}, HP {hp}.\nПосмотреть порядок: /init")
        elif action == "next":
            rows = con.execute("SELECT id,name,initiative FROM combatants WHERE chat_id=? ORDER BY initiative DESC,id", (message.chat.id,)).fetchall()
            if not rows:
                await message.answer("Сначала добавь участников через /init add Имя Иниц HP.")
                return
            state = con.execute("SELECT current_id FROM combat_state WHERE chat_id=?", (message.chat.id,)).fetchone()
            ids = [r["id"] for r in rows]
            current_id = state["current_id"] if state else None
            idx = ids.index(current_id) if current_id in ids else -1
            nxt = rows[(idx + 1) % len(rows)]
            con.execute("INSERT OR REPLACE INTO combat_state(chat_id,current_id) VALUES(?,?)", (message.chat.id, nxt["id"]))
            await message.answer(f"▶️ Ход: <b>{e(nxt['name'])}</b> (инициатива {nxt['initiative']})")
        elif action == "hp" and len(parts) >= 3:
            name = " ".join(parts[1:-1])
            try:
                delta = int(parts[-1])
            except ValueError:
                await message.answer("Формат: <code>/init hp Гоблин -3</code>")
                return
            row = con.execute("SELECT id,hp,max_hp FROM combatants WHERE chat_id=? AND name=? COLLATE NOCASE ORDER BY id LIMIT 1", (message.chat.id, name)).fetchone()
            if not row:
                await message.answer("Участник не найден. Посмотри имена командой /init.")
                return
            hp = max(0, min(row["max_hp"], row["hp"] + delta))
            con.execute("UPDATE combatants SET hp=? WHERE id=?", (hp, row["id"]))
            change = f"{delta:+d}"
            await message.answer(f"❤️ <b>{e(name)}</b>: {row['hp']} {change} → <b>{hp}/{row['max_hp']} HP</b>")
        elif action == "clear":
            con.execute("DELETE FROM combatants WHERE chat_id=?", (message.chat.id,))
            con.execute("DELETE FROM combat_state WHERE chat_id=?", (message.chat.id,))
            await message.answer("🧹 Трекер инициативы очищен.")
        else:
            await message.answer("Не понял команду. См. /init. Добавлять участников: <code>/init add Гоблин 12 7</code>.")


@router.message(Command("ask"))
async def cmd_ask(message: Message, command: CommandObject, bot: Bot) -> None:
    if not await is_master(message, bot):
        await message.answer("ИИ-помощник мастера доступен мастеру или администратору группы.")
        return
    question = (command.args or "").strip()
    if not question:
        await message.answer("Формат: <code>/ask Как сбалансировать бой для 4 персонажей 5 уровня?</code>")
        return
    if not OPENAI_API_KEY:
        await message.answer("ИИ пока не подключён. На хостинге нужно задать OPENAI_API_KEY и OPENAI_MODEL в .env. Не отправляй API-ключ в Telegram или сюда в чат.")
        return
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        prompt = ("Ты русскоязычный помощник мастера D&D. Мы смешиваем 5e 2014 и 2024. "
                  "Всегда уточняй редакцию, различай официальные правила и свои советы, не выдумывай точные цитаты. "
                  "Если правило зависит от редакции, объясни обе версии. Ответ короткий и практичный.\n\nВопрос: " + question)
        response = await client.responses.create(model=OPENAI_MODEL, input=prompt)
        answer = getattr(response, "output_text", "") or "Не удалось получить текстовый ответ."
        await message.answer("🧠 <b>Помощник мастера</b>\n" + e(answer[:3500]))
    except Exception as exc:
        print(f"AI error: {type(exc).__name__}: {exc}")
        await message.answer("Не получилось получить ответ от ИИ. Проверь API-ключ, выбранную модель и доступность API на сервере.")


@router.callback_query(F.data.startswith("menu:"))
async def menu_callback(call: CallbackQuery, bot: Bot) -> None:
    action = call.data.split(":", 1)[1]
    uid = call.from_user.id
    if action == "home":
        await call.message.edit_text("<b>РУ • D&D-компаньон</b>\nВыбери раздел:", reply_markup=main_menu())
    elif action == "dice":
        dice_configs.setdefault(uid, {"faces": 20, "count": 1, "bonus": 0})
        await call.message.edit_text("<b>🎲 Кубики</b>\nВыбери кость, количество и бонус, затем нажми «Бросить». В группе также можно использовать команды /d20 или /roll 5d8+3.", reply_markup=dice_keyboard(uid))
    elif action == "bestiary":
        await call.message.edit_text("<b>🐉 Бестиарий</b>\nВыбери редакцию. Каталог D&D.su содержит собственный поиск по разделу; бот пока даёт ссылки и локальный краткий индекс, а не копирует полные статблоки.", reply_markup=bestiary_keyboard())
    elif action == "spells":
        await call.message.edit_text("<b>✨ Заклинания</b>\nВыбери редакцию и используй поиск внутри справочника.", reply_markup=source_keyboard("spells"))
    elif action == "items":
        await call.message.edit_text("<b>💎 Магические предметы</b>\nВыбери редакцию и используй поиск внутри справочника.", reply_markup=source_keyboard("items"))
    elif action == "master":
        if not await callback_is_master(call, bot):
            await call.answer("Инструменты мастера доступны мастеру или администраторам группы.", show_alert=True)
            return
        await call.message.edit_text("<b>🧰 Инструменты мастера</b>\nГенераторы работают в личном кабинете для владельцев/назначенных мастеров и в группе для администраторов. Команды: /loot, /encounter, /npc, /event, /ask.\n\nВыбери инструмент:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Генератор лута", callback_data="master:loot")],
            [InlineKeyboardButton(text="⚔️ Генератор противников", callback_data="master:encounter")],
            [InlineKeyboardButton(text="🧙 Случайный NPC", callback_data="random:npc")],
            [InlineKeyboardButton(text="🌩 Случайное событие", callback_data="random:event")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:home")],
        ]))
    elif action == "rules":
        await call.message.edit_text("<b>📚 Правила и лор</b>\nИспользуй /spell, /item и /monster для перехода к справочникам. В общей группе мастер может хранить заметки через /note и просматривать их через /notes. ИИ-помощник вызывается через /ask, если настроен API-ключ.", reply_markup=back_menu())
    elif action == "linkhelp":
        await call.message.edit_text("<b>🔗 Связать кабинет с группой</b>\n1. Добавь этого бота в общую беседу.\n2. В группе отправь /link.\n3. Нажми выданную ссылку и нажми Start в личке.\n4. Теперь у бросков в личном меню появится кнопка публикации результата в эту группу.", reply_markup=back_menu())
    elif action == "help":
        await call.message.edit_text("Открой /help для списка команд. Кратко: кубики — /d20, /roll 5d8+3; бестиарий — /monster goblin; мастер — /loot, /encounter, /npc, /event.", reply_markup=back_menu())
    await call.answer()


@router.callback_query(F.data.startswith("dice:"))
async def dice_callback(call: CallbackQuery, bot: Bot) -> None:
    uid = call.from_user.id
    cfg = dice_configs.setdefault(uid, {"faces": 20, "count": 1, "bonus": 0})
    parts = call.data.split(":")
    if parts[1] == "face":
        cfg["faces"] = int(parts[2])
    elif parts[1] == "count":
        cfg["count"] = int(parts[2])
    elif parts[1] == "bonus":
        cfg["bonus"] = int(parts[2])
    elif parts[1] == "do":
        result = roll_values(cfg["count"], cfg["faces"], cfg["bonus"])
        rid = secrets.token_urlsafe(7).replace("-", "a").replace("_", "b")[:12]
        recent_rolls[rid] = {"result": result, "owner": uid, "name": call.from_user.full_name}
        await call.message.answer(roll_text(result), reply_markup=roll_result_keyboard(rid, call.message.chat.type == ChatType.PRIVATE))
        await call.answer("Бросок выполнен")
        return
    await call.message.edit_reply_markup(reply_markup=dice_keyboard(uid))
    await call.answer()


@router.callback_query(F.data.startswith("roll:"))
async def roll_callback(call: CallbackQuery, bot: Bot) -> None:
    _, action, rid = call.data.split(":", 2)
    data = recent_rolls.get(rid)
    if not data:
        await call.answer("Результат уже не доступен в памяти. Сделай новый бросок.", show_alert=True)
        return
    if action == "again":
        old = data["result"]
        result = roll_values(old["count"], old["sides"], old["bonus"])
        new_id = secrets.token_urlsafe(7).replace("-", "a").replace("_", "b")[:12]
        recent_rolls[new_id] = {"result": result, "owner": call.from_user.id, "name": call.from_user.full_name}
        await call.message.answer(roll_text(result, call.from_user.full_name if call.message.chat.type != ChatType.PRIVATE else None), reply_markup=roll_result_keyboard(new_id, call.message.chat.type == ChatType.PRIVATE))
        await call.answer("Новый бросок выполнен")
    elif action == "share":
        if data["owner"] != call.from_user.id:
            await call.answer("Это не твой бросок.", show_alert=True)
            return
        with db() as con:
            link = con.execute("SELECT chat_id,title FROM group_links WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (call.from_user.id,)).fetchone()
        if not link:
            await call.answer("Сначала свяжи личку с группой командой /link в беседе.", show_alert=True)
            return
        txt = roll_text(data["result"], data.get("name", call.from_user.full_name))
        try:
            await bot.send_message(link["chat_id"], txt)
            await call.answer(f"Отправлено в «{link['title']}»")
        except Exception:
            await call.answer("Не удалось отправить. Проверь, что бот остался в группе.", show_alert=True)


@router.callback_query(F.data.startswith("loot:"))
async def loot_callback(call: CallbackQuery, bot: Bot) -> None:
    if not await callback_is_master(call, bot):
        await call.answer("Генератор добычи доступен мастеру или администраторам группы.", show_alert=True)
        return
    uid = call.from_user.id
    cfg = loot_configs.setdefault(uid, {"level": 1, "kind": "all"})
    parts = call.data.split(":")
    if parts[1] == "level":
        cfg["level"] = int(parts[2])
        await call.message.edit_reply_markup(reply_markup=loot_keyboard(uid))
    elif parts[1] == "kind":
        cfg["kind"] = parts[2]
        await call.message.edit_reply_markup(reply_markup=loot_keyboard(uid))
    elif parts[1] == "generate":
        await call.message.answer("<b>🎁 Сгенерированная добыча</b>\n" + e(generate_loot(cfg)))
    await call.answer()


@router.callback_query(F.data.startswith("enc:"))
async def encounter_callback(call: CallbackQuery, bot: Bot) -> None:
    if not await callback_is_master(call, bot):
        await call.answer("Генератор столкновений доступен мастеру или администраторам группы.", show_alert=True)
        return
    uid = call.from_user.id
    cfg = encounter_configs.setdefault(uid, {"party": 4, "level": 3, "difficulty": "Средне", "env": "подземелье"})
    parts = call.data.split(":")
    if parts[1] == "party":
        cfg["party"] = int(parts[2])
    elif parts[1] == "level":
        cfg["level"] = int(parts[2])
    elif parts[1] == "difficulty":
        cfg["difficulty"] = parts[2]
    elif parts[1] == "env":
        cfg["env"] = parts[2]
    elif parts[1] == "generate":
        chosen, target, adjusted = generate_encounter(cfg)
        if not chosen:
            await call.message.answer("Не удалось подобрать встречу; измени настройки.")
            await call.answer()
            return
        lines = ["<b>⚔️ Черновик столкновения</b>", f"Партия: {cfg['party']} персонажа, уровень {cfg['level']}; сложность: {e(cfg['difficulty'])}; среда: {e(cfg['env'])}.", ""]
        for mon in chosen:
            lines.append(f"• {e(mon['name'])} — ПО {e(mon['cr'])}, {mon['xp']} XP")
        lines.extend(["", f"Базовый XP: {sum(m['xp'] for m in chosen)}", f"XP с множителем за количество: {adjusted}", f"Ориентир бюджета: {target} XP.", "", "⚠️ Оценка ориентируется на пороги D&D 5e 2014 и небольшой локальный индекс. Это черновик, не полноценный расчёт 2024; проверь способности, окружение и фактическую сложность. Полные статблоки — в каталоге D&D.su."])
        await call.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть бестиарий 2014", url=DND_LINKS["bestiary14"]), InlineKeyboardButton(text="Открыть бестиарий 2024", url=DND_LINKS["bestiary24"])]]))
        await call.answer("Встреча сгенерирована")
        return
    if parts[1] == "noop":
        await call.answer()
        return
    await call.message.edit_reply_markup(reply_markup=encounter_keyboard(uid))
    await call.answer()


@router.callback_query(F.data.startswith("master:"))
async def master_callback(call: CallbackQuery, bot: Bot) -> None:
    action = call.data.split(":", 1)[1]
    uid = call.from_user.id
    if not await callback_is_master(call, bot):
        await call.answer("Добавь свой ID в MASTER_IDS или используй меню от имени администратора группы.", show_alert=True)
        return
    if action == "loot":
        loot_configs.setdefault(uid, {"level": 1, "kind": "all"})
        await call.message.answer("<b>🎁 Генератор добычи</b>\nВыбери настройки:", reply_markup=loot_keyboard(uid))
    elif action == "encounter":
        encounter_configs.setdefault(uid, {"party": 4, "level": 3, "difficulty": "Средне", "env": "подземелье"})
        await call.message.answer("<b>⚔️ Генератор столкновения</b>\nВыбери настройки:", reply_markup=encounter_keyboard(uid))
    await call.answer()


@router.callback_query(F.data.startswith("random:"))
async def random_callback(call: CallbackQuery, bot: Bot) -> None:
    action = call.data.split(":", 1)[1]
    # Генераторы доступны назначенному мастеру или администратору группы.
    if not await callback_is_master(call, bot):
        await call.answer("Инструменты мастера доступны мастеру или администраторам группы.", show_alert=True)
        return
    if action == "npc":
        text = f"<b>🧙 Случайный NPC</b>\nИмя: <b>{e(random.choice(NPC_NAMES))}</b>\nМанера: {e(random.choice(NPC_MANNERISMS))}.\nЦель: {e(random.choice(NPC_GOALS))}."
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Ещё NPC", callback_data="random:npc")], [InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:home")]])
    else:
        text = f"<b>🎲 Случайное событие</b>\n{e(random.choice(RANDOM_EVENTS))}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Ещё событие", callback_data="random:event")], [InlineKeyboardButton(text="⬅️ Меню", callback_data="menu:home")]])
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


@router.message(F.text)
async def catch_dice_syntax(message: Message) -> None:
    text = message.text or ""
    # Прямой короткий синтаксис /d20+5, /5d8 и /d20 работает, если Telegram доставил сообщение боту.
    # В обычном групповом режиме команды с цифры могут не распознаваться как команды Telegram.
    await try_roll_message(message, text)


async def main() -> None:
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    commands = [
        BotCommand(command="start", description="Открыть меню"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="roll", description="Бросок, например 5d8+3"),
        BotCommand(command="d20", description="Бросить d20"),
        BotCommand(command="monster", description="Поиск монстра"),
        BotCommand(command="spell", description="Поиск заклинания"),
        BotCommand(command="item", description="Поиск предмета"),
        BotCommand(command="loot", description="Генератор добычи (мастер)"),
        BotCommand(command="encounter", description="Генератор боя (мастер)"),
        BotCommand(command="npc", description="Случайный NPC (мастер)"),
        BotCommand(command="event", description="Случайное событие (мастер)"),
        BotCommand(command="init", description="Трекер инициативы"),
        BotCommand(command="link", description="Связать личный кабинет с группой"),
        BotCommand(command="note", description="Добавить заметку мастера"),
        BotCommand(command="notes", description="Последние заметки кампании"),
        BotCommand(command="ask", description="Вопрос ИИ-помощнику мастера"),
        BotCommand(command="myid", description="Показать мой Telegram ID"),
    ]
    await bot.set_my_commands(commands)
    me = await bot.get_me()
    print(f"Started @{me.username}. DB: {DB_PATH.resolve()}")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
