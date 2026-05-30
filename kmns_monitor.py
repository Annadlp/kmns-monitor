#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   КМНС МОНИТОР v1.2                                              ║
║   Мониторинг законодательства о коренных народах России          ║
║                                                                   ║
║   Источники:                                                      ║
║   • publication.pravo.gov.ru — принятые НПА (JSON API)           ║
║   • fadn.gov.ru              — новости и документы ФАДН          ║
║   • news.google.com          — агрегатор российских новостей     ║
║   • sozd.duma.gov.ru         — законопроекты (только рос. IP)    ║
║   • regulation.gov.ru        — проекты НПА (только рос. IP)      ║
╚══════════════════════════════════════════════════════════════════╝
"""

import requests
import json
import csv
import os
import sys
import time
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote
from email.utils import parsedate_to_datetime

# RSS/API fallback
try:
    from kmns_rss import (fetch_with_cascade, check_sources_health,
                           parse_faolex, faolex_indigenous_russia)
    CASCADE_AVAILABLE = True
except ImportError:
    CASCADE_AVAILABLE = False

# ════════════════════════════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ════════════════════════════════════════════════════════════════════

KEYWORDS_PRIMARY = [
    "коренные малочисленные народы",
    "КМНС",
    "традиционное природопользование",
    "исконная среда обитания",
]

KEYWORDS_SECONDARY = [
    "территории традиционного природопользования",
    "малочисленные народы Севера",
    "малочисленные народы Арктики",
    "этническая община",
    "родовые угодья",
    "общины коренных народов",
]

KEYWORDS_ETHNIC = [
    "чукчи", "эскимосы", "юпик", "алеуты", "нанайцы",
    "ненцы", "эвенки", "саами", "юкагиры", "ительмены",
    "нивхи", "коряки", "долганы", "нганасаны", "селькупы",
    "кеты", "манси", "ханты", "шорцы", "телеуты",
]

ALL_KEYWORDS = KEYWORDS_PRIMARY + KEYWORDS_SECONDARY + KEYWORDS_ETHNIC

# Поисковые запросы для Google News — специально подобраны
GOOGLE_NEWS_TERMS = [
    "КМНС закон",
    "КМНС приказ",
    "коренные малочисленные народы законодательство",
    "коренные малочисленные народы приказ",
    "коренные народы Севера приказ постановление",
    "Минсельхоз КМНС",
    "Минсельхоз коренные народы",
]

STATE_FILE = "kmns_state.json"
OUTPUT_DIR = "kmns_output"
LOG_FILE   = "kmns_monitor.log"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ════════════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ════════════════════════════════════════════════════════════════════

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def load_state():
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen": {}, "last_run": None, "stats": {}}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def ensure_dirs():
    Path(OUTPUT_DIR).mkdir(exist_ok=True)

def make_uid(source, title):
    return f"{source}::{title[:80].strip()}"

def is_relevant(text):
    t = text.lower()
    return any(kw.lower() in t for kw in ALL_KEYWORDS)

def safe_get(url, params=None, timeout=60, retries=2):
    for attempt in range(retries):
        try:
            r = requests.get(
                url, params=params,
                headers=HEADERS, timeout=timeout,
                allow_redirects=True
            )
            r.raise_for_status()
            return r
        except requests.exceptions.Timeout:
            log(f"Таймаут [{attempt+1}/{retries}]: {url}", "WARN")
        except requests.exceptions.ConnectionError as e:
            log(f"Ошибка соединения: {url} — {e}", "WARN")
            break
        except requests.exceptions.HTTPError as e:
            log(f"HTTP ошибка {e.response.status_code}: {url}", "WARN")
            break
        except Exception as e:
            log(f"Неизвестная ошибка: {url} — {e}", "WARN")
            break
        time.sleep(2)
    return None


# ════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 1: PUBLICATION.PRAVO.GOV.RU
# Принятые НПА после регистрации в Минюсте — JSON API
# ════════════════════════════════════════════════════════════════════

def parse_pravo(keywords, days_back=30):
    results = []
    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    failed = False

    for kw in keywords:
        url = "https://publication.pravo.gov.ru/api/Documents"
        params = {
            "pageSize":      25,
            "pageIndex":     1,
            "searchText":    kw,
            "dateFrom":      date_from,
            "sort":          "Date",
            "sortDirection": "desc",
        }

        r = safe_get(url, params=params)
        if not r:
            failed = True
            continue

        try:
            data = r.json()
        except Exception as e:
            log(f"pravo JSON parse error [{kw[:20]}]: {e}", "WARN")
            failed = True
            continue

        items = data.get("items", data.get("Items", []))
        if not items:
            items = data if isinstance(data, list) else []

        for item in items:
            title = (
                item.get("complexName") or
                item.get("name") or
                item.get("Name") or ""
            )
            if not title:
                continue

            doc_id   = item.get("eoNumber") or item.get("id") or item.get("Id") or ""
            date_str = (
                item.get("signDate") or
                item.get("publicationDate") or
                item.get("SignDate") or ""
            )[:10]

            href = (
                f"https://publication.pravo.gov.ru/document/{doc_id}"
                if doc_id else "https://publication.pravo.gov.ru"
            )

            doc_type_raw = item.get("documentType") or item.get("DocumentType") or {}
            if isinstance(doc_type_raw, dict):
                type_name = doc_type_raw.get("name") or doc_type_raw.get("Name") or "НПА"
            else:
                type_name = str(doc_type_raw) or "НПА"

            if type_name == "НПА":
                t = title.lower()
                if "федеральный закон" in t:      type_name = "Федеральный закон"
                elif "указ президента" in t:       type_name = "Указ Президента"
                elif "постановление правительства" in t: type_name = "Постановление Правительства"
                elif "приказ" in t:                type_name = "Приказ"
                elif "распоряжение" in t:          type_name = "Распоряжение"

            results.append({
                "источник":   "pravo.gov.ru",
                "тип":        type_name,
                "статус":     "Принят/подписан",
                "заголовок":  title,
                "дата":       date_str,
                "ключ_слово": kw,
                "url":        href,
                "получено":   datetime.now().isoformat(),
            })

        time.sleep(0.8)

    status = "failed" if (failed and not results) else ("ok" if results else "empty")
    log(f"pravo.gov.ru: найдено {len(results)} документов")
    return results, status


# ════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 2: SOZD.DUMA.GOV.RU (только с российского IP)
# ════════════════════════════════════════════════════════════════════

def parse_sozd(keywords, days_back=90):
    results = []
    failed = False

    for kw in keywords:
        r = safe_get("https://sozd.duma.gov.ru/oz", params={"keywords": kw, "stype": 0})
        if not r:
            failed = True
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.select_one("table.table")
        rows = table.select("tbody tr") if table else soup.select(".OzItem, .oz-item, li.law-item")

        for row in rows:
            cells = row.find_all("td")
            if cells and len(cells) >= 3:
                number_cell = cells[0]
                name_cell   = cells[1]
                date_cell   = cells[2] if len(cells) > 2 else None
                status_cell = cells[-1]
                a_tag  = number_cell.find("a") or name_cell.find("a")
                title  = name_cell.get_text(strip=True)
                number = number_cell.get_text(strip=True)
            else:
                a_tag  = row.find("a")
                title  = a_tag.get_text(strip=True) if a_tag else row.get_text(strip=True)
                number = ""
                date_cell = status_cell = None

            if not title or len(title) < 5:
                continue

            href = a_tag.get("href", "") if a_tag else ""
            if href and not href.startswith("http"):
                href = "https://sozd.duma.gov.ru" + href

            date_str   = date_cell.get_text(strip=True) if date_cell else ""
            status_str = status_cell.get_text(strip=True) if status_cell else ""
            urgent = any(s in status_str.lower() for s in [
                "подписан", "принят", "третье чтение", "одобрен советом федерации"
            ])

            results.append({
                "источник":   "sozd.duma.gov.ru",
                "тип":        "Законопроект",
                "статус":     status_str,
                "заголовок":  title,
                "номер":      number,
                "дата":       date_str,
                "ключ_слово": kw,
                "url":        href,
                "срочно":     "⚡ ДА" if urgent else "",
                "получено":   datetime.now().isoformat(),
            })

        time.sleep(0.8)

    status = "failed" if (failed and not results) else ("ok" if results else "empty")
    log(f"sozd.duma.gov.ru: найдено {len(results)} законопроектов")
    return results, status


# ════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 3: REGULATION.GOV.RU (только с российского IP)
# ════════════════════════════════════════════════════════════════════

def parse_regulation(keywords, days_back=60):
    results = []
    failed = False
    search_url = "https://regulation.gov.ru/projects/List/AdvancedSearch"

    for kw in keywords[:6]:
        try:
            payload = {
                "Name":          kw,
                "SortColumn":    "PublicationDate",
                "SortAscending": False,
                "PageSize":      20,
                "Page":          1,
                "StatusId":      None,
            }
            r = requests.post(
                search_url, json=payload, timeout=60,
                headers={**HEADERS,
                         "Content-Type": "application/json",
                         "X-Requested-With": "XMLHttpRequest"},
            )

            if r.status_code == 200:
                data  = r.json()
                items = data.get("Data", data.get("data", []))

                for item in items:
                    npa_id   = item.get("ID") or item.get("Id") or ""
                    title    = item.get("Name", item.get("name", ""))
                    date_pub = item.get("PublicationDate", "")
                    status   = item.get("CurrentStageName", item.get("Status", ""))
                    dev_org  = item.get("DeveloperOrganizationName", "")

                    if not title:
                        continue

                    link    = f"https://regulation.gov.ru/projects#npa={npa_id}" if npa_id else search_url
                    is_open = "обсуждение" in status.lower() or "публичное" in status.lower()

                    results.append({
                        "источник":    "regulation.gov.ru",
                        "тип":         "Проект НПА",
                        "статус":      status,
                        "заголовок":   title,
                        "разработчик": dev_org,
                        "дата":        date_pub[:10] if date_pub else "",
                        "ключ_слово":  kw,
                        "url":         link,
                        "открыто_для": "📢 ОТКРЫТО ДЛЯ ЗАМЕЧАНИЙ" if is_open else "",
                        "получено":    datetime.now().isoformat(),
                    })
            else:
                failed = True

        except requests.exceptions.Timeout:
            log(f"regulation.gov.ru таймаут [{kw[:20]}]", "WARN")
            failed = True
        except requests.exceptions.JSONDecodeError:
            r2 = safe_get(f"https://regulation.gov.ru/projects?search={quote(kw)}")
            if r2:
                soup = BeautifulSoup(r2.text, "html.parser")
                for item in soup.select(".npa-list li, .project-item"):
                    a = item.find("a")
                    if not a:
                        continue
                    results.append({
                        "источник":   "regulation.gov.ru",
                        "тип":        "Проект НПА",
                        "статус":     "",
                        "заголовок":  a.get_text(strip=True),
                        "ключ_слово": kw,
                        "url":        urljoin("https://regulation.gov.ru", a.get("href", "")),
                        "получено":   datetime.now().isoformat(),
                    })
        except Exception as e:
            log(f"regulation.gov.ru [{kw[:20]}]: {e}", "WARN")
            failed = True

        time.sleep(1.0)

    status = "failed" if (failed and not results) else ("ok" if results else "empty")
    log(f"regulation.gov.ru: найдено {len(results)} проектов")
    return results, status


# ════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 4: FADN.GOV.RU
# ════════════════════════════════════════════════════════════════════

def parse_fadn(days_back=30):
    results = []
    failed  = False
    sections = [
        ("https://fadn.gov.ru/news/",      "Новость"),
        ("https://fadn.gov.ru/documents/", "Документ"),
    ]

    for url, section_type in sections:
        r = safe_get(url)
        if not r:
            failed = True
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        candidates = (
            soup.select("article.news") or
            soup.select(".news-item") or
            soup.select(".content-list li") or
            soup.select("div.item") or
            soup.select("ul.list li")
        )

        for item in candidates:
            a_tag = item.find("a")
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            if not is_relevant(title) and not is_relevant(item.get_text()):
                continue

            href = a_tag.get("href", "")
            if href and not href.startswith("http"):
                href = "https://fadn.gov.ru" + href

            date_el = (
                item.select_one(".date") or
                item.select_one("time") or
                item.select_one(".news__date") or
                item.select_one("span.date")
            )

            results.append({
                "источник":   "fadn.gov.ru",
                "тип":        section_type,
                "статус":     "",
                "заголовок":  title,
                "дата":       date_el.get_text(strip=True) if date_el else "",
                "ключ_слово": "КМНС (ФАДН)",
                "url":        href,
                "получено":   datetime.now().isoformat(),
            })

        time.sleep(0.8)

    status = "failed" if (failed and not results) else ("ok" if results else "empty")
    log(f"fadn.gov.ru: найдено {len(results)} релевантных материалов")
    return results, status


# ════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 5: GOOGLE NEWS RSS
# Работает с любого IP — агрегирует российские СМИ и правовые сервисы
# ════════════════════════════════════════════════════════════════════

def parse_google_news(days_back=7):
    results = []
    failed  = False
    cutoff  = datetime.now() - timedelta(days=days_back)

    for term in GOOGLE_NEWS_TERMS:
        r = safe_get(
            "https://news.google.com/rss/search",
            params={"q": term, "hl": "ru", "gl": "RU", "ceid": "RU:ru"}
        )
        if not r:
            failed = True
            continue

        try:
            root = ET.fromstring(r.content)
        except Exception as e:
            log(f"Google News XML parse error [{term[:20]}]: {e}", "WARN")
            continue

        for item in root.findall(".//item"):
            title    = item.findtext("title", "").strip()
            link     = item.findtext("link", "").strip()
            pub_date = item.findtext("pubDate", "").strip()
            source   = item.findtext("source", "").strip()

            if not title or len(title) < 10:
                continue

            # Фильтр по дате
            try:
                pub_dt = parsedate_to_datetime(pub_date).replace(tzinfo=None)
                if pub_dt < cutoff:
                    continue
            except Exception:
                pass

            # Для Google News не применяем is_relevant() —
            # поиск уже тематический, фильтр только отсеивал бы нужное
            results.append({
                "источник":   "news.google.com",
                "тип":        "Новость/НПА",
                "статус":     source,
                "заголовок":  title,
                "дата":       pub_date[:16] if pub_date else "",
                "ключ_слово": term,
                "url":        link,
                "получено":   datetime.now().isoformat(),
            })

        time.sleep(0.5)

    status = "failed" if (failed and not results) else ("ok" if results else "empty")
    log(f"Google News: найдено {len(results)} материалов")
    return results, status


# ════════════════════════════════════════════════════════════════════
# ДЕДУПЛИКАЦИЯ
# ════════════════════════════════════════════════════════════════════

def deduplicate(results, state):
    seen_in_run = {}
    unique = []

    for r in results:
        uid = make_uid(r["источник"], r["заголовок"])
        if uid in seen_in_run:
            seen_in_run[uid]["ключ_слово"] += f", {r.get('ключ_слово','')}"
            continue
        r["uid"]   = uid
        r["новый"] = uid not in state["seen"]
        seen_in_run[uid] = r
        unique.append(r)

    return unique


# ════════════════════════════════════════════════════════════════════
# СОХРАНЕНИЕ
# ════════════════════════════════════════════════════════════════════

def save_csv(results, path):
    if not results:
        return
    fieldnames = list(results[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

def save_json(results, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

def save_digest(results, path, source_status=None, new_only=True):
    to_print = [r for r in results if r.get("новый")] if new_only else results

    with open(path, "w", encoding="utf-8") as f:
        f.write("═" * 70 + "\n")
        f.write("  ДАЙДЖЕСТ: КМНС-ЗАКОНОДАТЕЛЬСТВО\n")
        f.write(f"  Сформирован: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
        f.write(f"  Всего: {len(results)}  |  Новых: {len([r for r in results if r.get('новый')])}\n")

        # Статус источников
        if source_status:
            failed = [s for s, st in source_status.items() if st == "failed"]
            if failed:
                f.write(f"  ⚠️ Недоступны: {', '.join(failed)}\n")

        f.write("═" * 70 + "\n\n")

        sources = [
            ("pravo.gov.ru",      "📜 ПРИНЯТЫЕ НПА (pravo.gov.ru)"),
            ("sozd.duma.gov.ru",  "🏛️  ЗАКОНОПРОЕКТЫ ГОСДУМЫ"),
            ("regulation.gov.ru", "📋 ПРОЕКТЫ НПА (regulation.gov.ru)"),
            ("fadn.gov.ru",       "🏢 НОВОСТИ И ДОКУМЕНТЫ ФАДН"),
            ("news.google.com",   "📰 НОВОСТИ (Google News)"),
        ]

        for src_id, src_label in sources:
            src_items = [r for r in to_print if r["источник"] == src_id]
            if not src_items:
                continue

            f.write(f"\n{'─' * 70}\n")
            f.write(f"{src_label}  [{len(src_items)} документов]\n")
            f.write(f"{'─' * 70}\n\n")

            for r in src_items:
                new_mark = "★ НОВЫЙ  " if r.get("новый") else ""
                urgent   = r.get("срочно", "") or r.get("открыто_для", "")
                if urgent or new_mark:
                    f.write(f"  {new_mark}{urgent}\n")
                f.write(f"  ▸ {r['заголовок']}\n")
                if r.get("номер"):      f.write(f"    Номер:       {r['номер']}\n")
                if r.get("статус"):     f.write(f"    Статус:      {r['статус']}\n")
                if r.get("разработчик"): f.write(f"    Разработчик: {r['разработчик']}\n")
                if r.get("дата"):       f.write(f"    Дата:        {r['дата']}\n")
                if r.get("ключ_слово"): f.write(f"    Найдено по:  {r['ключ_слово']}\n")
                f.write(f"    URL:         {r['url']}\n\n")

        f.write("═" * 70 + "\n")


# ════════════════════════════════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ
# ════════════════════════════════════════════════════════════════════

def run(
    days_back  = 30,
    sources    = ("pravo", "fadn"),
    keywords   = None,
    new_only   = True,
    output_fmt = "all",
    verbose    = True,
):
    ensure_dirs()
    state = load_state()
    kws   = keywords or KEYWORDS_PRIMARY

    log(f"Запуск мониторинга. Источников: {len(sources)}, "
        f"ключевых слов: {len(kws)}, глубина: {days_back} дней")

    all_results   = []
    source_status = {}

    if "pravo" in sources:
        log("→ pravo.gov.ru...")
        res, st = parse_pravo(kws, days_back)
        source_status["pravo.gov.ru"] = st
        all_results += res

    if "sozd" in sources:
        log("→ sozd.duma.gov.ru...")
        res, st = parse_sozd(kws, days_back)
        source_status["sozd.duma.gov.ru"] = st
        all_results += res

    if "regulation" in sources:
        log("→ regulation.gov.ru...")
        res, st = parse_regulation(kws, days_back)
        source_status["regulation.gov.ru"] = st
        all_results += res

    if "fadn" in sources:
        log("→ fadn.gov.ru...")
        res, st = parse_fadn(days_back)
        source_status["fadn.gov.ru"] = st
        all_results += res

        if CASCADE_AVAILABLE:
            log("→ faolex.fao.org...")
            try:
                fao_results = faolex_indigenous_russia(days_back)
                source_status["faolex.fao.org"] = "ok" if fao_results else "empty"
                all_results += fao_results
                log(f"faolex.fao.org: найдено {len(fao_results)} документов")
            except Exception as e:
                log(f"faolex.fao.org: ошибка — {e}", "WARN")
                source_status["faolex.fao.org"] = "failed"

    # Google News — всегда, с любого IP
    log("→ Google News RSS...")
    res, st = parse_google_news(days_back=7)
    source_status["news.google.com"] = st
    all_results += res

    # Итог по недоступным источникам
    failed = [s for s, st in source_status.items() if st == "failed"]
    if failed:
        log(f"Недоступны: {', '.join(failed)}", "WARN")

    results   = deduplicate(all_results, state)
    new_count = len([r for r in results if r.get("новый")])
    log(f"Итого: {len(results)} уникальных документов, новых: {new_count}")

    ts = datetime.now().strftime("%Y%m%d_%H%M")

    if output_fmt in ("csv", "all"):
        p = f"{OUTPUT_DIR}/kmns_{ts}.csv"
        save_csv(results, p)
        log(f"Сохранено: {p}")

    if output_fmt in ("json", "all"):
        p = f"{OUTPUT_DIR}/kmns_{ts}.json"
        save_json(results, p)
        log(f"Сохранено: {p}")

    digest_path = f"{OUTPUT_DIR}/дайджест_{ts}.txt"
    save_digest(results, digest_path, source_status=source_status, new_only=new_only)
    log(f"Дайджест: {digest_path}")

    # Обновляем state
    for r in results:
        state["seen"][r["uid"]] = {
            "первый_раз": datetime.now().isoformat(),
            "источник":   r["источник"],
        }
    if len(state["seen"]) > 10000:
        for k in sorted(state["seen"].keys())[:-8000]:
            del state["seen"][k]

    state["last_run"] = datetime.now().isoformat()
    state["stats"][ts] = {"total": len(results), "new": new_count}
    save_state(state)

    if verbose:
        if new_count > 0:
            print("\n" + "═" * 60)
            print(f"  НОВЫХ ДОКУМЕНТОВ: {new_count}")
            print("═" * 60)
            for r in results:
                if r.get("новый"):
                    flag = r.get("срочно", "") or r.get("открыто_для", "") or ""
                    print(f"\n  [{r['источник']}] {r['тип']}")
                    print(f"  {r['заголовок'][:80]}")
                    if r.get("статус"): print(f"  Статус: {r['статус']}")
                    if flag:            print(f"  {flag}")
                    print(f"  {r['url']}")
        else:
            print("\n  Новых документов не найдено.")
            if failed:
                print(f"  ⚠️  Недоступны: {', '.join(failed)}")
            print(f"  Последнее обновление: {state['last_run']}")

    print(f"\n  Файлы сохранены в папке: {OUTPUT_DIR}/\n")

    # Возвращаем оба значения — для kmns_bot.py
    return results, source_status


# ════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="КМНС Монитор — мониторинг законодательства о коренных народах России",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python kmns_monitor.py                        # стандартный запуск
  python kmns_monitor.py --days 90              # глубже в историю
  python kmns_monitor.py --all                  # все, не только новые
  python kmns_monitor.py --source pravo fadn    # конкретные источники
  python kmns_monitor.py --keywords "ТТП"       # свои ключевые слова
  python kmns_monitor.py --format json          # только JSON
  python kmns_monitor.py --full                 # все ~40 ключевых слов
        """
    )
    parser.add_argument("--days",     type=int, default=30)
    parser.add_argument("--all",      action="store_true")
    parser.add_argument("--source",   nargs="+",
                        choices=["pravo", "sozd", "regulation", "fadn"],
                        default=["pravo", "fadn"])
    parser.add_argument("--format",   choices=["csv", "json", "all"], default="all")
    parser.add_argument("--keywords", nargs="+")
    parser.add_argument("--full",     action="store_true")
    parser.add_argument("--quiet",    action="store_true")

    args = parser.parse_args()

    kws = None
    if args.full:
        kws = ALL_KEYWORDS
    elif args.keywords:
        kws = KEYWORDS_PRIMARY + args.keywords

    results, source_status = run(
        days_back  = args.days,
        sources    = tuple(args.source),
        keywords   = kws,
        new_only   = not args.all,
        output_fmt = args.format,
        verbose    = not args.quiet,
    )
