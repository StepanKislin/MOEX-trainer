"""Работа с MySQL для MOEX Trainer.

Модуль инкапсулирует:
- подключение и служебные миграции схемы;
- операции с пользователями и школьными кодами;
- работу с бумагами и портфелями;
- комнатный режим и рейтинг.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import string
import threading
from datetime import datetime, timedelta
from typing import Any

import pymysql
from pymysql import MySQLError
from pymysql.err import IntegrityError
from pymysql.cursors import Cursor, DictCursor

from config import Config

logger = logging.getLogger(__name__)

# Базовый список бумаг, которые должны быть доступны в системе.
SECURITY_SEED = [
    ("SBER", "Сбербанк", "Финансы", "RUB", 10),
    ("GAZP", "Газпром", "Энергетика", "RUB", 10),
    ("LKOH", "Лукойл", "Нефть и газ", "RUB", 1),
    ("YDEX", "Яндекс", "IT", "RUB", 1),
    ("MGNT", "Магнит", "Ритейл", "RUB", 1),
    ("GMKN", "Норникель", "Металлы", "RUB", 10),
    ("AFLT", "Аэрофлот", "Транспорт", "RUB", 10),
    ("VTBR", "ВТБ", "Финансы", "RUB", 10),
    ("ROSN", "Роснефть", "Нефть и газ", "RUB", 1),
    ("NVTK", "НОВАТЭК", "Нефть и газ", "RUB", 1),
    ("TATN", "Татнефть", "Нефть и газ", "RUB", 1),
    ("CHMF", "Северсталь", "Металлы", "RUB", 1),
    ("PLZL", "Полюс", "Металлы", "RUB", 1),
    ("MOEX", "Московская Биржа", "Финансы", "RUB", 10),
    ("IRAO", "Интер РАО", "Энергетика", "RUB", 100),
    ("ALRS", "АЛРОСА", "Материалы", "RUB", 10),
    ("SNGS", "Сургутнефтегаз", "Нефть и газ", "RUB", 100),
    ("PHOR", "ФосАгро", "Химия", "RUB", 1),
    ("CHMK", "ЧМК", "Металлы", "RUB", 10),
    ("MTSS", "МТС", "Телеком", "RUB", 10),
    ("RASP", "Распадская", "Металлы", "RUB", 10),
]

SECURITY_SEED_BY_SECID = {row[0]: row for row in SECURITY_SEED}
DEFAULT_DIVIDEND_YIELDS = {
    "SBER": 10.8,
    "GAZP": 11.4,
    "LKOH": 12.1,
    "YDEX": 0.0,
    "MGNT": 7.2,
    "GMKN": 8.4,
    "AFLT": 0.0,
    "VTBR": 0.0,
    "ROSN": 11.2,
    "NVTK": 8.3,
    "TATN": 9.7,
    "CHMF": 12.4,
    "PLZL": 4.6,
    "MOEX": 8.1,
    "IRAO": 3.2,
    "ALRS": 10.3,
    "SNGS": 7.6,
    "PHOR": 9.1,
    "CHMK": 5.4,
    "MTSS": 12.8,
    "RASP": 0.0,
}
DEFAULT_VOLATILITY_LEVELS = {
    "SBER": "средняя",
    "GAZP": "средняя",
    "LKOH": "средняя",
    "YDEX": "высокая",
    "MGNT": "средняя",
    "GMKN": "высокая",
    "AFLT": "высокая",
    "VTBR": "средняя",
    "ROSN": "средняя",
    "NVTK": "средняя",
    "TATN": "средняя",
    "CHMF": "высокая",
    "PLZL": "высокая",
    "MOEX": "средняя",
    "IRAO": "высокая",
    "ALRS": "высокая",
    "SNGS": "средняя",
    "PHOR": "средняя",
    "CHMK": "высокая",
    "MTSS": "средняя",
    "RASP": "высокая",
}

SECID_ALIASES = {
    "YNDX": "YDEX",
    "VTB": "VTBR",
}


class Database:
    def __init__(self) -> None:
        self.cfg = {
            "host": os.getenv("DB_HOST", Config.DB_HOST),
            "database": os.getenv("DB_NAME", Config.DB_NAME),
            "user": os.getenv("DB_USER", Config.DB_USER),
            "password": os.getenv("DB_PASSWORD", Config.DB_PASSWORD),
            "port": int(os.getenv("DB_PORT", str(Config.DB_PORT))),
            "charset": "utf8mb4",
            "autocommit": True,
            "connect_timeout": 10,
        }
        db_socket = os.getenv("DB_SOCKET", "").strip()
        if db_socket:
            self.cfg["unix_socket"] = db_socket
            self.cfg.pop("host", None)
            self.cfg.pop("port", None)
        self._local = threading.local()
        self._schema_lock = threading.Lock()
        self._schema_ready = False
        self._connect()

    def _connect(self) -> None:
        current_conn = getattr(self._local, "conn", None)
        if current_conn:
            try:
                current_conn.close()
            except Exception:
                pass
        self._local.conn = pymysql.connect(**self.cfg)
        if not self._schema_ready:
            with self._schema_lock:
                if not self._schema_ready:
                    self._ensure_runtime_schema()
                    self.ensure_securities_seed()
                    self._schema_ready = True

    def get_conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self._connect()
            conn = getattr(self._local, "conn", None)
        else:
            try:
                conn.ping(reconnect=True)
            except Exception:
                self._connect()
                conn = getattr(self._local, "conn", None)
        return conn

    def _cursor(self, dictionary: bool = False):
        cursor_class = DictCursor if dictionary else Cursor
        return self.get_conn().cursor(cursor=cursor_class)

    def _execute(self, query: str, params: tuple[Any, ...] | None = None, dictionary=False):
        try:
            cursor = self._cursor(dictionary=dictionary)
            cursor.execute(query, params or ())
            return cursor
        except (MySQLError, IndexError) as exc:
            logger.warning("DB query failed, reconnecting once: %s", exc)
            self._connect()
            cursor = self._cursor(dictionary=dictionary)
            cursor.execute(query, params or ())
            return cursor

    def _table_exists(self, table: str) -> bool:
        cursor = self._execute(
            """
            SELECT COUNT(*) AS c
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            """,
            (self.cfg["database"], table),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return bool(row and int(row["c"]) > 0)

    def _has_column(self, table: str, column: str) -> bool:
        cursor = self._execute(
            """
            SELECT COUNT(*) AS c
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s AND column_name = %s
            """,
            (self.cfg["database"], table, column),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return bool(row and int(row["c"]) > 0)

    def _ensure_runtime_schema(self) -> None:
        """Добавляет недостающие поля и таблицы при старте приложения.

        Это удобно для учебного проекта: приложение само подстраивает схему
        под новые версии без отдельной ручной миграции в каждом запуске.
        """
        cursor = self._cursor()

        if self._table_exists("securities") and not self._has_column("securities", "lot_size"):
            cursor.execute(
                "ALTER TABLE securities ADD COLUMN lot_size INT NOT NULL DEFAULT 1 AFTER currency"
            )
        if self._table_exists("securities") and not self._has_column("securities", "sector"):
            cursor.execute(
                "ALTER TABLE securities ADD COLUMN sector VARCHAR(100) NULL AFTER shortname"
            )
        if self._table_exists("securities") and not self._has_column("securities", "dividend_yield"):
            cursor.execute(
                "ALTER TABLE securities ADD COLUMN dividend_yield DECIMAL(6,2) NULL AFTER lot_size"
            )
        if self._table_exists("securities") and not self._has_column("securities", "volatility_level"):
            cursor.execute(
                "ALTER TABLE securities ADD COLUMN volatility_level VARCHAR(20) NULL AFTER dividend_yield"
            )
        if self._table_exists("users") and not self._has_column("users", "room_joined_at"):
            cursor.execute(
                "ALTER TABLE users ADD COLUMN room_joined_at TIMESTAMP NULL DEFAULT NULL AFTER class_joined_at"
            )
        if self._table_exists("users") and not self._has_column("users", "school_name"):
            cursor.execute(
                "ALTER TABLE users ADD COLUMN school_name VARCHAR(150) NULL DEFAULT NULL AFTER email"
            )
        if self._table_exists("stress_scenarios") and not self._has_column("stress_scenarios", "created_by"):
            cursor.execute(
                "ALTER TABLE stress_scenarios ADD COLUMN created_by INT NULL DEFAULT NULL AFTER market_context"
            )
        if self._table_exists("stress_scenarios") and not self._has_column("stress_scenarios", "is_global"):
            cursor.execute(
                "ALTER TABLE stress_scenarios ADD COLUMN is_global TINYINT(1) NOT NULL DEFAULT 1 AFTER created_by"
            )
        if self._table_exists("room_sessions") and not self._has_column("room_sessions", "initial_balance"):
            cursor.execute(
                f"ALTER TABLE room_sessions ADD COLUMN initial_balance DECIMAL(15,2) NOT NULL DEFAULT {Config.INITIAL_BALANCE:.2f} AFTER ends_at"
            )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS room_sessions (
                id INT NOT NULL AUTO_INCREMENT,
                teacher_id INT NOT NULL,
                title VARCHAR(150) NOT NULL,
                description TEXT NULL,
                room_code VARCHAR(20) NOT NULL,
                mode ENUM('practice', 'stress') NOT NULL DEFAULT 'practice',
                scenario_id INT NULL,
                starts_at DATETIME NOT NULL,
                ends_at DATETIME NOT NULL,
                initial_balance DECIMAL(15,2) NOT NULL DEFAULT 1000000.00,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uk_room_code (room_code),
                KEY idx_room_teacher (teacher_id),
                KEY idx_room_dates (starts_at, ends_at),
                KEY idx_room_scenario (scenario_id),
                CONSTRAINT fk_room_teacher FOREIGN KEY (teacher_id) REFERENCES users (id) ON DELETE CASCADE,
                CONSTRAINT fk_room_scenario FOREIGN KEY (scenario_id) REFERENCES stress_scenarios (id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        if self._table_exists("room_sessions") and not self._has_column("room_sessions", "initial_balance"):
            cursor.execute(
                f"ALTER TABLE room_sessions ADD COLUMN initial_balance DECIMAL(15,2) NOT NULL DEFAULT {Config.INITIAL_BALANCE:.2f} AFTER ends_at"
            )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS school_access_codes (
                id INT NOT NULL AUTO_INCREMENT,
                school_name VARCHAR(150) NOT NULL,
                school_code VARCHAR(32) NOT NULL,
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                created_by INT NULL,
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP NULL DEFAULT NULL,
                PRIMARY KEY (id),
                UNIQUE KEY uk_school_code (school_code),
                KEY idx_school_name (school_name),
                CONSTRAINT fk_school_code_creator FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS room_participants (
                id INT NOT NULL AUTO_INCREMENT,
                room_id INT NOT NULL,
                user_id INT NOT NULL,
                joined_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP NULL DEFAULT NULL,
                portfolio_value DECIMAL(15,2) NOT NULL DEFAULT 0.00,
                stress_value DECIMAL(15,2) NOT NULL DEFAULT 0.00,
                total_return_pct DECIMAL(9,4) NOT NULL DEFAULT 0.0000,
                sharpe_ratio DECIMAL(9,4) NOT NULL DEFAULT 0.0000,
                score DECIMAL(15,4) NOT NULL DEFAULT 0.0000,
                rank_position INT NULL,
                PRIMARY KEY (id),
                UNIQUE KEY uk_room_user (room_id, user_id),
                KEY idx_room_participants_room (room_id),
                KEY idx_room_participants_user (user_id),
                CONSTRAINT fk_room_participant_room FOREIGN KEY (room_id) REFERENCES room_sessions (id) ON DELETE CASCADE,
                CONSTRAINT fk_room_participant_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS room_allowed_securities (
                room_id INT NOT NULL,
                security_id INT NOT NULL,
                PRIMARY KEY (room_id, security_id),
                KEY idx_room_allowed_security (security_id),
                CONSTRAINT fk_room_allowed_room FOREIGN KEY (room_id) REFERENCES room_sessions (id) ON DELETE CASCADE,
                CONSTRAINT fk_room_allowed_security FOREIGN KEY (security_id) REFERENCES securities (id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS room_portfolios (
                id INT NOT NULL AUTO_INCREMENT,
                room_id INT NOT NULL,
                user_id INT NOT NULL,
                initial_balance DECIMAL(15,2) NOT NULL DEFAULT 1000000.00,
                current_cash DECIMAL(15,2) NOT NULL DEFAULT 1000000.00,
                created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uk_room_portfolio (room_id, user_id),
                CONSTRAINT fk_room_portfolio_room FOREIGN KEY (room_id) REFERENCES room_sessions (id) ON DELETE CASCADE,
                CONSTRAINT fk_room_portfolio_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS room_portfolio_items (
                id INT NOT NULL AUTO_INCREMENT,
                room_portfolio_id INT NOT NULL,
                security_id INT NOT NULL,
                quantity DECIMAL(12,4) NOT NULL,
                avg_buy_price DECIMAL(12,4) NOT NULL,
                first_bought_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uk_room_portfolio_security (room_portfolio_id, security_id),
                CONSTRAINT fk_room_portfolio_items_portfolio FOREIGN KEY (room_portfolio_id) REFERENCES room_portfolios (id) ON DELETE CASCADE,
                CONSTRAINT fk_room_portfolio_items_security FOREIGN KEY (security_id) REFERENCES securities (id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS room_transactions (
                id BIGINT NOT NULL AUTO_INCREMENT,
                room_portfolio_id INT NOT NULL,
                security_id INT NOT NULL,
                tx_type ENUM('BUY','SELL') NOT NULL,
                quantity DECIMAL(12,4) NOT NULL,
                price DECIMAL(12,4) NOT NULL,
                executed_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
                market_price_at_tx DECIMAL(12,4) DEFAULT NULL,
                PRIMARY KEY (id),
                KEY idx_room_tx_portfolio (room_portfolio_id, executed_at),
                CONSTRAINT fk_room_transactions_portfolio FOREIGN KEY (room_portfolio_id) REFERENCES room_portfolios (id) ON DELETE CASCADE,
                CONSTRAINT fk_room_transactions_security FOREIGN KEY (security_id) REFERENCES securities (id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        if self._table_exists("portfolios"):
            cursor.execute(
                f"""
                ALTER TABLE portfolios
                MODIFY COLUMN initial_balance DECIMAL(15,2) NOT NULL DEFAULT {Config.INITIAL_BALANCE:.2f},
                MODIFY COLUMN current_cash DECIMAL(15,2) NOT NULL DEFAULT {Config.INITIAL_BALANCE:.2f}
                """
            )
            cursor.execute(
                """
                UPDATE portfolios p
                LEFT JOIN portfolio_items pi ON pi.portfolio_id = p.id
                LEFT JOIN transactions t ON t.portfolio_id = p.id
                SET
                    p.initial_balance = %s,
                    p.current_cash = %s
                WHERE p.initial_balance IN (100000.00, 1000000.00)
                  AND p.current_cash IN (100000.00, 1000000.00)
                  AND pi.id IS NULL
                  AND t.id IS NULL
                """,
                (Config.INITIAL_BALANCE, Config.INITIAL_BALANCE),
            )
        
        # При старте приводим локальную учебную базу к одному виду: убираем устаревшие тикеры и скрываем служебные сценарии, которые не должны попадать в пользовательский интерфейс.
        self._migrate_yndx_to_ydex()
        self._migrate_vtb_to_vtbr()
        self._hide_unwanted_default_scenarios()
        
        self.get_conn().commit()
        cursor.close()

    def _migrate_yndx_to_ydex(self) -> None:
        """Мигрирует позиции с устаревшего YNDX на актуальный YDEX."""
        cursor = self._cursor()
        try:
            # Находим security_id для YDEX
            cursor.execute("SELECT id FROM securities WHERE secid = 'YDEX' LIMIT 1")
            ydex_row = cursor.fetchone()
            if not ydex_row:
                return
            ydex_id = ydex_row[0]
            
            # Находим security_id для YNDX (если есть)
            cursor.execute("SELECT id FROM securities WHERE secid = 'YNDX' LIMIT 1")
            yndx_row = cursor.fetchone()
            if not yndx_row:
                return
            yndx_id = yndx_row[0]
            
            # Обновляем только те позиции, где не возникнет конфликт уникальности.
            cursor.execute(
                """
                UPDATE portfolio_items
                SET security_id = %s
                WHERE security_id = %s
                  AND portfolio_id NOT IN (
                      SELECT blocked.portfolio_id
                      FROM (
                          SELECT portfolio_id
                          FROM portfolio_items
                          WHERE security_id = %s
                      ) AS blocked
                  )
                """,
                (ydex_id, yndx_id, ydex_id),
            )
            cursor.execute(
                """
                UPDATE room_portfolio_items
                SET security_id = %s
                WHERE security_id = %s
                  AND room_portfolio_id NOT IN (
                      SELECT blocked.room_portfolio_id
                      FROM (
                          SELECT room_portfolio_id
                          FROM room_portfolio_items
                          WHERE security_id = %s
                      ) AS blocked
                  )
                """,
                (ydex_id, yndx_id, ydex_id),
            )
            cursor.execute("UPDATE transactions SET security_id = %s WHERE security_id = %s", (ydex_id, yndx_id))
            cursor.execute("UPDATE room_transactions SET security_id = %s WHERE security_id = %s", (ydex_id, yndx_id))
            cursor.execute("UPDATE room_allowed_securities SET security_id = %s WHERE security_id = %s", (ydex_id, yndx_id))

            # Саму старую бумагу безопаснее скрыть, чем удалять: так мы не ломаем старые ссылки.
            cursor.execute("UPDATE securities SET is_active = 0 WHERE secid = 'YNDX'")

            logger.info("Применена миграция YNDX -> YDEX")
        except Exception as exc:
            logger.warning("Ошибка миграции YNDX -> YDEX: %s", exc)
        finally:
            cursor.close()

    def _migrate_vtb_to_vtbr(self) -> None:
        """Приводит старый или введённый вручную тикер VTB к биржевому VTBR."""
        cursor = self._cursor()
        try:
            cursor.execute("SELECT id FROM securities WHERE secid = 'VTBR' LIMIT 1")
            vtbr_row = cursor.fetchone()
            if not vtbr_row:
                return
            vtbr_id = vtbr_row[0]

            cursor.execute("SELECT id FROM securities WHERE secid = 'VTB' LIMIT 1")
            vtb_row = cursor.fetchone()
            if not vtb_row:
                return
            vtb_id = vtb_row[0]

            cursor.execute(
                """
                UPDATE portfolio_items
                SET security_id = %s
                WHERE security_id = %s
                  AND portfolio_id NOT IN (
                      SELECT portfolio_id
                      FROM portfolio_items
                      WHERE security_id = %s
                  )
                """,
                (vtbr_id, vtb_id, vtbr_id),
            )
            cursor.execute(
                """
                UPDATE room_portfolio_items
                SET security_id = %s
                WHERE security_id = %s
                  AND room_portfolio_id NOT IN (
                      SELECT room_portfolio_id
                      FROM room_portfolio_items
                      WHERE security_id = %s
                  )
                """,
                (vtbr_id, vtb_id, vtbr_id),
            )
            cursor.execute("UPDATE transactions SET security_id = %s WHERE security_id = %s", (vtbr_id, vtb_id))
            cursor.execute("UPDATE room_transactions SET security_id = %s WHERE security_id = %s", (vtbr_id, vtb_id))
            cursor.execute("UPDATE room_allowed_securities SET security_id = %s WHERE security_id = %s", (vtbr_id, vtb_id))
            cursor.execute("UPDATE securities SET is_active = 0 WHERE secid = 'VTB'")
            logger.info("Применена миграция VTB -> VTBR")
        except Exception as exc:
            logger.warning("Ошибка миграции VTB -> VTBR: %s", exc)
        finally:
            cursor.close()

    def _hide_unwanted_default_scenarios(self) -> None:
        """Скрывает автосценарии, которые не должны дублироваться в интерфейсе."""
        cursor = self._cursor()
        try:
            cursor.execute(
                """
                UPDATE stress_scenarios
                SET is_active = 0
                WHERE name = %s
                """,
                ("Рост ключевой ставки 2023",),
            )
            if self._has_column("stress_scenarios", "is_global") and self._has_column("stress_scenarios", "created_by"):
                # Старые пользовательские сценарии, созданные до разделения ролей, не должны висеть в общем списке стресс-тестов.
                cursor.execute(
                    """
                    UPDATE stress_scenarios
                    SET is_global = 0
                    WHERE created_by IS NULL
                      AND slug NOT IN ('crisis-2014', 'pandemic-2020', 'crisis-2022')
                    """
                )
        except Exception as exc:
            logger.warning("Не удалось скрыть лишние сценарии: %s", exc)
        finally:
            cursor.close()

    def _normalize_secid(self, secid: str | None) -> str:
        return SECID_ALIASES.get(str(secid or "").strip().upper(), str(secid or "").strip().upper())

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn and getattr(conn, "open", False):
            conn.close()
        self._local.conn = None

    #Работа со справочником бумаг

    def ensure_securities_seed(self) -> None:
        if not self._table_exists("securities"):
            return
        has_lot_size = self._has_column("securities", "lot_size")
        has_sector = self._has_column("securities", "sector")
        has_dividend = self._has_column("securities", "dividend_yield")
        has_volatility = self._has_column("securities", "volatility_level")
        cursor = self.get_conn().cursor()

        if has_lot_size and has_sector and has_dividend and has_volatility:
            cursor.executemany(
                """
                INSERT INTO securities (secid, shortname, sector, currency, lot_size, dividend_yield, volatility_level, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    shortname = VALUES(shortname),
                    sector = VALUES(sector),
                    currency = VALUES(currency),
                    lot_size = VALUES(lot_size),
                    dividend_yield = VALUES(dividend_yield),
                    volatility_level = VALUES(volatility_level),
                    is_active = 1
                """,
                [
                    (secid, name, sector, currency, lot_size, DEFAULT_DIVIDEND_YIELDS.get(secid), DEFAULT_VOLATILITY_LEVELS.get(secid))
                    for secid, name, sector, currency, lot_size in SECURITY_SEED
                ],
            )
        elif has_lot_size and has_sector:
            cursor.executemany(
                """
                INSERT INTO securities (secid, shortname, sector, currency, lot_size, is_active)
                VALUES (%s, %s, %s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    shortname = VALUES(shortname),
                    sector = VALUES(sector),
                    currency = VALUES(currency),
                    lot_size = VALUES(lot_size),
                    is_active = 1
                """,
                SECURITY_SEED,
            )
        elif has_lot_size:
            cursor.executemany(
                """
                INSERT INTO securities (secid, shortname, currency, lot_size, is_active)
                VALUES (%s, %s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    shortname = VALUES(shortname),
                    currency = VALUES(currency),
                    lot_size = VALUES(lot_size),
                    is_active = 1
                """,
                [(s, n, c, cur, lot) for s, n, c, cur, lot in SECURITY_SEED],
            )
        else:
            cursor.executemany(
                """
                INSERT INTO securities (secid, shortname, currency, is_active)
                VALUES (%s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    shortname = VALUES(shortname),
                    currency = VALUES(currency),
                    is_active = 1
                """,
                [(s, n, cur) for s, n, _c, cur, _lot in SECURITY_SEED],
            )
        self.get_conn().commit()
        cursor.close()

    def get_or_create_security_id(self, secid: str) -> int | None:
        secid = self._normalize_secid(secid)
        security = self.get_security_by_secid(secid)
        if security:
            return int(security["id"])

        if secid in SECURITY_SEED_BY_SECID:
            self.create_security_from_seed(secid)
            security = self.get_security_by_secid(secid)
            if security:
                return int(security["id"])

        seed = SECURITY_SEED_BY_SECID.get(secid)
        if seed and self._table_exists("securities"):
            _, shortname, sector, currency, lot_size = seed
            has_lot_size = self._has_column("securities", "lot_size")
            has_sector = self._has_column("securities", "sector")
            cursor = self._cursor()
            if has_lot_size and has_sector:
                cursor.execute(
                    """
                    INSERT INTO securities (secid, shortname, sector, currency, lot_size, is_active)
                    VALUES (%s, %s, %s, %s, %s, 1)
                    """,
                    (secid, shortname, sector, currency, lot_size),
                )
            elif has_lot_size:
                cursor.execute(
                    """
                    INSERT INTO securities (secid, shortname, currency, lot_size, is_active)
                    VALUES (%s, %s, %s, %s, 1)
                    """,
                    (secid, shortname, currency, lot_size),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO securities (secid, shortname, currency, is_active)
                    VALUES (%s, %s, %s, 1)
                    """,
                    (secid, shortname, currency),
                )
            self.get_conn().commit()
            cursor.close()
            security = self.get_security_by_secid(secid)
            if security:
                return int(security["id"])
        return None

    def get_securities(self):
        columns = ["id", "secid", "shortname", "currency"]
        if self._has_column("securities", "sector"):
            columns.append("sector")
        if self._has_column("securities", "lot_size"):
            columns.append("lot_size")
        if self._has_column("securities", "dividend_yield"):
            columns.append("dividend_yield")
        if self._has_column("securities", "volatility_level"):
            columns.append("volatility_level")
        cursor = self._execute(
            f"""
            SELECT {', '.join(columns)}
            FROM securities
            WHERE is_active = 1
              AND secid NOT IN ('YNDX', 'VTB')
            ORDER BY secid
            """,
            dictionary=True,
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows or []

    def get_active_tickers(self) -> list[str]:
        cursor = self._execute(
            """
            SELECT secid
            FROM securities
            WHERE is_active = 1
              AND secid NOT IN ('YNDX', 'VTB')
            ORDER BY secid
            """,
            dictionary=True,
        )
        rows = cursor.fetchall()
        cursor.close()
        return [str(row["secid"]).upper() for row in (rows or []) if row.get("secid")]

    def get_security_by_secid(self, secid: str):
        secid = self._normalize_secid(secid)
        self.ensure_securities_seed()
        columns = ["id", "secid", "shortname", "currency"]
        if self._has_column("securities", "sector"):
            columns.append("sector")
        if self._has_column("securities", "lot_size"):
            columns.append("lot_size")
        if self._has_column("securities", "dividend_yield"):
            columns.append("dividend_yield")
        if self._has_column("securities", "volatility_level"):
            columns.append("volatility_level")
        cursor = self._execute(
            f"SELECT {', '.join(columns)} FROM securities WHERE secid = %s AND is_active = 1",
            (secid,),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        if not row and secid in SECURITY_SEED_BY_SECID:
            self.create_security_from_seed(secid)
            cursor = self._execute(
                f"SELECT {', '.join(columns)} FROM securities WHERE secid = %s AND is_active = 1",
                (secid,),
                dictionary=True,
            )
            row = cursor.fetchone()
            cursor.close()
        return row

    def get_security_by_id(self, security_id: int):
        columns = ["id", "secid", "shortname", "currency"]
        if self._has_column("securities", "sector"):
            columns.append("sector")
        if self._has_column("securities", "lot_size"):
            columns.append("lot_size")
        if self._has_column("securities", "dividend_yield"):
            columns.append("dividend_yield")
        if self._has_column("securities", "volatility_level"):
            columns.append("volatility_level")
        cursor = self._execute(
            f"SELECT {', '.join(columns)} FROM securities WHERE id = %s LIMIT 1",
            (security_id,),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return row

    def create_security_from_seed(self, secid: str) -> None:
        secid = self._normalize_secid(secid)
        seed = SECURITY_SEED_BY_SECID.get(secid)
        if not seed:
            return
        _, shortname, sector, currency, lot_size = seed
        dividend_yield = DEFAULT_DIVIDEND_YIELDS.get(secid)
        volatility_level = DEFAULT_VOLATILITY_LEVELS.get(secid)
        has_lot_size = self._has_column("securities", "lot_size")
        has_sector = self._has_column("securities", "sector")
        has_dividend = self._has_column("securities", "dividend_yield")
        has_volatility = self._has_column("securities", "volatility_level")
        cursor = self._cursor()
        if has_lot_size and has_sector and has_dividend and has_volatility:
            cursor.execute(
                """
                INSERT INTO securities (secid, shortname, sector, currency, lot_size, dividend_yield, volatility_level, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    shortname = VALUES(shortname),
                    sector = VALUES(sector),
                    currency = VALUES(currency),
                    lot_size = VALUES(lot_size),
                    dividend_yield = VALUES(dividend_yield),
                    volatility_level = VALUES(volatility_level),
                    is_active = 1
                """,
                (secid, shortname, sector, currency, lot_size, dividend_yield, volatility_level),
            )
        elif has_lot_size and has_sector:
            cursor.execute(
                """
                INSERT INTO securities (secid, shortname, sector, currency, lot_size, is_active)
                VALUES (%s, %s, %s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    shortname = VALUES(shortname),
                    sector = VALUES(sector),
                    currency = VALUES(currency),
                    lot_size = VALUES(lot_size),
                    is_active = 1
                """,
                (secid, shortname, sector, currency, lot_size),
            )
        elif has_lot_size:
            cursor.execute(
                """
                INSERT INTO securities (secid, shortname, currency, lot_size, is_active)
                VALUES (%s, %s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    shortname = VALUES(shortname),
                    currency = VALUES(currency),
                    lot_size = VALUES(lot_size),
                    is_active = 1
                """,
                (secid, shortname, currency, lot_size),
            )
        else:
            cursor.execute(
                """
                INSERT INTO securities (secid, shortname, currency, is_active)
                VALUES (%s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    shortname = VALUES(shortname),
                    currency = VALUES(currency),
                    is_active = 1
                """,
                (secid, shortname, currency),
            )
        self.get_conn().commit()
        cursor.close()

    def create_security(
        self,
        secid: str,
        shortname: str,
        sector: str | None = None,
        currency: str = "RUB",
        lot_size: int = 1,
        dividend_yield: float | None = None,
        volatility_level: str | None = None,
    ):
        secid = self._normalize_secid(secid)
        has_dividend = self._has_column("securities", "dividend_yield")
        has_volatility = self._has_column("securities", "volatility_level")
        cursor = self._cursor()
        if has_dividend and has_volatility:
            cursor.execute(
                """
                INSERT INTO securities (secid, shortname, sector, currency, lot_size, dividend_yield, volatility_level, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    shortname = VALUES(shortname),
                    sector = VALUES(sector),
                    currency = VALUES(currency),
                    lot_size = VALUES(lot_size),
                    dividend_yield = VALUES(dividend_yield),
                    volatility_level = VALUES(volatility_level),
                    is_active = 1
                """,
                (secid, shortname, sector, currency, max(int(lot_size or 1), 1), dividend_yield, volatility_level),
            )
        else:
            cursor.execute(
                """
                INSERT INTO securities (secid, shortname, sector, currency, lot_size, is_active)
                VALUES (%s, %s, %s, %s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    shortname = VALUES(shortname),
                    sector = VALUES(sector),
                    currency = VALUES(currency),
                    lot_size = VALUES(lot_size),
                    is_active = 1
                """,
                (secid, shortname, sector, currency, max(int(lot_size or 1), 1)),
            )
        self.get_conn().commit()
        cursor.close()
        return self.get_security_by_secid(secid)

    def update_security(
        self,
        security_id: int,
        secid: str,
        shortname: str,
        sector: str | None = None,
        currency: str = "RUB",
        lot_size: int = 1,
        dividend_yield: float | None = None,
        volatility_level: str | None = None,
    ):
        secid = self._normalize_secid(secid)
        has_dividend = self._has_column("securities", "dividend_yield")
        has_volatility = self._has_column("securities", "volatility_level")
        cursor = self._cursor()
        if has_dividend and has_volatility:
            cursor.execute(
                """
                UPDATE securities
                SET secid = %s,
                    shortname = %s,
                    sector = %s,
                    currency = %s,
                    lot_size = %s,
                    dividend_yield = %s,
                    volatility_level = %s,
                    is_active = 1
                WHERE id = %s
                """,
                (secid, shortname, sector, currency, max(int(lot_size or 1), 1), dividend_yield, volatility_level, security_id),
            )
        else:
            cursor.execute(
                """
                UPDATE securities
                SET secid = %s,
                    shortname = %s,
                    sector = %s,
                    currency = %s,
                    lot_size = %s,
                    is_active = 1
                WHERE id = %s
                """,
                (secid, shortname, sector, currency, max(int(lot_size or 1), 1), security_id),
            )
        self.get_conn().commit()
        cursor.close()
        return self.get_security_by_id(security_id)

    def deactivate_security(self, security_id: int) -> bool:
        cursor = self._execute(
            """
            UPDATE securities
            SET is_active = 0
            WHERE id = %s
            """,
            (security_id,),
        )
        self.get_conn().commit()
        changed = cursor.rowcount
        cursor.close()
        return changed > 0

    #Портфели и сделки

    def _build_portfolio_view(self, portfolio: dict, items: list[dict] | None) -> dict:
        return {
            "id": portfolio["id"],
            "initial_balance": float(portfolio["initial_balance"]),
            "current_cash": float(portfolio["current_cash"]),
            "items": items or [],
        }

    def _load_portfolio_items(self, portfolio_table_key: str, items_table: str, portfolio_id: int):
        cursor = self._execute(
            f"""
            SELECT
                pi.security_id,
                s.secid,
                s.shortname,
                COALESCE(s.sector, '') AS sector,
                COALESCE(s.lot_size, 1) AS lot_size,
                pi.quantity,
                pi.avg_buy_price,
                pi.first_bought_at
            FROM {items_table} pi
            JOIN securities s ON s.id = pi.security_id
            WHERE pi.{portfolio_table_key} = %s
            ORDER BY s.secid
            """,
            (portfolio_id,),
            dictionary=True,
        )
        items = cursor.fetchall()
        cursor.close()
        return items or []

    def _count_positions(self, items_table: str, portfolio_key: str, portfolio_id: int) -> int:
        cursor = self._execute(
            f"SELECT COUNT(*) AS c FROM {items_table} WHERE {portfolio_key} = %s",
            (portfolio_id,),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return int((row or {}).get("c") or 0)

    def _position_exists(self, items_table: str, portfolio_key: str, portfolio_id: int, security_id: int) -> bool:
        cursor = self._execute(
            f"SELECT 1 AS present FROM {items_table} WHERE {portfolio_key} = %s AND security_id = %s LIMIT 1",
            (portfolio_id, security_id),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return bool(row)

    def get_or_create_portfolio(self, user_id: int):
        if not self.get_user_by_id(int(user_id)):
            raise ValueError("Пользователь не найден или не активен")
        cursor = self._execute(
            "SELECT id, initial_balance, current_cash FROM portfolios WHERE user_id = %s LIMIT 1",
            (user_id,),
            dictionary=True,
        )
        portfolio = cursor.fetchone()
        if not portfolio:
            cursor.execute(
                """
                INSERT INTO portfolios (user_id, initial_balance, current_cash, name)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, Config.INITIAL_BALANCE, Config.INITIAL_BALANCE, "Мой портфель"),
            )
            self.get_conn().commit()
            portfolio = {
                "id": cursor.lastrowid,
                "initial_balance": Config.INITIAL_BALANCE,
                "current_cash": Config.INITIAL_BALANCE,
            }
        cursor.close()
        return portfolio

    def get_portfolio(self, user_id: int):
        portfolio = self.get_or_create_portfolio(user_id)
        items = self._load_portfolio_items("portfolio_id", "portfolio_items", portfolio["id"])
        return self._build_portfolio_view(portfolio, items)

    def get_or_create_room_portfolio(self, room_id: int, user_id: int):
        room = self.get_room_by_id(room_id)
        if not room:
            return None
        cursor = self._execute(
            """
            SELECT id, initial_balance, current_cash
            FROM room_portfolios
            WHERE room_id = %s AND user_id = %s
            LIMIT 1
            """,
            (room_id, user_id),
            dictionary=True,
        )
        portfolio = cursor.fetchone()
        if not portfolio:
            initial_balance = float(room.get("initial_balance") or Config.INITIAL_BALANCE)
            cursor.execute(
                """
                INSERT INTO room_portfolios (room_id, user_id, initial_balance, current_cash)
                VALUES (%s, %s, %s, %s)
                """,
                (room_id, user_id, initial_balance, initial_balance),
            )
            self.get_conn().commit()
            portfolio = {
                "id": cursor.lastrowid,
                "initial_balance": initial_balance,
                "current_cash": initial_balance,
            }
        cursor.close()
        return portfolio

    def get_room_portfolio(self, room_id: int, user_id: int):
        portfolio = self.get_or_create_room_portfolio(room_id, user_id)
        if not portfolio:
            return None
        items = self._load_portfolio_items("room_portfolio_id", "room_portfolio_items", portfolio["id"])
        return self._build_portfolio_view(portfolio, items)

    def _resolve_security_trade_context(self, secid: str):
        secid = (secid or "").strip().upper()
        security = self.get_security_by_secid(secid)
        security_id = int(security["id"]) if security else None
        lot_size = int((security or {}).get("lot_size") or 1)
        return secid, security, security_id, lot_size

    def _validate_lot_count(self, lots: int) -> dict | None:
        if lots <= 0:
            return {"success": False, "error": "Количество лотов должно быть положительным"}
        return None

    def _insert_or_update_position(
        self,
        cursor,
        *,
        items_table: str,
        portfolio_key: str,
        portfolio_id: int,
        security_id: int,
        quantity: float,
        price: float,
    ) -> None:
        cursor.execute(
            f"""
            SELECT quantity, avg_buy_price
            FROM {items_table}
            WHERE {portfolio_key} = %s AND security_id = %s
            """,
            (portfolio_id, security_id),
        )
        existing = cursor.fetchone()

        if existing:
            old_qty = float(existing["quantity"])
            old_price = float(existing["avg_buy_price"])
            new_qty = old_qty + quantity
            avg_buy_price = ((old_qty * old_price) + (quantity * price)) / new_qty
            cursor.execute(
                f"""
                UPDATE {items_table}
                SET quantity = %s, avg_buy_price = %s, updated_at = NOW()
                WHERE {portfolio_key} = %s AND security_id = %s
                """,
                (new_qty, avg_buy_price, portfolio_id, security_id),
            )
            return

        cursor.execute(
            f"""
            INSERT INTO {items_table} ({portfolio_key}, security_id, quantity, avg_buy_price)
            VALUES (%s, %s, %s, %s)
            """,
            (portfolio_id, security_id, quantity, price),
        )

    def _decrease_or_remove_position(
        self,
        cursor,
        *,
        items_table: str,
        portfolio_key: str,
        portfolio_id: int,
        security_id: int,
        quantity: float,
        lot_size: int,
        missing_error: str,
        insufficient_error_builder,
    ) -> tuple[bool, dict | None]:
        cursor.execute(
            f"""
            SELECT quantity
            FROM {items_table}
            WHERE {portfolio_key} = %s AND security_id = %s
            """,
            (portfolio_id, security_id),
        )
        existing = cursor.fetchone()
        if not existing:
            return False, {"success": False, "error": missing_error}

        current_qty = float(existing["quantity"])
        if float(quantity) > current_qty:
            available_lots = int(current_qty / lot_size)
            return False, insufficient_error_builder(available_lots, current_qty)

        new_qty = current_qty - float(quantity)
        if new_qty <= 0:
            cursor.execute(
                f"DELETE FROM {items_table} WHERE {portfolio_key} = %s AND security_id = %s",
                (portfolio_id, security_id),
            )
        else:
            cursor.execute(
                f"""
                UPDATE {items_table}
                SET quantity = %s, updated_at = NOW()
                WHERE {portfolio_key} = %s AND security_id = %s
                """,
                (new_qty, portfolio_id, security_id),
            )
        return True, None

    def _record_trade(
        self,
        cursor,
        *,
        portfolios_table: str,
        transactions_table: str,
        portfolio_id_column: str,
        portfolio_id: int,
        security_id: int,
        tx_type: str,
        quantity: float,
        price: float,
        new_cash: float,
    ) -> None:
        cursor.execute(
            f"UPDATE {portfolios_table} SET current_cash = %s, updated_at = NOW() WHERE id = %s",
            (new_cash, portfolio_id),
        )
        cursor.execute(
            f"""
            INSERT INTO {transactions_table} ({portfolio_id_column}, security_id, tx_type, quantity, price, market_price_at_tx)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (portfolio_id, security_id, tx_type, quantity, price, price),
        )

    def buy_stock(self, user_id: int, secid: str, lots: int, price: float):
        """Покупка акций в личный портфель.

        Во внешнем API используется количество лотов, а внутри БД хранится
        фактическое число акций. Поэтому здесь сначала переводим лоты в штуки.
        """
        portfolio = self.get_or_create_portfolio(user_id)
        secid, _security, security_id, lot_size = self._resolve_security_trade_context(secid)
        if not security_id:
            logger.error("buy_stock: security %s not found after seed sync", secid)
            return {"success": False, "error": "Бумага не найдена"}
        validation_error = self._validate_lot_count(lots)
        if validation_error:
            return validation_error

        quantity = lots * lot_size
        total_cost = float(quantity) * float(price)
        cash = float(portfolio["current_cash"])
        if total_cost > cash:
            return {
                "success": False,
                "error": f"Недостаточно средств. Нужно {total_cost:.2f} ₽, доступно {cash:.2f} ₽",
            }
        if not self._position_exists("portfolio_items", "portfolio_id", portfolio["id"], security_id):
            positions_count = self._count_positions("portfolio_items", "portfolio_id", portfolio["id"])
            if positions_count >= 10:
                return {
                    "success": False,
                    "error": "По правилам тренажёра в портфеле может быть не больше 10 разных активов.",
                }

        cursor = self._cursor(dictionary=True)
        self._insert_or_update_position(
            cursor,
            items_table="portfolio_items",
            portfolio_key="portfolio_id",
            portfolio_id=portfolio["id"],
            security_id=security_id,
            quantity=quantity,
            price=price,
        )
        self._record_trade(
            cursor,
            portfolios_table="portfolios",
            transactions_table="transactions",
            portfolio_id_column="portfolio_id",
            portfolio_id=portfolio["id"],
            security_id=security_id,
            tx_type="BUY",
            quantity=quantity,
            price=price,
            new_cash=cash - total_cost,
        )
        self.get_conn().commit()
        cursor.close()
        return {
            "success": True, "secid": secid, "lots": lots, "quantity": quantity,
            "lot_size": lot_size, "executed_price": price,
            "total_cost": total_cost, "cash_remaining": cash - total_cost,
        }

    def sell_stock(self, user_id: int, secid: str, lots: int, price: float):
        """Продажа акций. lots — количество лотов, price — цена за акцию."""
        portfolio = self.get_or_create_portfolio(user_id)
        secid, _security, security_id, lot_size = self._resolve_security_trade_context(secid)
        if not security_id:
            logger.error("sell_stock: security %s not found after seed sync", secid)
            return {"success": False, "error": "Бумага не найдена"}
        validation_error = self._validate_lot_count(lots)
        if validation_error:
            return validation_error

        quantity = lots * lot_size

        cursor = self._cursor(dictionary=True)
        updated, error = self._decrease_or_remove_position(
            cursor,
            items_table="portfolio_items",
            portfolio_key="portfolio_id",
            portfolio_id=portfolio["id"],
            security_id=security_id,
            quantity=quantity,
            lot_size=lot_size,
            missing_error="Бумага отсутствует в портфеле",
            insufficient_error_builder=lambda available_lots, current_qty: {
                "success": False,
                "error": f"Недостаточно бумаг. В портфеле {available_lots} лот. ({current_qty:.0f} шт.)",
            },
        )
        if not updated:
            cursor.close()
            return error

        revenue = float(quantity) * float(price)
        cash = float(portfolio["current_cash"])
        self._record_trade(
            cursor,
            portfolios_table="portfolios",
            transactions_table="transactions",
            portfolio_id_column="portfolio_id",
            portfolio_id=portfolio["id"],
            security_id=security_id,
            tx_type="SELL",
            quantity=quantity,
            price=price,
            new_cash=cash + revenue,
        )
        self.get_conn().commit()
        cursor.close()
        return {
            "success": True, "secid": secid, "lots": lots, "quantity": quantity,
            "lot_size": lot_size, "executed_price": price,
            "total_revenue": revenue, "cash_remaining": cash + revenue,
        }

    def room_buy_stock(self, room_id: int, user_id: int, secid: str, lots: int, price: float):
        portfolio = self.get_or_create_room_portfolio(room_id, user_id)
        secid, _security, security_id, lot_size = self._resolve_security_trade_context(secid)
        if not portfolio or not security_id:
            return {"success": False, "error": "Бумага не найдена"}
        validation_error = self._validate_lot_count(lots)
        if validation_error:
            return validation_error

        quantity = lots * lot_size
        total_cost = float(quantity) * float(price)
        cash = float(portfolio["current_cash"])
        if total_cost > cash:
            return {"success": False, "error": f"Недостаточно средств. Нужно {total_cost:.2f} ₽, доступно {cash:.2f} ₽"}
        if not self._position_exists("room_portfolio_items", "room_portfolio_id", portfolio["id"], security_id):
            positions_count = self._count_positions("room_portfolio_items", "room_portfolio_id", portfolio["id"])
            if positions_count >= 10:
                return {
                    "success": False,
                    "error": "В комнате можно держать не больше 10 разных активов одновременно.",
                }

        cursor = self._cursor(dictionary=True)
        self._insert_or_update_position(
            cursor,
            items_table="room_portfolio_items",
            portfolio_key="room_portfolio_id",
            portfolio_id=portfolio["id"],
            security_id=security_id,
            quantity=quantity,
            price=price,
        )
        self._record_trade(
            cursor,
            portfolios_table="room_portfolios",
            transactions_table="room_transactions",
            portfolio_id_column="room_portfolio_id",
            portfolio_id=portfolio["id"],
            security_id=security_id,
            tx_type="BUY",
            quantity=quantity,
            price=price,
            new_cash=cash - total_cost,
        )
        self.get_conn().commit()
        cursor.close()
        return {
            "success": True, "secid": secid, "lots": lots, "quantity": quantity,
            "lot_size": lot_size, "executed_price": price,
            "total_cost": total_cost, "cash_remaining": cash - total_cost,
        }

    def room_sell_stock(self, room_id: int, user_id: int, secid: str, lots: int, price: float):
        portfolio = self.get_or_create_room_portfolio(room_id, user_id)
        secid, _security, security_id, lot_size = self._resolve_security_trade_context(secid)
        if not portfolio or not security_id:
            return {"success": False, "error": "Бумага не найдена"}
        validation_error = self._validate_lot_count(lots)
        if validation_error:
            return validation_error
        quantity = lots * lot_size

        cursor = self._cursor(dictionary=True)
        updated, error = self._decrease_or_remove_position(
            cursor,
            items_table="room_portfolio_items",
            portfolio_key="room_portfolio_id",
            portfolio_id=portfolio["id"],
            security_id=security_id,
            quantity=quantity,
            lot_size=lot_size,
            missing_error="Бумага отсутствует в портфеле комнаты",
            insufficient_error_builder=lambda available_lots, _current_qty: {
                "success": False,
                "error": f"Недостаточно бумаг. В комнате доступно {available_lots} лот.",
            },
        )
        if not updated:
            cursor.close()
            return error

        revenue = float(quantity) * float(price)
        cash = float(portfolio["current_cash"])
        self._record_trade(
            cursor,
            portfolios_table="room_portfolios",
            transactions_table="room_transactions",
            portfolio_id_column="room_portfolio_id",
            portfolio_id=portfolio["id"],
            security_id=security_id,
            tx_type="SELL",
            quantity=quantity,
            price=price,
            new_cash=cash + revenue,
        )
        self.get_conn().commit()
        cursor.close()
        return {
            "success": True, "secid": secid, "lots": lots, "quantity": quantity,
            "lot_size": lot_size, "executed_price": price,
            "total_revenue": revenue, "cash_remaining": cash + revenue,
        }

    def get_user_by_username(self, username: str):
        cursor = self._execute(
            """
            SELECT id, username, email, school_name, password_hash, is_admin, is_teacher, is_active
            FROM users
            WHERE username = %s AND is_active = 1
            """,
            (username,),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return row

    def get_user_by_email(self, email: str):
        cursor = self._execute(
            """
            SELECT id, username, email, school_name, password_hash, is_admin, is_teacher, is_active
            FROM users
            WHERE email = %s AND is_active = 1
            """,
            (email,),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return row

    def get_user_by_id(self, user_id: int):
        cursor = self._execute(
            """
            SELECT id, username, email, school_name, password_hash, is_admin, is_teacher, is_active
            FROM users
            WHERE id = %s AND is_active = 1
            """,
            (user_id,),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return row

    def get_active_users_for_global_leaderboard(self) -> list[dict]:
        cursor = self._execute(
            """
            SELECT
                u.id AS user_id,
                u.username,
                COUNT(t.id) AS transactions_count,
                MAX(t.executed_at) AS last_activity_at
            FROM users u
            LEFT JOIN portfolios p ON p.user_id = u.id
            LEFT JOIN transactions t ON t.portfolio_id = p.id
            WHERE u.is_active = 1
            GROUP BY u.id, u.username
            ORDER BY u.username ASC
            """,
            dictionary=True,
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows or []

    def create_user(
        self,
        username: str,
        email: str,
        password_hash: str,
        is_teacher: bool = False,
        school_name: str | None = None,
    ):
        cursor = self._cursor(dictionary=True)
        cursor.execute(
            """
            INSERT INTO users (username, email, school_name, password_hash, is_active, is_admin, is_teacher)
            VALUES (%s, %s, %s, %s, 1, 0, %s)
            """,
            (username, email, school_name, password_hash, 1 if is_teacher else 0),
        )
        self.get_conn().commit()
        user_id = cursor.lastrowid
        cursor.close()
        self.get_or_create_portfolio(user_id)
        return {
            "id": user_id, "username": username, "email": email, "school_name": school_name,
            "is_admin": 0, "is_teacher": 1 if is_teacher else 0,
        }

    def create_school_access_code(self, school_name: str, created_by: int | None = None, school_code: str | None = None):
        normalized_name = str(school_name or "").strip()
        code = str(school_code or "").strip().upper() or self._generate_school_access_code()
        cursor = self._cursor(dictionary=True)
        cursor.execute(
            """
            INSERT INTO school_access_codes (school_name, school_code, is_active, created_by)
            VALUES (%s, %s, 1, %s)
            """,
            (normalized_name, code, created_by),
        )
        self.get_conn().commit()
        code_id = cursor.lastrowid
        cursor.close()
        return {"id": code_id, "school_name": normalized_name, "school_code": code, "is_active": 1}

    def list_school_access_codes(self):
        cursor = self._execute(
            """
            SELECT sac.id, sac.school_name, sac.school_code, sac.is_active, sac.created_at, sac.last_used_at,
                   u.username AS created_by_username
            FROM school_access_codes sac
            LEFT JOIN users u ON u.id = sac.created_by
            ORDER BY sac.created_at DESC, sac.id DESC
            """,
            dictionary=True,
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows or []

    def validate_school_access_code(self, school_name: str, school_code: str):
        cursor = self._execute(
            """
            SELECT id, school_name, school_code, is_active
            FROM school_access_codes
            WHERE LOWER(TRIM(school_name)) = LOWER(TRIM(%s))
              AND school_code = %s
              AND is_active = 1
            LIMIT 1
            """,
            (school_name, school_code),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return row

    def mark_school_access_code_used(self, code_id: int) -> None:
        cursor = self._execute(
            "UPDATE school_access_codes SET last_used_at = NOW() WHERE id = %s",
            (code_id,),
        )
        self.get_conn().commit()
        cursor.close()

    def _generate_school_access_code(self, length: int = 8) -> str:
        while True:
            code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(max(length, 6)))
            cursor = self._execute(
                "SELECT id FROM school_access_codes WHERE school_code = %s",
                (code,),
            )
            row = cursor.fetchone()
            cursor.close()
            if not row:
                return code

    def update_last_login(self, user_id: int) -> None:
        cursor = self._execute(
            "UPDATE users SET last_login = NOW() WHERE id = %s",
            (user_id,),
        )
        self.get_conn().commit()
        cursor.close()

    # ===== Пользователи, школы и сценарии =====

    def get_stress_scenarios(self, created_by: int | None = None, include_global: bool = True):
        conditions = ["is_active = 1"]
        params: list[Any] = []

        if self._has_column("stress_scenarios", "is_global") and self._has_column("stress_scenarios", "created_by"):
            if created_by is None:
                if include_global:
                    conditions.append("is_global = 1")
            else:
                if include_global:
                    conditions.append("(is_global = 1 OR created_by = %s)")
                    params.append(created_by)
                else:
                    conditions.append("created_by = %s")
                    params.append(created_by)

        cursor = self._execute(
            f"""
            SELECT id, name, slug, start_date, end_date, description, coefficients, market_context,
                   { 'created_by, is_global,' if self._has_column('stress_scenarios', 'created_by') and self._has_column('stress_scenarios', 'is_global') else '' }
                   created_at
            FROM stress_scenarios
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at ASC
            """,
            tuple(params),
            dictionary=True,
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows or []

    def get_stress_scenario(self, slug: str, created_by: int | None = None, include_global: bool = True):
        conditions = ["slug = %s", "is_active = 1"]
        params: list[Any] = [slug]

        if self._has_column("stress_scenarios", "is_global") and self._has_column("stress_scenarios", "created_by"):
            if created_by is None:
                if include_global:
                    conditions.append("is_global = 1")
            else:
                if include_global:
                    conditions.append("(is_global = 1 OR created_by = %s)")
                    params.append(created_by)
                else:
                    conditions.append("created_by = %s")
                    params.append(created_by)

        cursor = self._execute(
            f"""
            SELECT id, name, slug, start_date, end_date, description, coefficients, market_context
            FROM stress_scenarios
            WHERE {' AND '.join(conditions)}
            LIMIT 1
            """,
            tuple(params),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return row

    def get_stress_scenario_by_id(self, scenario_id: int):
        cursor = self._execute(
            """
            SELECT id, name, slug, start_date, end_date, description, coefficients, market_context,
                   created_by, is_global, is_active
            FROM stress_scenarios
            WHERE id = %s
            LIMIT 1
            """,
            (scenario_id,),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return row

    def create_stress_scenario(
        self,
        name: str,
        description: str,
        coefficients: dict[str, float],
        start_date=None,
        end_date=None,
        created_by: int | None = None,
        is_global: bool = True,
    ):
        slug = self._make_slug(name)
        cursor = self._cursor()
        try:
            if self._has_column("stress_scenarios", "created_by") and self._has_column("stress_scenarios", "is_global"):
                cursor.execute(
                    """
                    INSERT INTO stress_scenarios (name, slug, start_date, end_date, description, coefficients, created_by, is_global, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        name,
                        slug,
                        start_date,
                        end_date,
                        description,
                        json.dumps(coefficients, ensure_ascii=False),
                        created_by,
                        1 if is_global else 0,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO stress_scenarios (name, slug, start_date, end_date, description, coefficients, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, 1)
                    """,
                    (name, slug, start_date, end_date, description, json.dumps(coefficients, ensure_ascii=False)),
                )
            self.get_conn().commit()
            scenario_id = cursor.lastrowid
            return {"id": scenario_id, "name": name, "slug": slug, "start_date": start_date, "end_date": end_date}
        except IntegrityError as exc:
            self.get_conn().rollback()
            if getattr(exc, "args", None) and len(exc.args) > 0 and int(exc.args[0]) == 1062:
                existing = self.get_stress_scenario(slug, created_by=created_by) or self.get_stress_scenario(slug)
                if existing:
                    logger.info("Stress scenario with slug %s already exists, reusing it", slug)
                    return {
                        "id": existing["id"],
                        "name": existing["name"],
                        "slug": existing["slug"],
                        "start_date": existing.get("start_date"),
                        "end_date": existing.get("end_date"),
                    }
            raise
        finally:
            cursor.close()

    def update_stress_scenario(
        self,
        scenario_id: int,
        name: str,
        description: str,
        coefficients: dict[str, float],
        start_date=None,
        end_date=None,
        created_by: int | None = None,
        is_global: bool | None = None,
    ):
        current = self.get_stress_scenario_by_id(scenario_id)
        if not current:
            return None

        slug = current["slug"]
        cursor = self._cursor()
        if self._has_column("stress_scenarios", "created_by") and self._has_column("stress_scenarios", "is_global"):
            cursor.execute(
                """
                UPDATE stress_scenarios
                SET name = %s,
                    start_date = %s,
                    end_date = %s,
                    description = %s,
                    coefficients = %s,
                    created_by = %s,
                    is_global = %s,
                    is_active = 1
                WHERE id = %s
                """,
                (
                    name,
                    start_date,
                    end_date,
                    description,
                    json.dumps(coefficients, ensure_ascii=False),
                    created_by if created_by is not None else current.get("created_by"),
                    1 if (is_global if is_global is not None else current.get("is_global", 1)) else 0,
                    scenario_id,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE stress_scenarios
                SET name = %s,
                    start_date = %s,
                    end_date = %s,
                    description = %s,
                    coefficients = %s,
                    is_active = 1
                WHERE id = %s
                """,
                (name, start_date, end_date, description, json.dumps(coefficients, ensure_ascii=False), scenario_id),
            )
        self.get_conn().commit()
        cursor.close()
        updated = self.get_stress_scenario_by_id(scenario_id)
        if updated:
            updated["slug"] = slug
        return updated

    def deactivate_stress_scenario(self, scenario_id: int) -> bool:
        cursor = self._execute(
            """
            UPDATE stress_scenarios
            SET is_active = 0
            WHERE id = %s
            """,
            (scenario_id,),
        )
        self.get_conn().commit()
        changed = cursor.rowcount
        cursor.close()
        return changed > 0

    def _make_slug(self, value: str) -> str:
        base = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
        base = "-".join(filter(None, base.split("-"))) or "scenario"
        candidate = base
        index = 1
        cursor = self._execute(
            "SELECT id FROM stress_scenarios WHERE slug = %s LIMIT 1",
            (candidate,),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        while row:
            index += 1
            candidate = f"{base}-{index}"
            cursor = self._execute(
                "SELECT id FROM stress_scenarios WHERE slug = %s LIMIT 1",
                (candidate,),
                dictionary=True,
            )
            row = cursor.fetchone()
            cursor.close()
        return candidate

    def create_room(self, teacher_id: int, title: str, description: str | None,
                   scenario_slug: str | None, duration_minutes: int, starts_at: datetime | None = None,
                   allowed_secids: list[str] | None = None, initial_balance: float | None = None,
                   scenario_owner_id: int | None = None):
        scenario = self.get_stress_scenario(scenario_slug, created_by=scenario_owner_id) if scenario_slug else None
        room_code = self._generate_room_code()
        starts_at = starts_at or datetime.now()
        ends_at = starts_at + timedelta(minutes=max(duration_minutes, 1))
        mode = "stress" if scenario else "practice"

        cursor = self._cursor()
        cursor.execute(
            """
            INSERT INTO room_sessions
                (teacher_id, title, description, room_code, mode, scenario_id, starts_at, ends_at, initial_balance, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
            """,
            (teacher_id, title, description, room_code, mode,
             scenario["id"] if scenario else None, starts_at, ends_at,
             float(initial_balance or Config.INITIAL_BALANCE)),
        )
        self.get_conn().commit()
        room_id = cursor.lastrowid
        cursor.close()
        if allowed_secids is not None:
            self.set_room_allowed_securities(room_id, allowed_secids)
        return self.get_room_by_id(room_id)

    def find_teacher_room_overlap(self, teacher_id: int, starts_at: datetime, ends_at: datetime):
        cursor = self._execute(
            """
            SELECT id, title, starts_at, ends_at
            FROM room_sessions
            WHERE teacher_id = %s
              AND is_active = 1
              AND starts_at < %s
              AND ends_at > %s
            ORDER BY starts_at ASC
            LIMIT 1
            """,
            (teacher_id, ends_at, starts_at),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return row

    def _generate_room_code(self) -> str:
        while True:
            code = "".join(
                secrets.choice(string.ascii_uppercase + string.digits)
                for _ in range(Config.ROOM_CODE_LENGTH)
            )
            cursor = self._execute(
                "SELECT id FROM room_sessions WHERE room_code = %s",
                (code,),
                dictionary=True,
            )
            exists = cursor.fetchone()
            cursor.close()
            if not exists:
                return code

    def get_room_by_id(self, room_id: int):
        cursor = self._execute(
            """
            SELECT
                rs.id, rs.teacher_id, rs.title, rs.description, rs.room_code, rs.mode,
                rs.starts_at, rs.ends_at, rs.initial_balance, rs.is_active,
                ss.id AS scenario_id, ss.name AS scenario_name, ss.slug AS scenario_slug,
                ss.description AS scenario_description,
                ss.start_date AS scenario_start_date, ss.end_date AS scenario_end_date,
                GROUP_CONCAT(s.secid ORDER BY s.secid SEPARATOR ',') AS allowed_secids
            FROM room_sessions rs
            LEFT JOIN stress_scenarios ss ON ss.id = rs.scenario_id
            LEFT JOIN room_allowed_securities ras ON ras.room_id = rs.id
            LEFT JOIN securities s ON s.id = ras.security_id
            WHERE rs.id = %s
            GROUP BY rs.id
            """,
            (room_id,),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return row

    def get_room_by_code(self, room_code: str):
        cursor = self._execute(
            """
            SELECT
                rs.id, rs.teacher_id, rs.title, rs.description, rs.room_code, rs.mode,
                rs.starts_at, rs.ends_at, rs.initial_balance, rs.is_active,
                ss.id AS scenario_id, ss.name AS scenario_name, ss.slug AS scenario_slug,
                ss.description AS scenario_description,
                ss.start_date AS scenario_start_date, ss.end_date AS scenario_end_date,
                GROUP_CONCAT(s.secid ORDER BY s.secid SEPARATOR ',') AS allowed_secids
            FROM room_sessions rs
            LEFT JOIN stress_scenarios ss ON ss.id = rs.scenario_id
            LEFT JOIN room_allowed_securities ras ON ras.room_id = rs.id
            LEFT JOIN securities s ON s.id = ras.security_id
            WHERE rs.room_code = %s
            GROUP BY rs.id
            LIMIT 1
            """,
            (room_code,),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return row

    def join_room(self, user_id: int, room_code: str):
        room = self.get_room_by_code(room_code)
        if not room:
            return {"success": False, "error": "Комната с таким кодом не найдена. Проверьте код и попробуйте ещё раз."}
        if not room["is_active"]:
            return {"success": False, "error": "Комната уже закрыта"}
        if room["ends_at"] < datetime.now():
            return {"success": False, "error": "Время комнаты уже истекло"}

        cursor = self._cursor()
        cursor.execute(
            """
            INSERT INTO room_participants (room_id, user_id)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE joined_at = joined_at
            """,
            (room["id"], user_id),
        )
        cursor.execute(
            "UPDATE users SET room_joined_at = NOW() WHERE id = %s",
            (user_id,),
        )
        self.get_conn().commit()
        cursor.close()
        return {"success": True, "room": room}

    def get_teacher_rooms(self, teacher_id: int):
        cursor = self._execute(
            """
            SELECT
                rs.id, rs.title, rs.description, rs.room_code, rs.mode,
                rs.starts_at, rs.ends_at, rs.initial_balance, rs.is_active, ss.name AS scenario_name,
                COUNT(DISTINCT rp.id) AS participants_count,
                GROUP_CONCAT(DISTINCT s.secid ORDER BY s.secid SEPARATOR ',') AS allowed_secids
            FROM room_sessions rs
            LEFT JOIN stress_scenarios ss ON ss.id = rs.scenario_id
            LEFT JOIN room_participants rp ON rp.room_id = rs.id
            LEFT JOIN room_allowed_securities ras ON ras.room_id = rs.id
            LEFT JOIN securities s ON s.id = ras.security_id
            WHERE rs.teacher_id = %s
            GROUP BY rs.id
            ORDER BY rs.created_at DESC
            """,
            (teacher_id,),
            dictionary=True,
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows or []

    def get_student_rooms(self, user_id: int):
        cursor = self._execute(
            """
            SELECT
                rs.id, rs.title, rs.description, rs.room_code, rs.mode,
                rs.starts_at, rs.ends_at, rs.initial_balance, rs.is_active, ss.name AS scenario_name,
                rp.rank_position, rp.score, rp.portfolio_value,
                rp.stress_value, rp.total_return_pct, rp.sharpe_ratio,
                GROUP_CONCAT(DISTINCT s.secid ORDER BY s.secid SEPARATOR ',') AS allowed_secids
            FROM room_participants rp
            JOIN room_sessions rs ON rs.id = rp.room_id
            LEFT JOIN stress_scenarios ss ON ss.id = rs.scenario_id
            LEFT JOIN room_allowed_securities ras ON ras.room_id = rs.id
            LEFT JOIN securities s ON s.id = ras.security_id
            WHERE rp.user_id = %s
            GROUP BY rs.id
            ORDER BY rs.created_at DESC
            """,
            (user_id,),
            dictionary=True,
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows or []

    def close_room(self, room_id: int, teacher_id: int):
        cursor = self._execute(
            """
            UPDATE room_sessions
            SET is_active = 0, ends_at = NOW()
            WHERE id = %s AND teacher_id = %s
            """,
            (room_id, teacher_id),
        )
        self.get_conn().commit()
        changed = cursor.rowcount
        cursor.close()
        return changed > 0

    def close_room_system(self, room_id: int):
        cursor = self._execute(
            """
            UPDATE room_sessions
            SET is_active = 0, ends_at = NOW()
            WHERE id = %s AND is_active = 1
            """,
            (room_id,),
        )
        self.get_conn().commit()
        changed = cursor.rowcount
        cursor.close()
        return changed > 0

    def upsert_room_result(self, room_id: int, user_id: int, portfolio_value: float,
                          stress_value: float, total_return_pct: float, sharpe_ratio: float,
                          score: float, mark_completed: bool):
        completed_at = datetime.now() if mark_completed else None
        cursor = self._cursor()
        cursor.execute(
            """
            INSERT INTO room_participants
                (room_id, user_id, portfolio_value, stress_value, total_return_pct, sharpe_ratio, score, completed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                portfolio_value = VALUES(portfolio_value),
                stress_value = VALUES(stress_value),
                total_return_pct = VALUES(total_return_pct),
                sharpe_ratio = VALUES(sharpe_ratio),
                score = VALUES(score),
                completed_at = COALESCE(VALUES(completed_at), completed_at)
            """,
            (room_id, user_id, portfolio_value, stress_value, total_return_pct, sharpe_ratio, score, completed_at),
        )
        self.get_conn().commit()
        cursor.close()

    def set_room_ranks(self, room_id: int, ordered_user_ids: list[int]):
        cursor = self._cursor()
        for index, user_id in enumerate(ordered_user_ids, start=1):
            cursor.execute(
                """
                UPDATE room_participants
                SET rank_position = %s
                WHERE room_id = %s AND user_id = %s
                """,
                (index, room_id, user_id),
            )
        self.get_conn().commit()
        cursor.close()

    def get_room_participants(self, room_id: int):
        cursor = self._execute(
            """
            SELECT
                rp.user_id, u.username, rp.joined_at, rp.completed_at,
                rp.portfolio_value, rp.stress_value, rp.total_return_pct,
                rp.sharpe_ratio, rp.score, rp.rank_position
            FROM room_participants rp
            JOIN users u ON u.id = rp.user_id
            WHERE rp.room_id = %s
            ORDER BY rp.rank_position ASC, rp.score DESC, u.username ASC
            """,
            (room_id,),
            dictionary=True,
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows or []

    def get_room_for_user(self, room_id: int, user_id: int):
        cursor = self._execute(
            """
            SELECT COUNT(*) AS c
            FROM room_participants
            WHERE room_id = %s AND user_id = %s
            """,
            (room_id, user_id),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return bool(row and int(row["c"]) > 0)

    def get_global_leaderboard(self, limit: int = 50):
        safe_limit = max(1, min(int(limit or 50), 200))
        cursor = self._execute(
            f"""
            SELECT
                u.id AS user_id,
                u.username,
                COUNT(rp.room_id) AS rooms_played,
                AVG(rp.total_return_pct) AS avg_return_pct,
                AVG(rp.sharpe_ratio) AS avg_sharpe_ratio,
                SUM(COALESCE(rp.portfolio_value, 0)) AS total_portfolio_value,
                MAX(rp.portfolio_value) AS best_portfolio_value,
                AVG(rp.rank_position) AS avg_rank_position,
                SUM(CASE WHEN rp.rank_position = 1 THEN 1 ELSE 0 END) AS wins_count,
                MAX(COALESCE(rp.completed_at, rp.joined_at)) AS last_activity_at
            FROM room_participants rp
            JOIN users u ON u.id = rp.user_id
            JOIN room_sessions rs ON rs.id = rp.room_id
            WHERE rp.portfolio_value IS NOT NULL
              AND rp.score IS NOT NULL
              AND rp.completed_at IS NOT NULL
              AND (rs.is_active = 0 OR rs.ends_at <= NOW())
            GROUP BY u.id, u.username
            HAVING COUNT(rp.room_id) > 0
            ORDER BY
                AVG(rp.total_return_pct) DESC,
                AVG(rp.sharpe_ratio) DESC,
                SUM(CASE WHEN rp.rank_position = 1 THEN 1 ELSE 0 END) DESC,
                COUNT(rp.room_id) DESC,
                u.username ASC
            LIMIT {safe_limit}
            """,
            dictionary=True,
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows or []

    def get_global_money_leaderboard(self, limit: int = 50):
        safe_limit = max(1, min(int(limit or 50), 200))
        cursor = self._execute(
            f"""
            SELECT
                u.id AS user_id,
                u.username,
                COUNT(rp.room_id) AS rooms_played,
                SUM(COALESCE(rp.portfolio_value, 0) - COALESCE(rs.initial_balance, 0)) AS earned_money_total,
                AVG(COALESCE(rp.portfolio_value, 0) - COALESCE(rs.initial_balance, 0)) AS avg_earned_money,
                MAX(COALESCE(rp.portfolio_value, 0) - COALESCE(rs.initial_balance, 0)) AS best_earned_money,
                MAX(COALESCE(rp.completed_at, rp.joined_at)) AS last_activity_at
            FROM room_participants rp
            JOIN users u ON u.id = rp.user_id
            JOIN room_sessions rs ON rs.id = rp.room_id
            WHERE rp.portfolio_value IS NOT NULL
              AND rp.completed_at IS NOT NULL
              AND (rs.is_active = 0 OR rs.ends_at <= NOW())
            GROUP BY u.id, u.username
            HAVING COUNT(rp.room_id) > 0
            ORDER BY earned_money_total DESC, best_earned_money DESC, rooms_played DESC, u.username ASC
            LIMIT {safe_limit}
            """,
            dictionary=True,
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows or []

    def set_room_allowed_securities(self, room_id: int, secids: list[str]) -> None:
        # В комнате может использоваться не весь рынок, а только бумаги, которые учитель явно открыл для конкретной сессии.
        normalized = []
        seen = set()
        for secid in secids or []:
            value = str(secid or "").strip().upper()
            if not value or value in seen:
                continue
            seen.add(value)
            security = self.get_security_by_secid(value)
            if security:
                normalized.append(int(security["id"]))
        cursor = self._cursor()
        cursor.execute("DELETE FROM room_allowed_securities WHERE room_id = %s", (room_id,))
        if normalized:
            cursor.executemany(
                "INSERT INTO room_allowed_securities (room_id, security_id) VALUES (%s, %s)",
                [(room_id, security_id) for security_id in normalized],
            )
        self.get_conn().commit()
        cursor.close()

    def get_room_allowed_secids(self, room_id: int) -> list[str]:
        cursor = self._execute(
            """
            SELECT s.secid
            FROM room_allowed_securities ras
            JOIN securities s ON s.id = ras.security_id
            WHERE ras.room_id = %s AND s.is_active = 1
            ORDER BY s.secid
            """,
            (room_id,),
            dictionary=True,
        )
        rows = cursor.fetchall()
        cursor.close()
        return [str(row["secid"]).upper() for row in (rows or []) if row.get("secid")]

    def get_current_active_room_for_user(self, user_id: int):
        cursor = self._execute(
            """
            SELECT rs.id, rs.title, rs.room_code, rs.starts_at, rs.ends_at, rs.initial_balance, rs.is_active,
                   GROUP_CONCAT(DISTINCT s.secid ORDER BY s.secid SEPARATOR ',') AS allowed_secids
            FROM room_participants rp
            JOIN room_sessions rs ON rs.id = rp.room_id
            LEFT JOIN room_allowed_securities ras ON ras.room_id = rs.id
            LEFT JOIN securities s ON s.id = ras.security_id
            WHERE rp.user_id = %s
              AND rs.is_active = 1
              AND rs.ends_at >= NOW()
            GROUP BY rs.id
            ORDER BY
                CASE WHEN rs.starts_at <= NOW() THEN 0 ELSE 1 END ASC,
                CASE WHEN rs.starts_at <= NOW() THEN rs.starts_at ELSE NULL END DESC,
                CASE WHEN rs.starts_at > NOW() THEN rs.starts_at ELSE NULL END ASC,
                rp.joined_at DESC
            LIMIT 1
            """,
            (user_id,),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return row

    def get_transaction_count(self, user_id: int) -> int:
        cursor = self._execute(
            """
            SELECT COUNT(*) AS c
            FROM transactions t
            JOIN portfolios p ON p.id = t.portfolio_id
            WHERE p.user_id = %s AND t.tx_type IN ('BUY', 'SELL')
            """,
            (user_id,),
            dictionary=True,
        )
        row = cursor.fetchone()
        cursor.close()
        return int(row["c"] or 0) if row else 0

    def get_portfolio_transactions(self, user_id: int):
        cursor = self._execute(
            """
            SELECT
                t.tx_type,
                t.quantity,
                t.price,
                t.executed_at,
                s.secid
            FROM transactions t
            JOIN portfolios p ON p.id = t.portfolio_id
            JOIN securities s ON s.id = t.security_id
            WHERE p.user_id = %s
            ORDER BY t.executed_at ASC, t.id ASC
            """,
            (user_id,),
            dictionary=True,
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows or []
