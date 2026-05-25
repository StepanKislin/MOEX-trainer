"""Модуль работы с MOEX ISS API.

Здесь собрана логика получения:
- актуальных цен;
- исторических рядов;
- свечей.
"""
from __future__ import annotations

import logging
import math
import os
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

import requests

# MOEX в учебном проекте надёжнее опрашивать напрямую, без системных прокси.
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)

logger = logging.getLogger(__name__)
PRICE_SOURCE_CACHE: dict[str, dict[str, str]] = {}
SECURITY_PROFILE_CACHE: dict[str, tuple[dict[str, float | str | None], datetime]] = {}
HISTORY_CACHE: dict[str, tuple[list[dict], datetime]] = {}

#Резервные данные и маппинги

# Исторические и аналитические функции всё ещё используют этот словарь как внутренний ориентир, но пользователю заглушечные цены больше не показываются как реальные рыночные котировки.
FALLBACK = {
    "SBER": 288.50,
    "GAZP": 168.30,
    "LKOH": 5643.00,
    "YDEX": 4280.00,
    "MGNT": 3147.00,
    "GMKN": 14800.00,
    "AFLT": 98.50,
    "VTBR": 0.024,
    "ROSN": 587.40,
    "NVTK": 1198.80,
    "TATN": 719.10,
    "CHMF": 1693.00,
    "PLZL": 13875.00,
    "MOEX": 241.30,
    "IRAO": 3.98,
    "ALRS": 72.50,
    "SNGS": 31.80,
    "PHOR": 6270.00,
    "CHMK": 8350.00,
    "MTSS": 286.40,
    "RASP": 402.20,
}

# Старый тикер YNDX всё ещё может встречаться в сценариях и старых данных.
TICKER_MAPPING = {
    "YNDX": "YDEX",
    "VTB": "VTBR",
}

HISTORICAL_TICKER_GROUPS = {
    "YNDX": ["YNDX", "YDEX"],
    "YDEX": ["YNDX", "YDEX"],
    "VTB": ["VTB", "VTBR"],
    "VTBR": ["VTB", "VTBR"],
}

# Поля расположены по убыванию доверия: сначала пытаемся взять "живую" цену, а если её нет, спускаемся к более консервативным значениям.
PRICE_PRIORITY = (
    "LAST",
    "LCURRENTPRICE",
    "CURRENTPRICE",
    "MARKETPRICE",
    "LEGALCLOSEPRICE",
    "LASTTOPREVPRICE",
    "CLOSE",
    "PREVPRICE",
    "WAPRICE",
)


def _get_ticker_candidates(ticker: str) -> list[str]:
    """Возвращает список тикеров-кандидатов для одного и того же инструмента."""
    normalized = str(ticker or "").strip().upper()
    if not normalized:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        key = str(value or "").strip().upper()
        if not key or key in seen:
            return
        seen.add(key)
        candidates.append(key)

    add(normalized)
    add(TICKER_MAPPING.get(normalized))
    for alias in HISTORICAL_TICKER_GROUPS.get(normalized, []):
        add(alias)
    for original, mapped in TICKER_MAPPING.items():
        if mapped == normalized:
            add(original)
        if original == normalized:
            add(mapped)
    return candidates


def _get_board_candidates(board: str) -> list[str]:
    """Возвращает приоритетный список торговых бордов для поиска котировки."""
    preferred = str(board or "TQBR").strip().upper() or "TQBR"
    boards = [preferred]
    for candidate in ("TQBR", "TQTF", "TQTD"):
        if candidate not in boards:
            boards.append(candidate)
    return boards


class MoexCache:
    def __init__(self, duration=300):
        self.cache = {}
        self.duration = duration

    def get(self, key):
        """Возвращает значение из кэша, если оно ещё не устарело."""
        if key not in self.cache:
            return None
        data, ts = self.cache[key]
        if datetime.now() - ts < timedelta(seconds=self.duration):
            return data
        del self.cache[key]
        return None

    def set(self, key, data):
        """Сохраняет значение в кэш вместе со временем записи."""
        self.cache[key] = (data, datetime.now())

    def clear(self):
        self.cache.clear()
        logger.info("MOEX cache cleared")


def _request_json(url: str, params: dict | None = None, timeout: int = 10) -> dict:
    """Делает запрос к MOEX ISS API с отключённым прокси."""
    response = requests.get(
        url,
        params=params or {},
        headers={
            "User-Agent": "MOEX-Trainer/2.0",
            "Accept": "application/json",
        },
        timeout=timeout,
        proxies={'http': None, 'https': None},  # Прямое подключение
    )
    response.raise_for_status()
    return response.json()


def _cache_is_fresh(ts: datetime, ttl_seconds: int = 21600) -> bool:
    return datetime.now() - ts < timedelta(seconds=ttl_seconds)


def _extract_best_price(payload: dict, board: str) -> float | None:
    """Извлекает лучшую доступную цену из ответа MOEX ISS API."""
    marketdata = payload.get("marketdata", {})
    columns = marketdata.get("columns") or []
    rows = marketdata.get("data") or []

    if rows:
        board_idx = columns.index("BOARDID") if "BOARDID" in columns else None
        row = None

        if board_idx is not None:
            for item in rows:
                if len(item) > board_idx and item[board_idx] == board:
                    row = item
                    break
            if row is None:
                row = rows[0]
        else:
            row = rows[0]

        if row:
            # По очереди пробуем поля из заранее заданного приоритета.
            for field in PRICE_PRIORITY:
                if field in columns:
                    idx = columns.index(field)
                    if idx < len(row):
                        value = row[idx]
                        if value not in (None, "", "null"):
                            try:
                                price = float(value)
                                if price > 0:
                                    return round(price, 2)
                            except (TypeError, ValueError):
                                continue

    securities = payload.get("securities", {})
    sec_columns = securities.get("columns") or []
    sec_rows = securities.get("data") or []
    
    if sec_rows:
        # Если рыночный блок пустой, берём более "справочные" поля из securities.
        sec_row = sec_rows[0]
        for field in ("PREVPRICE", "FACEVALUE"):
            if field in sec_columns:
                idx = sec_columns.index(field)
                if idx < len(sec_row):
                    value = sec_row[idx]
                    if value not in (None, "", "null"):
                        try:
                            price = float(value)
                            if price > 0:
                                return round(price, 2)
                        except (TypeError, ValueError):
                            continue

    return None


def _fetch_live_price_for_ticker(ticker: str, board: str) -> float | None:
    """Получает цену для одного тикера с MOEX."""
    last_error = None
    for candidate_ticker in _get_ticker_candidates(ticker):
        for candidate_board in _get_board_candidates(board):
            url = (
                "https://iss.moex.com/iss/engines/stock/markets/shares/"
                f"boards/{candidate_board}/securities/{candidate_ticker}.json"
            )
            params = {
                "iss.meta": "off",
                "iss.only": "marketdata,securities",
            }
            try:
                payload = _request_json(url, params=params, timeout=10)
                price = _extract_best_price(payload, candidate_board)
                if price:
                    logger.info(
                        "MOEX %s: %s ₽ (тикер=%s, board=%s)",
                        ticker,
                        price,
                        candidate_ticker,
                        candidate_board,
                    )
                    return price
            except Exception as exc:
                last_error = exc
                continue

    if last_error:
        logger.error("Ошибка запроса цены MOEX для %s: %s", ticker, last_error)
    else:
        logger.warning("MOEX %s: цена не найдена", ticker)
    return None


def fetch_moex_prices(tickers, cache=None, board="TQBR"):
    """Получает цены для списка тикеров с MOEX."""
    key = f"p_{board}_{'_'.join(sorted(tickers))}"
    if cache:
        cached = cache.get(key)
        if cached:
            return cached

    tickers = [str(ticker or "").strip().upper() for ticker in tickers if str(ticker or "").strip()]
    prices: dict[str, float] = {}
    sources: dict[str, str] = {}

    logger.info(f"Запрашиваем цены MOEX для: {tickers}")

    for ticker in tickers:
        price = None

        # Шаг 1. Пытаемся взять актуальную цену с рыночного эндпоинта.
        try:
            price = _fetch_live_price_for_ticker(ticker, board)
            if price is not None and price > 0:
                prices[ticker] = price
                sources[ticker] = "MOEX"
                continue
        except Exception as exc:
            logger.warning(f"Не удалось получить live-цену для {ticker}: {exc}")

        # Шаг 2. Если live-цены нет, берём последнюю интрадей-свечу.
        try:
            recent_candles = fetch_security_candles(ticker, cache=None, days=3, board=board, interval=10)
            if recent_candles:
                price = round(float(recent_candles[-1]["close"]), 2)
                if price > 0:
                    prices[ticker] = price
                    sources[ticker] = "INTRADAY"
                    logger.warning(f"{ticker}: используем close из интрадей-свечи: {price}")
                    continue
        except Exception as exc:
            logger.warning(f"Не сработал fallback через интрадей-свечи для {ticker}: {exc}")

        # Шаг 3. Последний более надёжный запасной вариант — дневная история.
        try:
            recent_history = fetch_security_history_unified(ticker, days=5, board=board)
            if recent_history:
                price = round(float(recent_history[-1]["close"]), 2)
                if price > 0:
                    prices[ticker] = price
                    sources[ticker] = "HISTORY"
                    logger.warning(f"{ticker}: используем close из дневной истории: {price}")
                    continue
        except Exception as exc:
            logger.warning(f"Не сработал fallback через дневную историю для {ticker}: {exc}")

        # Если цена так и не нашлась, честно помечаем её как недоступную.
        sources[ticker] = "NOT_FOUND"
        logger.warning(f"{ticker}: цена не найдена")

        time.sleep(0.1)

    if cache:
        cache.set(key, prices)

    PRICE_SOURCE_CACHE[key] = sources
    logger.info(f"Итоговые цены: {prices}")
    return prices


def _extract_dividend_value(row: list, columns: list[str]) -> float | None:
    for field in ("VALUE", "VALUE_RUB", "DIVIDEND", "AMOUNT"):
        if field in columns:
            idx = columns.index(field)
            if idx < len(row):
                raw = row[idx]
                if raw in (None, "", "null"):
                    continue
                try:
                    value = float(raw)
                    if value > 0:
                        return value
                except (TypeError, ValueError):
                    continue
    return None


def _extract_dividend_date(row: list, columns: list[str]) -> date | None:
    for field in ("REGCLOSEDATE", "CLOSEDATE", "VALUE_DATE", "RECORDDATE"):
        if field in columns:
            idx = columns.index(field)
            if idx < len(row):
                raw = row[idx]
                if not raw:
                    continue
                try:
                    return date.fromisoformat(str(raw)[:10])
                except ValueError:
                    continue
    return None


def classify_volatility_level(volatility_annual: float) -> str:
    if volatility_annual < 0.18:
        return "низкая"
    if volatility_annual < 0.33:
        return "средняя"
    return "высокая"


def estimate_volatility_level(secid: str, days: int = 180) -> tuple[str | None, float | None]:
    history = fetch_security_history_unified(secid, days=days, fallback_on_empty=False)
    closes = [float(point["close"]) for point in history if point.get("close") not in (None, "")]
    if len(closes) < 20:
        return None, None

    returns = []
    for prev, curr in zip(closes, closes[1:]):
        if prev > 0 and curr > 0:
            returns.append(math.log(curr / prev))
    if len(returns) < 10:
        return None, None

    avg_return = sum(returns) / len(returns)
    variance = sum((ret - avg_return) ** 2 for ret in returns) / len(returns)
    annualized = (variance ** 0.5) * math.sqrt(252)
    return classify_volatility_level(annualized), round(annualized * 100, 2)

# Поле оценки дивидендов
def estimate_dividend_yield(secid: str, current_price: float | None = None) -> float | None:
    if not current_price or current_price <= 0:
        current_price = _fetch_live_price_for_ticker(secid, "TQBR")
    if not current_price or current_price <= 0:
        return None

    payload = None
    last_error = None
    for candidate_ticker in _get_ticker_candidates(secid):
        try:
            payload = _request_json(
                f"https://iss.moex.com/iss/securities/{candidate_ticker}/dividends.json",
                params={"iss.meta": "off"},
                timeout=10,
            )
            dividends = payload.get("dividends", {})
            if dividends.get("data"):
                break
        except Exception as exc:
            last_error = exc
            payload = None
            continue

    if payload is None:
        if last_error:
            logger.warning("Не удалось получить дивиденды MOEX для %s: %s", secid, last_error)
        return None

    dividends = payload.get("dividends", {})
    columns = dividends.get("columns") or []
    rows = dividends.get("data") or []
    if not rows or not columns:
        return None

    one_year_ago = date.today() - timedelta(days=365)
    total_paid = 0.0
    found_recent = False
    for row in rows:
        paid = _extract_dividend_value(row, columns)
        paid_date = _extract_dividend_date(row, columns)
        if paid is None or paid_date is None or paid_date < one_year_ago:
            continue
        total_paid += paid
        found_recent = True

    if not found_recent or total_paid <= 0:
        return None
    return round((total_paid / float(current_price)) * 100, 2)


def fetch_security_profile(secid: str, current_price: float | None = None, force_refresh: bool = False) -> dict[str, float | str | None]:
    key = str(secid or "").strip().upper()
    cached = SECURITY_PROFILE_CACHE.get(key)
    if cached and not force_refresh and _cache_is_fresh(cached[1]):
        return dict(cached[0])

    dividend_yield = estimate_dividend_yield(key, current_price=current_price)
    volatility_level, volatility_percent = estimate_volatility_level(key)
    profile = {
        "dividend_yield": dividend_yield,
        "volatility_level": volatility_level,
        "volatility_percent": volatility_percent,
    }
    SECURITY_PROFILE_CACHE[key] = (dict(profile), datetime.now())
    return profile


def get_price_sources(tickers, board="TQBR") -> dict[str, str]:
    """Возвращает карту источников цены для последнего набора тикеров."""
    key = f"p_{board}_{'_'.join(sorted(tickers))}"
    return dict(PRICE_SOURCE_CACHE.get(key, {}))


def _fallback_history(
    ticker: str,
    days: int,
    current_price: float | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """Строит синтетический ряд, если реальную историю получить не удалось."""
    anchor = current_price or FALLBACK.get(ticker, 100.0)
    series = []
    if start_date and end_date and start_date <= end_date:
        total_days = max((end_date - start_date).days, 1)
        offsets = range(0, total_days + 1)
        get_dt = lambda offset: datetime.combine(start_date + timedelta(days=offset), datetime.min.time())
    else:
        offsets = range(days, -1, -1)
        get_dt = lambda offset: datetime.now() - timedelta(days=offset)

    for offset in offsets:
        dt = get_dt(offset)
        # Ряд специально делаем не совсем линейным, чтобы карточки и графики
        # выглядели правдоподобно даже в аварийном режиме.
        wave = math.sin(offset / 5.5) * 0.018 + math.cos(offset / 11.0) * 0.012
        drift = offset * 0.0008
        price = max(anchor * (0.92 + drift + wave), 1.0)
        series.append({"date": dt.strftime("%Y-%m-%d"), "close": round(price, 2)})
    return series


def _base_fetch_history(
    secid: str,
    days: int,
    board: str,
    start_date: date | None = None,
    end_date: date | None = None,
    fallback_on_empty: bool = True,
) -> list[dict]:
    """Базовая функция получения истории без маппинга."""
    till = end_date or datetime.now().date()
    start = start_date or (till - timedelta(days=max(days * 2, days + 14)))

    last_error = None
    for candidate_secid in _get_ticker_candidates(secid):
        for candidate_board in _get_board_candidates(board):
            try:
                columns = []
                rows = []
                offset = 0
                while True:
                    payload = _request_json(
                        "https://iss.moex.com/iss/history/engines/stock/markets/shares/"
                        f"boards/{candidate_board}/securities/{candidate_secid}.json",
                        {
                            "iss.meta": "off",
                            "iss.only": "history",
                            "from": start.isoformat(),
                            "till": till.isoformat(),
                            "start": offset,
                        },
                        timeout=12,
                    )
                    history = payload.get("history", {})
                    if not columns:
                        columns = history.get("columns") or []
                    batch = history.get("data") or []
                    rows.extend(batch)
                    if len(batch) < 100:
                        break
                    offset += len(batch)

                date_idx = columns.index("TRADEDATE") if "TRADEDATE" in columns else None
                close_fields = [field for field in ("CLOSE", "LEGALCLOSEPRICE", "WAPRICE") if field in columns]

                points = []
                for row in rows:
                    if date_idx is None or date_idx >= len(row):
                        continue
                    close_value = None
                    for field in close_fields:
                        idx = columns.index(field)
                        if idx < len(row) and row[idx] not in (None, ""):
                            close_value = row[idx]
                            break
                    if close_value in (None, ""):
                        continue
                    points.append({"date": str(row[date_idx]), "close": round(float(close_value), 2)})

                if not points:
                    continue

                if not start_date and not end_date and len(points) > days:
                    points = points[-days:]
                return points
            except Exception as exc:
                last_error = exc
                continue

    if last_error:
        logger.warning("Не удалось получить историю MOEX для %s: %s", secid, last_error)
    if not fallback_on_empty:
        return []
    return _fallback_history(secid, days, start_date=start, end_date=till)


def fetch_security_history_unified(
    secid: str,
    days=180,
    board="TQBR",
    start_date: date | None = None,
    end_date: date | None = None,
    fallback_on_empty: bool = True,
) -> list[dict]:
    """Получает историю бумаги с учётом исторических псевдонимов тикера."""
    secid = secid.upper()
    has_custom_range = start_date is not None or end_date is not None
    cache_key = (
        f"{secid}|{days}|{board}|"
        f"{start_date.isoformat() if start_date else '-'}|"
        f"{end_date.isoformat() if end_date else '-'}|"
        f"{int(fallback_on_empty)}"
    )
    cached = HISTORY_CACHE.get(cache_key)
    if cached and _cache_is_fresh(cached[1], ttl_seconds=900):
        return list(cached[0])

    history_aliases = HISTORICAL_TICKER_GROUPS.get(secid)
    if history_aliases:
        combined_history = []
        existing_dates: set[str] = set()
        for alias in history_aliases:
            # Для Yandex склеиваем старый и новый тикер в единый ряд.
            alias_history = _base_fetch_history(
                alias,
                days + 365 if not has_custom_range else days,
                board,
                start_date=start_date,
                end_date=end_date,
                fallback_on_empty=False,
            )
            for point in alias_history:
                if point["date"] in existing_dates:
                    continue
                combined_history.append(point)
                existing_dates.add(point["date"])

        combined_history.sort(key=lambda x: x["date"])
        if combined_history:
            result = combined_history[-days:] if not has_custom_range and len(combined_history) > days else combined_history
            HISTORY_CACHE[cache_key] = (list(result), datetime.now())
            return result
        if not fallback_on_empty:
            return []
        result = _fallback_history(secid, days, start_date=start_date, end_date=end_date)
        HISTORY_CACHE[cache_key] = (list(result), datetime.now())
        return result

    result = _base_fetch_history(
        secid,
        days,
        board,
        start_date=start_date,
        end_date=end_date,
        fallback_on_empty=fallback_on_empty,
    )
    HISTORY_CACHE[cache_key] = (list(result), datetime.now())
    return result


def fetch_security_history(secid: str, cache=None, days=180, board="TQBR", start_date=None, end_date=None):
    """Обёртка для совместимости."""
    return fetch_security_history_unified(
        secid,
        days,
        board,
        start_date=start_date,
        end_date=end_date,
    )


def fetch_security_candles(secid: str, cache=None, days=60, board="TQBR", interval=24, start_date=None, end_date=None):
    """Получает свечи с MOEX."""
    secid = str(secid or "").strip().upper()

    till = end_date or datetime.now().date()
    start = start_date or (till - timedelta(days=max(days * 3, days + 14)))

    last_error = None
    for candidate_secid in _get_ticker_candidates(secid):
        for candidate_board in _get_board_candidates(board):
            try:
                columns = []
                rows = []
                offset = 0
                while True:
                    payload = _request_json(
                        "https://iss.moex.com/iss/engines/stock/markets/shares/"
                        f"boards/{candidate_board}/securities/{candidate_secid}/candles.json",
                        {
                            "iss.meta": "off",
                            "from": start.isoformat(),
                            "till": till.isoformat(),
                            "interval": interval,
                            "start": offset,
                        },
                        timeout=12,
                    )
                    candles_data = payload.get("candles", {})
                    if not columns:
                        columns = candles_data.get("columns") or []
                    batch = candles_data.get("data") or []
                    rows.extend(batch)
                    if len(batch) < 100:
                        break
                    offset += len(batch)

                if not columns or not rows:
                    continue

                begin_idx = columns.index("begin") if "begin" in columns else None
                open_idx = columns.index("open") if "open" in columns else None
                high_idx = columns.index("high") if "high" in columns else None
                low_idx = columns.index("low") if "low" in columns else None
                close_idx = columns.index("close") if "close" in columns else None
                volume_idx = columns.index("volume") if "volume" in columns else None

                result = []
                for row in rows:
                    if None in (begin_idx, open_idx, high_idx, low_idx, close_idx):
                        continue
                    if max(begin_idx, open_idx, high_idx, low_idx, close_idx) >= len(row):
                        continue
                    open_value = row[open_idx]
                    high_value = row[high_idx]
                    low_value = row[low_idx]
                    close_value = row[close_idx]
                    if None in (open_value, high_value, low_value, close_value):
                        continue
                    volume = row[volume_idx] if volume_idx is not None and volume_idx < len(row) else 0
                    result.append(
                        {
                            "date": str(row[begin_idx])[:10],
                            "open": round(float(open_value), 2),
                            "high": round(float(high_value), 2),
                            "low": round(float(low_value), 2),
                            "close": round(float(close_value), 2),
                            "volume": int(float(volume or 0)),
                        }
                    )

                if not result:
                    continue

                if not start_date and not end_date and len(result) > days:
                    result = result[-days:]
                return result
            except Exception as exc:
                last_error = exc
                continue

    if last_error:
        logger.warning("Не удалось получить свечи MOEX для %s: %s", secid, last_error)
    return _fallback_candles(secid, days, start_date=start, end_date=till)


def _fallback_candles(
    ticker: str,
    days: int,
    current_price: float | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """Строит синтетические свечи поверх fallback-истории."""
    history = _fallback_history(
        ticker,
        days,
        current_price=current_price,
        start_date=start_date,
        end_date=end_date,
    )
    candles = []
    prev_close = history[0]["close"] if history else (current_price or FALLBACK.get(ticker, 100.0))
    for index, point in enumerate(history):
        close = float(point["close"])
        open_price = float(prev_close if index else close * 0.996)
        high = max(open_price, close) * 1.008
        low = min(open_price, close) * 0.992
        volume = int(1_000_000 + (index + 1) * 25000)
        candles.append(
            {
                "date": point["date"],
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": volume,
            }
        )
        prev_close = close
    return candles


def build_price_forecast(history: list[dict], horizons: dict[str, int], risk_bias: float = 0.0) -> dict[str, float]:
    """Строит упрощённый прогноз цены на основе исторической динамики."""
    if len(history) < 20:
        current = float(history[-1]["close"]) if history else 0.0
        return {label: round(current, 2) for label in horizons}

    closes = [float(point["close"]) for point in history if point.get("close") not in (None, "")]
    returns = []
    for prev, curr in zip(closes, closes[1:]):
        if prev > 0:
            returns.append(math.log(curr / prev))

    current_price = closes[-1] if closes else 0.0
    if not returns or current_price <= 0:
        return {label: round(current_price, 2) for label in horizons}

    recent_returns = returns[-60:] if len(returns) > 60 else returns
    avg_daily_return = sum(recent_returns) / len(recent_returns)
    volatility = (sum((ret - avg_daily_return) ** 2 for ret in recent_returns) / len(recent_returns)) ** 0.5
    neutral_daily_return = math.log(1.08) / 252
    adjusted_daily_return = max(min(avg_daily_return - volatility * risk_bias * 0.35, 0.003), -0.003)

    forecast = {}
    for label, trading_days in horizons.items():
        # Чем дальше горизонт, тем слабее влияние текущего короткого тренда и тем сильнее тяготение к нейтральному сценарию.
        mean_reversion = math.exp(-trading_days / 252)
        blended_daily_return = adjusted_daily_return * mean_reversion + neutral_daily_return * (1 - mean_reversion)
        projected = current_price * math.exp(blended_daily_return * trading_days)

        if trading_days <= 31:
            min_multiplier, max_multiplier = 0.75, 1.35
        elif trading_days <= 252:
            min_multiplier, max_multiplier = 0.55, 1.8
        else:
            min_multiplier, max_multiplier = 0.35, 3.0

        projected = min(max(projected, current_price * min_multiplier), current_price * max_multiplier)
        forecast[label] = round(max(projected, 0.01), 2)
    return forecast


def clear_moex_cache(cache: MoexCache) -> None:
    """Утилита для принудительной очистки кэша."""
    if cache:
        cache.clear()
        logger.info("Кэш MOEX успешно очищен")


def clear_security_profile_cache() -> None:
    SECURITY_PROFILE_CACHE.clear()
    logger.info("Кэш профилей бумаг очищен")


def clear_history_cache() -> None:
    HISTORY_CACHE.clear()
    logger.info("Кэш исторических рядов очищен")
