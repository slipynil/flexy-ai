import sqlite3
import time


class DB:
    """SQLite-репозиторий аккаунтов.

    Потокобезопасен: на каждый вызов открывается отдельное соединение,
    поэтому методы можно звать из разных потоков (asyncio.to_thread).
    """

    def __init__(self, db_path="./data/data.db"):
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30)

    def _init_schema(self):
        conn = self._connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    api_key TEXT PRIMARY KEY,
                    email TEXT,
                    password TEXT,
                    tries INTEGER,
                    last_used REAL,
                    busy_until REAL
                )
            """)
            # миграция старой базы: добавляем новые колонки, если их еще нет
            columns = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
            if "last_used" not in columns:
                conn.execute("ALTER TABLE accounts ADD COLUMN last_used REAL")
            if "busy_until" not in columns:
                conn.execute("ALTER TABLE accounts ADD COLUMN busy_until REAL")
            conn.commit()
        finally:
            conn.close()

    def close(self):
        """Оставлено для совместимости: соединения больше не держатся открытыми."""

    def add_account(self, api_key, email, password):
        """Сохранение аккаунта для дальнейшей работы с flexy-ai"""
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO accounts (api_key, email, password, tries)
                VALUES (?, ?, ?, ?)
                """,
                (api_key, email, password, 3),
            )
            conn.commit()
        finally:
            conn.close()

    def get_account(self) -> tuple | None:
        """Взять свободный аккаунт, наименее недавно использованный.

        Свободный = tries > 0 и не в cooldown (busy_until).
        Атомарно помечает его как использованный (last_used), поэтому
        параллельные запросы получат разные аккаунты (round-robin).
        Возвращает (api_key, email, password) или None.
        """
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT api_key, email, password
                FROM accounts
                WHERE tries > 0 AND (busy_until IS NULL OR busy_until <= ?)
                ORDER BY COALESCE(last_used, 0) ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE accounts SET last_used = ? WHERE api_key = ?",
                    (now, row[0]),
                )
            conn.commit()
            return row
        finally:
            conn.close()

    def get_pool_stats(self) -> dict[str, int]:
        """Статистика пула аккаунтов (для UI).

        Доступный аккаунт = tries > 0 и не в cooldown — та же семантика,
        что и в get_account(). Возвращает словарь:
        - total — всего аккаунтов;
        - available — доступные сейчас;
        - busy — в cooldown (rate-limit);
        - exhausted — исчерпанные (tries <= 0);
        - tries_available — суммарно попыток у доступных;
        - tries_total — суммарно попыток у всех аккаунтов.
        """
        now = time.time()
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*),
                    COALESCE(SUM(tries > 0 AND (busy_until IS NULL OR busy_until <= :now)), 0),
                    COALESCE(SUM(busy_until IS NOT NULL AND busy_until > :now), 0),
                    COALESCE(SUM(tries <= 0), 0),
                    COALESCE(SUM(CASE
                        WHEN tries > 0 AND (busy_until IS NULL OR busy_until <= :now)
                        THEN tries END), 0),
                    COALESCE(SUM(tries), 0)
                FROM accounts
                """,
                {"now": now},
            ).fetchone()
            total, available, busy, exhausted, tries_available, tries_total = row
            return {
                "total": total,
                "available": available,
                "busy": busy,
                "exhausted": exhausted,
                "tries_available": tries_available,
                "tries_total": tries_total,
            }
        finally:
            conn.close()

    def update_account_tries(self, api_key):
        """Обновление количества попыток для аккаунта"""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE accounts SET tries = tries - 1 WHERE api_key = ?",
                (api_key,),
            )
            conn.commit()
        finally:
            conn.close()

    def set_account_busy(self, api_key, seconds=120):
        """Временно исключить аккаунт из ротации (rate-limit со стороны API)."""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE accounts SET busy_until = ? WHERE api_key = ?",
                (time.time() + seconds, api_key),
            )
            conn.commit()
        finally:
            conn.close()
