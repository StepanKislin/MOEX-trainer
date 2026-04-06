"""Конфигурация приложения."""
import os


class Config:
    DEBUG = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    SECRET_KEY = os.environ.get("SECRET_KEY", "moex-trainer-dev-secret-key")
    HOST = os.environ.get("HOST", "0.0.0.0")
    PORT = int(os.environ.get("PORT", "5001"))

    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = int(os.environ.get("DB_PORT", "8889"))
    DB_NAME = os.environ.get("DB_NAME", "evriki-nto")
    DB_USER = os.environ.get("DB_USER", "root")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "root")
    DB_SSL_DISABLED = os.environ.get("DB_SSL_DISABLED", "true").lower() == "true"

    MOEX_CACHE_SECONDS = int(os.environ.get("MOEX_CACHE_SECONDS", "300"))
    MOEX_REFRESH_SECONDS = int(os.environ.get("MOEX_REFRESH_SECONDS", "300"))
    INITIAL_BALANCE = float(os.environ.get("INITIAL_BALANCE", "1000000"))
    RISK_FREE_RATE_ANNUAL = float(os.environ.get("RISK_FREE_RATE_ANNUAL", "0.16"))
    MARKET_GROWTH_ANNUAL = float(os.environ.get("MARKET_GROWTH_ANNUAL", "0.08"))
    INFLATION_RATE_ANNUAL = float(os.environ.get("INFLATION_RATE_ANNUAL", "0.06"))
    ROOM_CODE_LENGTH = int(os.environ.get("ROOM_CODE_LENGTH", "8"))
    ROOM_DEFAULT_DURATION_MINUTES = int(
        os.environ.get("ROOM_DEFAULT_DURATION_MINUTES", "60")
    )

    # Корректные тикеры Мосбиржи (Яндекс = YDEX после редомициляции)
    TICKERS = [
        "SBER", "GAZP", "LKOH", "YDEX", "MGNT", "GMKN", "AFLT", "VTBR",
        "ROSN", "NVTK", "TATN", "CHMF", "PLZL", "MOEX", "IRAO", "ALRS", "SNGS", "PHOR",
        "CHMK", "MTSS", "RASP",
    ]
