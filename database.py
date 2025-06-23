import sqlite3
import asyncpg
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Union
import json
import os
from contextlib import asynccontextmanager


class DatabaseManager:
    def __init__(self, db_type: str = "sqlite", **kwargs):
        """
        Инициализация менеджера базы данных

        Args:
            db_type: Тип БД ("sqlite" или "postgresql")
            **kwargs: Параметры подключения для PostgreSQL
        """
        self.db_type = db_type
        self.db_params = kwargs

        # Лимиты для разных типов подписки
        self.DEFAULT_LIMITS = {
            "photo_analysis": 7,
            "flux_generation": 5,
            "midjourney_generation": 3,
            "text_requests": 50
        }

        self.PREMIUM_LIMITS = {
            "photo_analysis": 50,
            "flux_generation": 25,
            "midjourney_generation": 15,
            "text_requests": 500
        }

        # VIP пользователи (можно хранить в коде для быстрого доступа)
        self.VIP_USERS = set()

    async def init_database(self):
        """Инициализация базы данных и создание таблиц"""
        if self.db_type == "sqlite":
            await self._init_sqlite()
        elif self.db_type == "postgresql":
            await self._init_postgresql()
        else:
            raise ValueError(f"Неподдерживаемый тип БД: {self.db_type}")

    async def _init_sqlite(self):
        """Инициализация SQLite"""
        db_path = self.db_params.get('database', 'bot_limits.db')

        # Создаем подключение и таблицы
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NULL,
                first_name TEXT NULL,
                last_name TEXT NULL,
                subscription_type TEXT DEFAULT 'free',
                subscription_expires TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица использования лимитов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                date DATE,
                limit_type TEXT,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, date, limit_type),
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')

        # Таблица статистики
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usage_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action_type TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')

        # Индексы для оптимизации
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_daily_usage_user_date ON daily_usage(user_id, date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_usage_stats_user_timestamp ON usage_stats(user_id, timestamp)')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_users_subscription ON users(subscription_type, subscription_expires)')

        conn.commit()
        conn.close()

        logging.info("SQLite база данных инициализирована")

    async def _init_postgresql(self):
        """Инициализация PostgreSQL"""
        conn = await asyncpg.connect(**self.db_params)

        # Таблица пользователей
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username VARCHAR(100) NULL,
                first_name VARCHAR(100) NULL,
                last_name VARCHAR(100) NULL,
                subscription_type VARCHAR(20) DEFAULT 'free',
                subscription_expires TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица использования лимитов
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS daily_usage (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                date DATE,
                limit_type VARCHAR(50),
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, date, limit_type),
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        ''')

        # Таблица статистики
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS usage_stats (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                action_type VARCHAR(50),
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata JSONB NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
        ''')

        # Индексы для оптимизации
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_daily_usage_user_date ON daily_usage(user_id, date)')
        await conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_usage_stats_user_timestamp ON usage_stats(user_id, timestamp)')
        await conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_users_subscription ON users(subscription_type, subscription_expires)')

        # Дополнительные индексы для больших нагрузок
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_daily_usage_date ON daily_usage(date)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_usage_stats_timestamp ON usage_stats(timestamp)')

        await conn.close()

        logging.info("PostgreSQL база данных инициализирована")

    @asynccontextmanager
    async def get_connection(self):
        """Получение подключения к БД"""
        if self.db_type == "sqlite":
            conn = sqlite3.connect(self.db_params.get('database', 'bot_limits.db'))
            conn.row_factory = sqlite3.Row  # Для доступа по именам колонок
            try:
                yield conn
            finally:
                conn.close()
        elif self.db_type == "postgresql":
            conn = await asyncpg.connect(**self.db_params)
            try:
                yield conn
            finally:
                await conn.close()

    async def user_exists(self, user_id: int) -> bool:
        """Проверяет существование пользователя - ТОЛЬКО SELECT!"""
        try:
            logging.debug(f"Проверка существования пользователя {user_id}")

            async with self.get_connection() as conn:
                if self.db_type == "sqlite":
                    cursor = conn.cursor()
                    cursor.execute('SELECT 1 FROM users WHERE user_id = ? LIMIT 1', (user_id,))
                    result = cursor.fetchone() is not None
                else:
                    result = await conn.fetchval('SELECT 1 FROM users WHERE user_id = $1 LIMIT 1', user_id)
                    result = result is not None

            logging.debug(f"Пользователь {user_id} {'найден' if result else 'не найден'}")
            return result

        except Exception as e:
            logging.error(f"Ошибка проверки существования пользователя {user_id}: {e}")
            return False

    async def update_user_info_selective(self, user_id: int, username: str = None, first_name: str = None,
                                         last_name: str = None):
        """Обновляет информацию о пользователе, обновляя только переданные поля"""
        try:
            logging.debug(
                f"Селективное обновление пользователя {user_id}: username={username}, first_name={first_name}, last_name={last_name}")

            async with self.get_connection() as conn:
                if self.db_type == "sqlite":
                    cursor = conn.cursor()

                    # Проверяем, существует ли пользователь
                    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
                    exists = cursor.fetchone()

                    if exists:
                        # Строим динамический запрос для обновления только переданных полей
                        update_parts = []
                        params = []

                        if username is not None:
                            update_parts.append("username = ?")
                            params.append(username)
                        if first_name is not None:
                            update_parts.append("first_name = ?")
                            params.append(first_name)
                        if last_name is not None:
                            update_parts.append("last_name = ?")
                            params.append(last_name)

                        if update_parts:  # Обновляем только если есть что обновлять
                            update_parts.append("updated_at = CURRENT_TIMESTAMP")
                            params.append(user_id)

                            query = f"UPDATE users SET {', '.join(update_parts)} WHERE user_id = ?"
                            cursor.execute(query, params)
                            logging.debug(f"Обновлен существующий пользователь {user_id}")
                    else:
                        # Создаем нового пользователя
                        cursor.execute('''
                            INSERT INTO users (user_id, username, first_name, last_name, subscription_type, created_at, updated_at)
                            VALUES (?, ?, ?, ?, 'free', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        ''', (user_id, username, first_name, last_name))
                        logging.debug(f"Создан новый пользователь {user_id}")

                    conn.commit()

                else:  # PostgreSQL
                    # Проверяем существование пользователя
                    exists = await conn.fetchval('SELECT 1 FROM users WHERE user_id = $1', user_id)

                    if exists:
                        # Строим динамический запрос для PostgreSQL
                        update_parts = []
                        params = [user_id]  # user_id всегда первый параметр
                        param_num = 2

                        if username is not None:
                            update_parts.append(f"username = ${param_num}")
                            params.append(username)
                            param_num += 1
                        if first_name is not None:
                            update_parts.append(f"first_name = ${param_num}")
                            params.append(first_name)
                            param_num += 1
                        if last_name is not None:
                            update_parts.append(f"last_name = ${param_num}")
                            params.append(last_name)
                            param_num += 1

                        if update_parts:  # Обновляем только если есть что обновлять
                            update_parts.append("updated_at = CURRENT_TIMESTAMP")
                            query = f"UPDATE users SET {', '.join(update_parts)} WHERE user_id = $1"
                            await conn.execute(query, *params)
                            logging.debug(f"Обновлен существующий пользователь {user_id}")
                    else:
                        # Создаем нового пользователя
                        await conn.execute('''
                            INSERT INTO users (user_id, username, first_name, last_name, subscription_type, created_at, updated_at)
                            VALUES ($1, $2, $3, $4, 'free', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        ''', user_id, username, first_name, last_name)
                        logging.debug(f"Создан новый пользователь {user_id}")

        except Exception as e:
            logging.error(f"Ошибка обновления пользователя {user_id}: {e}")
            raise

    async def get_user_limits(self, user_id: int) -> Dict[str, int]:
        """Получает лимиты пользователя в зависимости от подписки"""
        # Проверяем VIP
        if user_id in self.VIP_USERS:
            return {key: 999999 for key in self.DEFAULT_LIMITS.keys()}

        async with self.get_connection() as conn:
            if self.db_type == "sqlite":
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT subscription_type, subscription_expires FROM users WHERE user_id = ?',
                    (user_id,)
                )
                row = cursor.fetchone()
            else:
                row = await conn.fetchrow(
                    'SELECT subscription_type, subscription_expires FROM users WHERE user_id = $1',
                    user_id
                )

        if not row:
            # Пользователь не найден, возвращаем базовые лимиты
            return self.DEFAULT_LIMITS.copy()

        subscription_type = row['subscription_type']
        subscription_expires = row['subscription_expires']

        # Проверяем VIP из БД
        if subscription_type == 'vip':
            return {key: 999999 for key in self.DEFAULT_LIMITS.keys()}

        # Проверяем Premium
        if subscription_type == 'premium':
            if subscription_expires and subscription_expires > datetime.now():
                return self.PREMIUM_LIMITS.copy()
            else:
                # Подписка истекла, сбрасываем
                await self.set_subscription(user_id, 'free')

        return self.DEFAULT_LIMITS.copy()

    async def get_today_usage(self, user_id: int) -> Dict[str, int]:
        """Получает использование пользователя за сегодня"""
        today = datetime.now().date()

        async with self.get_connection() as conn:
            if self.db_type == "sqlite":
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT limit_type, usage_count FROM daily_usage WHERE user_id = ? AND date = ?',
                    (user_id, today)
                )
                rows = cursor.fetchall()
            else:
                rows = await conn.fetch(
                    'SELECT limit_type, usage_count FROM daily_usage WHERE user_id = $1 AND date = $2',
                    user_id, today
                )

        usage = {}
        for row in rows:
            usage[row['limit_type']] = row['usage_count']

        # Заполняем недостающие типы нулями
        for limit_type in self.DEFAULT_LIMITS.keys():
            if limit_type not in usage:
                usage[limit_type] = 0

        return usage

    async def check_limit(self, user_id: int, limit_type: str) -> Dict[str, Any]:
        """Проверяет лимит пользователя"""
        # Убеждаемся, что пользователь существует (без удаления данных)
        if not await self.user_exists(user_id):
            await self.update_user_info_selective(user_id=user_id)

        user_limits = await self.get_user_limits(user_id)
        today_usage = await self.get_today_usage(user_id)

        used = today_usage.get(limit_type, 0)
        limit = user_limits.get(limit_type, 0)
        remaining = max(0, limit - used)
        allowed = used < limit

        # Получаем тип подписки
        async with self.get_connection() as conn:
            if self.db_type == "sqlite":
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT subscription_type FROM users WHERE user_id = ?',
                    (user_id,)
                )
                row = cursor.fetchone()
            else:
                row = await conn.fetchrow(
                    'SELECT subscription_type FROM users WHERE user_id = $1',
                    user_id
                )

        subscription_type = row['subscription_type'] if row else 'free'

        return {
            "allowed": allowed,
            "used": used,
            "limit": limit,
            "remaining": remaining,
            "subscription_type": subscription_type
        }

    async def use_limit(self, user_id: int, limit_type: str) -> bool:
        """Использует лимит пользователя"""
        check_result = await self.check_limit(user_id, limit_type)

        if not check_result["allowed"]:
            return False

        today = datetime.now().date()

        async with self.get_connection() as conn:
            if self.db_type == "sqlite":
                cursor = conn.cursor()
                # Используем INSERT OR REPLACE для SQLite
                cursor.execute('''
                    INSERT OR REPLACE INTO daily_usage (user_id, date, limit_type, usage_count, updated_at)
                    VALUES (?, ?, ?, 
                        COALESCE((SELECT usage_count FROM daily_usage WHERE user_id = ? AND date = ? AND limit_type = ?), 0) + 1,
                        CURRENT_TIMESTAMP)
                ''', (user_id, today, limit_type, user_id, today, limit_type))
                conn.commit()
            else:
                # Используем UPSERT для PostgreSQL
                await conn.execute('''
                    INSERT INTO daily_usage (user_id, date, limit_type, usage_count, updated_at)
                    VALUES ($1, $2, $3, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id, date, limit_type)
                    DO UPDATE SET usage_count = daily_usage.usage_count + 1, updated_at = CURRENT_TIMESTAMP
                ''', user_id, today, limit_type)

        # Записываем в статистику
        await self._log_usage_stat(user_id, f"{limit_type}_used")

        return True

    async def get_user_status(self, user_id: int) -> Dict[str, Any]:
        """Получает полный статус пользователя - ТОЛЬКО SELECT запросы!"""
        try:
            logging.debug(f"Получение статуса пользователя {user_id}")

            # Убеждаемся, что пользователь существует
            if not await self.user_exists(user_id):
                logging.warning(f"Пользователь {user_id} не найден при получении статуса, создаем...")
                await self.update_user_info_selective(user_id=user_id)

            user_limits = await self.get_user_limits(user_id)
            today_usage = await self.get_today_usage(user_id)

            # Получаем информацию о подписке и пользователе - ТОЛЬКО SELECT!
            async with self.get_connection() as conn:
                if self.db_type == "sqlite":
                    cursor = conn.cursor()
                    cursor.execute(
                        'SELECT subscription_type, subscription_expires, username, first_name, last_name FROM users WHERE user_id = ?',
                        (user_id,)
                    )
                    row = cursor.fetchone()
                else:
                    row = await conn.fetchrow(
                        'SELECT subscription_type, subscription_expires, username, first_name, last_name FROM users WHERE user_id = $1',
                        user_id
                    )

            subscription_type = row['subscription_type'] if row else 'free'
            subscription_expires = row['subscription_expires'] if row else None
            username = row['username'] if row else None
            first_name = row['first_name'] if row else None
            last_name = row['last_name'] if row else None

            status = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "subscription_type": subscription_type,
                "subscription_expires": subscription_expires.isoformat() if subscription_expires else None,
                "is_vip": user_id in self.VIP_USERS or subscription_type == "vip",
                "limits": {}
            }

            for limit_type in self.DEFAULT_LIMITS.keys():
                used = today_usage.get(limit_type, 0)
                limit = user_limits.get(limit_type, 0)
                remaining = max(0, limit - used)

                status["limits"][limit_type] = {
                    "used": used,
                    "limit": limit,
                    "remaining": remaining,
                    "allowed": used < limit
                }

            logging.debug(f"Статус пользователя {user_id} успешно получен")
            return status

        except Exception as e:
            logging.error(f"Ошибка получения статуса пользователя {user_id}: {e}")
            raise

    async def _log_usage_stat(self, user_id: int, action_type: str, metadata: dict = None):
        """Записывает статистику использования"""
        async with self.get_connection() as conn:
            if self.db_type == "sqlite":
                cursor = conn.cursor()
                metadata_json = json.dumps(metadata) if metadata else None
                cursor.execute(
                    'INSERT INTO usage_stats (user_id, action_type, metadata) VALUES (?, ?, ?)',
                    (user_id, action_type, metadata_json)
                )
                conn.commit()
            else:
                await conn.execute(
                    'INSERT INTO usage_stats (user_id, action_type, metadata) VALUES ($1, $2, $3)',
                    user_id, action_type, json.dumps(metadata) if metadata else None
                )

    async def set_subscription(self, user_id: int, subscription_type: str, days: int = None):
        """Устанавливает подписку пользователю"""
        # Убеждаемся, что пользователь существует
        if not await self.user_exists(user_id):
            await self.update_user_info_selective(user_id=user_id)

        subscription_expires = None
        if subscription_type == "premium" and days:
            subscription_expires = datetime.now() + timedelta(days=days)

        async with self.get_connection() as conn:
            if self.db_type == "sqlite":
                cursor = conn.cursor()
                cursor.execute(
                    'UPDATE users SET subscription_type = ?, subscription_expires = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?',
                    (subscription_type, subscription_expires, user_id)
                )
                conn.commit()
            else:
                await conn.execute(
                    'UPDATE users SET subscription_type = $1, subscription_expires = $2, updated_at = CURRENT_TIMESTAMP WHERE user_id = $3',
                    subscription_type, subscription_expires, user_id
                )

        # Логируем изменение подписки
        await self._log_usage_stat(user_id, f"subscription_changed", {
            "new_type": subscription_type,
            "expires": subscription_expires.isoformat() if subscription_expires else None
        })

        logging.info(f"Пользователю {user_id} установлена подписка: {subscription_type}")

    async def add_vip_user(self, user_id: int):
        """Добавляет пользователя в VIP"""
        self.VIP_USERS.add(user_id)
        await self.set_subscription(user_id, "vip")
        logging.info(f"Пользователь {user_id} добавлен в VIP")

    async def remove_vip_user(self, user_id: int):
        """Удаляет пользователя из VIP"""
        self.VIP_USERS.discard(user_id)
        await self.set_subscription(user_id, "free")
        logging.info(f"Пользователь {user_id} удален из VIP")

    async def get_statistics(self, days: int = 7) -> Dict[str, Any]:
        """Получает статистику за указанное количество дней"""
        start_date = datetime.now().date() - timedelta(days=days)

        async with self.get_connection() as conn:
            if self.db_type == "sqlite":
                cursor = conn.cursor()

                # Общая статистика пользователей
                cursor.execute('SELECT COUNT(*) as total_users FROM users')
                total_users = cursor.fetchone()['total_users']

                cursor.execute('SELECT subscription_type, COUNT(*) as count FROM users GROUP BY subscription_type')
                subscription_stats = {row['subscription_type']: row['count'] for row in cursor.fetchall()}

                # Активность за период
                cursor.execute('''
                    SELECT date, SUM(usage_count) as total_usage 
                    FROM daily_usage 
                    WHERE date >= ? 
                    GROUP BY date 
                    ORDER BY date
                ''', (start_date,))
                daily_activity = cursor.fetchall()

                # Топ функций
                cursor.execute('''
                    SELECT limit_type, SUM(usage_count) as total_usage 
                    FROM daily_usage 
                    WHERE date >= ? 
                    GROUP BY limit_type 
                    ORDER BY total_usage DESC
                ''', (start_date,))
                feature_usage = cursor.fetchall()

            else:
                # PostgreSQL версия
                total_users = await conn.fetchval('SELECT COUNT(*) FROM users')

                subscription_rows = await conn.fetch(
                    'SELECT subscription_type, COUNT(*) as count FROM users GROUP BY subscription_type')
                subscription_stats = {row['subscription_type']: row['count'] for row in subscription_rows}

                daily_activity = await conn.fetch('''
                    SELECT date, SUM(usage_count) as total_usage 
                    FROM daily_usage 
                    WHERE date >= $1 
                    GROUP BY date 
                    ORDER BY date
                ''', start_date)

                feature_usage = await conn.fetch('''
                    SELECT limit_type, SUM(usage_count) as total_usage 
                    FROM daily_usage 
                    WHERE date >= $1 
                    GROUP BY limit_type 
                    ORDER BY total_usage DESC
                ''', start_date)

        return {
            "total_users": total_users,
            "subscription_stats": subscription_stats,
            "daily_activity": [dict(row) for row in daily_activity],
            "feature_usage": [dict(row) for row in feature_usage],
            "period_days": days
        }

    def get_limit_message(self, user_id: int, limit_type: str, check_result: Dict[str, Any]) -> str:
        """Возвращает сообщение о превышении лимита"""
        limit_names = {
            "photo_analysis": "анализ изображений",
            "flux_generation": "генерация изображений Flux",
            "midjourney_generation": "генерация изображений Midjourney",
            "text_requests": "текстовые запросы"
        }

        limit_name = limit_names.get(limit_type, limit_type)

        if check_result["allowed"]:
            return f"✅ {limit_name.title()}: {check_result['used']}/{check_result['limit']} (осталось: {check_result['remaining']})"

        message = f"❌ **Лимит превышен**\n\n"
        message += f"🚫 {limit_name.title()}: {check_result['used']}/{check_result['limit']}\n"
        message += f"📊 Ваш тариф: **{check_result['subscription_type'].title()}**\n\n"

        if check_result['subscription_type'] == 'free':
            message += "💎 **Хотите больше возможностей?**\n"
            message += "• Premium: увеличенные лимиты\n"
            message += "• VIP: без ограничений\n\n"
            message += "📞 Обратитесь к администратору для подключения подписки"

        message += f"\n🔄 Лимиты обновляются каждый день в 00:00"

        return message


# Конфигурация для разных сред
class DatabaseConfig:
    @staticmethod
    def get_sqlite_config():
        """Конфигурация для SQLite (развертывание/тестирование)"""
        return {
            "db_type": "sqlite",
            "database": "bot_limits.db"
        }

    @staticmethod
    def get_postgresql_config():
        """Конфигурация для PostgreSQL (продакшн)"""
        return {
            "db_type": "postgresql",
            "host": os.getenv("DB_HOST", "localhost"),
            "port": int(os.getenv("DB_PORT", "5432")),
            "database": os.getenv("DB_NAME", "bot_limits"),
            "user": os.getenv("DB_USER", "bot_user"),
            "password": os.getenv("DB_PASSWORD", "your_password"),
            "min_size": 5,  # Минимальный размер пула соединений
            "max_size": 20,  # Максимальный размер пула соединений
        }

    @staticmethod
    def get_config_for_environment():
        """Автоматически выбирает конфигурацию в зависимости от окружения"""
        if os.getenv("ENVIRONMENT") == "production":
            return DatabaseConfig.get_postgresql_config()
        else:
            return DatabaseConfig.get_sqlite_config()