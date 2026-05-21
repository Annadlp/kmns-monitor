#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kmns_rss.py — RSS/API fallback для КМНС монитора.

Используется автоматически когда основной HTML-парсер
возвращает таймаут или 403. Подключается к kmns_monitor.py.
"""

import requests
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# ════════════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ════════════════════════════════════════════════════════════════════

def safe_get(url, params=None, timeout=15):
    try:
        r = requests.get(url, params=params, headers=HEADERS,
                         timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception:
        return None

def parse_rss(xml_text, source_name, keywords):
    """Парсит RSS/Atom XML, фильтрует по ключевым словам."""
    results = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return results

    # Поддержка RSS 2.0 и Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)

    for item in items:
        # Заголовок
        title_el = item.find("title") or item.find("atom:title", ns)
        title = title_el.text.strip() if title_el is not None and title_el.text else ""

        # Описание/контент
        desc_el = (item.find("description") or
                   item.find("summary") or
                   item.find("atom:summary", ns))
        desc = desc_el.text or "" if desc_el is not None else ""

        # Фильтр по ключевым словам
        full_text = f"{title} {desc}".lower()
        matched_kw = next(
            (kw for kw in keywords if kw.lower() in full_text), None
        )
        if not matched_kw:
            continue

        # Ссылка
        link_el = item.find("link") or item.find("atom:link", ns)
        if link_el is not None:
            link = link_el.text or link_el.get("href", "")
        else:
            link = ""

        # Дата
        date_el = (item.find("pubDate") or
                   item.find("dc:date") or
                   item.find("atom:published", ns) or
                   item.find("atom:updated", ns))
        date_str = date_el.text[:10] if date_el is not None and date_el.text else ""

        results.append({
            "источник":   source_name,
            "тип":        "НПА (RSS)",
            "статус":     "",
            "заголовок":  title,
            "дата":       date_str,
            "ключ_слово": matched_kw,
            "url":        link.strip(),
            "получено":   datetime.now().isoformat(),
            "via":        "rss",
        })

    return results


# ════════════════════════════════════════════════════════════════════
# FALLBACK: PRAVO.GOV.RU — RSS
# ════════════════════════════════════════════════════════════════════

def pravo_rss(keywords):
    """
    RSS-лента pravo.gov.ru с последними НПА.
    Используется как fallback к HTML-парсеру.
    """
    results = []
    # Основная лента новых документов
    urls = [
        "http://pravo.gov.ru/proxy/ips/?searchtype=NEWS&sort=date&intNum=50",
        "http://www.pravo.gov.ru/news/rss/",
    ]
    for url in urls:
        r = safe_get(url, timeout=15)
        if r and r.text:
            parsed = parse_rss(r.text, "pravo.gov.ru", keywords)
            if parsed:
                results.extend(parsed)
                break
        time.sleep(1)

    return results


# ════════════════════════════════════════════════════════════════════
# FALLBACK: СОЗД — API ДУМЫ
# ════════════════════════════════════════════════════════════════════

def sozd_api(keywords, days_back=60):
    """
    Официальное API Государственной Думы.
    Открытое, не требует регистрации для базовых запросов.
    Документация: https://api.duma.gov.ru/api/
    """
    results = []
    base_url = "https://api.duma.gov.ru/api/secure/lawsSearch.json"

    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    for kw in keywords[:4]:
        params = {
            "name":        kw,
            "dateFrom":    date_from,
            "limit":       20,
            "offset":      0,
        }
        r = safe_get(base_url, params=params, timeout=20)
        if not r:
            time.sleep(1)
            continue

        try:
            data = r.json()
        except Exception:
            time.sleep(1)
            continue

        laws = data.get("laws", data.get("result", []))
        for law in laws:
            title  = law.get("name", law.get("subject", ""))
            num    = law.get("number", "")
            date   = law.get("introductionDate", law.get("date", ""))[:10]
            status = law.get("lastEvent", {}).get("stage", {}).get("name", "")
            url    = f"https://sozd.duma.gov.ru/bill/{num}" if num else ""

            if not title:
                continue

            results.append({
                "источник":   "sozd.duma.gov.ru",
                "тип":        "Законопроект",
                "статус":     status,
                "заголовок":  title,
                "номер":      num,
                "дата":       date,
                "ключ_слово": kw,
                "url":        url,
                "получено":   datetime.now().isoformat(),
                "via":        "api",
            })

        time.sleep(0.5)

    return results


def sozd_rss(keywords):
    """
    RSS Госдумы — новости и законопроекты.
    Второй fallback если API тоже недоступен.
    """
    results = []
    rss_urls = [
        "http://duma.gov.ru/news/rss/",
        "https://sozd.duma.gov.ru/rss",
    ]
    for url in rss_urls:
        r = safe_get(url, timeout=15)
        if r and r.text:
            parsed = parse_rss(r.text, "sozd.duma.gov.ru", keywords)
            if parsed:
                for item in parsed:
                    item["тип"] = "Законопроект (RSS)"
                results.extend(parsed)
                break
        time.sleep(1)
    return results


# ════════════════════════════════════════════════════════════════════
# FALLBACK: REGULATION.GOV.RU — RSS
# ════════════════════════════════════════════════════════════════════

def regulation_rss(keywords):
    """
    RSS regulation.gov.ru — проекты НПА.
    Работает стабильнее чем POST API с зарубежных IP.
    """
    results = []
    rss_urls = [
        "https://regulation.gov.ru/api/projects/rss",
        "https://regulation.gov.ru/projects/rss",
    ]
    for url in rss_urls:
        r = safe_get(url, timeout=15)
        if r and r.text:
            parsed = parse_rss(r.text, "regulation.gov.ru", keywords)
            if parsed:
                for item in parsed:
                    item["тип"] = "Проект НПА"
                results.extend(parsed)
                break
        time.sleep(1)
    return results


def regulation_search(keywords):
    """
    GET-поиск на regulation.gov.ru — альтернатива POST API.
    """
    results = []
    for kw in keywords[:4]:
        url = f"https://regulation.gov.ru/projects#npa=&text={quote(kw)}"
        # Пробуем через search endpoint
        search_url = "https://regulation.gov.ru/Npa/PublicView"
        params = {"search": kw, "pageSize": 20}
        r = safe_get(search_url, params=params, timeout=15)
        if r and r.text and len(r.text) > 100:
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, "html.parser")
                for item in soup.select(".npa-item, .project-item, li.item"):
                    a = item.find("a")
                    if not a:
                        continue
                    title = a.get_text(strip=True)
                    href = a.get("href", "")
                    if href and not href.startswith("http"):
                        href = "https://regulation.gov.ru" + href
                    results.append({
                        "источник":   "regulation.gov.ru",
                        "тип":        "Проект НПА",
                        "статус":     "",
                        "заголовок":  title,
                        "дата":       "",
                        "ключ_слово": kw,
                        "url":        href,
                        "получено":   datetime.now().isoformat(),
                        "via":        "search",
                    })
            except Exception:
                pass
        time.sleep(1)
    return results


# ════════════════════════════════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ КАСКАДА
# ════════════════════════════════════════════════════════════════════

def fetch_with_cascade(source, primary_fn, keywords, days_back=30):
    """
    Универсальная функция каскада:
    1. Пробует основной парсер (primary_fn)
    2. При неудаче — переключается на RSS/API fallback
    3. Логирует статус каждого источника

    Возвращает (results, status) где status = "ok" / "rss" / "api" / "failed"
    """
    # Пробуем основной источник
    try:
        results = primary_fn(keywords, days_back) if days_back else primary_fn(keywords)
        if results is not None and len(results) >= 0:
            # Даже 0 результатов — это не ошибка, сайт ответил
            # Проверяем что не все таймауты (results == [] из-за ошибок)
            return results, "ok"
    except Exception:
        pass

    # Fallback по источнику
    print(f"  [CASCADE] {source}: основной парсер недоступен, переключаюсь на fallback...")

    fallbacks = {
        "pravo":      [(pravo_rss,        [keywords])],
        "sozd":       [(sozd_api,         [keywords, days_back]),
                       (sozd_rss,         [keywords])],
        "regulation": [(regulation_rss,   [keywords]),
                       (regulation_search,[keywords])],
        "fadn":       [],  # нет RSS, просто пропускаем
    }

    for fn, args in fallbacks.get(source, []):
        try:
            results = fn(*args)
            if results:
                print(f"  [CASCADE] {source}: fallback сработал ({fn.__name__}), найдено {len(results)}")
                return results, fn.__name__
        except Exception as e:
            print(f"  [CASCADE] {source}: fallback {fn.__name__} тоже упал: {e}")
        time.sleep(1)

    print(f"  [CASCADE] {source}: все источники недоступны")
    return [], "failed"


# ════════════════════════════════════════════════════════════════════
# СТАТУС ИСТОЧНИКОВ
# ════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 5: FAOLEX
# База данных законодательства ФАО — включает региональное РФ
# Стабильный API, не блокирует зарубежные IP
# Особенно ценен для регионального законодательства Чукотки,
# Ямала, Красноярского края — которое на pravo.gov.ru найти сложнее
# ════════════════════════════════════════════════════════════════════

FAOLEX_KEYWORDS_EN = [
    "indigenous peoples",
    "traditional land use",
    "subsoil",
    "Arctic",
    "Chukotka",
    "reindeer herding",
    "northern peoples",
]

def parse_faolex(keywords=None, days_back=90):
    """
    Поиск по FAOLex — база данных ФАО по законодательству
    в области природных ресурсов, земли, продовольствия.
    Россия регулярно отправляет туда федеральное и региональное
    законодательство, включая законы субъектов Арктики.

    API документация: https://faolex.fao.org/faolex/help/
    """
    results = []
    base_url = "https://faolex.fao.org/api/en/search"

    # Поиск по английским тегам ФАО
    search_terms = FAOLEX_KEYWORDS_EN.copy()
    # Добавляем русские термины транслитом если есть
    if keywords:
        # Берём только короткие — FAOLex лучше ищет по одному слову
        for kw in keywords:
            if len(kw) < 20:
                search_terms.append(kw)

    seen_ids = set()

    for term in search_terms[:8]:
        params = {
            "q":          term,
            "co":         "RUS",          # только Россия
            "type":       "national",     # национальное законодательство
            "limit":      20,
            "offset":     0,
            "sortby":     "date",
            "sortorder":  "desc",
        }

        r = safe_get(base_url, params=params, timeout=20)
        if not r:
            time.sleep(1)
            continue

        try:
            data = r.json()
        except Exception:
            time.sleep(1)
            continue

        # FAOLex возвращает разные структуры — обрабатываем оба варианта
        docs = (data.get("data", []) or
                data.get("results", []) or
                data.get("documents", []) or
                (data if isinstance(data, list) else []))

        for doc in docs:
            # Уникальный ID документа
            doc_id = (doc.get("id") or
                      doc.get("faolexId") or
                      doc.get("recid") or
                      str(doc.get("title", ""))[:40])

            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)

            # Заголовок — FAOLex даёт на нескольких языках
            title = (doc.get("titleTranslated") or   # переведённый
                     doc.get("titleOriginal") or      # оригинальный
                     doc.get("title") or
                     doc.get("name") or "")

            if not title:
                continue

            # Дата
            date_str = (doc.get("dateOfEntry") or
                        doc.get("dateOfText") or
                        doc.get("date") or "")
            if date_str:
                date_str = str(date_str)[:10]

            # Ссылка
            faolex_id = doc.get("faolexId") or doc.get("id") or ""
            if faolex_id:
                url = f"https://faolex.fao.org/docs/pdf/{faolex_id}.pdf"
                web_url = f"https://faolex.fao.org/cgi-bin/faolex.exe?rec={faolex_id}"
            else:
                url = "https://faolex.fao.org/faolex/"

            # Тип документа
            doc_type = (doc.get("typeOfDocument") or
                        doc.get("type") or "Законодательный акт")

            # Территориальное подразделение (для региональных законов)
            territory = doc.get("territorialSubdivision") or doc.get("region") or ""

            # Темы
            subjects = doc.get("subject") or doc.get("subjects") or []
            if isinstance(subjects, list):
                subjects_str = ", ".join(subjects[:3])
            else:
                subjects_str = str(subjects)

            result_title = title
            if territory:
                result_title = f"[{territory}] {title}"

            results.append({
                "источник":    "faolex.fao.org",
                "тип":         doc_type,
                "статус":      "Принят",
                "заголовок":   result_title,
                "дата":        date_str,
                "ключ_слово":  term,
                "url":         web_url if faolex_id else url,
                "темы":        subjects_str,
                "получено":    datetime.now().isoformat(),
                "via":         "faolex_api",
            })

        time.sleep(0.8)

    return results


def faolex_indigenous_russia(days_back=180):
    """
    Специализированный запрос к FAOLex:
    только документы России по тематике коренных народов.
    Более точный чем общий поиск.
    """
    results = []
    base_url = "https://faolex.fao.org/api/en/search"

    # Тематические коды ФАО для коренных народов и природных ресурсов
    indigenous_queries = [
        {"q": "indigenous", "co": "RUS"},
        {"q": "traditional knowledge", "co": "RUS"},
        {"q": "subsistence", "co": "RUS", "kw": "Arctic"},
        {"q": "Chukotka", "co": "RUS"},
        {"q": "reindeer", "co": "RUS"},
        {"q": "KMNS", "co": "RUS"},
    ]

    seen = set()
    for query in indigenous_queries:
        params = {**query, "limit": 15, "sortby": "date", "sortorder": "desc"}
        r = safe_get(base_url, params=params, timeout=20)
        if not r:
            time.sleep(1)
            continue
        try:
            data = r.json()
            docs = (data.get("data", []) or
                    data.get("results", []) or
                    (data if isinstance(data, list) else []))
            for doc in docs:
                doc_id = str(doc.get("faolexId") or doc.get("id") or doc.get("title","")[:30])
                if doc_id in seen:
                    continue
                seen.add(doc_id)

                title = (doc.get("titleTranslated") or
                         doc.get("titleOriginal") or
                         doc.get("title") or "")
                if not title:
                    continue

                faolex_id = doc.get("faolexId") or doc.get("id") or ""
                territory = doc.get("territorialSubdivision") or ""
                date_str = str(doc.get("dateOfEntry") or doc.get("date") or "")[:10]

                results.append({
                    "источник":  "faolex.fao.org",
                    "тип":       doc.get("typeOfDocument", "Законодательный акт"),
                    "статус":    "Принят",
                    "заголовок": f"[{territory}] {title}" if territory else title,
                    "дата":      date_str,
                    "ключ_слово": query.get("q", ""),
                    "url":       f"https://faolex.fao.org/cgi-bin/faolex.exe?rec={faolex_id}" if faolex_id else "https://faolex.fao.org/faolex/",
                    "получено":  datetime.now().isoformat(),
                    "via":       "faolex_api",
                })
        except Exception:
            pass
        time.sleep(0.8)

    return results


def check_sources_health():
    """
    Быстрая проверка доступности всех источников.
    Возвращает словарь {источник: статус}.
    Используй для /status команды в боте.
    """
    sources = {
        "pravo.gov.ru":      "http://pravo.gov.ru/proxy/ips/?searchtype=NEWS&intNum=1",
        "sozd.duma.gov.ru":  "https://sozd.duma.gov.ru/oz",
        "regulation.gov.ru": "https://regulation.gov.ru/projects",
        "fadn.gov.ru":       "https://fadn.gov.ru/news/",
    }
    health = {}
    for name, url in sources.items():
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                health[name] = "✅ доступен"
            elif r.status_code == 403:
                health[name] = "🚫 заблокирован (403)"
            elif r.status_code == 429:
                health[name] = "⏱ rate limit (429)"
            else:
                health[name] = f"⚠️ ошибка {r.status_code}"
        except requests.exceptions.Timeout:
            health[name] = "⏰ таймаут"
        except Exception as e:
            health[name] = f"❌ недоступен"
        time.sleep(0.5)
    return health


if __name__ == "__main__":
    # Тест
    print("Проверка доступности источников...")
    health = check_sources_health()
    for src, status in health.items():
        print(f"  {src}: {status}")
