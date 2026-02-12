import os
import json
import asyncio

from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart ,Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder


from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env")

# Elevator IDs
ELEVATORS = ["8240", "8241", "8242", "8243"]
WALL_COLOR = {
    "8240": "⬛",
    "8243": "⬛",
    "8241": "🟧",
    "8242": "🟧",
}

STATUS_LABELS = {
    "ok": "работает",
    "warn": "условно работает",
    "bad": "не работает",
    "unknown": "нет данных",
}

# Statuses: (label, key)
STATUSES = [
    ("✅ Отлично работает", "ok"),
    ("🟡 Работает, но с оговорками", "warn"),
    ("❌ Не работает", "bad"),
]

STATUS_ICON = {
    "ok": "✅",
    "warn": "🟡",
    "bad": "❌",
}


LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

def now_iso_local() -> str:
    # Local time of the machine running the bot
    return datetime.now().isoformat(timespec="seconds")

def log_status(elevator_id: str, status_key: str, user) -> None:
    # JSON Lines: one JSON object per line
    record = {
        "ts": now_iso_local(),
        "elevator": elevator_id,
        "status": status_key,
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
}

    log_path = LOG_DIR / f"{elevator_id}.log"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def parse_events_for_elevator(elevator_id: str) -> list[tuple[datetime, str]]:
    log_path = LOG_DIR / f"{elevator_id}.log"
    if not log_path.exists():
        return []
    events = []
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts = datetime.fromisoformat(rec["ts"])
                st = rec["status"]
                if st not in ("ok", "warn", "bad"):
                    continue
                events.append((ts, st))
            except Exception:
                continue
    events.sort(key=lambda x: x[0])
    return events


def format_duration(seconds: int) -> str:
    minutes = seconds // 60
    h = minutes // 60
    m = minutes % 60
    if h == 0:
        return f"{m}м"
    return f"{h}ч {m}м"


def compute_uptime(elevator_id: str, start: datetime, end: datetime) -> dict[str, int]:
    # returns seconds per status in the interval [start, end)
    events = parse_events_for_elevator(elevator_id)
    if start >= end:
        return {"ok": 0, "warn": 0, "bad": 0, "unknown": 0}

    # Find last status before start
    current_status = "unknown"
    for ts, st in events:
        if ts < start:
            current_status = st
        else:
            break

    # Iterate events within [start, end)
    totals = {"ok": 0, "warn": 0, "bad": 0, "unknown": 0}
    cursor = start

    for ts, st in events:
        if ts < start:
            continue
        if ts >= end:
            break

        seg_end = ts
        if seg_end > cursor:
            totals[current_status] += int((seg_end - cursor).total_seconds())
        current_status = st
        cursor = ts

    # Tail segment to end
    if end > cursor:
        totals[current_status] += int((end - cursor).total_seconds())

    return totals


def render_report_block(elevator_id: str, totals: dict[str, int]) -> str:
    known_total = totals["ok"] + totals["warn"] + totals["bad"]
    full_total = known_total + totals["unknown"]

    if full_total == 0:
        return f"Лифт {elevator_id}:\nнет данных\n"

    # Если неизвестного времени нет — проценты считаем по known_total.
    # Если есть — считаем по full_total и добавляем строку "нет данных".
    denom = full_total if totals["unknown"] > 0 else known_total
    if denom == 0:
        denom = full_total  # на всякий

    def pct(sec: int) -> int:
        return round(sec * 100 / denom) if denom else 0

    lines = [f"Лифт {elevator_id}:"]
    lines.append(f"{STATUS_LABELS['ok']} = {format_duration(totals['ok'])} ({pct(totals['ok'])}%)")
    lines.append(f"{STATUS_LABELS['warn']} = {format_duration(totals['warn'])} ({pct(totals['warn'])}%)")
    lines.append(f"{STATUS_LABELS['bad']} = {format_duration(totals['bad'])} ({pct(totals['bad'])}%)")

    if totals["unknown"] > 0:
        lines.append(f"{STATUS_LABELS['unknown']} = {format_duration(totals['unknown'])} ({pct(totals['unknown'])}%)")

    return "\n".join(lines) + "\n"


def elevators_keyboard():
    kb = InlineKeyboardBuilder()

    for elevator_id in ["8240", "8241", "8243", "8242"]:
        wall = WALL_COLOR.get(elevator_id, "")
        last_status = get_last_status(elevator_id)
        status_icon = STATUS_ICON.get(last_status, "➖") # type: ignore

        text = f"{wall} {elevator_id} {status_icon}"
        kb.button(text=text, callback_data=f"e:{elevator_id}")

    kb.adjust(2, 2)
    return kb.as_markup()
def get_last_status(elevator_id: str) -> str | None:
    log_path = LOG_DIR / f"{elevator_id}.log"
    if not log_path.exists():
        return None

    try:
        with log_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
            if not lines:
                return None
            last_line = lines[-1].strip()
            if not last_line:
                return None
            obj = json.loads(last_line)
            return obj.get("status")
    except Exception:
        return None


def statuses_keyboard(elevator_id: str):
    kb = InlineKeyboardBuilder()
    for label, key in STATUSES:
        kb.button(text=label, callback_data=f"s:{elevator_id}:{key}")
    kb.adjust(1, 1, 1)
    return kb.as_markup()

def report_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="24h", callback_data="r:24h")
    kb.button(text="7d", callback_data="r:7d")
    kb.button(text="30d", callback_data="r:30d")
    kb.button(text="all", callback_data="r:all")
    kb.adjust(2, 2)
    return kb.as_markup()


bot = Bot(BOT_TOKEN)
dp = Dispatcher()
LAST_MENU_MSG_ID: dict[int, int] = {}  # chat_id -> message_id

@dp.message(CommandStart())
async def start(message: Message):
    await show_elevators_menu(message.chat.id)
    
@dp.callback_query(F.data.startswith("e:"))
async def choose_elevator(callback: CallbackQuery):
    data = callback.data
    if not data:
        await callback.answer("Нет данных", show_alert=True)
        return

    elevator_id = data.split(":", 1)[1]
    if elevator_id not in ELEVATORS:
        await callback.answer("Неизвестный лифт", show_alert=True)
        return

    msg = callback.message
    
    if not msg:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    
    await callback.message.answer( # type: ignore
        f"Лифт {elevator_id}. Укажи статус:",
        reply_markup=statuses_keyboard(elevator_id),
    )
    await callback.answer()

async def show_elevators_menu(chat_id: int):
    # удалить прошлое меню, если помним его
    old_id = LAST_MENU_MSG_ID.get(chat_id)
    if old_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_id)
        except Exception:
            pass  # если уже удалено/нельзя удалить — не падаем

    msg = await bot.send_message(chat_id, "Выбери лифт:", reply_markup=elevators_keyboard())
    LAST_MENU_MSG_ID[chat_id] = msg.message_id

async def refresh_elevators_menu(chat_id: int):
    menu_id = LAST_MENU_MSG_ID.get(chat_id)
    if not menu_id:
        return

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=menu_id,
            text="Выбери лифт:",
            reply_markup=elevators_keyboard(),
        )
    except Exception:
        pass

def confirm_keyboard(elevator_id: str, status_key: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, уверен", callback_data=f"c:{elevator_id}:{status_key}")
    kb.button(text="↩️ Нет", callback_data=f"e:{elevator_id}")
    kb.adjust(1, 1)
    return kb.as_markup()


@dp.callback_query(F.data.startswith("s:"))
async def choose_status(callback: CallbackQuery):
    data = callback.data
    if not data:
        await callback.answer("Нет данных", show_alert=True)
        return

    parts = data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    _, elevator_id, status_key = parts

    if elevator_id not in ELEVATORS:
        await callback.answer("Неизвестный лифт", show_alert=True)
        return

    valid_keys = {k for _, k in STATUSES}
    if status_key not in valid_keys:
        await callback.answer("Неизвестный статус", show_alert=True)
        return
    
    msg = callback.message
    if not msg:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return


    last2 = get_last_statuses(elevator_id, 2)

    # --- если нужно подтверждение ---
    if needs_confirm(last2, status_key):
        await msg.edit_text( # type: ignore
            "Статус меняет группу (работает ↔ не работает).\nУверен?",
            reply_markup=confirm_keyboard(elevator_id, status_key),
        )
        await callback.answer()
        return

    # --- если подтверждение не нужно ---
    log_status(elevator_id, status_key, callback.from_user)


    await refresh_elevators_menu(callback.message.chat.id) # type: ignore

    try:
     await msg.delete() # type: ignore
    except Exception:
        pass

    await callback.answer()

@dp.callback_query(F.data.startswith("c:"))
async def confirm_status(callback: CallbackQuery):
    data = callback.data
    if not data:
        await callback.answer("Нет данных", show_alert=True)
        return

    parts = data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    _, elevator_id, status_key = parts

    msg = callback.message
    if not msg:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return

    # записываем статус
    log_status(elevator_id, status_key, callback.from_user)

    # удаляем сообщение подтверждения
    await refresh_elevators_menu(callback.message.chat.id) # type: ignore

    try:
        await msg.delete() # type: ignore
    except Exception:
        pass

    await callback.answer()

@dp.message(Command("report"))
async def report_cmd(message: Message):
    await message.answer("Выбери период отчёта:", reply_markup=report_keyboard())

@dp.callback_query(F.data.startswith("r:"))
async def report_pick(callback: CallbackQuery):
    data = callback.data or ""
    period = data.split(":", 1)[1] if ":" in data else "24h"

    end = datetime.now()
    if period == "24h":
        start = end - timedelta(hours=24)
        title = "за 24 часа"
    elif period == "7d":
        start = end - timedelta(days=7)
        title = "за 7 дней"
    elif period == "30d":
        start = end - timedelta(days=30)
        title = "за 30 дней"
    elif period == "all":
        # Считаем от первого события по каждому лифту; общий start берём как min из первых событий
        firsts = []
        for e in ELEVATORS:
            ev = parse_events_for_elevator(e)
            if ev:
                firsts.append(ev[0][0])
        start = min(firsts) if firsts else end
        title = "за всё время"
    else:
        await callback.answer("Неизвестный период", show_alert=True)
        return

    blocks = [f"Отчёт {title}\n"]
    for e in ELEVATORS:
        totals = compute_uptime(e, start, end)
        blocks.append(render_report_block(e, totals))

    text = "\n".join(blocks).strip()
    msg = callback.message
    if msg is None:
        await callback.answer()
        return

    await msg.edit_text(text) # type: ignore
    await callback.answer()
    

def get_last_statuses(elevator_id: str, n: int = 2) -> list[str]:
    log_path = LOG_DIR / f"{elevator_id}.log"
    if not log_path.exists():
        return []

    statuses: list[str] = []
    # читаем файл с конца без усложнений (для небольших логов норм)
    with log_path.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            s = obj.get("status")
            if s in {"ok", "warn", "bad"}:
                statuses.append(s)
                if len(statuses) >= n:
                    break
        except Exception:
            continue

    return list(reversed(statuses))

def needs_confirm(last2: list[str], new_status: str) -> bool:
    """
    Группы:
    working = {"ok", "warn"}
    broken = {"bad"}

    Подтверждение нужно, если происходит переход
    между группами (работает ↔ не работает),
    при условии что два последних статуса были в одной группе.
    """

    if len(last2) < 2:
        return False

    working = {"ok", "warn"}
    broken = {"bad"}

    last_a, last_b = last2[-2], last2[-1]

    # Если два последних были "работает" (ok или warn)
    if last_a in working and last_b in working and new_status in broken:
        return True

    # Если два последних были "не работает"
    if last_a in broken and last_b in broken and new_status in working:
        return True

    return False

if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))

