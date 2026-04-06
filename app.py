"""Главный модуль Flask-приложения MOEX Trainer.

Файл пока остаётся монолитным, но внутри него логика разбита на смысловые блоки:
- авторизация и проверки доступа;
- вспомогательные функции для данных и валидации;
- расчёты портфеля и стресс-сценариев;
- HTML-маршруты;
- JSON API для фронтенда.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps

import bcrypt
import requests
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

from config import Config
from database import Database, SECURITY_SEED
from moex_api import (
    FALLBACK,
    TICKER_MAPPING,
    MoexCache,
    build_price_forecast,
    clear_security_profile_cache,
    fetch_security_profile,
    fetch_security_candles,
    fetch_moex_prices,
    fetch_security_history_unified,
    get_price_sources,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

db = Database()
moex_cache = MoexCache(duration=Config.MOEX_CACHE_SECONDS)
_moex_refresh_started = False
PORTFOLIO_CACHE_TTL = 60
PORTFOLIO_HISTORY_CACHE_TTL = 180
_portfolio_cache: dict[tuple[int, bool], tuple[float, dict]] = {}
_portfolio_history_cache: dict[tuple[int, bool], tuple[float, dict]] = {}
_room_price_cache: dict[tuple[int, str, bool], tuple[float, dict[str, float]]] = {}
_news_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}
_global_money_leaderboard_cache: dict[int, tuple[float, dict]] = {}
APP_BUILD = "2026-04-01-ydex-final"
FORECAST_HORIZONS = {"month": 22, "year": 252, "ten_years": 2520}
NEWS_API_URL = "https://newsapi.org/v2/everything"
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "ab82bdf4e2e64dc88010f1f6c1a44c74")
NEWS_DEFAULT_QUERY = "stock market OR MOEX OR inflation OR central bank rates"
NEWS_DEFAULT_FROM = "2026-03-06"
NEWS_TIMEOUT_SECONDS = 10
NEWS_CACHE_TTL_SECONDS = 240
GLOBAL_LEADERBOARD_CACHE_TTL_SECONDS = 180
MIN_PORTFOLIO_ASSETS = 5
MAX_PORTFOLIO_ASSETS = 10
MIN_ROOM_SECURITIES = 3
MIN_MOEX_HISTORY_DATE = date(2013, 1, 1)
STRESS_MARKERS = {
    "crisis-2014": "2014-10-01",
    "pandemic-2020": "2020-03-01",
    "crisis-2022": "2022-02-24",
}
STRESS_NEWS_HINTS = {
    "crisis-2014": "currency crisis OR sanctions OR oil price",
    "pandemic-2020": "pandemic OR lockdown OR demand shock",
    "crisis-2022": "sanctions OR geopolitics OR supply chain",
}
SECURITY_PROFILE = {
    "SBER": {"dividend_yield": 10.8, "volatility_level": "средняя"},
    "GAZP": {"dividend_yield": 11.4, "volatility_level": "средняя"},
    "LKOH": {"dividend_yield": 12.1, "volatility_level": "средняя"},
    "YDEX": {"dividend_yield": 0.0, "volatility_level": "высокая"},
    "MGNT": {"dividend_yield": 7.2, "volatility_level": "средняя"},
    "GMKN": {"dividend_yield": 8.4, "volatility_level": "высокая"},
    "AFLT": {"dividend_yield": 0.0, "volatility_level": "высокая"},
    "VTBR": {"dividend_yield": 0.0, "volatility_level": "средняя"},
    "ROSN": {"dividend_yield": 11.2, "volatility_level": "средняя"},
    "NVTK": {"dividend_yield": 8.3, "volatility_level": "средняя"},
    "TATN": {"dividend_yield": 9.7, "volatility_level": "средняя"},
    "CHMF": {"dividend_yield": 12.4, "volatility_level": "высокая"},
    "PLZL": {"dividend_yield": 4.6, "volatility_level": "высокая"},
    "MOEX": {"dividend_yield": 8.1, "volatility_level": "средняя"},
    "IRAO": {"dividend_yield": 3.2, "volatility_level": "высокая"},
    "ALRS": {"dividend_yield": 10.3, "volatility_level": "высокая"},
    "SNGS": {"dividend_yield": 7.6, "volatility_level": "средняя"},
    "PHOR": {"dividend_yield": 9.1, "volatility_level": "средняя"},
    "CHMK": {"dividend_yield": 5.4, "volatility_level": "высокая"},
    "MTSS": {"dividend_yield": 12.8, "volatility_level": "средняя"},
    "RASP": {"dividend_yield": 0.0, "volatility_level": "высокая"},
}
SECURITY_NEWS_QUERY_HINTS = {
    "SBER": "Sberbank OR SBER",
    "GAZP": "Gazprom OR GAZP",
    "LKOH": "Lukoil OR LKOH",
    "YDEX": "Yandex OR YDEX",
    "MGNT": "Magnit OR MGNT",
    "GMKN": "Nornickel OR GMKN",
    "AFLT": "Aeroflot OR AFLT",
    "VTBR": "VTB OR VTBR",
    "ROSN": "Rosneft OR ROSN",
    "NVTK": "Novatek OR NVTK",
    "TATN": "Tatneft OR TATN",
    "CHMF": "Severstal OR CHMF",
    "PLZL": "Polyus OR PLZL",
    "MOEX": "Moscow Exchange OR MOEX",
    "IRAO": "Inter RAO OR IRAO",
    "ALRS": "ALROSA OR ALRS",
    "SNGS": "Surgutneftegas OR SNGS",
    "PHOR": "PhosAgro OR PHOR",
    "CHMK": "Chelyabinsk Metallurgical Plant OR CHMK OR ЧМК",
    "MTSS": "MTS OR MTSS",
    "RASP": "Raspadskaya OR RASP",
}

POSITIVE_NEWS_TERMS = (
    "growth", "record", "beat", "strong", "upgrade", "profit", "surge", "improve", "optimistic",
    "рост", "рекорд", "прибыль", "выручка", "сильный", "улучш", "повыш", "позитив",
)
NEGATIVE_NEWS_TERMS = (
    "drop", "fall", "miss", "weak", "downgrade", "loss", "decline", "risk", "concern",
    "паден", "убыт", "слаб", "снижен", "риск", "давлен", "негатив", "санкц", "инфляц",
)

# Все тикеры корректные (Яндекс = YDEX)
STRESS_EXPLANATIONS = {
    "crisis-2014": {
        "SBER": "Банковский сектор оказался под давлением из-за девальвации рубля и роста стоимости фондирования.",
        "GAZP": "Нефтегазовый сектор просел на фоне падения цен на сырьё и общей рыночной неопределённости.",
        "LKOH": "Нефтяные компании снижались из-за падения нефти и роста валютной волатильности.",
        "YDEX": "IT-компания выглядела устойчивее рынка из-за экспортной и цифровой модели бизнеса.",
        "MGNT": "Ритейл просел умеренно: спрос сохранялся, но доходы населения снижались.",
        "GMKN": "Металлурги снижались вслед за ухудшением внешней конъюнктуры.",
        "AFLT": "Транспортный сектор пострадал сильнее из-за дорогого топлива и падения спроса.",
    },
    "pandemic-2020": {
        "SBER": "Финансовый сектор реагировал на резкое ухудшение ожиданий по экономике и рост неопределённости.",
        "GAZP": "Энергетика просела из-за падения мирового спроса на сырьё в локдауны.",
        "LKOH": "Нефтяные компании резко теряли стоимость из-за обвала нефтяного рынка весной 2020 года.",
        "YDEX": "Цифровые сервисы оказались устойчивее благодаря росту онлайн-спроса.",
        "MGNT": "Ритейл выглядел стабильнее рынка, потому что спрос на товары первой необходимости сохранился.",
        "GMKN": "Металлы снижались умеренно на фоне общего риска и снижения промышленной активности.",
        "AFLT": "Авиаперевозки пострадали сильнее всех из-за закрытия границ и остановки перелётов.",
    },
    "crisis-2022": {
        "SBER": "Банковский сектор оказался под сильнейшим санкционным давлением и переоценкой рисков.",
        "GAZP": "Газовый сектор оставался волатильным из-за геополитики и ограничений на внешних рынках.",
        "LKOH": "Нефтегаз сохранял устойчивость лучше рынка, но снижался из-за санкционных рисков.",
        "YDEX": "Технологический сектор оказался под давлением из-за ограничений и реструктуризации бизнеса.",
        "MGNT": "Ритейл выглядел защитнее рынка, так как внутренний спрос поддерживал выручку.",
        "GMKN": "Металлурги теряли в цене из-за санкций, логистических сложностей и экспортных ограничений.",
        "AFLT": "Авиакомпания снижалась сильнее рынка из-за ограничений на полёты и рост затрат.",
    },
}

# Корректные размеры лотов
LOT_SIZES = {
    "SBER": 10,
    "GAZP": 10,
    "LKOH": 1,
    "YDEX": 1,
    "MGNT": 1,
    "GMKN": 10,
    "AFLT": 10,
    "VTBR": 10,
    "ROSN": 1,
    "NVTK": 1,
    "TATN": 1,
    "CHMF": 1,
    "PLZL": 1,
    "MOEX": 10,
    "IRAO": 100,
    "ALRS": 10,
    "SNGS": 100,
    "PHOR": 1,
    "CHMK": 10,
    "MTSS": 10,
    "RASP": 10,
}
SECID_ALIASES = {
    "YNDX": "YDEX",
    "VTB": "VTBR",
}
EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.IGNORECASE)


# Доступ и сессия 
# Декоратор для защиты маршрутов: перенаправляет неавторизованных на login, для API возвращает 401
def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Требуется авторизация"}), 401
            return redirect(url_for("login"))
        user = db.get_user_by_id(int(user_id))
        if not user:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({"error": "Сессия устарела. Войдите снова."}), 401
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped


# Декоратор для маршрутов, доступных только учителям: проверяет is_teacher в сессии
def teacher_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Требуется авторизация"}), 401
        if not session.get("is_teacher"):
            return jsonify({"error": "Доступ только для учителя"}), 403
        return fn(*args, **kwargs)
    return wrapped


# Декоратор для админ-маршрутов: проверяет is_admin, для API возвращает 403, для HTML — редирект
def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Требуется авторизация"}), 401
            return redirect(url_for("login"))
        if not session.get("is_admin"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Доступ только для администратора"}), 403
            return redirect(url_for("dashboard"))
        return fn(*args, **kwargs)
    return wrapped


def now() -> datetime:
    return datetime.now()


def current_user_id() -> int:
    return int(session["user_id"])


# Общие утилиты
# Безопасное преобразование в float: возвращает 0.0 при ошибке или бесконечном значении
def safe_float(value) -> float:
    try:
        result = float(value or 0)
        if not math.isfinite(result):
            return 0.0
        return round(result, 2)
    except (TypeError, ValueError):
        return 0.0


# Нормализация тикера: приводит к верхнему регистру и применяет алиасы (YNDX → YDEX)
def normalize_secid_alias(secid: str | None) -> str:
    value = str(secid or "").strip().upper()
    return SECID_ALIASES.get(value, value)


# Получение размера лота с приоритетом: переданный item → конфиг LOT_SIZES → база данных → дефолт 1
def get_lot_size(secid: str, item: dict | None = None) -> int:
    """Получить размер лота с проверкой всех источников."""
    secid = str(secid or "").strip().upper()
    secid = SECID_ALIASES.get(secid, secid)
    if item:
        lot_size = item.get("lot_size")
        if lot_size is not None:
            try:
                ls = int(lot_size)
                if ls > 0:
                    return ls
            except (TypeError, ValueError):
                pass
    if secid in LOT_SIZES:
        return LOT_SIZES[secid]
    try:
        security = db.get_security_by_secid(secid)
        if security:
            lot_size = security.get("lot_size")
            if lot_size is not None:
                try:
                    ls = int(lot_size)
                    if ls > 0:
                        return ls
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    return 1


def get_available_tickers() -> list[str]:
    tickers = db.get_active_tickers()
    return tickers or list(Config.TICKERS)


# Парсинг списка тикеров из строки/списка: удаляет дубликаты, применяет алиасы, фильтрует пустые
def parse_secid_list(raw_value) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        values = raw_value
    else:
        values = str(raw_value).split(",")
    result = []
    seen = set()
    for item in values:
        secid = str(item or "").strip().upper()
        secid = SECID_ALIASES.get(secid, secid)
        if not secid or secid in seen:
            continue
        seen.add(secid)
        result.append(secid)
    return result


# Получение ограничений по бумагам для текущей активной комнаты пользователя
def get_current_room_restrictions(user_id: int | None = None) -> list[str]:
    if not user_id:
        return []
    room = db.get_current_active_room_for_user(user_id)
    if not room:
        return []
    return parse_secid_list(room.get("allowed_secids"))


def get_current_active_room(user_id: int | None = None):
    if not user_id:
        return None
    return db.get_current_active_room_for_user(user_id)


def is_teacher_context() -> bool:
    return bool(session.get("is_teacher"))


# Универсальный парсер тела запроса: поддерживает JSON и form-data
def request_payload():
    return request.get_json() or request.form


# Очистка кэшей портфеля для конкретного пользователя: удаляет записи по user_id из обоих словарей
def clear_portfolio_caches(user_id: int) -> None:
    for cache_dict in (_portfolio_cache, _portfolio_history_cache):
        for key in list(cache_dict.keys()):
            if key[0] == user_id:
                cache_dict.pop(key, None)


# Очистка кэша цен комнаты: по room_id или всех, если room_id=None
def clear_room_price_cache(room_id: int | None = None) -> None:
    for key in list(_room_price_cache.keys()):
        if room_id is None or key[0] == room_id:
            _room_price_cache.pop(key, None)


def parse_optional_datetime(raw_value) -> datetime | None:
    if not raw_value:
        return None
    return datetime.fromisoformat(str(raw_value))


def parse_date_range(start_raw: str, end_raw: str) -> tuple[date, date]:
    return date.fromisoformat(start_raw), date.fromisoformat(end_raw)


# Нормализация названия компании для сравнения: удаляет спецсимволы, приводит к верхнему регистру, убирает ПАО/АО
def normalize_company_name(value: str) -> str:
    cleaned = re.sub(r"[^A-ZА-Я0-9]+", "", str(value or "").upper())
    replacements = {
        "ПАО": "",
        "АО": "",
        "МКПАО": "",
        "ПУБЛИЧНОЕАКЦИОНЕРНОЕОБЩЕСТВО": "",
        "АКЦИОНЕРНОЕОБЩЕСТВО": "",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    return cleaned


# Валидация бумаги против MOEX API: проверяет существование тикера и совпадение названия компании
def validate_security_input(secid: str, shortname: str) -> str | None:
    secid = str(secid or "").strip().upper()
    entered_name = str(shortname or "").strip()
    if not secid or not entered_name:
        return "Укажите тикер и название компании"

    api_ticker = SECID_ALIASES.get(secid, secid)
    url = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{api_ticker}.json"
    try:
        response = requests.get(
            url,
            params={"iss.meta": "off", "iss.only": "securities"},
            headers={"User-Agent": "MOEX-Trainer/2.0", "Accept": "application/json"},
            timeout=8,
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Failed to validate security %s against MOEX: %s", secid, exc)
        return "Не удалось проверить бумагу на MOEX. Попробуйте ещё раз."

    securities = payload.get("securities", {})
    columns = securities.get("columns") or []
    rows = securities.get("data") or []
    if not rows:
        return f"Тикер {secid} не найден на MOEX. Проверьте обозначение бумаги."

    row = rows[0]
    official_names = []
    for field in ("SHORTNAME", "SECNAME", "LATNAME", "NAME"):
        if field in columns:
            idx = columns.index(field)
            if idx < len(row) and row[idx]:
                official_names.append(str(row[idx]).strip())
    official_names = [name for name in official_names if name]
    if not official_names:
        return None

    normalized_entered = normalize_company_name(entered_name)
    normalized_official = [normalize_company_name(name) for name in official_names]
    if normalized_entered in normalized_official:
        return None
    if any(normalized_entered and (normalized_entered in name or name in normalized_entered) for name in normalized_official):
        return None

    expected_name = official_names[0]
    return f"Название не совпадает с MOEX. Для тикера {secid} на MOEX указано: «{expected_name}»."


# Проверка диапазона дат истории: не раньше 2013-01-01, end > start
def validate_moex_history_range(start_date: date, end_date: date) -> str | None:
    if start_date < MIN_MOEX_HISTORY_DATE:
        return f"Исторические данные MOEX доступны только с {MIN_MOEX_HISTORY_DATE.isoformat()}."
    if end_date < MIN_MOEX_HISTORY_DATE:
        return f"Исторические данные MOEX доступны только с {MIN_MOEX_HISTORY_DATE.isoformat()}."
    if start_date >= end_date:
        return "Дата окончания должна быть позже даты начала."
    return None


# Расчёт "виртуальной даты" комнаты: преобразует реальное время в дату внутри сценария на основе прогресса
def compute_room_virtual_date(room: dict, step_minutes: int = 5) -> date | None:
    scenario_start = room.get("scenario_start_date")
    scenario_end = room.get("scenario_end_date")
    starts_at = room.get("starts_at")
    ends_at = room.get("ends_at")
    if not scenario_start or not scenario_end or not starts_at or not ends_at:
        return None

    total_minutes = max(int((ends_at - starts_at).total_seconds() // 60), 1)
    elapsed_minutes = int(max(0, min((now() - starts_at).total_seconds() // 60, total_minutes)))
    # Округление до шага (по умолчанию 5 минут)
    rounded_minutes = min(((elapsed_minutes + step_minutes - 1) // step_minutes) * step_minutes, total_minutes)
    scenario_days = max((scenario_end - scenario_start).days, 0)
    progress = rounded_minutes / total_minutes if total_minutes > 0 else 0.0
    day_offset = min(int(round(progress * scenario_days)), scenario_days)
    return scenario_start + timedelta(days=day_offset)


# Построение карты цен для комнаты: кэширует цены по (room_id, virtual_date), загружает историю вокруг виртуальной даты
def build_room_price_map(room: dict, securities: list[dict], force_refresh: bool = False) -> tuple[dict[str, float], date | None]:
    virtual_date = compute_room_virtual_date(room, step_minutes=1 if force_refresh else 5)
    if not virtual_date:
        return (refresh_prices() if force_refresh else get_prices()), None

    cache_key = (int(room["id"]), virtual_date.isoformat(), force_refresh)
    cached = _room_price_cache.get(cache_key)
    if cached and not force_refresh and (time.time() - cached[0]) < 240:
        return dict(cached[1]), virtual_date

    prices: dict[str, float] = {}
    for security in securities:
        secid = str(security.get("secid", "")).upper()
        # Загружаем историю ±14 дней от виртуальной даты для поиска ближайшей цены
        history = fetch_security_history_unified(
            secid,
            days=45,
            start_date=virtual_date - timedelta(days=14),
            end_date=virtual_date + timedelta(days=14),
            fallback_on_empty=False,
        )
        if history:
            valid_points = [
                point for point in history
                if point.get("date") and point.get("close") not in (None, "", 0)
            ]
            if valid_points:
                # Ищем точку с минимальным расстоянием по дате, приоритет — даты не позже виртуальной
                nearest = min(
                    valid_points,
                    key=lambda point: (
                        abs((date.fromisoformat(point["date"]) - virtual_date).days),
                        0 if date.fromisoformat(point["date"]) <= virtual_date else 1,
                    ),
                )
                prices[secid] = safe_float(nearest.get("close"))
            else:
                prices[secid] = 0.0
        else:
            prices[secid] = 0.0
    _room_price_cache[cache_key] = (time.time(), dict(prices))
    return prices, virtual_date


# Получение контекста для торговли в комнате: возвращает бумагу, цену, ошибку и виртуальную дату
def get_room_trade_context(room: dict, secid: str, force_refresh: bool = False) -> tuple[dict | None, float | None, str | None, date | None]:
    secid = str(secid or "").strip().upper()
    security = db.get_security_by_secid(secid)
    if not security:
        return None, None, "Бумага не найдена", None
    prices, virtual_date = build_room_price_map(room, [security], force_refresh=force_refresh)
    price = prices.get(secid)
    if price in (None, 0):
        return security, None, "Не удалось получить цену бумаги для текущего дня комнаты", virtual_date
    return security, float(price), None, virtual_date


# Загрузка коэффициентов стресс-сценария из JSON-строки или dict
def load_scenario_coefficients(scenario: dict) -> dict[str, float]:
    coefficients = scenario["coefficients"]
    if isinstance(coefficients, str):
        coefficients = json.loads(coefficients)
    return normalize_stress_coefficients(coefficients)


# Получение коэффициентов для списка тикеров: дополняет отсутствующие расчётом по истории сценария
def get_scenario_coefficients_for_secids(scenario: dict, secids: list[str] | set[str] | tuple[str, ...]) -> dict[str, float]:
    raw_coefficients = scenario.get("coefficients") or {}
    if isinstance(raw_coefficients, str):
        try:
            raw_coefficients = json.loads(raw_coefficients)
        except json.JSONDecodeError:
            raw_coefficients = {}
    if not isinstance(raw_coefficients, dict):
        raw_coefficients = {}

    completed = dict(raw_coefficients)
    scenario_start = scenario.get("start_date")
    scenario_end = scenario.get("end_date")
    for secid in {str(item or "").strip().upper() for item in secids if item}:
        aliases = {secid}
        if secid in TICKER_MAPPING:
            aliases.add(TICKER_MAPPING[secid])
        aliases.update({original for original, mapped in TICKER_MAPPING.items() if mapped == secid})
        if any(alias in completed for alias in aliases):
            continue
        if not scenario_start or not scenario_end:
            continue
        # Если коэффициента нет — считаем его как отношение последней цены к первой за период сценария
        history = fetch_security_history_unified(
            secid,
            days=max((scenario_end - scenario_start).days + 10, 30),
            board="TQBR",
            start_date=scenario_start,
            end_date=scenario_end,
            fallback_on_empty=False,
        )
        closes = [float(point["close"]) for point in history if point.get("close") not in (None, "")]
        if len(closes) >= 2 and closes[0] > 0:
            completed[secid] = round(min(max(closes[-1] / closes[0], 0.05), 3.0), 4)
    return normalize_stress_coefficients(completed)


def serialize_scenario(scenario: dict) -> dict:
    return {
        "id": scenario["id"],
        "name": scenario["name"],
        "slug": scenario["slug"],
        "description": scenario["description"],
        "coefficients": load_scenario_coefficients(scenario),
    }


# Сериализация бумаги для рынка: объединяет данные из БД, цен, профиля и fallback-значений
def serialize_market_security(security: dict, prices: dict[str, float], force_refresh: bool = False) -> dict:
    secid = security["secid"]
    profile = SECURITY_PROFILE.get(secid, {})
    price_source = security.get("price_source") or "MOEX"
    raw_price = prices.get(secid)
    has_price = raw_price not in (None, "", 0)
    current_price = safe_float(raw_price) if has_price else None
    auto_profile = fetch_security_profile(
        secid,
        current_price=float(current_price) if current_price is not None else None,
        force_refresh=force_refresh,
    )
    return {
        "secid": secid,
        "shortname": security.get("shortname", secid),
        "sector": security.get("sector") or "Другое",
        "price": current_price,
        "price_label": f"{current_price:.2f} ₽" if current_price is not None else "Цена не найдена",
        "lot_size": get_lot_size(secid, security),
        "dividend_yield": (
            auto_profile.get("dividend_yield")
            if auto_profile.get("dividend_yield") is not None
            else security.get("dividend_yield", profile.get("dividend_yield"))
        ),
        "volatility_level": (
            auto_profile.get("volatility_level")
            or security.get("volatility_level")
            or profile.get("volatility_level")
            or "не указана"
        ),
        "price_source": price_source,
        "is_fallback_price": False,
        "is_price_missing": current_price is None,
    }


# Сериализация комнаты: вычисляет статус (active/ended/scheduled) и флаг trading_open
def serialize_room_response(room: dict) -> dict:
    room_status = "inactive"
    trading_open = False
    starts_at = room.get("starts_at")
    ends_at = room.get("ends_at")
    current_ts = now()
    if ends_at and ends_at <= current_ts:
        room_status = "ended"
    elif not room.get("is_active"):
        room_status = "ended"
    elif starts_at and starts_at > current_ts:
        room_status = "scheduled"
    else:
        room_status = "active"
        trading_open = True
    return {
        "id": room["id"],
        "title": room["title"],
        "room_code": room["room_code"],
        "starts_at": starts_at.isoformat() if starts_at else None,
        "ends_at": ends_at.isoformat() if ends_at else None,
        "initial_balance": safe_float(room.get("initial_balance")),
        "allowed_secids": parse_secid_list(room.get("allowed_secids")),
        "scenario_name": room.get("scenario_name"),
        "scenario_slug": room.get("scenario_slug"),
        "scenario_start_date": room["scenario_start_date"].isoformat()
        if room.get("scenario_start_date")
        else None,
        "scenario_end_date": room["scenario_end_date"].isoformat()
        if room.get("scenario_end_date")
        else None,
        "mode": room.get("mode"),
        "is_active": bool(room.get("is_active")),
        "room_status": room_status,
        "trading_open": trading_open,
    }


# Проверка, открыты ли торги в комнате: активна, started_at <= now <= ends_at
def is_room_trade_open(room: dict | None) -> bool:
    if not room or not room.get("is_active"):
        return False
    starts_at = room.get("starts_at")
    ends_at = room.get("ends_at")
    current_ts = now()
    if starts_at and starts_at > current_ts:
        return False
    if ends_at and ends_at < current_ts:
        return False
    return True


# Автозакрытие комнаты по таймеру: если ends_at <= now и is_active — вызываем db.close_room_system
def refresh_room_lifecycle(room: dict | None) -> dict | None:
    if not room:
        return None
    ends_at = room.get("ends_at")
    if room.get("is_active") and ends_at and ends_at <= now():
        if db.close_room_system(int(room["id"])):
            logger.info("Room auto-closed by timer: room_id=%s", room["id"])
        fresh_room = db.get_room_by_id(int(room["id"]))
        if fresh_room:
            merged_room = dict(room)
            merged_room.update(fresh_room)
            room = merged_room
    return room


# Добавление runtime-состояния к комнате: статус, trading_open, сериализация
def attach_room_runtime_state(room: dict | None) -> dict | None:
    room = refresh_room_lifecycle(room)
    if not room:
        return None
    payload = serialize_room_response(room)
    enriched = dict(room)
    enriched["room_status"] = payload["room_status"]
    enriched["trading_open"] = payload["trading_open"]
    return enriched


# Парсинг количества лотов из запроса: поддерживает lots напрямую или quantity с проверкой кратности лоту
def parse_lots_from_payload(payload: dict, secid: str) -> int:
    lots = int(float(payload.get("lots", 0) or 0))
    if lots > 0:
        return lots

    quantity = int(float(payload.get("quantity", 0) or 0))
    if quantity <= 0:
        return 0

    lot_size = get_lot_size(secid)
    if quantity % lot_size != 0:
        raise ValueError(f"Количество должно быть кратно лоту ({lot_size})")
    return quantity // lot_size


# Расчёт окна истории для графика: расширяет период сценария ±120 дней, или ±180 дней вокруг shock_date
def build_history_window(
    scenario_start: date | None,
    scenario_end: date | None,
    shock_date: date | None = None,
) -> tuple[date | None, date | None]:
    if not scenario_start and not scenario_end:
        return None, None

    if shock_date:
        history_start = shock_date - timedelta(days=180)
        history_end = shock_date + timedelta(days=180)
        if scenario_start:
            history_start = min(history_start, scenario_start - timedelta(days=45))
        if scenario_end:
            history_end = max(history_end, scenario_end + timedelta(days=45))
        return history_start, history_end

    history_start = scenario_start - timedelta(days=120) if scenario_start else None
    history_end = scenario_end + timedelta(days=120) if scenario_end else None
    return history_start, history_end


# Нормализация коэффициентов стресса: заполняет отсутствующие тикеры значением 1.0, ограничивает диапазон [0.05, 3.0]
def normalize_stress_coefficients(raw_coefficients) -> dict[str, float]:
    normalized = {ticker: 1.0 for ticker in get_available_tickers()}
    if not isinstance(raw_coefficients, dict):
        return normalized
    for ticker, value in raw_coefficients.items():
        secid = str(ticker or "").strip().upper()
        if secid in TICKER_MAPPING:
            secid = TICKER_MAPPING[secid]
        if secid not in normalized:
            continue
        try:
            coefficient = float(value)
        except (TypeError, ValueError):
            continue
        if coefficient <= 0:
            continue
        normalized[secid] = round(coefficient, 4)
    return normalized


# Рыночные данные и расчёты 
# Расчёт коэффициентов стресса по периоду: отношение последней цены к первой для каждого тикера
def build_period_stress_coefficients(start_date: date, end_date: date) -> dict[str, float]:
    coefficients: dict[str, float] = {}
    for ticker in get_available_tickers():
        history = fetch_security_history_unified(
            ticker,
            days=max((end_date - start_date).days + 10, 30),
            board="TQBR",
            start_date=start_date,
            end_date=end_date,
        )
        closes = [float(point["close"]) for point in history if point.get("close") not in (None, "")]
        if len(closes) < 2 or closes[0] <= 0:
            coefficients[ticker] = 1.0
            continue
        ratio = closes[-1] / closes[0]
        coefficients[ticker] = round(min(max(ratio, 0.05), 3.0), 4)
    return normalize_stress_coefficients(coefficients)


def get_prices(*, allow_network: bool = True) -> dict[str, float]:
    tickers = get_available_tickers()
    cache_key = f"p_TQBR_{'_'.join(sorted(tickers))}"
    cached_prices = moex_cache.get(cache_key) or {}

    if not allow_network:
        quick_prices: dict[str, float] = {}
        for ticker in tickers:
            cached_value = cached_prices.get(ticker)
            if cached_value not in (None, "", 0):
                quick_prices[ticker] = float(cached_value)
            elif ticker in FALLBACK:
                quick_prices[ticker] = float(FALLBACK[ticker])
        return quick_prices

    try:
        return fetch_moex_prices(tickers, moex_cache)
    except Exception as exc:
        logger.warning("Live MOEX prices failed, fallback to cached/default: %s", exc)
        fallback_prices: dict[str, float] = {}
        for ticker in tickers:
            cached_value = cached_prices.get(ticker)
            if cached_value not in (None, "", 0):
                fallback_prices[ticker] = float(cached_value)
            elif ticker in FALLBACK:
                fallback_prices[ticker] = float(FALLBACK[ticker])
        return fallback_prices


def refresh_prices() -> dict[str, float]:
    moex_cache.clear()
    clear_security_profile_cache()
    clear_room_price_cache()
    return fetch_moex_prices(get_available_tickers(), moex_cache)


# Проверка флага ?refresh=1 в запросе для принудительного обновления кэша
def wants_fresh_prices() -> bool:
    return str(request.args.get("refresh", "")).strip().lower() in {"1", "true", "yes", "on"}


def get_security_price_sources() -> dict[str, str]:
    return get_price_sources(get_available_tickers())


# Обогащение списка бумаг метаданными рынка: цена, источник, профиль, статус missing
def enrich_securities_with_market_meta(
    securities: list[dict],
    prices: dict[str, float],
    force_refresh: bool = False,
) -> list[dict]:
    sources = get_security_price_sources()
    enriched = []
    for security in securities:
        enriched_security = dict(security)
        enriched_security["price_source"] = sources.get(str(security.get("secid", "")).upper(), "MOEX")
        enriched.append(serialize_market_security(enriched_security, prices, force_refresh=force_refresh))
    return enriched


# Получение контекста для торговли: бумага, актуальная цена, ошибка
def get_trade_context(secid: str) -> tuple[dict | None, float | None, str | None]:
    secid = str(secid or "").strip().upper()
    security = db.get_security_by_secid(secid)
    if not security:
        return None, None, "Бумага не найдена"
    prices = get_prices()
    price = prices.get(secid)
    if price in (None, 0):
        return security, None, "Не удалось получить актуальную цену с биржи"
    return security, float(price), None


# Пересчёт баланса из транзакций: начальный баланс ± BUY/SELL/DEPOSIT/WITHDRAW
def reconcile_cash_from_transactions(initial_balance: float, transactions: list[dict]) -> float:
    cash = float(initial_balance)
    for tx in transactions or []:
        tx_type = str(tx.get("tx_type", "")).upper()
        quantity = float(tx.get("quantity") or 0)
        price = float(tx.get("price") or 0)
        amount = quantity * price
        if tx_type == "BUY":
            cash -= amount
        elif tx_type == "SELL":
            cash += amount
        elif tx_type == "DEPOSIT":
            cash += amount
        elif tx_type == "WITHDRAW":
            cash -= amount
    return safe_float(cash)


# Расчёт дневных доходностей из ряда цен: (curr - prev) / prev
def compute_return_series(closes: list[float]) -> list[float]:
    returns = []
    for prev, curr in zip(closes, closes[1:]):
        if prev > 0:
            returns.append((curr - prev) / prev)
    return returns


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def build_security_news_query(secid: str, shortname: str | None = None) -> str:
    secid = str(secid or "").strip().upper()
    if secid in SECURITY_NEWS_QUERY_HINTS:
        return SECURITY_NEWS_QUERY_HINTS[secid]
    company = str(shortname or "").strip()
    if company:
        return f"\"{company}\" OR {secid}"
    return secid


def fetch_market_news(query: str, *, from_date: str = NEWS_DEFAULT_FROM, limit: int = 12) -> dict:
    normalized_query = str(query or "").strip() or NEWS_DEFAULT_QUERY
    page_size = int(clamp(int(limit or 12), 1, 25))
    cache_key = (normalized_query, from_date, page_size)
    cached = _news_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < NEWS_CACHE_TTL_SECONDS:
        return dict(cached[1])
    try:
        response = requests.get(
            NEWS_API_URL,
            params={
                "q": normalized_query,
                "from": from_date,
                "sortBy": "publishedAt",
                "pageSize": page_size,
                "language": "en",
                "apiKey": NEWS_API_KEY,
            },
            timeout=NEWS_TIMEOUT_SECONDS,
            proxies={"http": None, "https": None},
            headers={"User-Agent": "MOEX-Trainer/2.0", "Accept": "application/json"},
        )
        payload = response.json()
    except Exception as exc:
        logger.warning("Failed to fetch market news for query=%s: %s", normalized_query, exc)
        payload = {
            "query": normalized_query,
            "articles": [],
            "total_results": 0,
            "message": "Сервис новостей временно недоступен.",
        }
        _news_cache[cache_key] = (time.time(), dict(payload))
        return payload

    if response.status_code >= 400 or payload.get("status") != "ok":
        logger.warning("News API error for query=%s: status=%s payload_status=%s", normalized_query, response.status_code, payload.get("status"))
        result = {
            "query": normalized_query,
            "articles": [],
            "total_results": 0,
            "message": payload.get("message") or "Не удалось получить новости.",
        }
        _news_cache[cache_key] = (time.time(), dict(result))
        return result

    query_tokens = [token.lower() for token in re.split(r"[^a-zA-Z0-9а-яА-Я]+", normalized_query) if token.strip()]
    articles = []
    for item in payload.get("articles") or []:
        source = item.get("source") or {}
        title = str(item.get("title") or "").strip() or "Без заголовка"
        description = str(item.get("description") or "").strip()
        content_blob = f"{title} {description}".lower()
        relevance_score = 0
        for token in query_tokens:
            if len(token) < 3:
                continue
            if token in content_blob:
                relevance_score += 1
        quality_score = 0
        if item.get("urlToImage"):
            quality_score += 1
        if description:
            quality_score += 1
        published_at = item.get("publishedAt")
        if published_at:
            quality_score += 1
        articles.append({
            "title": title,
            "description": description,
            "url": str(item.get("url") or "").strip(),
            "source": str(source.get("name") or "").strip() or "Источник не указан",
            "published_at": published_at,
            "image_url": item.get("urlToImage"),
            "relevance_score": relevance_score,
            "quality_score": quality_score,
        })

    articles.sort(
        key=lambda article: (
            int(article.get("relevance_score") or 0),
            int(article.get("quality_score") or 0),
            str(article.get("published_at") or ""),
        ),
        reverse=True,
    )
    articles = articles[:page_size]

    result = {
        "query": normalized_query,
        "articles": articles,
        "total_results": int(payload.get("totalResults") or 0),
        "message": "",
    }
    _news_cache[cache_key] = (time.time(), dict(result))
    return result


def get_cached_global_money_leaderboard(limit: int = 30) -> dict:
    safe_limit = max(1, min(int(limit or 30), 200))
    cached = _global_money_leaderboard_cache.get(safe_limit)
    if cached and (time.time() - cached[0]) < GLOBAL_LEADERBOARD_CACHE_TTL_SECONDS:
        return dict(cached[1])
    users = db.get_active_users_for_global_leaderboard()
    try:
        prices = get_prices(allow_network=False)
    except Exception as exc:
        logger.warning("Global leaderboard prices fallback due to error: %s", exc)
        prices = {}
    rows: list[dict] = []
    for user in users:
        user_id = int(user.get("user_id") or 0)
        if user_id <= 0:
            continue
        try:
            portfolio = compute_portfolio(user_id, prices=prices, include_metrics=False, include_forecast=False)
        except Exception as exc:
            logger.warning("Skip global leaderboard user_id=%s due to portfolio error: %s", user_id, exc)
            continue
        rows.append({
            "user_id": user_id,
            "username": str(user.get("username") or f"user_{user_id}"),
            "transactions_count": int(user.get("transactions_count") or 0),
            "last_activity_at": user.get("last_activity_at"),
            "total_value": safe_float(portfolio.get("total_value", 0.0)),
            "account_total_profit": safe_float(portfolio.get("account_total_profit", 0.0)),
            "account_total_profit_pct": safe_float(portfolio.get("account_total_profit_pct", 0.0)),
        })
    rows.sort(
        key=lambda row: (
            float(row.get("account_total_profit") or 0.0),
            float(row.get("account_total_profit_pct") or 0.0),
            float(row.get("total_value") or 0.0),
            int(row.get("transactions_count") or 0),
            str(row.get("username") or "").lower(),
        ),
        reverse=True,
    )
    rows = rows[:safe_limit]
    leaderboard = []
    for index, row in enumerate(rows, start=1):
        leaderboard.append({
            "global_rank": index,
            "user_id": int(row.get("user_id") or 0),
            "username": row.get("username"),
            "transactions_count": int(row.get("transactions_count") or 0),
            "total_value": safe_float(row.get("total_value") or 0.0),
            "account_total_profit": safe_float(row.get("account_total_profit") or 0.0),
            "account_total_profit_pct": safe_float(row.get("account_total_profit_pct") or 0.0),
            "last_activity_at": row.get("last_activity_at").isoformat() if row.get("last_activity_at") else None,
        })
    payload = {"leaderboard": leaderboard, "updated_at": now().isoformat()}
    _global_money_leaderboard_cache[safe_limit] = (time.time(), dict(payload))
    return payload


def summarize_news_sentiment(news_payload: dict | None) -> dict:
    articles = (news_payload or {}).get("articles") or []
    positive = 0
    negative = 0
    neutral = 0
    top_headlines: list[str] = []
    for article in articles[:8]:
        title = str(article.get("title") or "")
        description = str(article.get("description") or "")
        blob = f"{title} {description}".lower()
        pos_hits = sum(1 for term in POSITIVE_NEWS_TERMS if term in blob)
        neg_hits = sum(1 for term in NEGATIVE_NEWS_TERMS if term in blob)
        if pos_hits > neg_hits:
            positive += 1
        elif neg_hits > pos_hits:
            negative += 1
        else:
            neutral += 1
        if title:
            top_headlines.append(title)
    score = positive - negative
    sentiment = "neutral"
    if score >= 2:
        sentiment = "positive"
    elif score <= -2:
        sentiment = "negative"
    return {
        "sentiment": sentiment,
        "score": int(score),
        "positive_count": int(positive),
        "negative_count": int(negative),
        "neutral_count": int(neutral),
        "headlines": top_headlines[:4],
    }


def build_security_explanation(
    secid: str,
    history: list[dict],
    diagnostics: dict,
    news_payload: dict | None = None,
    *,
    stress_context: str | None = None,
) -> dict:
    closes = [float(point["close"]) for point in history if point.get("close") not in (None, "")]
    if len(closes) < 3:
        return {
            "summary": "Недостаточно исторических данных для детального объяснения движения цены.",
            "drivers": [],
            "news_sentiment": summarize_news_sentiment(news_payload),
        }

    last_price = closes[-1]
    prev_price = closes[-2]
    change_1d_pct = ((last_price - prev_price) / prev_price * 100) if prev_price > 0 else 0.0

    month_back_idx = max(0, len(closes) - 22)
    month_price = closes[month_back_idx]
    month_change_pct = ((last_price - month_price) / month_price * 100) if month_price > 0 else 0.0

    trend_strength = float(diagnostics.get("trend_strength_pct") or 0.0)
    momentum = float(diagnostics.get("momentum_3m_pct") or 0.0)
    volatility = float(diagnostics.get("annual_volatility_pct") or 0.0)
    mean_reversion = float(diagnostics.get("mean_reversion_pressure_pct") or 0.0)

    news_sentiment = summarize_news_sentiment(news_payload)
    drivers: list[str] = []
    if abs(month_change_pct) >= 1:
        drivers.append(
            f"Динамика за ~1 месяц: {month_change_pct:+.2f}% (последняя сессия: {change_1d_pct:+.2f}%)."
        )
    if abs(momentum) >= 1:
        drivers.append(f"3-месячный моментум: {momentum:+.2f}% — {'поддерживает рост' if momentum > 0 else 'давит на котировку'}.")
    if abs(mean_reversion) >= 0.5:
        drivers.append(f"Эффект возврата к среднему: {mean_reversion:+.2f}% (цена {'ниже' if mean_reversion > 0 else 'выше'} средней).")
    drivers.append(f"Годовая волатильность: {volatility:.2f}% — {'высокий риск резких колебаний' if volatility >= 35 else 'умеренный риск колебаний'}.")
    if news_sentiment["sentiment"] == "positive":
        drivers.append("Новостной фон преимущественно позитивный и поддерживает спрос.")
    elif news_sentiment["sentiment"] == "negative":
        drivers.append("Новостной фон преимущественно негативный и усиливает давление на цену.")
    else:
        drivers.append("Новостной фон смешанный: драйверы роста и падения уравновешивают друг друга.")
    if stress_context:
        drivers.append(f"Контекст стресса: {stress_context}")

    summary = (
        f"{secid}: текущий импульс {change_1d_pct:+.2f}% за день и {month_change_pct:+.2f}% за месяц. "
        f"Тренд={trend_strength:+.2f}%, моментум={momentum:+.2f}%, волатильность={volatility:.2f}%."
    )
    return {
        "summary": summary,
        "drivers": drivers[:6],
        "news_sentiment": news_sentiment,
    }


def build_advanced_security_forecast(
    secid: str,
    history: list[dict],
    *,
    dividend_yield: float | None = None,
) -> tuple[dict[str, float], dict]:
    closes = [float(point["close"]) for point in history if point.get("close") not in (None, "")]
    current_price = closes[-1] if closes else 0.0
    if len(closes) < 25 or current_price <= 0:
        fallback = build_price_forecast(history, FORECAST_HORIZONS, risk_bias=0.15)
        diagnostics = {
            "model": "fallback",
            "confidence": 0.25,
            "expected_annual_return_pct": 0.0,
            "annual_volatility_pct": 0.0,
            "trend_strength_pct": 0.0,
            "momentum_3m_pct": 0.0,
            "mean_reversion_pressure_pct": 0.0,
            "dividend_support_pct": safe_float(dividend_yield or 0.0),
        }
        return fallback, diagnostics

    returns = []
    for prev, curr in zip(closes, closes[1:]):
        if prev > 0 and curr > 0:
            returns.append(math.log(curr / prev))
    if not returns:
        fallback = {label: safe_float(current_price) for label in FORECAST_HORIZONS}
        diagnostics = {
            "model": "flat",
            "confidence": 0.2,
            "expected_annual_return_pct": 0.0,
            "annual_volatility_pct": 0.0,
            "trend_strength_pct": 0.0,
            "momentum_3m_pct": 0.0,
            "mean_reversion_pressure_pct": 0.0,
            "dividend_support_pct": safe_float(dividend_yield or 0.0),
        }
        return fallback, diagnostics

    long_window = min(len(returns), 180)
    short_window = min(len(returns), 21)
    mid_window = min(len(returns), 63)
    vol_window = min(len(returns), 90)

    long_avg = sum(returns[-long_window:]) / long_window
    short_avg = sum(returns[-short_window:]) / short_window
    mid_avg = sum(returns[-mid_window:]) / mid_window
    vol_daily = (sum((ret - long_avg) ** 2 for ret in returns[-vol_window:]) / max(vol_window, 1)) ** 0.5
    annual_vol = vol_daily * (252 ** 0.5)

    long_annualized = math.exp(long_avg * 252) - 1
    short_annualized = math.exp(short_avg * 252) - 1
    momentum_3m = math.exp(mid_avg * 63) - 1

    ma_window = min(len(closes), 60)
    ma60 = sum(closes[-ma_window:]) / ma_window
    mean_reversion_pressure = ((ma60 - current_price) / ma60) if ma60 > 0 else 0.0
    mean_reversion_pressure = clamp(mean_reversion_pressure, -0.35, 0.35)

    div_support = max(float(dividend_yield or 0.0), 0.0) / 100
    volatility_penalty = annual_vol * 0.55
    regime_penalty = 0.06 if str(SECURITY_PROFILE.get(secid, {}).get("volatility_level", "")).lower() == "высокая" else 0.0

    base_market_growth = max(float(Config.MARKET_GROWTH_ANNUAL), 0.0)
    inflation_rate = max(float(Config.INFLATION_RATE_ANNUAL), 0.0)
    real_market_growth = max(base_market_growth - inflation_rate, -0.02)

    expected_annual_return = (
        real_market_growth * 0.28
        + long_annualized * 0.36
        + short_annualized * 0.22
        + momentum_3m * 0.16
        + mean_reversion_pressure * 0.18
        + div_support * 0.25
        - volatility_penalty
        - regime_penalty
        + Config.RISK_FREE_RATE_ANNUAL * 0.08
    )
    expected_annual_return = clamp(expected_annual_return, -0.45, 0.55)

    confidence = 0.82 - annual_vol * 0.9 - (0.18 if len(closes) < 90 else 0.0)
    confidence = clamp(confidence, 0.18, 0.92)

    forecast: dict[str, float] = {}
    for label, trading_days in FORECAST_HORIZONS.items():
        years = trading_days / 252
        uncertainty = annual_vol * (years ** 0.5)
        mean_reversion_weight = clamp(0.18 + years * 0.25, 0.18, 0.72)
        neutral_annual = clamp(real_market_growth * 0.65 + Config.RISK_FREE_RATE_ANNUAL * 0.2 + div_support * 0.6, -0.06, 0.16)

        scenario_annual = (expected_annual_return * (1 - mean_reversion_weight)) + (neutral_annual * mean_reversion_weight)
        conservative_log_return = (scenario_annual - 0.45 * uncertainty) * years
        projected = current_price * math.exp(conservative_log_return)

        lower_band = current_price * math.exp(-(0.65 * uncertainty + 0.06 * years))
        upper_band = current_price * math.exp(0.75 * uncertainty + 0.12 * years)
        projected = clamp(projected, lower_band, upper_band)
        forecast[label] = safe_float(max(projected, 0.01))

    diagnostics = {
        "model": "multi_factor_v2",
        "confidence": safe_float(confidence),
        "expected_annual_return_pct": safe_float(expected_annual_return * 100),
        "annual_volatility_pct": safe_float(annual_vol * 100),
        "trend_strength_pct": safe_float(long_annualized * 100),
        "momentum_3m_pct": safe_float(momentum_3m * 100),
        "mean_reversion_pressure_pct": safe_float(mean_reversion_pressure * 100),
        "dividend_support_pct": safe_float(div_support * 100),
        "market_growth_assumption_pct": safe_float(base_market_growth * 100),
        "inflation_assumption_pct": safe_float(inflation_rate * 100),
        "real_market_growth_pct": safe_float(real_market_growth * 100),
    }
    return forecast, diagnostics


def compute_trade_profit_stats(transactions: list[dict], positions: list[dict]) -> dict:
    buy_volume = 0.0
    sell_volume = 0.0
    realized_profit = 0.0
    buy_count = 0
    sell_count = 0
    profitable_sell_count = 0
    inventory: dict[str, dict[str, float]] = {}

    for tx in transactions or []:
        tx_type = str(tx.get("tx_type", "")).upper()
        secid = normalize_secid_alias(tx.get("secid"))
        quantity = float(tx.get("quantity") or 0.0)
        price = float(tx.get("price") or 0.0)
        if not secid or quantity <= 0 or price <= 0:
            continue

        slot = inventory.setdefault(secid, {"qty": 0.0, "cost": 0.0})
        if tx_type == "BUY":
            amount = quantity * price
            buy_volume += amount
            buy_count += 1
            slot["qty"] += quantity
            slot["cost"] += amount
            continue

        if tx_type == "SELL":
            proceeds = quantity * price
            sell_volume += proceeds
            sell_count += 1
            available_qty = max(float(slot["qty"]), 0.0)
            avg_cost = (float(slot["cost"]) / available_qty) if available_qty > 0 else price
            matched_qty = min(quantity, available_qty) if available_qty > 0 else quantity
            cost_basis = matched_qty * avg_cost
            tx_profit = proceeds - cost_basis
            realized_profit += tx_profit
            if tx_profit > 0:
                profitable_sell_count += 1

            slot["qty"] = max(available_qty - matched_qty, 0.0)
            slot["cost"] = max(float(slot["cost"]) - cost_basis, 0.0)

    unrealized_profit = sum(float(position.get("profit_loss") or 0.0) for position in positions or [])
    total_profit = realized_profit + unrealized_profit
    sell_win_rate = (profitable_sell_count / sell_count * 100) if sell_count > 0 else 0.0

    return {
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_volume": safe_float(buy_volume),
        "sell_volume": safe_float(sell_volume),
        "realized_profit": safe_float(realized_profit),
        "unrealized_profit": safe_float(unrealized_profit),
        "total_profit": safe_float(total_profit),
        "sell_win_rate_pct": safe_float(sell_win_rate),
    }


# Расчёт метрик портфеля по позициям: средняя доходность, волатильность, коэффициент Шарпа (годовой)
# Использует взвешенную по рыночной стоимости комбинацию исторических доходностей бумаг
def compute_positions_return_metrics(positions: list[dict]) -> dict:
    """Расчёт метрик портфеля по текущим позициям с коэффициентом Шарпа."""
    weighted_positions = [p for p in positions if float(p.get("market_value") or 0) > 0]
    total_positions_value = sum(float(p["market_value"]) for p in weighted_positions)
    
    if not weighted_positions or total_positions_value <= 0:
        return {"avg_return": 0.0, "volatility": 0.0, "sharpe_ratio": 0.0,
                "message": "Недостаточно данных по позициям для расчёта Sharpe"}

    history_map = {}
    min_series_len = None
    
    for position in weighted_positions:
        secid = position["secid"]
        history = fetch_security_history_unified(secid, days=120)
        closes = [Decimal(str(point["close"])) for point in history if point.get("close")]
        if len(closes) < 2:
            continue
        returns = []
        for prev, curr in zip(closes, closes[1:]):
            if prev > 0:
                returns.append(float((curr - prev) / prev))
        if not returns:
            continue
        history_map[secid] = returns
        min_series_len = len(returns) if min_series_len is None else min(min_series_len, len(returns))

    if not history_map or not min_series_len or min_series_len < 10:
        return {"avg_return": 0.0, "volatility": 0.0, "sharpe_ratio": 0.0,
                "message": "Недостаточно исторических данных для расчёта Sharpe (минимум 10 дней)"}

    # Веса позиций = рыночная стоимость / общая стоимость
    weights = {position["secid"]: float(position["market_value"]) / total_positions_value
               for position in weighted_positions if position["secid"] in history_map}
    if not weights:
        return {"avg_return": 0.0, "volatility": 0.0, "sharpe_ratio": 0.0,
                "message": "Не удалось построить временной ряд портфеля"}

    # Построение взвешенного ряда доходностей портфеля по общим датам
    portfolio_returns = []
    for idx in range(-min_series_len, 0):
        portfolio_return = 0.0
        for secid, weight in weights.items():
            if idx < len(history_map[secid]):
                portfolio_return += history_map[secid][idx] * weight
        portfolio_returns.append(portfolio_return)

    if not portfolio_returns:
        return {"avg_return": 0.0, "volatility": 0.0, "sharpe_ratio": 0.0,
                "message": "Не удалось рассчитать доходность"}

    avg_return = sum(portfolio_returns) / len(portfolio_returns)
    variance = sum((ret - avg_return) ** 2 for ret in portfolio_returns) / len(portfolio_returns)
    volatility = variance ** 0.5
    risk_free_daily = Config.RISK_FREE_RATE_ANNUAL / 252
    sharpe_ratio = (avg_return - risk_free_daily) / volatility if volatility > 0 else 0.0
    sharpe_annual = sharpe_ratio * (252 ** 0.5)

    return {
        "avg_return": round(avg_return * 100, 2),
        "volatility": round(volatility * 100, 2),
        "sharpe_ratio": round(sharpe_annual, 2),
        "message": "Sharpe рассчитан по формуле (Rp - Rf) / σp с годовой экстраполяцией",
    }


def compute_portfolio_return_metrics(user_id: int, positions: list[dict]) -> dict:
    return compute_positions_return_metrics(positions)


# Прогноз портфеля: суммирует прогнозы по каждой позиции (количество × прогнозная цена) + кэш
def compute_portfolio_forecast(user_id: int, positions: list[dict], cash: float) -> dict[str, float]:
    projections = {label: float(cash) for label in FORECAST_HORIZONS}
    for position in positions:
        history = fetch_security_history_unified(position["secid"], days=180)
        forecast = build_price_forecast(history, FORECAST_HORIZONS, risk_bias=0.15)
        quantity = float(position["quantity"])
        for label, projected_price in forecast.items():
            projections[label] += quantity * projected_price
    return {label: safe_float(value) for label, value in projections.items()}


# Построение истории портфеля: пошаговая симуляция баланса и позиций по датам транзакций и историческим ценам
# Возвращает список точек: дата, стоимость, прибыль/убыток, доходность %, Sharpe
def compute_portfolio_history(
    positions: list[dict],
    initial_balance: float,
    transactions: list[dict],
    current_cash: float,
    current_sharpe_ratio: float = 0.0,
) -> list[dict]:
    buy_transactions = [
        tx
        for tx in transactions
        if str(tx.get("tx_type", "")).upper() == "BUY" and tx.get("executed_at")
    ]
    if not buy_transactions:
        return []

    start_date = min(tx["executed_at"].date() for tx in buy_transactions)
    end_date = date.today()
    secids = sorted({normalize_secid_alias(tx.get("secid")) for tx in transactions if tx.get("secid")})
    if not secids:
        return []

    point_maps: dict[str, dict[str, float]] = {}
    timeline: set[str] = set()
    latest_prices = {normalize_secid_alias(position["secid"]): float(position["current_price"]) for position in positions}

    for secid in secids:
        history = fetch_security_history_unified(
            secid,
            days=max((end_date - start_date).days + 10, 30),
            start_date=start_date,
            end_date=end_date,
        )
        point_map = {
            point["date"]: float(point["close"])
            for point in history
            if point.get("date") and point.get("close") not in (None, "")
        }
        if not point_map:
            continue
        point_maps[secid] = point_map
        timeline.update(point_map.keys())

    if not timeline:
        return []

    tx_by_date: dict[str, list[dict]] = {}
    for tx in transactions:
        executed_at = tx.get("executed_at")
        if not executed_at:
            continue
        tx_by_date.setdefault(executed_at.date().isoformat(), []).append(tx)

    ordered_dates = sorted(
        iso_date
        for iso_date in timeline
        if start_date.isoformat() <= iso_date <= end_date.isoformat()
    )
    today_iso = date.today().isoformat()
    if today_iso not in ordered_dates:
        ordered_dates.append(today_iso)
    ordered_dates = sorted(set(ordered_dates))
    if not ordered_dates:
        return []

    # Симуляция: начальные значения
    holdings: dict[str, float] = {secid: 0.0 for secid in secids}
    cost_basis_map: dict[str, float] = {secid: 0.0 for secid in secids}
    previous_prices = {secid: latest_prices.get(secid, FALLBACK.get(secid, 0.0)) for secid in secids}
    cash = float(initial_balance)
    series = []

    for iso_date in ordered_dates:
        # Применяем транзакции за текущую дату
        for tx in tx_by_date.get(iso_date, []):
            secid = normalize_secid_alias(tx.get("secid"))
            quantity = float(tx.get("quantity") or 0)
            price = float(tx.get("price") or 0)
            if secid not in holdings or quantity <= 0 or price <= 0:
                continue
            if str(tx.get("tx_type", "")).upper() == "BUY":
                holdings[secid] += quantity
                cost_basis_map[secid] += quantity * price
                cash -= quantity * price
            elif str(tx.get("tx_type", "")).upper() == "SELL":
                current_qty = holdings.get(secid, 0.0)
                avg_cost = (cost_basis_map.get(secid, 0.0) / current_qty) if current_qty > 0 else 0.0
                holdings[secid] = max(0.0, holdings[secid] - quantity)
                cost_basis_map[secid] = max(0.0, cost_basis_map.get(secid, 0.0) - avg_cost * quantity)
                cash += quantity * price

        # Расчёт стоимости портфеля на дату
        total_value = cash
        positions_market_value = 0.0
        for secid in secids:
            point_map = point_maps.get(secid, {})
            if iso_date in point_map:
                previous_prices[secid] = point_map[iso_date]
            positions_market_value += holdings.get(secid, 0.0) * previous_prices.get(secid, 0.0)
        total_value += positions_market_value
        total_cost_basis = sum(cost_basis_map.values())
        profit_loss = positions_market_value - total_cost_basis
        profit_loss_pct = (profit_loss / total_cost_basis * 100) if total_cost_basis > 0 else 0.0
        series.append({
            "date": iso_date,
            "close": safe_float(total_value),
            "profit_loss": safe_float(profit_loss),
            "profit_loss_pct": safe_float(profit_loss_pct),
            "sharpe_ratio": 0.0,
        })

    # Корректировка последней точки актуальными значениями
    if series:
        current_total = float(current_cash) + sum(float(position["market_value"]) for position in positions)
        current_unrealized_profit = sum(float(position["profit_loss"]) for position in positions)
        current_cost_basis = sum(
            float(position["avg_buy_price"]) * float(position["quantity"])
            for position in positions
        )
        series[-1]["close"] = safe_float(current_total)
        series[-1]["profit_loss"] = safe_float(current_unrealized_profit)
        series[-1]["profit_loss_pct"] = safe_float(
            (current_unrealized_profit / current_cost_basis * 100) if current_cost_basis > 0 else 0.0
        )

    # Расчёт Sharpe для каждой точки истории по накопленным доходностям
    closes = [float(point["close"]) for point in series if point.get("close") not in (None, "")]
    portfolio_returns = compute_return_series(closes)
    for index, point in enumerate(series):
        if index < 1:
            point["sharpe_ratio"] = 0.0
            continue
        partial_returns = portfolio_returns[:index]
        if len(partial_returns) < 2:
            point["sharpe_ratio"] = 0.0
            continue
        avg_return = sum(partial_returns) / len(partial_returns)
        variance = sum((ret - avg_return) ** 2 for ret in partial_returns) / len(partial_returns)
        volatility = variance ** 0.5
        risk_free_daily = Config.RISK_FREE_RATE_ANNUAL / 252
        sharpe_ratio = ((avg_return - risk_free_daily) / volatility * (252 ** 0.5)) if volatility > 0 else 0.0
        point["sharpe_ratio"] = safe_float(sharpe_ratio)

    if series:
        series[-1]["sharpe_ratio"] = safe_float(current_sharpe_ratio)
    return series


# Сборка детальной информации по бумаге: история, свечи, прогноз, позиция пользователя если есть
def compute_security_view(secid: str, user_id: int | None = None) -> dict:
    security = db.get_security_by_secid(secid) or {}
    history = fetch_security_history_unified(secid, days=180)
    candles = fetch_security_candles(secid, moex_cache, days=60)
    current_price = float(history[-1]["close"]) if history else float(FALLBACK.get(secid, 0.0))
    dividend_yield = security.get("dividend_yield", SECURITY_PROFILE.get(secid, {}).get("dividend_yield"))
    forecast, diagnostics = build_advanced_security_forecast(secid, history, dividend_yield=dividend_yield)
    related_news_query = build_security_news_query(secid, security.get("shortname"))
    related_news = fetch_market_news(related_news_query, limit=6)
    analysis = build_security_explanation(secid, history, diagnostics, related_news)
    response = {
        "secid": secid,
        "shortname": security.get("shortname", secid),
        "sector": security.get("sector") or "Другое",
        "history": history,
        "candles": candles,
        "forecast": forecast,
        "forecast_diagnostics": diagnostics,
        "current_price": safe_float(current_price),
        "dividend_yield": safe_float(dividend_yield or 0.0),
        "volatility_level": security.get("volatility_level") or SECURITY_PROFILE.get(secid, {}).get("volatility_level") or "не указана",
        "related_news": related_news,
        "analysis": analysis,
    }
    if user_id:
        portfolio = db.get_portfolio(user_id)
        matching = next((item for item in portfolio["items"] if item["secid"] == secid), None)
        if matching:
            quantity = float(matching["quantity"])
            lot_size = get_lot_size(secid, matching)
            lots = int(quantity / lot_size)
            response["position"] = {
                "lots": lots, "quantity": quantity, "lot_size": lot_size,
                "avg_buy_price": safe_float(matching["avg_buy_price"]),
                "month": safe_float(quantity * forecast["month"]),
                "year": safe_float(quantity * forecast["year"]),
                "ten_years": safe_float(quantity * forecast["ten_years"]),
            }
    return response


# Кэшированный портфель: проверяет TTL, при промахе вычисляет и сохраняет
def get_cached_portfolio_payload(user_id: int, force_refresh: bool = False) -> dict:
    cache_key = (user_id, force_refresh)
    if not force_refresh:
        cached = _portfolio_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < PORTFOLIO_CACHE_TTL:
            return dict(cached[1])

    prices = refresh_prices() if force_refresh else get_prices(allow_network=False)
    data = compute_portfolio(user_id, prices=prices)
    data.pop("prices", None)
    _portfolio_cache[cache_key] = (time.time(), dict(data))
    return data


# Кэшированная история портфеля: аналогично, но с отдельным TTL и зависимостью от compute_portfolio
def get_cached_portfolio_history_payload(user_id: int, force_refresh: bool = False) -> dict:
    cache_key = (user_id, force_refresh)
    if not force_refresh:
        cached = _portfolio_history_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < PORTFOLIO_HISTORY_CACHE_TTL:
            return dict(cached[1])

    portfolio = get_cached_portfolio_payload(user_id, force_refresh=force_refresh)
    history = compute_portfolio_history(
        portfolio["positions"],
        portfolio["initial_balance"],
        db.get_portfolio_transactions(user_id),
        portfolio["cash"],
        portfolio["sharpe_ratio"],
    )
    payload = {
        "history": history,
        "assets_count": portfolio["assets_count"],
        "assets_rule_ok": portfolio["assets_rule_ok"],
        "message": portfolio["diversification_message"],
        "history_started_at": history[0]["date"] if history else None,
    }
    _portfolio_history_cache[cache_key] = (time.time(), dict(payload))
    return payload


def warmup_prices() -> None:
    try:
        get_prices()
        logger.info("MOEX prices cache warmed up")
    except Exception as exc:
        logger.warning("MOEX prices warmup failed: %s", exc)


# Фоновый цикл обновления цен: бесконечный цикл с интервалом Config.MOEX_REFRESH_SECONDS
def moex_refresh_loop() -> None:
    warmup_prices()
    while True:
        try:
            fetch_moex_prices(get_available_tickers(), moex_cache)
            logger.info("MOEX prices refreshed in background, next refresh in %s sec", Config.MOEX_REFRESH_SECONDS)
        except Exception as exc:
            logger.warning("Background MOEX refresh failed: %s", exc)
        refresh_window = max(Config.MOEX_REFRESH_SECONDS, 15)
        now_ts = time.time()
        next_slot = ((int(now_ts) // refresh_window) + 1) * refresh_window
        time.sleep(max(1, next_slot - now_ts))


# Запуск фоновых задач в отдельном потоке (daemon)
def start_background_jobs() -> None:
    global _moex_refresh_started
    if _moex_refresh_started:
        return
    _moex_refresh_started = True
    worker = threading.Thread(target=moex_refresh_loop, daemon=True, name="moex-refresh")
    worker.start()


# Автодобавление сценариев отключено: глобальные сценарии должен создавать админ,
# а учительские сценарии живут только в его комнатах.
def ensure_default_scenarios() -> None:
    return None


# Хук before_request: гарантирует запуск фоновых задач и инициализацию сценариев
@app.before_request
def ensure_background_jobs() -> None:
    if request.endpoint == "static":
        return None
    ensure_default_scenarios()
    start_background_jobs()


# Основная функция расчёта портфеля: баланс, позиции, метрики, прогноз, проверка диверсификации
# Возвращает полный словарь для API /api/portfolio
def compute_portfolio(
    user_id: int,
    prices: dict[str, float] | None = None,
    *,
    include_metrics: bool = True,
    include_forecast: bool = True,
) -> dict:
    prices = prices or get_prices()
    portfolio = db.get_portfolio(user_id)
    initial_balance = safe_float(portfolio["initial_balance"])
    transactions = db.get_portfolio_transactions(user_id)
    cash = reconcile_cash_from_transactions(initial_balance, transactions)

    positions = []
    positions_value = 0.0
    total_cost_basis = 0.0

    for item in portfolio["items"]:
        secid = normalize_secid_alias(item["secid"])
        quantity = float(item["quantity"])
        avg_buy_price = float(item["avg_buy_price"])
        lot_size = get_lot_size(secid, item)
        lots = int(quantity / lot_size)
        raw_current_price = prices.get(secid)
        current_price = float(raw_current_price) if raw_current_price not in (None, "", 0) else avg_buy_price
        market_value = current_price * quantity
        invested_value = avg_buy_price * quantity
        profit_loss = market_value - invested_value
        profit_loss_pct = (profit_loss / invested_value * 100) if invested_value > 0 else 0.0
        positions_value += market_value
        total_cost_basis += invested_value

        positions.append({
            "secid": secid, "shortname": item.get("shortname", secid),
            "sector": item.get("sector") or "Другое", "lots": lots,
            "quantity": int(quantity) if quantity.is_integer() else quantity,
            "lot_size": lot_size, "avg_buy_price": safe_float(avg_buy_price),
            "current_price": safe_float(current_price),
            "price_label": (
                f"{safe_float(raw_current_price):.2f} ₽"
                if raw_current_price not in (None, "", 0)
                else "Цена не найдена"
            ),
            "is_price_missing": raw_current_price in (None, "", 0),
            "market_value": safe_float(market_value),
            "profit_loss": safe_float(profit_loss),
            "profit_loss_pct": safe_float(profit_loss_pct),
        })

    total_value = cash + positions_value
    total_profit = sum(float(position["profit_loss"]) for position in positions)
    total_profit_pct = (total_profit / total_cost_basis * 100) if total_cost_basis > 0 else 0.0
    account_total_profit = total_value - initial_balance
    account_total_profit_pct = (account_total_profit / initial_balance * 100) if initial_balance > 0 else 0.0
    trade_stats = compute_trade_profit_stats(transactions, positions)
    metrics = compute_portfolio_return_metrics(user_id, positions) if include_metrics else {
        "avg_return": 0.0,
        "volatility": 0.0,
        "sharpe_ratio": 0.0,
        "message": "",
    }
    forecast = compute_portfolio_forecast(user_id, positions, cash) if include_forecast else {
        label: safe_float(cash + positions_value) for label in FORECAST_HORIZONS
    }
    assets_count = len(positions)
    diversification_message = ""
    if assets_count < MIN_PORTFOLIO_ASSETS:
        diversification_message = f"Для полного соответствия заданию держите минимум {MIN_PORTFOLIO_ASSETS} разных компаний."
    elif assets_count > MAX_PORTFOLIO_ASSETS:
        diversification_message = f"В учебном режиме допускается не больше {MAX_PORTFOLIO_ASSETS} разных компаний."
    else:
        diversification_message = f"Портфель соответствует правилу {MIN_PORTFOLIO_ASSETS}–{MAX_PORTFOLIO_ASSETS} разных компаний."

    return {
        "cash": safe_float(cash), "positions_value": safe_float(positions_value),
        "total_value": safe_float(total_value), "initial_balance": safe_float(initial_balance),
        "total_profit": safe_float(total_profit), "total_profit_pct": safe_float(total_profit_pct),
        "account_total_profit": safe_float(account_total_profit),
        "account_total_profit_pct": safe_float(account_total_profit_pct),
        "positions": positions, "transactions_count": db.get_transaction_count(user_id),
        "avg_return": metrics["avg_return"], "volatility": metrics["volatility"],
        "sharpe_ratio": metrics["sharpe_ratio"], "stats_message": metrics.get("message", ""),
        "trade_stats": trade_stats,
        "forecast": forecast, "prices": prices,
        "assets_count": assets_count,
        "assets_rule_min": MIN_PORTFOLIO_ASSETS,
        "assets_rule_max": MAX_PORTFOLIO_ASSETS,
        "assets_rule_ok": MIN_PORTFOLIO_ASSETS <= assets_count <= MAX_PORTFOLIO_ASSETS,
        "diversification_message": diversification_message,
    }


# Расчёт портфеля комнаты: аналогично compute_portfolio, но с учётом цен комнаты и ограничений
def compute_room_portfolio(room_id: int, user_id: int, prices: dict[str, float] | None = None) -> dict | None:
    prices = prices or get_prices()
    portfolio = db.get_room_portfolio(room_id, user_id)
    if not portfolio:
        return None
    room = db.get_room_by_id(room_id)
    cash = safe_float(portfolio["current_cash"])
    initial_balance = safe_float(portfolio["initial_balance"])

    positions = []
    positions_value = 0.0
    for item in portfolio["items"]:
        secid = normalize_secid_alias(item["secid"])
        quantity = float(item["quantity"])
        avg_buy_price = float(item["avg_buy_price"])
        lot_size = get_lot_size(secid, item)
        lots = int(quantity / lot_size)
        current_price = float(prices.get(secid, FALLBACK.get(secid, avg_buy_price)))
        market_value = current_price * quantity
        invested_value = avg_buy_price * quantity
        profit_loss = market_value - invested_value
        profit_loss_pct = (profit_loss / invested_value * 100) if invested_value > 0 else 0.0
        positions_value += market_value
        positions.append({
            "secid": secid, "shortname": item.get("shortname", secid),
            "sector": item.get("sector") or "Другое", "lots": lots,
            "quantity": int(quantity) if quantity.is_integer() else quantity,
            "lot_size": lot_size, "avg_buy_price": safe_float(avg_buy_price),
            "current_price": safe_float(current_price), "market_value": safe_float(market_value),
            "profit_loss": safe_float(profit_loss), "profit_loss_pct": safe_float(profit_loss_pct),
        })

    total_value = cash + positions_value
    total_profit = total_value - initial_balance
    total_profit_pct = (total_profit / initial_balance * 100) if initial_balance > 0 else 0.0
    metrics = compute_positions_return_metrics(positions)
    return {
        "room_id": room_id,
        "room_title": room.get("title") if room else None,
        "cash": safe_float(cash),
        "positions_value": safe_float(positions_value),
        "total_value": safe_float(total_value),
        "initial_balance": safe_float(initial_balance),
        "total_profit": safe_float(total_profit),
        "total_profit_pct": safe_float(total_profit_pct),
        "avg_return": metrics["avg_return"],
        "volatility": metrics["volatility"],
        "sharpe_ratio": metrics["sharpe_ratio"],
        "stats_message": metrics.get("message", ""),
        "positions": positions,
        "allowed_secids": parse_secid_list(room.get("allowed_secids") if room else []),
    }


# Расчёт результата стресс-теста: применяет коэффициенты сценария к позициям портфеля
def compute_stress_result(user_id: int, scenario_slug: str, created_by: int | None = None) -> dict | None:
    scenario = db.get_stress_scenario(scenario_slug, created_by=created_by)
    if not scenario:
        return None
    portfolio = compute_portfolio(user_id, include_metrics=False, include_forecast=False)
    cash = portfolio["cash"]
    portfolio_secids = [position["secid"] for position in portfolio["positions"]]
    coefficients = get_scenario_coefficients_for_secids(scenario, portfolio_secids)

    positions = []
    stressed_positions_value = 0.0
    for position in portfolio["positions"]:
        coefficient = float(coefficients.get(position["secid"], 1.0))
        current_price = float(position["current_price"])
        stressed_price = current_price * coefficient
        original_value = float(position["market_value"])
        quantity = float(position["quantity"])
        stress_value = quantity * stressed_price
        change_value = stress_value - original_value
        change_pct = ((stress_value / original_value) - 1) * 100 if original_value > 0 else 0.0
        stressed_positions_value += stress_value
        positions.append({
            "secid": position["secid"], "shortname": position.get("shortname", position["secid"]), "quantity": position["quantity"],
            "lots": position["lots"], "lot_size": position["lot_size"],
            "current_price": safe_float(current_price), "stress_price": safe_float(stressed_price),
            "original_value": safe_float(original_value), "stress_value": safe_float(stress_value),
            "change_value": safe_float(change_value), "change_pct": safe_float(change_pct),
            "coefficient": safe_float(coefficient), "coefficient_pct": safe_float((coefficient - 1.0) * 100),
            "explanation": STRESS_EXPLANATIONS.get(scenario_slug, {}).get(
                position["secid"], "На бумагу повлияли общий рыночный риск и структура выбранного стресс-сценария.",
            ),
        })

    original_total = cash + portfolio["positions_value"]
    stress_total = cash + stressed_positions_value
    total_change = stress_total - original_total
    total_change_pct = (total_change / original_total * 100) if original_total > 0 else 0.0

    return {
        "scenario_name": scenario["name"], "scenario_slug": scenario["slug"],
        "description": scenario["description"], "original_value": safe_float(original_total),
        "stress_value": safe_float(stress_total), "total_change": safe_float(total_change),
        "total_change_pct": safe_float(total_change_pct), "coefficients": coefficients,
        "positions": positions, "portfolio": portfolio,
    }


# Синхронизация комнаты: пересчёт результатов участников, обновление leaderboard, сохранение рангов
# Возвращает обновлённую комнату и список участников с метриками
def sync_room(room_id: int):
    room = refresh_room_lifecycle(db.get_room_by_id(room_id))
    if not room:
        return None, []
    participants = db.get_room_participants(room_id)
    ordered, leaderboard = [], []
    ended = room["ends_at"] <= now() or not room["is_active"]
    room_scenario = db.get_stress_scenario(room["scenario_slug"], created_by=room["teacher_id"]) if room.get("scenario_slug") else None
    room_secids = []
    if room_scenario:
        for participant in participants:
            portfolio = compute_room_portfolio(room_id, participant["user_id"])
            room_secids.extend([position["secid"] for position in portfolio["positions"]])
        room_coefficients = get_scenario_coefficients_for_secids(room_scenario, room_secids)
    else:
        room_coefficients = {}

    for participant in participants:
        user_id = participant["user_id"]
        if ended and participant.get("completed_at"):
            ordered.append({
                "user_id": user_id, "username": participant["username"],
                "portfolio_value": safe_float(participant["portfolio_value"]),
                "stress_value": safe_float(participant["stress_value"]),
                "total_return_pct": safe_float(participant["total_return_pct"]),
                "sharpe_ratio": safe_float(participant["sharpe_ratio"]),
                "score": safe_float(participant["score"]),
            })
            continue
        portfolio = compute_room_portfolio(room_id, user_id)
        if not portfolio:
            continue
        stress_total = portfolio["total_value"]
        if room_coefficients:
            # Применяем стресс-коэффициенты к стоимости позиций
            stressed_positions_value = 0.0
            for position in portfolio["positions"]:
                coefficient = float(room_coefficients.get(position["secid"], 1.0))
                stressed_positions_value += float(position["market_value"]) * coefficient
            stress_total = safe_float(float(portfolio["cash"]) + stressed_positions_value)
        sharpe_ratio = float(portfolio.get("sharpe_ratio") or 0.0)
        # Score = stress_total для режима stress, иначе total_value
        score = float(stress_total if room["mode"] == "stress" else portfolio["total_value"])
        db.upsert_room_result(room_id, user_id, portfolio["total_value"], stress_total,
                             portfolio["total_profit_pct"], sharpe_ratio, score, mark_completed=ended)
        ordered.append({
            "user_id": user_id, "username": participant["username"],
            "portfolio_value": safe_float(portfolio["total_value"]),
            "stress_value": safe_float(stress_total),
            "total_return_pct": safe_float(portfolio["total_profit_pct"]),
            "sharpe_ratio": safe_float(sharpe_ratio), "score": safe_float(score),
        })

    # Сортировка: по score (убыв.), затем по stress/portfolio value, затем по Sharpe
    ordered.sort(key=lambda item: (item["score"], item["stress_value"] if room["mode"]=="stress" else item["portfolio_value"], item["sharpe_ratio"]), reverse=True)
    db.set_room_ranks(room_id, [item["user_id"] for item in ordered])
    _global_money_leaderboard_cache.clear()
    fresh_rows = db.get_room_participants(room_id)
    for row in fresh_rows:
        leaderboard.append({
            "user_id": row["user_id"], "username": row["username"], "rank_position": row["rank_position"],
            "portfolio_value": safe_float(row["portfolio_value"]), "stress_value": safe_float(row["stress_value"]),
            "total_return_pct": safe_float(row["total_return_pct"]), "sharpe_ratio": safe_float(row["sharpe_ratio"]),
            "score": safe_float(row["score"]), "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        })
    return room, leaderboard


def build_existing_room_leaderboard(room_id: int) -> list[dict]:
    leaderboard = []
    for row in db.get_room_participants(room_id):
        leaderboard.append({
            "user_id": row["user_id"],
            "username": row["username"],
            "rank_position": row["rank_position"],
            "portfolio_value": safe_float(row["portfolio_value"]),
            "stress_value": safe_float(row["stress_value"]),
            "total_return_pct": safe_float(row["total_return_pct"]),
            "sharpe_ratio": safe_float(row["sharpe_ratio"]),
            "score": safe_float(row["score"]),
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        })
    return leaderboard


# Сборка карточек комнат для студента: с runtime-состоянием, лидербордом и личными результатами
def build_student_room_cards(user_id: int) -> list[dict]:
    rooms = []
    for room in db.get_student_rooms(user_id):
        room = attach_room_runtime_state(room)
        if not room:
            continue
        try:
            room_data, leaderboard = sync_room(int(room["id"]))
        except Exception as exc:
            logger.warning("Failed to sync student room summary room_id=%s user_id=%s: %s", room.get("id"), user_id, exc)
            room_data = room
            leaderboard = build_existing_room_leaderboard(int(room["id"]))
        merged_room = dict(room)
        if room_data:
            merged_room.update(room_data)
        my_row = next((row for row in leaderboard if row["user_id"] == user_id), None)
        if my_row:
            merged_room["rank_position"] = my_row.get("rank_position")
            merged_room["total_return_pct"] = my_row.get("total_return_pct")
            merged_room["score"] = my_row.get("score")
            merged_room["portfolio_value"] = my_row.get("portfolio_value")
            merged_room["stress_value"] = my_row.get("stress_value")
            merged_room["sharpe_ratio"] = my_row.get("sharpe_ratio")
        rooms.append(merged_room)
    return rooms


# Контекст лидерборда комнаты: проверка доступа, синхронизация, сериализация
def build_room_leaderboard_context(room_id: int, user_id: int) -> dict | None:
    room = refresh_room_lifecycle(db.get_room_by_id(room_id))
    if not room:
        return None
    is_teacher = room["teacher_id"] == user_id
    is_participant = db.get_room_for_user(room_id, user_id)
    if not is_teacher and not is_participant:
        return None
    try:
        room, leaderboard = sync_room(room_id)
    except Exception as exc:
        logger.warning("Failed to build dashboard leaderboard context room_id=%s user_id=%s: %s", room_id, user_id, exc)
        leaderboard = build_existing_room_leaderboard(room_id)
    room = refresh_room_lifecycle(room or db.get_room_by_id(room_id))
    if not room:
        return None
    room_payload = serialize_room_response(room)
    room_payload["description"] = room.get("description")
    return {"room": room_payload, "leaderboard": leaderboard}


# HTML-маршруты 
# Главная страница — просто рендер шаблона
@app.route("/")
def index():
    return render_template("index.html")

# Портфель — защищён login_required
@app.route("/portfolio")
@login_required
def portfolio():
    return render_template("portfolio.html")

# Стресс-тесты — список сценариев из БД
@app.route("/stress")
@login_required
def stress():
    return render_template("stress.html", scenarios=db.get_stress_scenarios())


# Рабочее пространство комнаты — для студентов, админы перенаправляются на dashboard
@app.route("/room")
@login_required
def room_workspace():
    if session.get("is_admin"):
        return redirect(url_for("dashboard"))
    return render_template("room.html")


@app.route("/leaderboard")
@login_required
def global_leaderboard_page():
    return render_template("global_leaderboard.html")

# Сборка контекста для dashboard: комнаты, сценарии, бумаги, личная статистика, выбранный лидерборд
def build_dashboard_context(
    *,
    dashboard_error: str | None = None,
    dashboard_success: str | None = None,
    dashboard_form: str | None = None,
):
    is_teacher = is_teacher_context()
    requested_room_id = request.args.get("room_leaderboard", type=int)
    teacher_rooms = db.get_teacher_rooms(current_user_id()) if is_teacher else []
    student_rooms = build_student_room_cards(current_user_id())
    teacher_rooms = [room for room in (attach_room_runtime_state(room) for room in teacher_rooms) if room]
    available_scenarios = db.get_stress_scenarios(created_by=current_user_id()) if is_teacher else db.get_stress_scenarios()
    selected_room_leaderboard_id = None
    selected_room_leaderboard = None
    visible_room_ids = {int(room["id"]) for room in teacher_rooms + student_rooms if room.get("id")}
    if requested_room_id and requested_room_id in visible_room_ids:
        selected_room_leaderboard_id = requested_room_id
        selected_room_leaderboard = build_room_leaderboard_context(requested_room_id, current_user_id())
    personal_stats = None
    if not session.get("is_admin"):
        try:
            personal_stats = get_cached_portfolio_payload(current_user_id(), force_refresh=False)
        except Exception as exc:
            logger.warning("Failed to pre-render personal stats for user_id=%s: %s", current_user_id(), exc)
            try:
                personal_stats = compute_portfolio(current_user_id(), include_metrics=True, include_forecast=False)
            except Exception as inner_exc:
                logger.warning("Fallback personal stats build failed for user_id=%s: %s", current_user_id(), inner_exc)
    return {
        "is_teacher": is_teacher,
        "teacher_rooms": teacher_rooms,
        "student_rooms": student_rooms,
        "scenarios": available_scenarios,
        "securities": db.get_securities(),
        "now_dt": now(),
        "personal_stats": personal_stats,
        "selected_room_leaderboard_id": selected_room_leaderboard_id,
        "selected_room_leaderboard": selected_room_leaderboard,
        "dashboard_error": dashboard_error,
        "dashboard_success": dashboard_success,
        "dashboard_form": dashboard_form,
    }


# Создание сценария учителем: валидация дат, расчёт коэффициентов, сохранение в БД
def create_teacher_scenario(payload):
    name = str(payload.get("name", "")).strip()
    description = str(payload.get("description", "")).strip()
    start_date_raw = str(payload.get("start_date", "")).strip()
    end_date_raw = str(payload.get("end_date", "")).strip()
    coefficients = payload.get("coefficients") or {}
    if not name:
        return None, "Укажите название сценария", 400
    if not start_date_raw or not end_date_raw:
        return None, "Укажите период сценария", 400
    try:
        start_date, end_date = parse_date_range(start_date_raw, end_date_raw)
    except ValueError:
        return None, "Некорректный формат даты", 400
    range_error = validate_moex_history_range(start_date, end_date)
    if range_error:
        return None, range_error, 400
    if (end_date - start_date).days < 5:
        return None, "Сценарий должен длиться минимум 5 дней", 400
    if coefficients and not isinstance(coefficients, dict):
        return None, "coefficients должен быть JSON-объектом", 400
    if not coefficients:
        coefficients = build_period_stress_coefficients(start_date, end_date)
    else:
        coefficients = normalize_stress_coefficients(coefficients)
    try:
        scenario = db.create_stress_scenario(
            name,
            description,
            coefficients,
            start_date=start_date,
            end_date=end_date,
            created_by=current_user_id(),
            is_global=False,
        )
        return scenario, None, 200
    except Exception as exc:
        logger.exception("Failed to create teacher scenario for user_id=%s", current_user_id())
        return None, f"Не удалось создать сценарий: {exc}", 500


# Создание комнаты учителем: валидация параметров, проверка пересечений по времени, сохранение
def create_teacher_room(payload):
    title = str(payload.get("title", "")).strip()
    description = str(payload.get("description", "")).strip()
    scenario_slug = str(payload.get("scenario_slug", "")).strip() or None
    starts_at_raw = payload.get("starts_at")
    raw_allowed_secids = payload.getlist("allowed_secids") if hasattr(payload, "getlist") else payload.get("allowed_secids")
    allowed_secids = parse_secid_list(raw_allowed_secids)
    try:
        duration_minutes = int(payload.get("duration_minutes") or Config.ROOM_DEFAULT_DURATION_MINUTES)
    except (TypeError, ValueError):
        return None, "Укажите корректную длительность комнаты в минутах", 400
    try:
        initial_balance = float(payload.get("initial_balance") or Config.INITIAL_BALANCE)
    except (TypeError, ValueError):
        return None, "Укажите корректный начальный капитал комнаты", 400
    if not starts_at_raw:
        return None, "Укажите дату и время старта комнаты", 400
    try:
        starts_at = parse_optional_datetime(starts_at_raw)
    except ValueError:
        return None, "Укажите корректную дату и время старта комнаты", 400
    if not title:
        return None, "Укажите название комнаты", 400
    if duration_minutes < 5:
        return None, "Длительность комнаты должна быть не меньше 5 минут", 400
    if initial_balance <= 0:
        return None, "Начальный капитал комнаты должен быть положительным", 400
    if starts_at <= now():
        return None, "Дата и время старта комнаты уже прошли. Укажите будущее время.", 400
    ends_at = starts_at + timedelta(minutes=max(duration_minutes, 1))
    # Проверка на пересечение с другими комнатами учителя
    overlapping_room = db.find_teacher_room_overlap(current_user_id(), starts_at, ends_at)
    if overlapping_room:
        overlap_title = str(overlapping_room.get("title") or "другая комната")
        overlap_starts = overlapping_room.get("starts_at")
        overlap_ends = overlapping_room.get("ends_at")
        overlap_period = ""
        if overlap_starts and overlap_ends:
            overlap_period = f" ({overlap_starts.strftime('%d.%m.%Y %H:%M')} — {overlap_ends.strftime('%d.%m.%Y %H:%M')})"
        return None, f"На это время уже запланирована или идёт комната «{overlap_title}»{overlap_period}. Выберите другое время.", 400
    if allowed_secids and len(allowed_secids) < MIN_ROOM_SECURITIES:
        return None, f"Невозможно создать комнату: выберите минимум {MIN_ROOM_SECURITIES} компании или не выбирайте ничего, тогда будут доступны все компании.", 400
    try:
        room = db.create_room(
            teacher_id=current_user_id(),
            title=title,
            description=description or None,
            scenario_slug=scenario_slug,
            duration_minutes=duration_minutes,
            starts_at=starts_at,
            allowed_secids=allowed_secids,
            initial_balance=initial_balance,
            scenario_owner_id=current_user_id(),
        )
        return room, None, 200
    except Exception as exc:
        logger.exception("Failed to create room for teacher_id=%s", current_user_id())
        return None, f"Не удалось создать комнату: {exc}", 500


# Вход в комнату по коду: валидация, вызов db.join_room, возврат результата
def join_room_from_payload(payload):
    room_code = str(payload.get("room_code", "")).strip().upper()
    if not room_code:
        return None, "Введите код комнаты", 400
    result = db.join_room(current_user_id(), room_code)
    if not result.get("success"):
        return None, result.get("error") or "Не удалось войти в комнату", 400
    return result.get("room"), None, 200


# Обработчик dashboard: GET — рендер, POST — обработка форм (вход в комнату, создание комнаты/сценария)
@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    if request.method == "POST":
        action = str(request.form.get("action", "")).strip()
        if action == "join_room":
            room, error, status_code = join_room_from_payload(request.form)
            if room:
                return redirect(url_for("dashboard"))
            return render_template(
                "dashboard.html",
                **build_dashboard_context(
                    dashboard_error=error or "Не удалось войти в комнату",
                    dashboard_form="join_room",
                ),
            ), status_code
        if not is_teacher_context():
            return redirect(url_for("dashboard"))
        if action == "create_room":
            room, error, status_code = create_teacher_room(request.form)
            if room:
                return redirect(f"{url_for('dashboard', room_leaderboard=room['id'])}#room-leaderboard-panel")
            return render_template(
                "dashboard.html",
                **build_dashboard_context(
                    dashboard_error=error or "Не удалось создать комнату",
                    dashboard_form="room",
                ),
            ), status_code
        if action == "create_scenario":
            scenario, error, status_code = create_teacher_scenario(request.form)
            if scenario:
                return render_template(
                    "dashboard.html",
                    **build_dashboard_context(
                        dashboard_success=f"Сценарий «{scenario['name']}» создан",
                        dashboard_form="scenario",
                    ),
                )
            return render_template(
                "dashboard.html",
                **build_dashboard_context(
                    dashboard_error=error or "Не удалось создать сценарий",
                    dashboard_form="scenario",
                ),
            ), status_code
    return render_template("dashboard.html", **build_dashboard_context())


# Закрытие комнаты учителем через HTML-форму
@app.route("/dashboard/rooms/<int:room_id>/close", methods=["POST"])
@teacher_required
def dashboard_close_room(room_id: int):
    response = api_close_room(room_id)
    if isinstance(response, tuple):
        payload, status_code = response
    else:
        payload, status_code = response, response.status_code
    if status_code >= 400:
        return response
    return redirect(f"{url_for('dashboard', room_leaderboard=room_id)}#room-leaderboard-panel")


# Админ-панель: управление бумагами, глобальными сценариями, кодами школ
@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel():
    error = None
    success = None
    last_action = "school_code"
    if request.method == "POST":
        action = str(request.form.get("action", "school_code")).strip()
        last_action = action
        if action == "security":
            secid = str(request.form.get("secid", "")).strip().upper()
            shortname = str(request.form.get("shortname", "")).strip()
            sector = str(request.form.get("sector", "")).strip() or None
            currency = str(request.form.get("currency", "RUB")).strip().upper() or "RUB"
            lot_size = int(request.form.get("lot_size") or 1)
            dividend_yield = request.form.get("dividend_yield")
            volatility_level = str(request.form.get("volatility_level", "")).strip() or None
            if len(secid) < 2 or len(shortname) < 2:
                error = "Укажите тикер и название компании"
            else:
                validation_error = validate_security_input(secid, shortname)
                if validation_error:
                    error = validation_error
            if not error:
                try:
                    db.create_security(
                        secid,
                        shortname,
                        sector=sector,
                        currency=currency,
                        lot_size=lot_size,
                        dividend_yield=float(dividend_yield) if str(dividend_yield or "").strip() else None,
                        volatility_level=volatility_level,
                    )
                    success = f"Бумага {secid} добавлена в общий список"
                except Exception as exc:
                    logger.warning("Failed to create security: %s", exc)
                    error = "Не удалось добавить бумагу. Проверьте, не конфликтует ли тикер"
        elif action == "update_security":
            security_id = int(request.form.get("security_id") or 0)
            secid = str(request.form.get("secid", "")).strip().upper()
            shortname = str(request.form.get("shortname", "")).strip()
            sector = str(request.form.get("sector", "")).strip() or None
            currency = str(request.form.get("currency", "RUB")).strip().upper() or "RUB"
            lot_size = int(request.form.get("lot_size") or 1)
            dividend_yield = request.form.get("dividend_yield")
            volatility_level = str(request.form.get("volatility_level", "")).strip() or None
            if security_id <= 0 or len(secid) < 2 or len(shortname) < 2:
                error = "Выберите бумагу и заполните её параметры"
            else:
                validation_error = validate_security_input(secid, shortname)
                if validation_error:
                    error = validation_error
            if not error:
                try:
                    db.update_security(
                        security_id,
                        secid,
                        shortname,
                        sector=sector,
                        currency=currency,
                        lot_size=lot_size,
                        dividend_yield=float(dividend_yield) if str(dividend_yield or "").strip() else None,
                        volatility_level=volatility_level,
                    )
                    success = f"Бумага {secid} обновлена"
                except Exception as exc:
                    logger.warning("Failed to update security: %s", exc)
                    error = "Не удалось обновить бумагу"
        elif action == "delete_security":
            security_id = int(request.form.get("security_id") or 0)
            if security_id <= 0:
                error = "Выберите бумагу для удаления"
            else:
                try:
                    deleted = db.deactivate_security(security_id)
                    if deleted:
                        success = "Бумага удалена из общего списка"
                    else:
                        error = "Не удалось удалить бумагу"
                except Exception as exc:
                    logger.warning("Failed to delete security: %s", exc)
                    error = "Не удалось удалить бумагу"
        elif action == "scenario":
            name = str(request.form.get("name", "")).strip()
            description = str(request.form.get("description", "")).strip()
            start_date_raw = str(request.form.get("start_date", "")).strip()
            end_date_raw = str(request.form.get("end_date", "")).strip()
            if not name:
                error = "Укажите название глобального сценария"
            elif not start_date_raw or not end_date_raw:
                error = "Укажите период глобального сценария"
            else:
                try:
                    start_date, end_date = parse_date_range(start_date_raw, end_date_raw)
                    range_error = validate_moex_history_range(start_date, end_date)
                    if range_error:
                        raise ValueError(range_error)
                    coefficients = build_period_stress_coefficients(start_date, end_date)
                    db.create_stress_scenario(
                        name,
                        description,
                        coefficients,
                        start_date=start_date,
                        end_date=end_date,
                        created_by=current_user_id(),
                        is_global=True,
                    )
                    success = f"Глобальный стресс-сценарий «{name}» создан"
                except ValueError as exc:
                    logger.warning("Invalid global stress scenario range: %s", exc)
                    error = str(exc)
                except Exception as exc:
                    logger.warning("Failed to create global stress scenario: %s", exc)
                    error = "Не удалось создать глобальный сценарий"
        elif action == "update_scenario":
            scenario_id = int(request.form.get("scenario_id") or 0)
            name = str(request.form.get("name", "")).strip()
            description = str(request.form.get("description", "")).strip()
            start_date_raw = str(request.form.get("start_date", "")).strip()
            end_date_raw = str(request.form.get("end_date", "")).strip()
            if scenario_id <= 0 or not name:
                error = "Выберите сценарий и заполните его название"
            elif not start_date_raw or not end_date_raw:
                error = "Укажите период сценария"
            else:
                try:
                    start_date, end_date = parse_date_range(start_date_raw, end_date_raw)
                    range_error = validate_moex_history_range(start_date, end_date)
                    if range_error:
                        raise ValueError(range_error)
                    coefficients = build_period_stress_coefficients(start_date, end_date)
                    db.update_stress_scenario(
                        scenario_id,
                        name,
                        description,
                        coefficients,
                        start_date=start_date,
                        end_date=end_date,
                        created_by=current_user_id(),
                        is_global=True,
                    )
                    success = f"Сценарий «{name}» обновлён"
                except ValueError as exc:
                    logger.warning("Invalid global stress scenario update range: %s", exc)
                    error = str(exc)
                except Exception as exc:
                    logger.warning("Failed to update global stress scenario: %s", exc)
                    error = "Не удалось обновить сценарий"
        elif action == "delete_scenario":
            scenario_id = int(request.form.get("scenario_id") or 0)
            if scenario_id <= 0:
                error = "Выберите сценарий для удаления"
            else:
                try:
                    deleted = db.deactivate_stress_scenario(scenario_id)
                    if deleted:
                        success = "Глобальный стресс-сценарий удалён"
                    else:
                        error = "Не удалось удалить сценарий"
                except Exception as exc:
                    logger.warning("Failed to delete global stress scenario: %s", exc)
                    error = "Не удалось удалить сценарий"
        else:
            school_name = str(request.form.get("school_name", "")).strip()
            school_code = str(request.form.get("school_code", "")).strip().upper()
            if len(school_name) < 2:
                error = "Укажите название школы"
            elif school_code and len(school_code) < 6:
                error = "Код школы должен содержать минимум 6 символов"
            else:
                try:
                    created = db.create_school_access_code(
                        school_name=school_name,
                        created_by=current_user_id(),
                        school_code=school_code or None,
                    )
                    success = f"Код для школы «{created['school_name']}» создан: {created['school_code']}"
                except Exception as exc:
                    logger.warning("Failed to create school access code: %s", exc)
                    error = "Не удалось создать код. Возможно, такой код уже существует"
    return render_template(
        "admin.html",
        school_codes=db.list_school_access_codes(),
        securities=db.get_securities(),
        scenarios=db.get_stress_scenarios(),
        error=error,
        success=success,
        last_action=last_action,
    )

# Авторизация: проверка пароля (bcrypt или plain), установка сессии
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get("user_id"):
            return redirect(url_for("dashboard"))
        return render_template("login.html")
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    user = db.get_user_by_username(username)
    if not user:
        return render_template("login.html", error="Пользователь не найден")
    stored_hash = user.get("password_hash") or ""
    password_ok = False
    try:
        if stored_hash.startswith("$2"):
            password_ok = bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
        else:
            password_ok = stored_hash == password
    except ValueError:
        password_ok = False
    if not password_ok:
        return render_template("login.html", error="Неверный пароль")
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["is_admin"] = bool(user.get("is_admin"))
    session["is_teacher"] = bool(user.get("is_teacher"))
    session["school_name"] = user.get("school_name")
    db.update_last_login(user["id"])
    return redirect(url_for("dashboard"))

# Регистрация: валидация полей, проверка уникальности, хеширование пароля, создание пользователя
# Для учителей — проверка кода школы
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        if session.get("user_id"):
            return redirect(url_for("dashboard"))
        return render_template("register.html")
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    role = str(request.form.get("role", "student")).strip().lower()
    school_name = str(request.form.get("school_name", "")).strip()
    school_code = str(request.form.get("school_code", "")).strip().upper()
    if not username or not email or not password:
        return render_template("register.html", error="Заполните все поля")
    if len(username) < 3:
        return render_template("register.html", error="Логин должен содержать минимум 3 символа")
    if not EMAIL_RE.match(email):
        return render_template("register.html", error="Укажите корректный email")
    if len(password) < 6:
        return render_template("register.html", error="Пароль должен содержать минимум 6 символов")
    is_teacher = role == "teacher"
    if is_teacher:
        if len(school_name) < 2:
            return render_template("register.html", error="Для учителя нужно указать школу")
        if not school_code:
            return render_template("register.html", error="Для учителя нужен код школы")
        valid_code = db.validate_school_access_code(school_name, school_code)
        if not valid_code:
            return render_template("register.html", error="Код школы не подошёл или не активен")
    if db.get_user_by_username(username):
        return render_template("register.html", error="Такой логин уже занят")
    if db.get_user_by_email(email):
        return render_template("register.html", error="Такой email уже зарегистрирован")
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    user = db.create_user(
        username,
        email,
        password_hash,
        is_teacher=is_teacher,
        school_name=school_name or None,
    )
    if is_teacher and valid_code:
        db.mark_school_access_code_used(valid_code["id"])
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["is_admin"] = False
    session["is_teacher"] = is_teacher
    session["school_name"] = school_name or None
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# JSON API 
# Список бумаг: кэшированные цены, обогащение метаданными, fallback при ошибке
@app.route("/api/securities")
def api_securities():
    try:
        force_refresh = wants_fresh_prices()
        prices = refresh_prices() if force_refresh else get_prices()
        securities = db.get_securities()
        result = enrich_securities_with_market_meta(securities, prices, force_refresh=force_refresh)
        if result:
            return jsonify(result)
    except Exception as exc:
        logger.warning("/api/securities failed: %s", exc)
    fallback_rows = []
    for secid, shortname, sector, _currency, lot_size in SECURITY_SEED:
        fallback_rows.append({
            "secid": secid,
            "shortname": shortname,
            "sector": sector,
            "price": None,
            "price_label": "Цена не найдена",
            "lot_size": lot_size,
            "dividend_yield": SECURITY_PROFILE.get(secid, {}).get("dividend_yield"),
            "volatility_level": SECURITY_PROFILE.get(secid, {}).get("volatility_level", "не указана"),
            "price_source": "NOT_FOUND",
            "is_fallback_price": False,
            "is_price_missing": True,
        })
    return jsonify(fallback_rows)


@app.route("/api/news")
def api_news():
    limit = request.args.get("limit", default=12, type=int) or 12
    secid = str(request.args.get("secid", "")).strip().upper()
    query = str(request.args.get("q", "")).strip()
    from_date = str(request.args.get("from", NEWS_DEFAULT_FROM)).strip() or NEWS_DEFAULT_FROM

    if secid:
        security = db.get_security_by_secid(secid)
        if not security:
            return jsonify({"error": "Бумага не поддерживается"}), 404
        query = build_security_news_query(secid, security.get("shortname"))

    if not query:
        query = NEWS_DEFAULT_QUERY
    return jsonify(fetch_market_news(query, from_date=from_date, limit=limit))


@app.route("/api/leaderboard/global")
@login_required
def api_global_leaderboard():
    limit = request.args.get("limit", default=30, type=int) or 30
    try:
        return jsonify(get_cached_global_money_leaderboard(limit=limit))
    except Exception as exc:
        logger.exception("Failed to build global leaderboard: %s", exc)
        return jsonify({"leaderboard": [], "updated_at": now().isoformat(), "error": "Не удалось сформировать рейтинг"}), 200

# Портфель пользователя: кэшированный ответ
@app.route("/api/portfolio")
@login_required
def api_portfolio():
    try:
        data = get_cached_portfolio_payload(current_user_id(), force_refresh=wants_fresh_prices())
        return jsonify(data)
    except Exception as exc:
        logger.exception("Failed to build portfolio for user_id=%s", current_user_id())
        return jsonify({"error": f"Не удалось загрузить портфель: {exc}"}), 500


# История портфеля: кэшированный ответ
@app.route("/api/portfolio/history")
@login_required
def api_portfolio_history():
    return jsonify(get_cached_portfolio_history_payload(current_user_id(), force_refresh=wants_fresh_prices()))

# Аналитика по бумаге: история, свечи, прогноз, позиция если авторизован
@app.route("/api/securities/<secid>/analytics")
def api_security_analytics(secid: str):
    secid = str(secid or "").strip().upper()
    if not db.get_security_by_secid(secid):
        return jsonify({"error": "Бумага не поддерживается"}), 404
    user_id = int(session["user_id"]) if session.get("user_id") else None
    return jsonify(compute_security_view(secid, user_id=user_id))


# Текущая комната: статус, портфель, доступные бумаги, виртуальная дата
@app.route("/api/room/current")
@login_required
def api_current_room():
    room = get_current_active_room(current_user_id())
    if not room:
        return jsonify({"active": False, "message": "Сейчас нет активной комнаты от учителя."})
    room = db.get_room_by_id(room["id"])
    force_refresh = wants_fresh_prices()
    securities = db.get_securities()
    allowed = set(parse_secid_list(room.get("allowed_secids")))
    if allowed:
        securities = [security for security in securities if str(security.get("secid", "")).upper() in allowed]
    prices, virtual_date = build_room_price_map(room, securities, force_refresh=force_refresh)
    portfolio = compute_room_portfolio(room["id"], current_user_id(), prices=prices)
    result = enrich_securities_with_market_meta(securities, prices, force_refresh=force_refresh)
    return jsonify({
        "active": True,
        "room": serialize_room_response(room),
        "portfolio": portfolio,
        "securities": result,
        "virtual_date": virtual_date.isoformat() if virtual_date else None,
    })


# История бумаги в комнате: период сценария ± буфер, маркер шока
@app.route("/api/room/<secid>/history")
@login_required
def api_room_security_history(secid: str):
    room = get_current_active_room(current_user_id())
    if not room:
        return jsonify({"error": "Нет активной комнаты"}), 404
    room = db.get_room_by_id(room["id"])
    secid = str(secid or "").strip().upper()
    allowed = set(parse_secid_list(room.get("allowed_secids")))
    if allowed and secid not in allowed:
        return jsonify({"error": "Бумага недоступна в комнате"}), 403
    security = db.get_security_by_secid(secid)
    if not security:
        return jsonify({"error": "Бумага не поддерживается"}), 404
    scenario_start = room.get("scenario_start_date")
    scenario_end = room.get("scenario_end_date")
    if not scenario_start or not scenario_end:
        return jsonify({
            "secid": secid,
            "shortname": security.get("shortname", secid),
            "room_title": room.get("title"),
            "chart_available": False,
            "chart_message": "Учитель не назначил исторический период для этой комнаты, поэтому график по периоду недоступен.",
            "history": [],
            "candles": [],
            "start_date": None,
            "end_date": None,
            "shock_date": None,
            "scenario_name": room.get("scenario_name"),
        })

    shock_marker = STRESS_MARKERS.get(room.get("scenario_slug"))
    if shock_marker:
        shock_date = datetime.fromisoformat(shock_marker).date()
    else:
        midpoint_days = max((scenario_end - scenario_start).days // 2, 0)
        shock_date = scenario_start + timedelta(days=midpoint_days)
        shock_marker = shock_date.isoformat()
    history_start, history_end = build_history_window(scenario_start, scenario_end, shock_date)

    history = fetch_security_history_unified(
        secid,
        days=260,
        board="TQBR",
        start_date=history_start,
        end_date=history_end,
        fallback_on_empty=True,
    )
    chart_available = len(history) >= 2
    return jsonify({
        "scenario_name": room.get("scenario_name") or room.get("title"),
        "room_title": room.get("title"),
        "secid": secid,
        "shortname": security.get("shortname", secid),
        "history": history,
        "candles": [],
        "start_date": scenario_start.isoformat() if scenario_start else None,
        "end_date": scenario_end.isoformat() if scenario_end else None,
        "shock_date": shock_marker,
        "chart_available": chart_available,
        "chart_message": None if chart_available else "Для этой бумаги нет точных исторических данных за период комнаты, поэтому график не показывается.",
    })

# История бумаги в стресс-сценарии: аналогично, но с глобальным сценарием
@app.route("/api/stress/<scenario_slug>/<secid>/history")
@login_required
def api_stress_security_history(scenario_slug: str, secid: str):
    scenario = db.get_stress_scenario(str(scenario_slug or "").strip())
    secid = str(secid or "").strip().upper()
    if not scenario:
        return jsonify({"error": "Сценарий не найден"}), 404
    security = db.get_security_by_secid(secid)
    if not security:
        return jsonify({"error": "Бумага не поддерживается"}), 404
    scenario_start = scenario.get("start_date")
    scenario_end = scenario.get("end_date")
    shock_marker = STRESS_MARKERS.get(scenario_slug)
    shock_date = datetime.fromisoformat(shock_marker).date() if shock_marker else None
    history_start, history_end = build_history_window(scenario_start, scenario_end, shock_date)

    history = fetch_security_history_unified(
        secid,
        days=260,
        board="TQBR",
        start_date=history_start,
        end_date=history_end,
        fallback_on_empty=True,
    )
    chart_available = len(history) >= 2
    stress_change_pct = 0.0
    stress_direction = "neutral"
    closes = [float(point.get("close") or 0.0) for point in history if point.get("close") not in (None, "")]
    if len(closes) >= 2 and closes[0] > 0:
        stress_change_pct = ((closes[-1] / closes[0]) - 1) * 100
        if stress_change_pct > 0:
            stress_direction = "up"
        elif stress_change_pct < 0:
            stress_direction = "down"
    stress_explanation = STRESS_EXPLANATIONS.get(scenario_slug, {}).get(
        secid,
        "Динамика отражает общий эффект выбранного стресс-сценария.",
    )
    stress_news_query = f"{build_security_news_query(secid, security.get('shortname'))} AND ({STRESS_NEWS_HINTS.get(scenario_slug, 'risk OR macro')})"
    stress_news = fetch_market_news(stress_news_query, limit=5)
    stress_analysis = build_security_explanation(
        secid,
        history,
        diagnostics={
            "trend_strength_pct": 0.0,
            "momentum_3m_pct": 0.0,
            "annual_volatility_pct": 0.0,
            "mean_reversion_pressure_pct": 0.0,
        },
        news_payload=stress_news,
        stress_context=stress_explanation,
    )
    return jsonify({
        "scenario_name": scenario["name"], "secid": secid, "shortname": security.get("shortname", secid), "history": history, "candles": [],
        "start_date": scenario["start_date"].isoformat() if scenario.get("start_date") else None,
        "end_date": scenario["end_date"].isoformat() if scenario.get("end_date") else None,
        "shock_date": shock_marker,
        "chart_available": chart_available,
        "chart_message": None if chart_available else "Для этой бумаги нет достоверных исторических данных за выбранный период, поэтому стресс-график не показывается.",
        "explanation": stress_explanation,
        "stress_news": stress_news,
        "analysis": stress_analysis,
        "stress_change_pct": safe_float(stress_change_pct),
        "stress_direction": stress_direction,
    })

# Покупка: парсинг лотов, получение цены, вызов db.buy_stock, очистка кэша
@app.route("/api/buy", methods=["POST"])
@login_required
def api_buy():
    payload = request_payload()
    secid = str(payload.get("secid", "")).strip().upper()
    try:
        lots = parse_lots_from_payload(payload, secid)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    _security, price, error = get_trade_context(secid)
    if not secid or lots <= 0 or error or not price:
        return jsonify({"success": False, "error": error or "Проверьте тикер и количество лотов"}), 400
    result = db.buy_stock(current_user_id(), secid, lots, price)
    if result.get("success"):
        clear_portfolio_caches(current_user_id())
    return jsonify(result), (200 if result.get("success") else 400)


# Покупка в комнате: проверка активности, ограничений, виртуальной цены
@app.route("/api/room/buy", methods=["POST"])
@login_required
def api_room_buy():
    room = get_current_active_room(current_user_id())
    if not room:
        return jsonify({"success": False, "error": "Нет активной комнаты"}), 400
    if not is_room_trade_open(room):
        return jsonify({"success": False, "error": "Комната уже выбрана, но торги в ней ещё не начались."}), 400
    payload = request_payload()
    secid = str(payload.get("secid", "")).strip().upper()
    allowed = set(parse_secid_list(room.get("allowed_secids")))
    if allowed and secid not in allowed:
        return jsonify({"success": False, "error": "Эта бумага недоступна в комнате"}), 400
    try:
        lots = parse_lots_from_payload(payload, secid)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    room = db.get_room_by_id(room["id"])
    _security, price, error, virtual_date = get_room_trade_context(room, secid, force_refresh=wants_fresh_prices())
    if not secid or lots <= 0 or error or not price:
        return jsonify({"success": False, "error": error or "Проверьте тикер и количество лотов"}), 400
    result = db.room_buy_stock(room["id"], current_user_id(), secid, lots, price)
    if result.get("success") and virtual_date:
        result["virtual_date"] = virtual_date.isoformat()
    return jsonify(result), (200 if result.get("success") else 400)

# Продажа: аналогично покупке
@app.route("/api/sell", methods=["POST"])
@login_required
def api_sell():
    payload = request_payload()
    secid = str(payload.get("secid", "")).strip().upper()
    try:
        lots = parse_lots_from_payload(payload, secid)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    _security, price, error = get_trade_context(secid)
    if not secid or lots <= 0 or error or not price:
        return jsonify({"success": False, "error": error or "Проверьте тикер и количество лотов"}), 400
    result = db.sell_stock(current_user_id(), secid, lots, price)
    if result.get("success"):
        clear_portfolio_caches(current_user_id())
    return jsonify(result), (200 if result.get("success") else 400)


# Продажа в комнате
@app.route("/api/room/sell", methods=["POST"])
@login_required
def api_room_sell():
    room = get_current_active_room(current_user_id())
    if not room:
        return jsonify({"success": False, "error": "Нет активной комнаты"}), 400
    if not is_room_trade_open(room):
        return jsonify({"success": False, "error": "Комната уже выбрана, но торги в ней ещё не начались."}), 400
    payload = request_payload()
    secid = str(payload.get("secid", "")).strip().upper()
    try:
        lots = parse_lots_from_payload(payload, secid)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    room = db.get_room_by_id(room["id"])
    _security, price, error, virtual_date = get_room_trade_context(room, secid, force_refresh=wants_fresh_prices())
    if not secid or lots <= 0 or error or not price:
        return jsonify({"success": False, "error": error or "Проверьте тикер и количество лотов"}), 400
    result = db.room_sell_stock(room["id"], current_user_id(), secid, lots, price)
    if result.get("success") and virtual_date:
        result["virtual_date"] = virtual_date.isoformat()
    return jsonify(result), (200 if result.get("success") else 400)

# Стресс-тест: проверка мин. диверсификации, расчёт результата, удаление портфеля из ответа
@app.route("/api/stress", methods=["POST"])
@login_required
def api_stress():
    payload = request.get_json() or request.form
    scenario_slug = str(payload.get("scenario", "")).strip()
    if not scenario_slug:
        return jsonify({"error": "Не выбран стресс-сценарий"}), 400
    portfolio = compute_portfolio(current_user_id())
    if portfolio["assets_count"] < MIN_PORTFOLIO_ASSETS:
        return jsonify({
            "error": f"Для стресс-теста соберите портфель минимум из {MIN_PORTFOLIO_ASSETS} разных компаний.",
        }), 400
    result = compute_stress_result(current_user_id(), scenario_slug)
    if not result:
        return jsonify({"error": "Сценарий не найден"}), 404
    result.pop("portfolio", None)
    return jsonify(result)

# Статистика: кэшированный портфель, метрики, прогноз
@app.route("/api/stats")
@login_required
def api_stats():
    try:
        portfolio = get_cached_portfolio_payload(current_user_id(), force_refresh=wants_fresh_prices())
        return jsonify({
            "transactions_count": portfolio["transactions_count"], "avg_return": portfolio["avg_return"],
            "volatility": portfolio["volatility"], "sharpe_ratio": portfolio["sharpe_ratio"],
            "total_value": portfolio["total_value"], "total_profit_pct": portfolio["total_profit_pct"],
            "forecast": portfolio["forecast"], "message": portfolio.get("stats_message", ""),
            "cash": portfolio.get("cash", 0.0),
            "positions_value": portfolio.get("positions_value", 0.0),
            "account_total_profit": portfolio.get("account_total_profit", 0.0),
            "account_total_profit_pct": portfolio.get("account_total_profit_pct", 0.0),
            "assets_count": portfolio.get("assets_count", 0),
            "assets_rule_ok": portfolio.get("assets_rule_ok", False),
            "trade_stats": portfolio.get("trade_stats", {}),
        })
    except Exception as exc:
        logger.warning("Failed to load stats for user_id=%s: %s", current_user_id(), exc)
        try:
            basic = compute_portfolio(
                current_user_id(),
                prices=get_prices(allow_network=False),
                include_metrics=False,
                include_forecast=False,
            )
            return jsonify({
                "transactions_count": basic["transactions_count"],
                "avg_return": 0.0,
                "volatility": 0.0,
                "sharpe_ratio": basic["sharpe_ratio"],
                "total_value": basic["total_value"],
                "total_profit_pct": basic["total_profit_pct"],
                "forecast": basic["forecast"],
                "message": "Статистика загружена в упрощённом режиме.",
                "cash": basic.get("cash", 0.0),
                "positions_value": basic.get("positions_value", 0.0),
                "account_total_profit": basic.get("account_total_profit", 0.0),
                "account_total_profit_pct": basic.get("account_total_profit_pct", 0.0),
                "assets_count": basic.get("assets_count", 0),
                "assets_rule_ok": basic.get("assets_rule_ok", False),
                "trade_stats": basic.get("trade_stats", {}),
            })
        except Exception as fallback_exc:
            logger.warning("Failed to load fallback stats for user_id=%s: %s", current_user_id(), fallback_exc)
            return jsonify({
                "transactions_count": 0,
                "avg_return": 0.0,
                "volatility": 0.0,
                "sharpe_ratio": 0.0,
                "total_value": 0.0,
                "total_profit_pct": 0.0,
                "forecast": {"month": 0.0, "year": 0.0, "ten_years": 0.0},
                "message": "Статистика временно недоступна.",
                "cash": 0.0,
                "positions_value": 0.0,
                "account_total_profit": 0.0,
                "account_total_profit_pct": 0.0,
                "assets_count": 0,
                "assets_rule_ok": False,
                "trade_stats": {},
            })

# Список сценариев: для учителя — только его, для остальных — глобальные
@app.route("/api/scenarios")
@login_required
def api_scenarios():
    if session.get("is_teacher"):
        raw_scenarios = db.get_stress_scenarios(created_by=current_user_id())
    else:
        raw_scenarios = db.get_stress_scenarios()
    scenarios = [serialize_scenario(scenario) for scenario in raw_scenarios]
    return jsonify({"scenarios": scenarios})

# Создание сценария учителем через API
@app.route("/api/scenarios", methods=["POST"])
@teacher_required
def api_create_scenario():
    scenario, error, status_code = create_teacher_scenario(request_payload())
    if not scenario:
        return jsonify({"error": error or "Не удалось создать сценарий"}), status_code
    return jsonify({"success": True, "scenario": scenario})

# Список комнат: для учителя — его комнаты, для студента — в которых участвует
@app.route("/api/rooms", methods=["GET"])
@login_required
def api_rooms():
    is_teacher = is_teacher_context()
    rooms = db.get_teacher_rooms(current_user_id()) if is_teacher else db.get_student_rooms(current_user_id())
    rooms = [room for room in (attach_room_runtime_state(room) for room in rooms) if room]
    return jsonify({"rooms": rooms})

# Создание комнаты учителем через API
@app.route("/api/rooms", methods=["POST"])
@teacher_required
def api_create_room():
    room, error, status_code = create_teacher_room(request_payload())
    if not room:
        return jsonify({"error": error or "Не удалось создать комнату"}), status_code
    return jsonify({"success": True, "room": room})

# Вход в комнату по коду через API
@app.route("/api/rooms/join", methods=["POST"])
@login_required
def api_join_room():
    room, error, status_code = join_room_from_payload(request_payload())
    if not room:
        return jsonify({"error": error or "Не удалось войти в комнату"}), status_code
    return jsonify({"success": True, "room": room})

# Детали комнаты: проверка доступа, лидерборд, мой результат
@app.route("/api/rooms/<int:room_id>")
@login_required
def api_room_detail(room_id: int):
    room = refresh_room_lifecycle(db.get_room_by_id(room_id))
    if not room:
        return jsonify({"error": "Комната не найдена"}), 404
    is_teacher = room["teacher_id"] == current_user_id()
    is_participant = db.get_room_for_user(room_id, current_user_id())
    if not is_teacher and not is_participant:
        return jsonify({"error": "Нет доступа к комнате"}), 403
    _, leaderboard = sync_room(room_id)
    my_result = next((row for row in leaderboard if row["user_id"] == current_user_id()), None)
    room_payload = serialize_room_response(room)
    room_payload["description"] = room["description"]
    return jsonify({"room": room_payload, "leaderboard": leaderboard, "my_result": my_result})

# Лидерборд комнаты: только учитель видит промежуточный, студенты — только после завершения
@app.route("/api/rooms/<int:room_id>/leaderboard")
@login_required
def api_room_leaderboard(room_id: int):
    room = refresh_room_lifecycle(db.get_room_by_id(room_id))
    if not room:
        return jsonify({"error": "Комната не найдена"}), 404
    is_teacher = room["teacher_id"] == current_user_id()
    is_participant = db.get_room_for_user(room_id, current_user_id())
    if not is_teacher and not is_participant:
        return jsonify({"error": "Нет доступа"}), 403
    room_payload = serialize_room_response(room)
    if not is_teacher and room_payload.get("room_status") != "ended":
        return jsonify({"error": "Промежуточный рейтинг комнаты доступен только учителю. Ученики увидят итоговый рейтинг после завершения комнаты."}), 403
    try:
        room, leaderboard = sync_room(room_id)
    except Exception as exc:
        logger.exception("Failed to build leaderboard for room_id=%s", room_id)
        leaderboard = build_existing_room_leaderboard(room_id)
        if not leaderboard:
            return jsonify({"error": f"Не удалось загрузить рейтинг комнаты: {exc}"}), 500
    room = refresh_room_lifecycle(room or db.get_room_by_id(room_id))
    room_payload = serialize_room_response(room)
    room_payload["description"] = room["description"]
    return jsonify({"room": room_payload, "leaderboard": leaderboard})

# Закрытие комнаты: проверка прав, авто-закрытие, синхронизация
@app.route("/api/rooms/<int:room_id>/close", methods=["POST"])
@teacher_required
def api_close_room(room_id: int):
    room = db.get_room_by_id(room_id)
    if not room:
        return jsonify({"error": "Комната не найдена"}), 404
    if room["teacher_id"] != current_user_id():
        return jsonify({"error": "Нет доступа к комнате"}), 403
    room = refresh_room_lifecycle(room)
    if not room.get("is_active"):
        _, leaderboard = sync_room(room_id)
        room_payload = serialize_room_response(room)
        room_payload["description"] = room["description"]
        return jsonify({"success": True, "room": room_payload, "leaderboard": leaderboard, "already_closed": True})
    closed = db.close_room(room_id, current_user_id())
    if not closed:
        room = refresh_room_lifecycle(db.get_room_by_id(room_id))
        if room and not room.get("is_active"):
            _, leaderboard = sync_room(room_id)
            room_payload = serialize_room_response(room)
            room_payload["description"] = room["description"]
            return jsonify({"success": True, "room": room_payload, "leaderboard": leaderboard, "already_closed": True})
        return jsonify({"error": "Не удалось закрыть комнату"}), 400
    room, leaderboard = sync_room(room_id)
    room = refresh_room_lifecycle(room or db.get_room_by_id(room_id))
    room_payload = serialize_room_response(room)
    room_payload["description"] = room["description"]
    return jsonify({"success": True, "room": room_payload, "leaderboard": leaderboard})

# Мои результаты: список комнат с рангом, скором, метриками
@app.route("/api/my-results")
@login_required
def api_my_results():
    results = []
    for room in db.get_student_rooms(current_user_id()):
        room_data, leaderboard = sync_room(room["id"])
        my_row = next((row for row in leaderboard if row["user_id"] == current_user_id()), None)
        if not my_row:
            continue
        results.append({
            "room_id": room_data["id"],
            "title": room_data["title"],
            "room_code": room_data["room_code"],
            "mode": room_data["mode"],
            "scenario_name": room_data.get("scenario_name"),
            "rank_position": my_row["rank_position"],
            "score": my_row["score"],
            "portfolio_value": my_row["portfolio_value"],
            "stress_value": my_row["stress_value"],
            "total_return_pct": my_row["total_return_pct"],
            "sharpe_ratio": my_row["sharpe_ratio"],
            "leaderboard_size": len(leaderboard),
        })
    return jsonify({"results": results})

# Текущий пользователь: базовая информация из сессии
@app.route("/api/me")
@login_required
def api_me():
    return jsonify({
        "user_id": session["user_id"],
        "username": session["username"],
        "is_teacher": bool(session.get("is_teacher")),
        "is_admin": bool(session.get("is_admin")),
    })

# Отладка цен: показывает источник каждой цены (MOEX или fallback)
@app.route("/api/debug/prices")
def debug_prices():
    """Показывает источник каждой цены для отладки."""
    tickers = get_available_tickers()
    prices_live = fetch_moex_prices(tickers, None)
    result = {}
    for ticker in tickers:
        live = prices_live.get(ticker)
        fallback = FALLBACK.get(ticker)
        result[ticker] = {
            "live": live, "fallback": fallback, "used": live if live else fallback,
            "source": "MOEX" if live else "FALLBACK",
        }
    return jsonify(result)

# Health check: проверка подключения к БД и метаданные сборки
@app.route("/api/health")
def api_health():
    try:
        db.get_conn()
        return jsonify({
            "status": "ok",
            "build": APP_BUILD,
            "app_file": os.path.abspath(__file__),
            "cwd": os.getcwd(),
        })
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 503

# Глобальный обработчик ошибок: для API возвращает JSON, для HTML — пробрасывает исключение
@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    if isinstance(exc, HTTPException):
        return exc
    logger.exception("Unhandled application error")
    if request.path.startswith("/api/"):
        return jsonify({"error": str(exc), "build": APP_BUILD, "app_file": os.path.abspath(__file__)}), 500
    raise exc


# Проверка доступности тикеров на MOEX при старте приложения
def validate_tickers_on_startup() -> bool:
    """Проверяет доступность тикеров на MOEX."""
    all_ok = True
    for ticker in get_available_tickers():
        try:
            url = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{ticker}.json"
            r = requests.get(url, params={"iss.meta": "off", "iss.only": "securities"},
                            timeout=5, proxies={'http': None, 'https': None})
            if r.status_code == 200:
                logger.info(f"✅ {ticker} — OK")
            else:
                logger.error(f"❌ Тикер {ticker} не найден на MOEX (HTTP {r.status_code})")
                all_ok = False
        except Exception as e:
            logger.warning(f"⚠️ Не удалось проверить {ticker}: {e}")
    return all_ok


if __name__ == "__main__":
    logger.info("Starting MOEX Trainer build=%s file=%s cwd=%s", APP_BUILD, os.path.abspath(__file__), os.getcwd())
    if not validate_tickers_on_startup():
        logger.warning("⚠️ Обнаружены проблемы с тикерами!")
    start_background_jobs()
    app.run(debug=Config.DEBUG, host=Config.HOST, port=Config.PORT)
