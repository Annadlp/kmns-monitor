#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
КМНС-БОТ — Telegram-уведомления о новых законодательных документах
по коренным малочисленным народам России.

Команды бота:
  /check   — проверить прямо сейчас
  /status  — последний запуск, статистика
  /digest  — полный дайджест последних результатов
  /sources — что откуда берётся
  /help    — справка

Настройка:
  1. Создай бота через @BotFather → получи BOT_TOKEN
  2. Узнай свой CHAT_ID (напиши @userinfobot)
  3. Впиши оба значения в config.json (или переменные среды)
  4. python kmns_bot.py
"""

import os
import sys
import json
import time
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

import requests

# Импортируем наш монитор
sys.path.insert(0, str(Path(__file__).parent))
from kmns_monitor import run as monitor_run, ALL_KEYWORDS, KEYWORDS_PRIMARY

# ════════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ════════════════════════════════════════════════════════════════════

CONFIG_FILE = "config.json"

def load_config():
    """Загрузить конфиг из файла или переменных среды."""
    config = {
        "BOT_TOKEN":      os.getenv("KMNS_BOT_TOKEN", ""),
        "CHAT_ID":        os.getenv("KMNS_CHAT_ID", ""),
        "CHECK_INTERVAL": int(os.getenv("KMNS_INTERVAL", "3600")),  # каждый час
        "DAYS_BACK":      int(os.getenv("KMNS_DAYS", "30")),
        "SOURCES":        ["pravo", "sozd", "regulation", "fadn"],
        "USE_ALL_KEYWORDS": False,
        "MORNING_CHECK":  "08:00",   # ежедневный утренний отчёт
        "QUIET_HOURS":    [0, 7],    # не слать ночью (0:00–7:59)
    }

    if Path(CONFIG_FILE).exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        config.update(saved)

    return config

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

# ════════════════════════════════════════════════════════════════════
# TELEGRAM API
# ════════════════════════════════════════════════════════════════════

def tg_request(method, token, **kwargs):
    """Базовый запрос к Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        r = requests.post(url, json=kwargs, timeout=30)
        return r.json()
    except Exception as e:
        logging.error(f"Telegram API error [{method}]: {e}")
        return {"ok": False}

def send_message(token, chat_id, text, parse_mode="HTML", disable_preview=True):
    """Отправить сообщение. Telegram ограничивает 4096 символов."""
    if len(text) > 4000:
        # Разбиваем на части
        parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for part in parts:
            tg_request("sendMessage", token,
                       chat_id=chat_id, text=part,
                       parse_mode=parse_mode,
                       disable_web_page_preview=disable_preview)
            time.sleep(0.3)
    else:
        tg_request("sendMessage", token,
                   chat_id=chat_id, text=text,
                   parse_mode=parse_mode,
                   disable_web_page_preview=disable_preview)

def send_document(token, chat_id, filepath, caption=""):
    """Отправить файл."""
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        with open(filepath, "rb") as f:
            requests.post(url, data={
                "chat_id": chat_id,
                "caption": caption,
            }, files={"document": f}, timeout=60)
    except Exception as e:
        logging.error(f"send_document: {e}")

def get_updates(token, offset=0):
    """Получить новые сообщения (long polling)."""
    result = tg_request("getUpdates", token,
                         offset=offset,
                         timeout=30,
                         allowed_updates=["message"])
    return result.get("result", [])

# ════════════════════════════════════════════════════════════════════
# ФОРМАТИРОВАНИЕ СООБЩЕНИЙ
# ════════════════════════════════════════════════════════════════════

SOURCE_EMOJI = {
    "pravo.gov.ru":       "📜",
    "sozd.duma.gov.ru":   "🏛",
    "regulation.gov.ru":  "📋",
    "fadn.gov.ru":        "🏢",
}

TYPE_EMOJI = {
    "Федеральный закон":         "⚖️",
    "Указ Президента":           "🔺",
    "Постановление Правительства": "🔷",
    "Законопроект":              "📝",
    "Проект НПА":                "🔍",
    "НПА":                       "📄",
    "Новость":                   "📰",
    "Документ":                  "📁",
}

def format_doc(r):
    """Форматировать один документ для Telegram (HTML)."""
    src_emoji  = SOURCE_EMOJI.get(r["источник"], "📌")
    type_emoji = TYPE_EMOJI.get(r.get("тип",""), "📄")

    lines = []
    lines.append(f"{src_emoji} <b>{r.get('тип','')}</b>")
    lines.append(f"{type_emoji} {r['заголовок']}")

    if r.get("статус"):
        lines.append(f"   Статус: <i>{r['статус']}</i>")
    if r.get("дата"):
        lines.append(f"   Дата: {r['дата']}")
    if r.get("срочно"):
        lines.append(f"   ⚡ <b>ФИНАЛЬНАЯ СТАДИЯ</b>")
    if r.get("открыто_для"):
        lines.append(f"   📢 <b>ОТКРЫТО ДЛЯ ЗАМЕЧАНИЙ</b>")
    if r.get("url"):
        lines.append(f'   <a href="{r["url"]}">→ Открыть документ</a>')

    return "\n".join(lines)

def format_new_docs_message(new_docs):
    """Сообщение о новых документах."""
    if not new_docs:
        return None

    header = (
        f"🔔 <b>КМНС-МОНИТОР: {len(new_docs)} новых документов</b>\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        + "─" * 35
    )

    # Группируем по источнику
    by_source = {}
    for r in new_docs:
        src = r["источник"]
        by_source.setdefault(src, []).append(r)

    parts = [header]

    for src, docs in by_source.items():
        src_emoji = SOURCE_EMOJI.get(src, "📌")
        parts.append(f"\n{src_emoji} <b>{src}</b> [{len(docs)}]")
        for r in docs[:5]:  # максимум 5 на источник в одном сообщении
            parts.append("\n" + format_doc(r))
        if len(docs) > 5:
            parts.append(f"\n   <i>...и ещё {len(docs)-5} документов</i>")

    return "\n".join(parts)

def format_status_message(config, state):
    """Сообщение о статусе бота."""
    last_run = state.get("last_run", "никогда")
    total_seen = len(state.get("seen", {}))
    stats = state.get("stats", {})
    last_stats = list(stats.values())[-1] if stats else {}

    return (
        f"🤖 <b>КМНС-БОТ — статус</b>\n\n"
        f"⏰ Последний запуск: <code>{last_run[:16] if last_run else 'не запускался'}</code>\n"
        f"🔄 Интервал проверки: каждые {config['CHECK_INTERVAL']//60} мин\n"
        f"📚 Всего в базе: {total_seen} документов\n"
        f"📊 Прошлая проверка: "
        f"{last_stats.get('new',0)} новых из {last_stats.get('total',0)}\n\n"
        f"🔍 Источники: {', '.join(config['SOURCES'])}\n"
        f"🔑 Режим: {'полный (~40 слов)' if config['USE_ALL_KEYWORDS'] else 'базовый (4 слова)'}"
    )

def format_digest_message(results):
    """Компактный дайджест всех последних результатов."""
    if not results:
        return "📭 Документов не найдено."

    new   = [r for r in results if r.get("новый")]
    total = len(results)

    lines = [
        f"📋 <b>ДАЙДЖЕСТ КМНС-МОНИТОРИНГА</b>",
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        f"Всего: {total} | Новых: {len(new)}",
        "─" * 35,
    ]

    for src, emoji in SOURCE_EMOJI.items():
        src_docs = [r for r in results if r["источник"] == src]
        if not src_docs:
            continue
        new_src = [r for r in src_docs if r.get("новый")]
        lines.append(
            f"\n{emoji} <b>{src}</b>: {len(src_docs)} "
            f"({'★' + str(len(new_src)) + ' новых' if new_src else 'без новых'})"
        )
        for r in src_docs[:3]:
            mark = "★ " if r.get("новый") else "   "
            title_short = r["заголовок"][:60] + ("…" if len(r["заголовок"]) > 60 else "")
            lines.append(f"  {mark}{title_short}")

    return "\n".join(lines)

# ════════════════════════════════════════════════════════════════════
# ОБРАБОТЧИК КОМАНД
# ════════════════════════════════════════════════════════════════════

# Глобальное состояние бота
_last_results = []

def handle_command(token, chat_id, text, config, state):
    """Обработать команду от пользователя."""
    global _last_results
    cmd = text.strip().lower().split()[0] if text else ""

    if cmd in ("/start", "/help"):
        send_message(token, chat_id,
            "🌍 <b>КМНС-МОНИТОР</b>\n\n"
            "Слежу за законодательством России о коренных малочисленных народах.\n\n"
            "<b>Команды:</b>\n"
            "/check — проверить прямо сейчас\n"
            "/status — статус бота и статистика\n"
            "/digest — дайджест последних результатов\n"
            "/sources — описание источников\n"
            "/help — эта справка\n\n"
            f"⏰ Автопроверка каждые {config['CHECK_INTERVAL']//60} мин."
        )

    elif cmd == "/check":
        send_message(token, chat_id, "🔍 Проверяю... (может занять 30–60 сек)")
        results = monitor_run(
            days_back  = config["DAYS_BACK"],
            sources    = tuple(config["SOURCES"]),
            keywords   = ALL_KEYWORDS if config["USE_ALL_KEYWORDS"] else None,
            new_only   = True,
            output_fmt = "json",
            verbose    = False,
        )
        _last_results = results
        new_docs = [r for r in results if r.get("новый")]
        msg = format_new_docs_message(new_docs)
        if msg:
            send_message(token, chat_id, msg)
        else:
            send_message(token, chat_id,
                "✅ Новых документов не найдено.\n"
                f"Проверено источников: {len(config['SOURCES'])}"
            )

    elif cmd == "/status":
        from kmns_monitor import load_state
        state = load_state()
        send_message(token, chat_id, format_status_message(config, state))

    elif cmd == "/digest":
        if _last_results:
            send_message(token, chat_id, format_digest_message(_last_results))
        else:
            send_message(token, chat_id,
                "Нет кэшированных результатов. Запусти /check")

    elif cmd == "/sources":
        send_message(token, chat_id,
            "📚 <b>Источники мониторинга</b>\n\n"

            "📜 <b>pravo.gov.ru</b>\n"
            "Принятые НПА: федеральные законы, указы Президента, "
            "постановления Правительства. Финальная стадия.\n\n"

            "🏛 <b>sozd.duma.gov.ru</b>\n"
            "СОЗД — система обеспечения законодательной деятельности. "
            "Все законопроекты от внесения до подписания. "
            "<b>Главный источник для отслеживания.</b>\n\n"

            "📋 <b>regulation.gov.ru</b>\n"
            "Проекты НПА на стадии до внесения в Думу. "
            "Самая ранняя стадия — можно подать замечания "
            "в рамках ОРВ. Ищи флаг 📢.\n\n"

            "🏢 <b>fadn.gov.ru</b>\n"
            "Федеральное агентство по делам национальностей. "
            "Позиция регулятора, ранние сигналы, планы."
        )

    else:
        send_message(token, chat_id,
            "Не знаю такой команды. Попробуй /help")

# ════════════════════════════════════════════════════════════════════
# ПЛАНИРОВЩИК
# ════════════════════════════════════════════════════════════════════

def scheduled_check(token, chat_id, config):
    """Плановая проверка — запускается по расписанию."""
    global _last_results

    now = datetime.now()

    # Не слать ночью
    quiet = config.get("QUIET_HOURS", [0, 7])
    if quiet[0] <= now.hour < quiet[1]:
        logging.info(f"Тихий час ({now.hour}:00), пропускаю проверку")
        return

    logging.info(f"Плановая проверка: {now.strftime('%d.%m.%Y %H:%M')}")

    results = monitor_run(
        days_back  = config["DAYS_BACK"],
        sources    = tuple(config["SOURCES"]),
        keywords   = ALL_KEYWORDS if config["USE_ALL_KEYWORDS"] else None,
        new_only   = True,
        output_fmt = "json",
        verbose    = False,
    )
    _last_results = results
    new_docs = [r for r in results if r.get("новый")]

    if new_docs:
        msg = format_new_docs_message(new_docs)
        send_message(token, chat_id, msg)
        logging.info(f"Отправлено уведомление: {len(new_docs)} новых документов")

        # Если есть открытые для замечаний — доп. напоминание
        open_for_comments = [r for r in new_docs if r.get("открыто_для")]
        if open_for_comments:
            reminder = (
                "⚠️ <b>ТРЕБУЕТ РЕАКЦИИ</b>\n\n"
                "Следующие проекты открыты для публичных замечаний:\n\n"
            )
            for r in open_for_comments:
                reminder += f"▸ {r['заголовок'][:80]}\n"
                reminder += f'  <a href="{r["url"]}">→ Подать замечания</a>\n\n'
            send_message(token, chat_id, reminder)
    else:
        logging.info("Новых документов нет")

def morning_report(token, chat_id, config):
    """Утренний дайджест — даже если новых документов нет."""
    global _last_results

    results = monitor_run(
        days_back  = 7,  # последняя неделя для утреннего отчёта
        sources    = tuple(config["SOURCES"]),
        new_only   = False,
        output_fmt = "json",
        verbose    = False,
    )
    _last_results = results
    send_message(token, chat_id, format_digest_message(results))

# ════════════════════════════════════════════════════════════════════
# ОСНОВНОЙ ЦИКЛ
# ════════════════════════════════════════════════════════════════════

def run_bot():
    config = load_config()

    if not config["BOT_TOKEN"] or not config["CHAT_ID"]:
        print("\n❌ Не настроен BOT_TOKEN или CHAT_ID!")
        print("\nВарианты настройки:")
        print("  1. Создай config.json:")
        print('     { "BOT_TOKEN": "xxx", "CHAT_ID": "yyy" }')
        print("\n  2. Или переменные среды:")
        print("     export KMNS_BOT_TOKEN=xxx")
        print("     export KMNS_CHAT_ID=yyy")
        print("\nGetting started:")
        print("  1. Напиши @BotFather в Telegram → /newbot → получи токен")
        print("  2. Напиши @userinfobot → получи свой chat_id")
        sys.exit(1)

    token   = config["BOT_TOKEN"]
    chat_id = config["CHAT_ID"]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("kmns_bot.log", encoding="utf-8"),
        ]
    )

    logging.info("КМНС-БОТ запущен")

    # Приветственное сообщение
    send_message(token, chat_id,
        f"✅ <b>КМНС-БОТ запущен</b>\n\n"
        f"⏰ Проверка каждые {config['CHECK_INTERVAL']//60} мин\n"
        f"☀️ Утренний дайджест в {config['MORNING_CHECK']}\n"
        f"🔍 Источников: {len(config['SOURCES'])}\n\n"
        f"Первая проверка — через несколько секунд.\n"
        f"/help — справка по командам"
    )

    offset         = 0
    last_check     = 0
    last_morning   = None

    while True:
        try:
            # ── Получаем команды ───────────────────────────────
            updates = get_updates(token, offset)
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                text = msg.get("text", "")
                from_id = str(msg.get("chat", {}).get("id", ""))

                # Разрешаем только наш chat_id
                if from_id != str(chat_id):
                    logging.warning(f"Запрос от неизвестного chat_id: {from_id}")
                    continue

                if text.startswith("/"):
                    from kmns_monitor import load_state
                    handle_command(token, chat_id, text, config, load_state())

            # ── Плановая проверка ──────────────────────────────
            now = time.time()
            if now - last_check >= config["CHECK_INTERVAL"]:
                scheduled_check(token, chat_id, config)
                last_check = now

            # ── Утренний дайджест ──────────────────────────────
            current_time = datetime.now().strftime("%H:%M")
            today = datetime.now().date()
            if (current_time == config["MORNING_CHECK"] and
                    last_morning != today):
                morning_report(token, chat_id, config)
                last_morning = today

            time.sleep(2)  # пауза между polling-итерациями

        except KeyboardInterrupt:
            logging.info("Остановка бота...")
            send_message(token, chat_id, "🛑 Бот остановлен.")
            break
        except Exception as e:
            logging.error(f"Ошибка в основном цикле: {e}")
            time.sleep(10)


if __name__ == "__main__":
    run_bot()
