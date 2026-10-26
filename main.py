"""
Поместье — Telegram-бот для Pydroid
Работает на python-telegram-bot
"""

import logging
import random
import time
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8825037113:AAFW-R-SZr4eCDXGlVmqsZtgxKg6Y4tspQk"   # ← Сюда вставь токен
DB_PATH = "pomestie.db"

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== КОНСТАНТЫ ====================
TITLES = [
    (0, "Крепостной"),
    (500, "Однодворец"),
    (2000, "Помещик"),
    (8000, "Барин"),
    (25000, "Граф"),
    (80000, "Князь"),
    (250000, "Великий Князь"),
]

BUILDINGS = {
    "field": {
        "name": "Поле",
        "emoji": "🌾",
        "base_cost": 50,
        "cost_mult": 1.55,
        "base_prod": 8,
        "prod_mult": 1.4,
        "resource": "grain",
    },
    "sawmill": {
        "name": "Лесопилка",
        "emoji": "🪵",
        "base_cost": 80,
        "cost_mult": 1.6,
        "base_prod": 5,
        "prod_mult": 1.45,
        "resource": "wood",
    },
    "mine": {
        "name": "Рудник",
        "emoji": "⛏️",
        "base_cost": 200,
        "cost_mult": 1.7,
        "base_prod": 2,
        "prod_mult": 1.5,
        "resource": "gold",
    },
    "house": {
        "name": "Избы",
        "emoji": "🏠",
        "base_cost": 100,
        "cost_mult": 1.65,
        "base_prod": 0,
        "prod_mult": 1.0,
        "resource": None,
        "workers_bonus": 2,
    },
    "walls": {
        "name": "Стены",
        "emoji": "🧱",
        "base_cost": 150,
        "cost_mult": 1.75,
        "base_prod": 0,
        "prod_mult": 1.0,
        "resource": None,
        "defense_bonus": 15,
    },
}


# ==================== БАЗА ДАННЫХ ====================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            grain INTEGER DEFAULT 100,
            wood INTEGER DEFAULT 50,
            gold INTEGER DEFAULT 10,
            workers INTEGER DEFAULT 2,
            max_workers INTEGER DEFAULT 5,
            power INTEGER DEFAULT 10,
            defense INTEGER DEFAULT 5,
            last_collect REAL,
            created_at REAL,
            total_raided INTEGER DEFAULT 0,
            total_lost INTEGER DEFAULT 0,
            raids_won INTEGER DEFAULT 0,
            raids_lost INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS buildings (
            user_id INTEGER,
            building_id TEXT,
            level INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, building_id)
        )
    """)
    conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def create_user(user_id: int, username: str, full_name: str):
    now = time.time()
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """INSERT INTO users (user_id, username, full_name, last_collect, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, username or "", full_name or "", now, now)
    )
    for bid in ["field", "sawmill", "house"]:
        c.execute(
            "INSERT INTO buildings (user_id, building_id, level) VALUES (?, ?, 1)",
            (user_id, bid)
        )
    conn.commit()
    conn.close()
    recalculate_stats(user_id)


def update_user(user_id: int, **kwargs):
    if not kwargs:
        return
    keys = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    conn = get_db()
    c = conn.cursor()
    c.execute(f"UPDATE users SET {keys} WHERE user_id = ?", values)
    conn.commit()
    conn.close()


def get_buildings(user_id: int) -> dict:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT building_id, level FROM buildings WHERE user_id = ?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return {r["building_id"]: r["level"] for r in rows}


def set_building_level(user_id: int, building_id: str, level: int):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """INSERT INTO buildings (user_id, building_id, level) VALUES (?, ?, ?)
           ON CONFLICT(user_id, building_id) DO UPDATE SET level = excluded.level""",
        (user_id, building_id, level)
    )
    conn.commit()
    conn.close()


def get_top_users(limit: int = 10):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """SELECT user_id, username, full_name, grain, wood, gold, power
           FROM users ORDER BY (grain + wood * 2 + gold * 10) DESC LIMIT ?""",
        (limit,)
    )
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_random_targets(exclude_id: int, limit: int = 5):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """SELECT user_id, username, full_name, grain, wood, gold, power, defense
           FROM users WHERE user_id != ? ORDER BY RANDOM() LIMIT ?""",
        (exclude_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ==================== ИГРОВАЯ ЛОГИКА ====================
def get_title(wealth: int) -> str:
    title = TITLES[0][1]
    for threshold, name in TITLES:
        if wealth >= threshold:
            title = name
    return title


def calc_wealth(user: dict) -> int:
    return user["grain"] + user["wood"] * 2 + user["gold"] * 10


def building_cost(building_id: str, current_level: int) -> dict:
    conf = BUILDINGS[building_id]
    cost = int(conf["base_cost"] * (conf["cost_mult"] ** current_level))
    return {"grain": cost, "wood": cost // 2, "gold": max(0, cost // 10 - 5)}


def building_production(building_id: str, level: int) -> float:
    if level <= 0:
        return 0.0
    conf = BUILDINGS[building_id]
    return conf["base_prod"] * (conf["prod_mult"] ** (level - 1))


def calc_production(buildings: dict) -> dict:
    prod = {"grain": 0.0, "wood": 0.0, "gold": 0.0}
    for bid, level in buildings.items():
        conf = BUILDINGS.get(bid)
        if not conf or not conf.get("resource"):
            continue
        res = conf["resource"]
        prod[res] += building_production(bid, level)
    return prod


def calc_defense(buildings: dict, base_defense: int) -> int:
    walls_lvl = buildings.get("walls", 0)
    bonus = walls_lvl * BUILDINGS["walls"].get("defense_bonus", 15)
    return int(base_defense + bonus)


def calc_max_workers(buildings: dict) -> int:
    house_lvl = buildings.get("house", 0)
    return 5 + house_lvl * BUILDINGS["house"].get("workers_bonus", 2)


def collect_resources(user_id: int) -> dict:
    user = get_user(user_id)
    if not user:
        return {}

    buildings = get_buildings(user_id)
    prod = calc_production(buildings)

    now = time.time()
    hours = min((now - user["last_collect"]) / 3600.0, 12.0)

    gained = {
        "grain": int(prod["grain"] * hours),
        "wood": int(prod["wood"] * hours),
        "gold": int(prod["gold"] * hours),
    }

    worker_bonus = 1.0 + (user["workers"] * 0.03)
    for k in gained:
        gained[k] = int(gained[k] * worker_bonus)

    update_user(
        user_id,
        grain=user["grain"] + gained["grain"],
        wood=user["wood"] + gained["wood"],
        gold=user["gold"] + gained["gold"],
        last_collect=now
    )
    return gained


def recalculate_stats(user_id: int):
    user = get_user(user_id)
    buildings = get_buildings(user_id)

    power = user["workers"] * 3
    for lvl in buildings.values():
        power += lvl * 5

    defense = calc_defense(buildings, 5 + user["workers"])
    max_workers = calc_max_workers(buildings)

    update_user(user_id, power=power, defense=defense, max_workers=max_workers)


# ==================== КЛАВИАТУРЫ ====================
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Моё поместье", callback_data="manor")],
        [
            InlineKeyboardButton("📦 Собрать", callback_data="collect"),
            InlineKeyboardButton("🏗️ Строить", callback_data="build"),
        ],
        [
            InlineKeyboardButton("⚔️ Набег", callback_data="raid"),
            InlineKeyboardButton("🏆 Рейтинг", callback_data="top"),
        ],
        [InlineKeyboardButton("💎 Донат", callback_data="donate")],
    ])


def back_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ В поместье", callback_data="manor")]
    ])


def build_kb(buildings: dict):
    rows = []
    for bid, conf in BUILDINGS.items():
        lvl = buildings.get(bid, 0)
        rows.append([
            InlineKeyboardButton(
                f"{conf['emoji']} {conf['name']} (ур. {lvl})",
                callback_data=f"upgrade_{bid}"
            )
        ])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="manor")])
    return InlineKeyboardMarkup(rows)


# ==================== ОБРАБОТЧИКИ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = get_user(user.id)

    if not db_user:
        create_user(user.id, user.username, user.full_name)
        text = (
            "🏰 <b>Добро пожаловать в Поместье!</b>\n\n"
            "Ты получил небольшое поле и пару работников.\n"
            "Собирай урожай, строй здания, грабь соседей и становись Барином!"
        )
    else:
        text = "🏰 С возвращением в твоё поместье!"

    await update.message.reply_text(text, reply_markup=main_kb(), parse_mode="HTML")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "manor":
        await show_manor(query)
    elif data == "collect":
        await do_collect(query)
    elif data == "build":
        await show_build(query)
    elif data.startswith("upgrade_"):
        await do_upgrade(query)
    elif data == "raid":
        await show_raid_menu(query)
    elif data.startswith("raid_"):
        await do_raid(query)
    elif data == "top":
        await show_top(query)
    elif data == "donate":
        await show_donate(query)


async def show_manor(query):
    user_id = query.from_user.id
    user = get_user(user_id)
    if not user:
        await query.edit_message_text("Сначала нажми /start")
        return

    buildings = get_buildings(user_id)
    prod = calc_production(buildings)
    wealth = calc_wealth(user)
    title = get_title(wealth)

    hours = min((time.time() - user["last_collect"]) / 3600, 12)
    bonus = 1 + user["workers"] * 0.03
    pending_grain = int(prod["grain"] * hours * bonus)
    pending_wood = int(prod["wood"] * hours * bonus)
    pending_gold = int(prod["gold"] * hours * bonus)

    text = (
        f"🏰 <b>Поместье</b> — {title}\n"
        f"👤 {user['full_name'] or user['username'] or 'Без имени'}\n\n"
        f"🌾 Зерно: <b>{user['grain']}</b>  (+{pending_grain} ждёт)\n"
        f"🪵 Дерево: <b>{user['wood']}</b>  (+{pending_wood} ждёт)\n"
        f"🪙 Золото: <b>{user['gold']}</b>  (+{pending_gold} ждёт)\n\n"
        f"👷 Работники: {user['workers']}/{user['max_workers']}\n"
        f"⚔️ Сила: {user['power']}   🛡️ Защита: {user['defense']}\n\n"
        f"📈 Производство в час:\n"
        f"   🌾 {prod['grain']:.1f}   🪵 {prod['wood']:.1f}   🪙 {prod['gold']:.1f}\n\n"
        f"🏆 Богатство: {wealth}"
    )
    await query.edit_message_text(text, reply_markup=main_kb(), parse_mode="HTML")


async def do_collect(query):
    user_id = query.from_user.id
    gained = collect_resources(user_id)

    if not gained or (gained["grain"] == 0 and gained["wood"] == 0 and gained["gold"] == 0):
        await query.answer("Пока нечего собирать. Подожди немного.", show_alert=True)
        return

    text = (
        f"📦 <b>Собрано!</b>\n\n"
        f"🌾 +{gained['grain']}\n"
        f"🪵 +{gained['wood']}\n"
        f"🪙 +{gained['gold']}"
    )
    await query.edit_message_text(text, reply_markup=back_kb(), parse_mode="HTML")


async def show_build(query):
    user_id = query.from_user.id
    buildings = get_buildings(user_id)

    lines = ["🏗️ <b>Строительство</b>\n"]
    for bid, conf in BUILDINGS.items():
        lvl = buildings.get(bid, 0)
        cost = building_cost(bid, lvl)
        lines.append(
            f"{conf['emoji']} <b>{conf['name']}</b> — ур. {lvl}\n"
            f"   Цена: 🌾{cost['grain']} 🪵{cost['wood']} 🪙{cost['gold']}"
        )

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=build_kb(buildings),
        parse_mode="HTML"
    )


async def do_upgrade(query):
    user_id = query.from_user.id
    building_id = query.data.replace("upgrade_", "")
    if building_id not in BUILDINGS:
        await query.answer("Неизвестное здание", show_alert=True)
        return

    user = get_user(user_id)
    buildings = get_buildings(user_id)
    lvl = buildings.get(building_id, 0)
    cost = building_cost(building_id, lvl)

    if user["grain"] < cost["grain"] or user["wood"] < cost["wood"] or user["gold"] < cost["gold"]:
        await query.answer("Не хватает ресурсов!", show_alert=True)
        return

    update_user(
        user_id,
        grain=user["grain"] - cost["grain"],
        wood=user["wood"] - cost["wood"],
        gold=user["gold"] - cost["gold"]
    )
    set_building_level(user_id, building_id, lvl + 1)
    recalculate_stats(user_id)

    conf = BUILDINGS[building_id]
    await query.answer(f"✅ {conf['name']} → {lvl + 1} ур.")
    await show_build(query)


async def show_raid_menu(query):
    user_id = query.from_user.id
    targets = get_random_targets(user_id, 5)

    if not targets:
        await query.answer("Пока некого грабить", show_alert=True)
        return

    rows = []
    for t in targets:
        name = t["full_name"] or t["username"] or f"ID{t['user_id']}"
        wealth = calc_wealth(t)
        rows.append([
            InlineKeyboardButton(
                f"⚔️ {name[:18]} (💰{wealth})",
                callback_data=f"raid_{t['user_id']}"
            )
        ])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="manor")])

    await query.edit_message_text(
        "⚔️ <b>Выбери, кого ограбить:</b>",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML"
    )


async def do_raid(query):
    attacker_id = query.from_user.id
    defender_id = int(query.data.replace("raid_", ""))

    attacker = get_user(attacker_id)
    defender = get_user(defender_id)

    if not defender:
        await query.answer("Игрок не найден", show_alert=True)
        return

    power_diff = attacker["power"] - defender["defense"]
    chance = max(15, min(85, 50 + power_diff * 2))
    success = random.randint(1, 100) <= chance

    if success:
        steal_pct = random.uniform(0.15, 0.30)
        stolen_grain = min(int(defender["grain"] * steal_pct), defender["grain"])
        stolen_wood = min(int(defender["wood"] * steal_pct), defender["wood"])
        stolen_gold = min(int(defender["gold"] * steal_pct), defender["gold"])

        update_user(
            attacker_id,
            grain=attacker["grain"] + stolen_grain,
            wood=attacker["wood"] + stolen_wood,
            gold=attacker["gold"] + stolen_gold,
            total_raided=attacker["total_raided"] + stolen_grain + stolen_wood + stolen_gold,
            raids_won=attacker["raids_won"] + 1
        )
        update_user(
            defender_id,
            grain=defender["grain"] - stolen_grain,
            wood=defender["wood"] - stolen_wood,
            gold=defender["gold"] - stolen_gold,
            total_lost=defender["total_lost"] + stolen_grain + stolen_wood + stolen_gold,
            raids_lost=defender["raids_lost"] + 1
        )

        text = (
            f"🎉 <b>Набег успешен!</b>\n\n"
            f"Ограблен: <b>{defender['full_name'] or defender['username']}</b>\n\n"
            f"Забрано:\n🌾 {stolen_grain}\n🪵 {stolen_wood}\n🪙 {stolen_gold}\n\n"
            f"Шанс был {chance}%"
        )
    else:
        loss_grain = min(int(attacker["grain"] * 0.05), 50)
        update_user(
            attacker_id,
            grain=max(0, attacker["grain"] - loss_grain),
            raids_lost=attacker["raids_lost"] + 1
        )
        update_user(defender_id, raids_won=defender["raids_won"] + 1)

        text = (
            f"😢 <b>Набег провалился!</b>\n\n"
            f"Стража отбила атаку.\n"
            f"Потеряно 🌾{loss_grain}\n\n"
            f"Шанс был {chance}%"
        )

    await query.edit_message_text(text, reply_markup=back_kb(), parse_mode="HTML")


async def show_top(query):
    top = get_top_users(10)
    if not top:
        await query.answer("Рейтинг пуст", show_alert=True)
        return

    lines = ["🏆 <b>Самые богатые помещики</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(top):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = u["full_name"] or u["username"] or f"ID{u['user_id']}"
        wealth = calc_wealth(u)
        title = get_title(wealth)
        lines.append(f"{medal} <b>{name}</b> — {title}\n    💰 {wealth}")

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=back_kb(),
        parse_mode="HTML"
    )


async def show_donate(query):
    text = (
        "💎 <b>Поддержать поместье</b>\n\n"
        "Здесь позже можно будет купить:\n"
        "• Ускорение сбора\n"
        "• Щит от набегов\n"
        "• Дополнительных работников\n"
        "• Уникальные титулы\n\n"
        "Пока заглушка."
    )
    await query.edit_message_text(text, reply_markup=back_kb(), parse_mode="HTML")


# ==================== ЗАПУСК ====================
def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("=" * 40)
        print("Вставь токен в BOT_TOKEN!")
        print("=" * 40)
        return

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("🏰 Бот «Поместье» запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
