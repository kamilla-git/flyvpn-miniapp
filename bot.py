import asyncio
import json
import os
import sqlite3
import urllib.request
from datetime import datetime
from urllib.parse import quote

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ТУТ")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PROXYLINE_API_KEY = os.getenv("PROXYLINE_API_KEY", "ВАШ_PROXYLINE_API_KEY")

# Для Telegram Mini App нужен публичный HTTPS URL.
# Сюда позже нужно подставить адрес, на котором будет лежать папка miniapp.
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://kamilla-git.github.io/flyvpn-miniapp/miniapp/")

DB_NAME = "vpn_bot.db"
PRICE_STARS = 150

GOALS = {
    "youtube": "YouTube (4K без лагов)",
    "social": "Instagram / TikTok",
    "games": "Игры и Steam",
}

COUNTRIES_BY_GOAL = {
    "youtube": ["Германия", "Нидерланды", "Финляндия"],
    "social": ["Франция", "Польша", "Швеция"],
    "games": ["Финляндия", "Польша", "Германия"],
}

PROXYLINE_COUNTRY_MAP = {
    "de": "Германия",
    "nl": "Нидерланды",
    "fi": "Финляндия",
    "fr": "Франция",
    "pl": "Польша",
    "se": "Швеция",
    "gb": "Великобритания",
    "us": "США",
    "cz": "Чехия",
}


def get_connection():
    """Создает новое соединение с SQLite-базой данных."""
    return sqlite3.connect(DB_NAME)


def init_db():
    """Создает таблицы базы данных, если проект запускается впервые."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            goal_key TEXT NOT NULL,
            country TEXT NOT NULL,
            access_key TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS proxy_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_key TEXT NOT NULL,
            country TEXT NOT NULL,
            access_key TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL DEFAULT 'manual',
            usage_count INTEGER NOT NULL DEFAULT 0,
            added_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            goal_key TEXT NOT NULL,
            country TEXT NOT NULL,
            stars_amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            telegram_payment_charge_id TEXT,
            created_at TEXT NOT NULL,
            paid_at TEXT
        )
        """
    )

    conn.commit()
    conn.close()


def save_user(message: Message):
    """Сохраняет пользователя в базе, если он еще не был зарегистрирован."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO users (user_id, username, full_name, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            message.from_user.id,
            message.from_user.username or "",
            message.from_user.full_name or "",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()


def normalize_proxy_to_happ_link(raw_proxy: str, title: str | None = None):
    """Превращает строку SOCKS5-прокси в ссылку формата socks:// для Happ."""
    proxy = raw_proxy.strip()

    if proxy.startswith("socks://") or proxy.startswith("ss://"):
        link = proxy
    elif "@" in proxy:
        creds, host_port = proxy.split("@", maxsplit=1)
        if ":" not in creds or ":" not in host_port:
            return None
        username, password = creds.split(":", maxsplit=1)
        host, port = host_port.rsplit(":", maxsplit=1)
        link = f"socks://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
    else:
        parts = proxy.split(":")
        if len(parts) == 4:
            host, port, username, password = parts
            link = f"socks://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
        elif len(parts) == 2:
            host, port = parts
            link = f"socks://{host}:{port}"
        else:
            return None

    if title and "#" not in link:
        link = f"{link}#{quote(title, safe='')}"

    return link


def add_proxy_key(goal_key: str, country: str, access_key: str, source: str = "manual"):
    """Добавляет новый прокси или обновляет существующий, сохраняя статистику выдач."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO proxy_keys (goal_key, country, access_key, source, added_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(access_key) DO UPDATE SET
            goal_key = excluded.goal_key,
            country = excluded.country,
            source = excluded.source
        """,
        (
            goal_key,
            country,
            access_key,
            source,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()


def get_available_countries(goal_key: str):
    """Возвращает только те страны, где сейчас есть прокси для выбранной цели."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT country
        FROM proxy_keys
        WHERE goal_key = ?
        ORDER BY country
        """,
        (goal_key,),
    )
    rows = [row[0] for row in cur.fetchall()]
    conn.close()
    return rows


def take_best_proxy(goal_key: str, country: str):
    """Выбирает многоразовый прокси с минимальным usage_count и увеличивает счетчик."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, access_key
        FROM proxy_keys
        WHERE goal_key = ? AND country = ?
        ORDER BY usage_count ASC, RANDOM()
        LIMIT 1
        """,
        (goal_key, country),
    )
    row = cur.fetchone()

    if not row:
        conn.close()
        return None

    proxy_id, access_key = row
    cur.execute(
        "UPDATE proxy_keys SET usage_count = usage_count + 1 WHERE id = ?",
        (proxy_id,),
    )
    conn.commit()
    conn.close()
    return access_key


def create_subscription(user_id: int, goal_key: str, country: str, access_key: str):
    """Создает запись о подписке после успешной оплаты."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO subscriptions (user_id, goal_key, country, access_key, status, created_at)
        VALUES (?, ?, ?, ?, 'active', ?)
        """,
        (
            user_id,
            goal_key,
            country,
            access_key,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()


def get_user_subscriptions(user_id: int):
    """Возвращает активные подписки конкретного пользователя."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT goal_key, country, access_key, created_at
        FROM subscriptions
        WHERE user_id = ? AND status = 'active'
        ORDER BY id DESC
        """,
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def format_subscriptions_text(rows):
    """Форматирует список подписок в готовый текст для Telegram."""
    if not rows:
        return (
            "📦 У вас пока нет активных подписок.\n\n"
            "Откройте приложение или нажмите «Выбрать цель», чтобы оформить доступ."
        )

    parts = ["📦 Ваши активные подписки:\n"]
    for index, row in enumerate(rows, start=1):
        goal_key, country, access_key, created_at = row
        parts.append(
            f"{index}. {GOALS.get(goal_key, goal_key)}\n"
            f"Страна: {country}\n"
            f"Ссылка для Happ: `{access_key}`\n"
            f"Выдано: {created_at}\n"
        )
    return "\n".join(parts)


def create_payment(payload: str, user_id: int, goal_key: str, country: str):
    """Создает запись о счете до отправки invoice."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO payments (
            payload, user_id, goal_key, country, stars_amount, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            payload,
            user_id,
            goal_key,
            country,
            PRICE_STARS,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()
    conn.close()


def get_payment(payload: str):
    """Возвращает запись о платеже по invoice payload."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id, goal_key, country, stars_amount, status
        FROM payments
        WHERE payload = ?
        """,
        (payload,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def mark_payment_paid(payload: str, charge_id: str):
    """Помечает платеж как успешно оплаченный."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE payments
        SET status = 'paid',
            telegram_payment_charge_id = ?,
            paid_at = ?
        WHERE payload = ?
        """,
        (
            charge_id,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            payload,
        ),
    )
    conn.commit()
    conn.close()


def detect_country_name(proxy_item: dict):
    """Пытается привести код или название страны из ProxyLine к читаемому виду."""
    candidates = [
        proxy_item.get("country"),
        proxy_item.get("country_code"),
        proxy_item.get("countryCode"),
        proxy_item.get("geo", {}).get("country") if isinstance(proxy_item.get("geo"), dict) else None,
    ]

    for value in candidates:
        if not value:
            continue
        value_str = str(value).strip()
        lowered = value_str.lower()
        if lowered in PROXYLINE_COUNTRY_MAP:
            return PROXYLINE_COUNTRY_MAP[lowered]
        return value_str

    return "Германия"


def extract_raw_proxy(proxy_item: dict):
    """Собирает строку прокси из наиболее вероятных полей ответа ProxyLine API."""
    candidates = [
        proxy_item.get("proxy"),
        proxy_item.get("socks5"),
        proxy_item.get("txt"),
        proxy_item.get("string"),
    ]
    for value in candidates:
        if value:
            return str(value).strip()

    host = proxy_item.get("ip") or proxy_item.get("host")
    port = proxy_item.get("port") or proxy_item.get("socks5_port") or proxy_item.get("port_socks5")
    username = proxy_item.get("user") or proxy_item.get("username") or proxy_item.get("login")
    password = proxy_item.get("password") or proxy_item.get("pass")

    if host and port and username and password:
        return f"{host}:{port}:{username}:{password}"
    if host and port:
        return f"{host}:{port}"
    return None


def sync_proxyline_proxies():
    """Подтягивает прокси из ProxyLine API и сохраняет их в локальную базу."""
    if not PROXYLINE_API_KEY or PROXYLINE_API_KEY == "ВАШ_PROXYLINE_API_KEY":
        return {"ok": False, "message": "Не указан PROXYLINE_API_KEY"}

    url = (
        "https://panel.proxyline.net/api/proxies/"
        f"?api_key={PROXYLINE_API_KEY}&status=active&limit=1000"
    )

    request = urllib.request.Request(url, headers={"Accept": "application/json"})

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="ignore")
    except Exception as error:
        return {"ok": False, "message": f"Ошибка запроса к ProxyLine: {error}"}

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "message": "ProxyLine вернул не JSON. Проверьте API-ключ и формат ответа."}

    if isinstance(data, dict):
        proxies = data.get("results") or data.get("data") or data.get("proxies") or []
    elif isinstance(data, list):
        proxies = data
    else:
        proxies = []

    imported = 0
    for item in proxies:
        if not isinstance(item, dict):
            continue

        raw_proxy = extract_raw_proxy(item)
        if not raw_proxy:
            continue

        country = detect_country_name(item)
        for goal_key, goal_name in GOALS.items():
            title = f"{country} {goal_name}"
            link = normalize_proxy_to_happ_link(raw_proxy, title)
            if link:
                add_proxy_key(goal_key, country, link, source="proxyline")
        imported += 1

    return {"ok": True, "message": f"Синхронизировано прокси: {imported}"}


def get_main_menu():
    """Создает основное меню бота, включая кнопку открытия Mini App."""
    builder = ReplyKeyboardBuilder()

    if WEB_APP_URL.startswith("https://"):
        builder.add(KeyboardButton(text="✨ Открыть приложение", web_app=WebAppInfo(url=WEB_APP_URL)))
    else:
        builder.button(text="✨ Открыть приложение")

    builder.button(text="🎯 Выбрать цель")
    builder.button(text="📦 Мои подписки")
    builder.button(text="🛟 Поддержка")
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="Выберите действие...")


def get_goal_keyboard():
    """Создает inline-клавиатуру с тремя сценариями использования VPN."""
    builder = InlineKeyboardBuilder()
    for goal_key, goal_name in GOALS.items():
        builder.button(text=goal_name, callback_data=f"goal:{goal_key}")
    builder.adjust(1)
    return builder.as_markup()


def get_country_keyboard(goal_key: str):
    """Создает inline-клавиатуру со странами, где есть доступные прокси."""
    builder = InlineKeyboardBuilder()
    countries = get_available_countries(goal_key) or COUNTRIES_BY_GOAL.get(goal_key, [])
    for country in countries:
        builder.button(text=f"🌍 {country}", callback_data=f"country:{goal_key}:{country}")
    builder.button(text="⬅️ Назад", callback_data="back_to_goals")
    builder.adjust(1)
    return builder.as_markup()


async def send_stars_invoice(bot: Bot, chat_id: int, user_id: int, goal_key: str, country: str):
    """Создает запись о заказе и отправляет пользователю счет в Telegram Stars."""
    payload = f"vpn:{user_id}:{goal_key}:{country}:{int(datetime.now().timestamp())}"
    create_payment(payload, user_id, goal_key, country)

    await bot.send_invoice(
        chat_id=chat_id,
        title=f"VPN доступ: {GOALS[goal_key]}",
        description=(
            f"Подключение для цели «{GOALS[goal_key]}».\n"
            f"Страна подключения: {country}.\n"
            "После успешной оплаты бот сразу выдаст ссылку для Happ."
        ),
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label="VPN доступ", amount=PRICE_STARS)],
        provider_token="",
    )


async def show_main_menu(target: Message):
    """Отправляет приветствие и объясняет, что бот умеет открывать Mini App."""
    await target.answer(
        "✨ Добро пожаловать в VPN-бота\n\n"
        "Можно пользоваться обычными кнопками или открыть красивое встроенное приложение внутри Telegram.",
        reply_markup=get_main_menu(),
    )


async def start_handler(message: Message):
    """Обрабатывает команду /start и показывает главное меню."""
    save_user(message)
    sync_proxyline_proxies()
    await show_main_menu(message)


async def choose_goal_handler(message: Message):
    """Показывает пользователю выбор целей через обычный интерфейс бота."""
    save_user(message)
    sync_proxyline_proxies()
    await message.answer(
        "🎯 Выберите цель подключения:\n\n"
        "• YouTube в высоком качестве\n"
        "• Instagram / TikTok\n"
        "• Игры и Steam с низким пингом\n\n"
        "Или откройте Mini App для более красивого интерфейса.",
        reply_markup=get_goal_keyboard(),
    )


async def my_subscriptions_handler(message: Message):
    """Показывает пользователю все его выданные ссылки."""
    save_user(message)
    await message.answer(
        format_subscriptions_text(get_user_subscriptions(message.from_user.id)),
        reply_markup=get_main_menu(),
        parse_mode="Markdown",
    )


async def support_handler(message: Message):
    """Показывает раздел поддержки с контактной информацией."""
    await message.answer(
        "🛟 Поддержка\n\n"
        "По оплате, подключению Happ или вопросам по доступу напишите:\n"
        "@your_support_username",
        reply_markup=get_main_menu(),
    )


async def paysupport_handler(message: Message):
    """Отвечает на обязательную платежную команду Telegram через тот же блок поддержки."""
    await support_handler(message)


async def open_app_placeholder_handler(message: Message):
    """Объясняет, что для Mini App нужен публичный HTTPS URL, если он пока не настроен."""
    if WEB_APP_URL.startswith("https://"):
        await message.answer(
            f"Mini App подключен. Если кнопка не открылась автоматически, проверьте URL: {WEB_APP_URL}",
            reply_markup=get_main_menu(),
        )
        return

    await message.answer(
        "Для Mini App нужно выложить папку `miniapp` на HTTPS-домен и прописать адрес в `WEB_APP_URL`.\n"
        "Пока можно пользоваться кнопками обычного меню.",
        reply_markup=get_main_menu(),
    )


async def goal_callback_handler(callback: CallbackQuery):
    """После выбора цели показывает страны для ручного сценария внутри чата."""
    goal_key = callback.data.split(":")[1]
    await callback.message.edit_text(
        f"🌐 Цель: {GOALS[goal_key]}\n\nВыберите страну подключения:",
        reply_markup=get_country_keyboard(goal_key),
    )
    await callback.answer()


async def country_callback_handler(callback: CallbackQuery, bot: Bot):
    """После выбора страны отправляет счет в Stars через обычный чат-интерфейс."""
    _, goal_key, country = callback.data.split(":", maxsplit=2)
    await callback.message.answer(
        "💫 Сейчас откроется счет на оплату в Telegram Stars.\n"
        f"Тариф: {GOALS[goal_key]}\n"
        f"Страна: {country}\n"
        f"Стоимость: {PRICE_STARS} Stars"
    )
    await send_stars_invoice(bot, callback.from_user.id, callback.from_user.id, goal_key, country)
    await callback.answer()


async def web_app_data_handler(message: Message, bot: Bot):
    """Принимает выбор из Telegram Mini App и превращает его в счет на оплату."""
    save_user(message)

    try:
        payload = json.loads(message.web_app_data.data)
    except json.JSONDecodeError:
        await message.answer("Не удалось обработать данные из приложения.")
        return

    if payload.get("action") != "buy":
        await message.answer("Неизвестное действие из приложения.")
        return

    goal_key = payload.get("goal_key")
    country = payload.get("country")

    if goal_key not in GOALS:
        await message.answer("Приложение передало неизвестную цель.")
        return

    available_countries = get_available_countries(goal_key) or COUNTRIES_BY_GOAL.get(goal_key, [])
    if country not in available_countries:
        await message.answer("Для выбранной страны сейчас нет доступных прокси.")
        return

    await message.answer(
        "✨ Заказ из Mini App принят.\n"
        f"Тариф: {GOALS[goal_key]}\n"
        f"Страна: {country}\n"
        "Открываю оплату в Telegram Stars..."
    )
    await send_stars_invoice(bot, message.chat.id, message.from_user.id, goal_key, country)


async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    """Подтверждает Telegram, что счет корректный и его можно оплачивать."""
    if not get_payment(pre_checkout_query.invoice_payload):
        await pre_checkout_query.answer(
            ok=False,
            error_message="Платеж не найден. Попробуйте оформить доступ заново."
        )
        return
    await pre_checkout_query.answer(ok=True)


async def successful_payment_handler(message: Message):
    """После успешной оплаты выдает пользователю ссылку для Happ."""
    save_user(message)

    payment = message.successful_payment
    payment_row = get_payment(payment.invoice_payload)
    if not payment_row:
        await message.answer(
            "Оплата прошла, но заказ не найден. Напишите в поддержку.",
            reply_markup=get_main_menu(),
        )
        return

    user_id, goal_key, country, stars_amount, status = payment_row
    if status == "paid":
        await message.answer(
            "Этот платеж уже обработан. Если ссылка не пришла, напишите в поддержку.",
            reply_markup=get_main_menu(),
        )
        return

    access_key = take_best_proxy(goal_key, country)
    if not access_key:
        await message.answer(
            "Оплата получена, но доступных прокси сейчас нет.\n"
            "Напишите в поддержку, чтобы вам выдали доступ вручную.",
            reply_markup=get_main_menu(),
        )
        return

    create_subscription(user_id, goal_key, country, access_key)
    mark_payment_paid(payment.invoice_payload, payment.telegram_payment_charge_id)

    await message.answer(
        "✅ Оплата прошла успешно\n\n"
        f"Тариф: {GOALS[goal_key]}\n"
        f"Страна: {country}\n"
        f"Списано: {stars_amount} Stars\n\n"
        "Ваша ссылка для Happ:\n"
        f"`{access_key}`\n\n"
        "Как подключить:\n"
        "1. Откройте Happ.\n"
        "2. Нажмите «+».\n"
        "3. Выберите добавление по ссылке.\n"
        "4. Вставьте ссылку из сообщения.",
        reply_markup=get_main_menu(),
        parse_mode="Markdown",
    )


async def back_to_goals_handler(callback: CallbackQuery):
    """Возвращает пользователя к выбору целей."""
    await callback.message.edit_text(
        "🎯 Выберите нужную цель:",
        reply_markup=get_goal_keyboard(),
    )
    await callback.answer()


async def add_proxy_handler(message: Message):
    """Позволяет администратору вручную добавить SOCKS5-прокси в базу."""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Эта команда доступна только администратору.")
        return

    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        await message.answer(
            "Используйте:\n"
            "`/addproxy <goal_key> <страна> <прокси>`\n\n"
            "Пример:\n"
            "`/addproxy games Финляндия 1.2.3.4:1080:login:password`",
            parse_mode="Markdown",
        )
        return

    _, goal_key, country, raw_proxy = parts
    if goal_key not in GOALS:
        await message.answer("Доступные значения цели: youtube, social, games.")
        return

    link = normalize_proxy_to_happ_link(raw_proxy, f"{country} {GOALS[goal_key]}")
    if not link:
        await message.answer("Не удалось распознать формат прокси.")
        return

    add_proxy_key(goal_key, country, link, source="manual")
    await message.answer(
        "✅ Прокси добавлен.\n\n"
        f"Цель: {GOALS[goal_key]}\n"
        f"Страна: {country}\n"
        f"Ссылка для Happ:\n`{link}`",
        parse_mode="Markdown",
    )


async def sync_proxy_handler(message: Message):
    """Позволяет администратору вручную запустить синхронизацию с ProxyLine."""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Эта команда доступна только администратору.")
        return
    result = sync_proxyline_proxies()
    await message.answer(result["message"])


async def unknown_message_handler(message: Message):
    """Перехватывает остальные сообщения и возвращает пользователя в основное меню."""
    await message.answer("Используйте кнопки меню ниже.", reply_markup=get_main_menu())


async def main():
    """Инициализирует базу, регистрирует хендлеры и запускает бота."""
    if not BOT_TOKEN or BOT_TOKEN == "ВАШ_ТОКЕН_ТУТ":
        raise RuntimeError("Укажите BOT_TOKEN в переменных окружения перед запуском бота.")

    init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(start_handler, CommandStart())
    dp.message.register(add_proxy_handler, Command("addproxy"))
    dp.message.register(sync_proxy_handler, Command("syncproxy"))
    dp.message.register(paysupport_handler, Command("paysupport"))
    dp.message.register(choose_goal_handler, F.text == "🎯 Выбрать цель")
    dp.message.register(my_subscriptions_handler, F.text == "📦 Мои подписки")
    dp.message.register(support_handler, F.text == "🛟 Поддержка")
    dp.message.register(open_app_placeholder_handler, F.text == "✨ Открыть приложение")
    dp.message.register(web_app_data_handler, F.web_app_data)
    dp.message.register(successful_payment_handler, F.successful_payment)

    dp.callback_query.register(goal_callback_handler, F.data.startswith("goal:"))
    dp.callback_query.register(country_callback_handler, F.data.startswith("country:"))
    dp.callback_query.register(back_to_goals_handler, F.data == "back_to_goals")

    dp.pre_checkout_query.register(pre_checkout_handler)
    dp.message.register(unknown_message_handler)

    print("Бот запущен и ожидает сообщения...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
