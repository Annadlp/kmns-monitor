#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).parent))
from kmns_monitor import run as monitor_run, ALL_KEYWORDS, KEYWORDS_PRIMARY

CONFIG_FILE = "config.json"

def load_config():
    config = {
        "BOT_TOKEN":        os.getenv("KMNS_BOT_TOKEN", ""),
        "CHAT_ID":          os.getenv("KMNS_CHAT_ID", ""),
        "CHECK_INTERVAL":   int(os.getenv("KMNS_INTERVAL", "3600")),
        "DAYS_BACK":        int(os.getenv("KMNS_DAYS", "30")),
        "SOURCES":          ["pravo", "fadn"],
        "USE_ALL_KEYWORDS": False,
        "MORNING_CHECK":    "08:00",
        "QUIET_HOURS":      [0, 7],
    }
    if Path(CONFIG_FILE).exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        config.update(saved)
    return config

def tg_post(token, method, timeout=15, **kwargs):
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        r = requests.post(url, json=kwargs, timeout=timeout)
        data = r.json()
        if not data.get("ok"):
            logging.warning(f"TG [{method}] not ok: {data.get('description','')}")
        return data
    except Exception as e:
        logging.error(f"TG [{method}] exception: {e}")
        return {"ok": False}

def send_message(token, chat_id, text, parse_mode="HTML"):
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        result = tg_post(token, "sendMessage",
                         chat_id=chat_id,
                         text=chunk,
                         parse_mode=parse_mode,
                         disable_web_page_preview=True)
        if result.get("ok"):
            logging.info(f"Сообщение отправлено в {chat_id}")
        time.sleep(0.3)

def get_updates(token, offset=0):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        r = requests.post(url, json={
            "offset": offset,
            "timeout": 25,
            "allowed_updates": ["message"]
        }, timeout=35)
        data = r.json()
        return data.get("result", [])
    except requests.exceptions.Timeout:
        return []
    except Exception as e:
        logging.error(f"get_updates error: {e}")
        return []

SOURCE_EMOJI = {
    "pravo.gov.ru":      "📜",
    "fadn.gov.ru":       "🏢",
    "faolex.fao.org":    "🌐",
    "news.google.com":   "📰",
    "sozd.duma.gov.ru":  "🏛",
    "regulation.gov.ru": "📋",
}

def format_doc(r):
    lines = [f"{SOURCE_EMOJI.get(r['источник'],'📌')} <b>{r.get('тип','')}</b>",
             r['заголовок']]
    if r.get("статус"):      lines.append(f"Статус: <i>{r['статус']}</i>")
    if r.get("дата"):        lines.append(f"Дата: {r['дата']}")
    if r.get("срочно"):      lines.append("⚡ <b>ФИНАЛЬНАЯ СТАДИЯ</b>")
    if r.get("открыто_для"): lines.append("📢 <b>ОТКРЫТО ДЛЯ ЗАМЕЧАНИЙ</b>")
    if r.get("url"):         lines.append(f'<a href="{r["url"]}">→ Открыть</a>')
    return "\n".join(lines)

def format_new_docs(new_docs, source_status=None):
    if not new_docs:
        return None
    by_src = {}
    for r in new_docs:
        by_src.setdefault(r["источник"], []).append(r)
    parts = [f"🔔 <b>КМНС-МОНИТОР: {len(new_docs)} новых</b>\n"
             f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"]
    for src, docs in by_src.items():
        parts.append(f"\n{SOURCE_EMOJI.get(src,'📌')} <b>{src}</b> [{len(docs)}]")
        for r in docs[:5]:
            parts.append("\n" + format_doc(r))
        if len(docs) > 5:
            parts.append(f"<i>...и ещё {len(docs)-5}</i>")
    # Предупреждение о недоступных источниках
    if source_status:
        failed = [s for s, st in source_status.items() if st == "failed"]
        if failed:
            parts.append(f"\n⚠️ <i>Недоступны: {', '.join(failed)} — данные могут быть неполными</i>")
    return "\n".join(parts)

def format_no_news(source_status=None):
    """Честное сообщение когда новых нет — с учётом статуса источников."""
    failed = []
    if source_status:
        failed = [s for s, st in source_status.items() if st == "failed"]

    if failed:
        return (
            f"⚠️ <b>Проверка неполная</b>\n\n"
            f"Недоступны: {', '.join(failed)}\n"
            f"По доступным источникам новых документов нет.\n\n"
            f"<i>Рекомендуем проверить sozd.duma.gov.ru и regulation.gov.ru вручную.</i>"
        )
    return "✅ Новых документов нет."

_last_results      = []
_last_source_status = {}

def handle_command(token, chat_id, text, config):
    global _last_results, _last_source_status
    cmd = text.strip().lower().split()[0].split("@")[0]  # убираем @botname
    logging.info(f"Команда: {cmd}")

    if cmd in ("/start", "/help"):
        send_message(token, chat_id,
            "🌍 <b>КМНС-МОНИТОР</b>\n\n"
            "Слежу за законодательством РФ о коренных народах.\n\n"
            "<b>Команды:</b>\n"
            "/check — проверить прямо сейчас\n"
            "/status — статус и статистика\n"
            "/digest — последние результаты\n"
            "/sources — описание источников\n\n"
            f"⏰ Автопроверка каждые {config['CHECK_INTERVAL']//60} мин.")

    elif cmd == "/check":
        send_message(token, chat_id, "🔍 Проверяю...")
        results, source_status = monitor_run(
            days_back=config["DAYS_BACK"],
            sources=tuple(config["SOURCES"]),
            keywords=ALL_KEYWORDS if config["USE_ALL_KEYWORDS"] else None,
            new_only=True, output_fmt="json", verbose=False,
        )
        _last_results       = results
        _last_source_status = source_status
        new_docs = [r for r in results if r.get("новый")]
        if new_docs:
            send_message(token, chat_id, format_new_docs(new_docs, source_status))
        else:
            send_message(token, chat_id, format_no_news(source_status))

    elif cmd == "/status":
        from kmns_monitor import load_state
        st = load_state() or {}
        # Статус источников из последней проверки
        status_lines = ""
        if _last_source_status:
            icons = {"ok": "✅", "empty": "🔵", "failed": "❌"}
            status_lines = "\n\n<b>Статус источников:</b>\n"
            for src, s in _last_source_status.items():
                status_lines += f"{icons.get(s,'❓')} {src}\n"

        send_message(token, chat_id,
            f"🤖 <b>КМНС-БОТ</b>\n\n"
            f"Последний запуск: <code>{st.get('last_run','никогда')[:16]}</code>\n"
            f"В базе: {len(st.get('seen', {}))} документов\n"
            f"Интервал: каждые {config['CHECK_INTERVAL']//60} мин"
            f"{status_lines}")

    elif cmd == "/digest":
        if _last_results:
            total = len(_last_results)
            new   = len([r for r in _last_results if r.get("новый")])
            lines = [f"📋 <b>Дайджест</b> | Всего: {total} | Новых: {new}"]
            for src, emoji in SOURCE_EMOJI.items():
                docs = [r for r in _last_results if r["источник"] == src]
                if docs:
                    lines.append(f"\n{emoji} {src}: {len(docs)}")
                    for r in docs[:3]:
                        lines.append(f"  {'★ ' if r.get('новый') else ''}{r['заголовок'][:60]}")
            if _last_source_status:
                failed = [s for s, st in _last_source_status.items() if st == "failed"]
                if failed:
                    lines.append(f"\n⚠️ Недоступны: {', '.join(failed)}")
            send_message(token, chat_id, "\n".join(lines))
        else:
            send_message(token, chat_id, "Нет данных — запусти /check")

    elif cmd == "/sources":
        send_message(token, chat_id,
            "📚 <b>Источники мониторинга</b>\n\n"
            "📜 <b>pravo.gov.ru</b>\n"
            "Принятые НПА: федеральные законы, указы Президента, постановления Правительства, приказы министерств.\n\n"
            "🏢 <b>fadn.gov.ru</b>\n"
            "Новости и документы Федерального агентства по делам национальностей.\n\n"
            "📰 <b>Google News</b>\n"
            "Агрегатор российских СМИ и правовых сервисов. Работает с любого IP.\n\n"
            "🌐 <b>faolex.fao.org</b>\n"
            "База данных ФАО — законодательство РФ по природным ресурсам и правам коренных народов.\n\n"
            "⚠️ <b>Недоступны автоматически:</b>\n"
            "🏛 sozd.duma.gov.ru — законопроекты Госдумы\n"
            "📋 regulation.gov.ru — проекты НПА до внесения в Думу\n"
            "Эти сайты блокируют зарубежные IP. Проверяйте вручную раз в неделю.")

    else:
        send_message(token, chat_id, "Не знаю такой команды. /help")

def scheduled_check(token, chat_id, config):
    global _last_results, _last_source_status
    now = datetime.now()
    quiet = config.get("QUIET_HOURS", [0, 7])
    if quiet[0] <= now.hour < quiet[1]:
        return

    logging.info(f"Плановая проверка: {now.strftime('%d.%m.%Y %H:%M')}")
    results, source_status = monitor_run(
        days_back=config["DAYS_BACK"],
        sources=tuple(config["SOURCES"]),
        new_only=True, output_fmt="json", verbose=False,
    )
    _last_results       = results
    _last_source_status = source_status

    new_docs = [r for r in results if r.get("новый")]
    if new_docs:
        send_message(token, chat_id, format_new_docs(new_docs, source_status))
        logging.info(f"Отправлено: {len(new_docs)} новых")
    else:
        # При плановой проверке пишем только если есть проблемы с источниками
        failed = [s for s, st in source_status.items() if st == "failed"]
        if failed:
            logging.warning(f"Недоступны: {', '.join(failed)}")
        logging.info("Новых нет")

def run_bot():
    config = load_config()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()]
    )

    if not config["BOT_TOKEN"] or not config["CHAT_ID"]:
        logging.error("❌ Не настроены KMNS_BOT_TOKEN и/или KMNS_CHAT_ID!")
        sys.exit(1)

    token   = config["BOT_TOKEN"]
    chat_id = config["CHAT_ID"]

    logging.info(f"КМНС-БОТ запущен. chat_id={chat_id}")

    result = tg_post(token, "getMe")
    if result.get("ok"):
        bot_name = result["result"].get("username", "?")
        logging.info(f"Бот авторизован: @{bot_name}")
    else:
        logging.error("❌ Токен неверный! Проверь KMNS_BOT_TOKEN")
        sys.exit(1)

    send_message(token, chat_id,
        f"✅ <b>КМНС-БОТ запущен</b>\n\n"
        f"⏰ Проверка каждые {config['CHECK_INTERVAL']//60} мин\n"
        f"🔍 Источников: pravo.gov.ru, fadn.gov.ru, Google News, FAOLex\n\n"
        "/help — команды")

    offset       = 0
    last_check   = 0
    last_morning = None
    poll_count   = 0

    while True:
        try:
            updates = get_updates(token, offset)
            poll_count += 1
            if poll_count % 10 == 0:
                logging.info(f"Polling активен. Итераций: {poll_count}")

            for upd in updates:
                offset  = upd["update_id"] + 1
                msg     = upd.get("message", {})
                text    = msg.get("text", "")
                from_id = str(msg.get("chat", {}).get("id", ""))

                logging.info(f"Сообщение от {from_id}: {text[:30]}")

                if from_id != str(chat_id):
                    logging.warning(f"Чужой chat_id: {from_id} (ожидался {chat_id})")
                    continue

                if text.startswith("/"):
                    handle_command(token, chat_id, text, config)

            now = time.time()
            if now - last_check >= config["CHECK_INTERVAL"]:
                scheduled_check(token, chat_id, config)
                last_check = now

            current_time = datetime.now().strftime("%H:%M")
            today = datetime.now().date()
            if current_time == config["MORNING_CHECK"] and last_morning != today:
                scheduled_check(token, chat_id, config)
                last_morning = today

        except KeyboardInterrupt:
            logging.info("Остановка.")
            send_message(token, chat_id, "🛑 Бот остановлен.")
            break
        except Exception as e:
            logging.error(f"Ошибка цикла: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_bot()
