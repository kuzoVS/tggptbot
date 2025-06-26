import sqlite3
import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import json
from contextlib import asynccontextmanager


class DatabaseManager:
    def __init__(self, db_path: str = "bot.db"):
        """Инициализация менеджера базы данных SQLite"""
        self.db_path = db_path

        # Импортируем лимиты из конфига
        from config import BotConfig
        self.FREE_LIMITS = BotConfig.FREE_LIMITS
        self.PREMIUM_LIMITS = BotConfig.PREMIUM_LIMITS

    async def init_database(self):
        """Инициализация базы данных и создание таблиц"""
        conn = sqlite3.connect(self.db_path)
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
                referral_code TEXT UNIQUE,
                invited_by INTEGER NULL,
                referral_bonus_expires TIMESTAMP NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (invited_by) REFERENCES users (user_id)
            )
        ''')

        # Таблица использования лимитов (дневные/недельные)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usage_limits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                limit_type TEXT,
                period_start DATE,
                period_end DATE,
                usage_count INTEGER DEFAULT 0,
                period_type TEXT DEFAULT 'daily',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, limit_type, period_start),
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')

        # Таблица рефералов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inviter_id INTEGER,
                invited_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                bonus_given BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (inviter_id) REFERENCES users (user_id),
                FOREIGN KEY (invited_id) REFERENCES users (user_id)
            )
        ''')

        # Таблица платежей через Telegram Stars
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                payment_id TEXT UNIQUE,
                amount INTEGER,
                currency TEXT DEFAULT 'XTR',
                status TEXT DEFAULT 'pending',
                subscription_type TEXT,
                telegram_payment_charge_id TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP NULL,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')

        # Таблица статистики (для админки)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE UNIQUE,
                new_users INTEGER DEFAULT 0,
                text_requests INTEGER DEFAULT 0,
                image_analysis INTEGER DEFAULT 0,
                image_generation INTEGER DEFAULT 0,
                payments_count INTEGER DEFAULT 0,
                revenue_stars INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Таблица транзакций тут должна быть


        # Индексы для оптимизации
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_usage_user_period ON usage_limits(user_id, period_start)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_referral ON users(referral_code)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_referrals_inviter ON referrals(inviter_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date)')

        conn.commit()
        conn.close()
        logging.info("SQLite база данных инициализирована")

    def get_connection(self):
        """Получение подключения к БД"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def generate_referral_code(self, user_id: int) -> str:
        """Генерирует уникальный реферальный код"""
        return f"REF{user_id}{str(uuid.uuid4())[:8].upper()}"

    async def create_user(self, user_id: int, username: str = None, first_name: str = None,
                          last_name: str = None, invited_by: int = None):
        """Создает нового пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        referral_code = self.generate_referral_code(user_id)

        try:
            cursor.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, referral_code, invited_by)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name, referral_code, invited_by))

            # Если пользователь приглашен по реферальной ссылке
            if invited_by:
                # Добавляем запись в таблицу рефералов
                cursor.execute('''
                    INSERT INTO referrals (inviter_id, invited_id)
                    VALUES (?, ?)
                ''', (invited_by, user_id))

                # Даем бонус приглашенному (удвоенные лимиты на день)
                referral_bonus_expires = datetime.now() + timedelta(days=1)
                cursor.execute('''
                    UPDATE users SET referral_bonus_expires = ? WHERE user_id = ?
                ''', (referral_bonus_expires, user_id))

                # Даем премиум на день приглашающему
                inviter_premium_expires = datetime.now() + timedelta(days=1)
                cursor.execute('''
                    UPDATE users SET 
                        subscription_type = CASE 
                            WHEN subscription_type = 'free' THEN 'premium'
                            ELSE subscription_type 
                        END,
                        subscription_expires = CASE 
                            WHEN subscription_type = 'free' THEN ?
                            WHEN subscription_expires IS NULL OR subscription_expires < ? THEN ?
                            ELSE datetime(subscription_expires, '+1 day')
                        END
                    WHERE user_id = ?
                ''', (inviter_premium_expires, inviter_premium_expires, inviter_premium_expires, invited_by))

                # Отмечаем что бонус выдан
                cursor.execute('''
                    UPDATE referrals SET bonus_given = TRUE 
                    WHERE inviter_id = ? AND invited_id = ?
                ''', (invited_by, user_id))

            conn.commit()

            # Обновляем статистику новых пользователей
            await self.increment_daily_stat('new_users')

            logging.info(f"Создан новый пользователь {user_id}" + (
                f" по реферальной ссылке от {invited_by}" if invited_by else ""))

        except sqlite3.IntegrityError:
            logging.warning(f"Пользователь {user_id} уже существует")
        finally:
            conn.close()

    async def get_user_by_referral_code(self, referral_code: str) -> Optional[int]:
        """Получает ID пользователя по реферальному коду"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT user_id FROM users WHERE referral_code = ?', (referral_code,))
        result = cursor.fetchone()
        conn.close()

        return result['user_id'] if result else None

    async def get_user_by_username(self, username: str) -> Optional[int]:
        """Получает ID пользователя по username"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT user_id FROM users WHERE username = ?', (username,))
        result = cursor.fetchone()
        conn.close()

        return result['user_id'] if result else None

    async def user_exists(self, user_id: int) -> bool:
        """Проверяет существование пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT 1 FROM users WHERE user_id = ? LIMIT 1', (user_id,))
        result = cursor.fetchone() is not None
        conn.close()

        return result

    async def update_user_info(self, user_id: int, username: str = None,
                               first_name: str = None, last_name: str = None):
        """Обновляет информацию о пользователе"""
        if not await self.user_exists(user_id):
            await self.create_user(user_id, username, first_name, last_name)
            return

        conn = self.get_connection()
        cursor = conn.cursor()

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

        if update_parts:
            update_parts.append("updated_at = CURRENT_TIMESTAMP")
            params.append(user_id)

            query = f"UPDATE users SET {', '.join(update_parts)} WHERE user_id = ?"
            cursor.execute(query, params)
            conn.commit()

        conn.close()

    def get_period_dates(self, period_type: str = 'daily') -> tuple:
        """Получает даты начала и конца периода"""
        now = datetime.now()

        if period_type == 'daily':
            start = now.date()
            end = start
        elif period_type == 'weekly':
            # Неделя начинается с понедельника
            days_since_monday = now.weekday()
            start = (now - timedelta(days=days_since_monday)).date()
            end = start + timedelta(days=6)
        else:
            raise ValueError(f"Неподдерживаемый период: {period_type}")

        return start, end

    async def get_user_limits(self, user_id: int) -> Dict[str, int]:
        """Получает лимиты пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT subscription_type, subscription_expires, referral_bonus_expires 
            FROM users WHERE user_id = ?
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()

        if not result:
            return self.FREE_LIMITS.copy()

        subscription_type = result['subscription_type']
        subscription_expires = result['subscription_expires']
        referral_bonus_expires = result['referral_bonus_expires']

        # Проверяем действительность подписки
        is_premium = False
        if subscription_type == 'premium' and subscription_expires:
            if datetime.fromisoformat(subscription_expires) > datetime.now():
                is_premium = True
            else:
                # Подписка истекла, сбрасываем
                await self.reset_subscription(user_id)

        # Проверяем реферальный бонус
        has_referral_bonus = False
        if referral_bonus_expires:
            if datetime.fromisoformat(referral_bonus_expires) > datetime.now():
                has_referral_bonus = True

        # Определяем лимиты
        if is_premium:
            limits = self.PREMIUM_LIMITS.copy()
        else:
            limits = self.FREE_LIMITS.copy()

        # Применяем реферальный бонус (удваиваем лимиты)
        if has_referral_bonus and not is_premium:
            for key in limits:
                limits[key] *= 2

        return limits

    async def get_usage_for_period(self, user_id: int, limit_type: str, period_type: str = 'daily') -> int:
        """Получает использование за период"""
        start_date, end_date = self.get_period_dates(period_type)

        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT usage_count FROM usage_limits 
            WHERE user_id = ? AND limit_type = ? AND period_start = ?
        ''', (user_id, limit_type, start_date))

        result = cursor.fetchone()
        conn.close()

        return result['usage_count'] if result else 0

    async def check_limit(self, user_id: int, limit_type: str) -> Dict[str, Any]:
        """Проверяет лимит пользователя"""
        if not await self.user_exists(user_id):
            await self.create_user(user_id)

        user_limits = await self.get_user_limits(user_id)

        # Определяем тип периода для лимита
        if limit_type in ['flux_generation', 'midjourney_generation']:
            # Для некоторых лимитов используется специальная логика
            if limit_type == 'midjourney_generation':
                # Для премиум - дневной лимит, для бесплатных - недельный
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT subscription_type FROM users WHERE user_id = ?', (user_id,))
                result = cursor.fetchone()
                conn.close()

                is_premium = result and result['subscription_type'] == 'premium'
                period_type = 'daily' if is_premium else 'weekly'
            else:
                period_type = 'weekly'
        else:
            period_type = 'daily'

        used = await self.get_usage_for_period(user_id, limit_type, period_type)
        limit = user_limits.get(limit_type, 0)
        remaining = max(0, limit - used)
        allowed = used < limit

        return {
            "allowed": allowed,
            "used": used,
            "limit": limit,
            "remaining": remaining,
            "period_type": period_type
        }

    async def use_limit(self, user_id: int, limit_type: str) -> bool:
        """Использует лимит пользователя"""
        check_result = await self.check_limit(user_id, limit_type)

        if not check_result["allowed"]:
            return False

        # Определяем период
        period_type = check_result["period_type"]
        start_date, end_date = self.get_period_dates(period_type)

        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO usage_limits 
            (user_id, limit_type, period_start, period_end, usage_count, period_type, updated_at)
            VALUES (?, ?, ?, ?, 
                COALESCE((SELECT usage_count FROM usage_limits 
                         WHERE user_id = ? AND limit_type = ? AND period_start = ?), 0) + 1,
                ?, CURRENT_TIMESTAMP)
        ''', (user_id, limit_type, start_date, end_date, user_id, limit_type, start_date, period_type))

        conn.commit()
        conn.close()

        # Обновляем статистику использования
        if limit_type in ['free_text_requests', 'premium_text_requests']:
            await self.increment_daily_stat('text_requests')
        elif limit_type == 'photo_analysis':
            await self.increment_daily_stat('image_analysis')
        elif limit_type in ['flux_generation', 'midjourney_generation']:
            await self.increment_daily_stat('image_generation')

        return True

    async def get_user_status(self, user_id: int) -> Dict[str, Any]:
        """Получает полный статус пользователя"""
        if not await self.user_exists(user_id):
            await self.create_user(user_id)

        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM users WHERE user_id = ?
        ''', (user_id,))
        user_data = cursor.fetchone()
        conn.close()

        user_limits = await self.get_user_limits(user_id)

        status = {
            "user_id": user_id,
            "username": user_data['username'],
            "first_name": user_data['first_name'],
            "last_name": user_data['last_name'],
            "subscription_type": user_data['subscription_type'],
            "subscription_expires": user_data['subscription_expires'],
            "referral_code": user_data['referral_code'],
            "referral_bonus_expires": user_data['referral_bonus_expires'],
            "limits": {}
        }

        # Получаем использование для каждого лимита
        for limit_type in user_limits.keys():
            if limit_type in ['flux_generation', 'midjourney_generation']:
                if limit_type == 'midjourney_generation':
                    is_premium = user_data['subscription_type'] == 'premium'
                    period_type = 'daily' if is_premium else 'weekly'
                else:
                    period_type = 'weekly'
            else:
                period_type = 'daily'

            used = await self.get_usage_for_period(user_id, limit_type, period_type)
            limit = user_limits[limit_type]
            remaining = max(0, limit - used)

            status["limits"][limit_type] = {
                "used": used,
                "limit": limit,
                "remaining": remaining,
                "allowed": used < limit,
                "period_type": period_type
            }

        return status

    async def set_subscription(self, user_id: int, subscription_type: str, days: int = None):
        """Устанавливает подписку пользователю"""
        if not await self.user_exists(user_id):
            await self.create_user(user_id)

        subscription_expires = None
        if subscription_type == "premium" and days:
            subscription_expires = datetime.now() + timedelta(days=days)

        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE users SET subscription_type = ?, subscription_expires = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE user_id = ?
        ''', (subscription_type, subscription_expires, user_id))

        conn.commit()
        conn.close()

        logging.info(f"Пользователю {user_id} установлена подписка: {subscription_type}")

    async def reset_subscription(self, user_id: int):
        """Сбрасывает подписку на бесплатную"""
        await self.set_subscription(user_id, "free")

    async def create_payment(self, user_id: int, payment_id: str, amount: int,
                             subscription_type: str, telegram_payment_charge_id: str = None) -> bool:
        """Создает запись о платеже"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO payments (user_id, payment_id, amount, subscription_type, telegram_payment_charge_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, payment_id, amount, subscription_type, telegram_payment_charge_id))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    async def confirm_payment(self, payment_id: str = None, telegram_payment_charge_id: str = None) -> Optional[Dict]:
        """Подтверждает платеж и активирует подписку"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Ищем платеж по ID или по telegram_payment_charge_id
        if payment_id:
            cursor.execute('''
                SELECT * FROM payments WHERE payment_id = ? AND status = 'pending'
            ''', (payment_id,))
        elif telegram_payment_charge_id:
            cursor.execute('''
                SELECT * FROM payments WHERE telegram_payment_charge_id = ? AND status = 'pending'
            ''', (telegram_payment_charge_id,))
        else:
            conn.close()
            return None

        payment = cursor.fetchone()

        if not payment:
            conn.close()
            return None

        # Определяем количество дней подписки
        days_map = {
            "week_trial": 7,
            "month": 30,
            "3months": 90
        }

        days = days_map.get(payment['subscription_type'], 30)

        # Активируем подписку
        cursor.execute('''
            UPDATE payments SET status = 'completed', completed_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (payment['id'],))

        conn.commit()
        conn.close()

        await self.set_subscription(payment['user_id'], "premium", days)

        # Обновляем статистику
        await self.increment_daily_stat('payments_count')
        await self.increment_daily_stat('revenue_stars', payment['amount'])

        return dict(payment)

    async def get_referral_stats(self, user_id: int) -> Dict[str, Any]:
        """Получает статистику рефералов"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Количество приглашенных
        cursor.execute('''
            SELECT COUNT(*) as count FROM referrals WHERE inviter_id = ?
        ''', (user_id,))
        invited_count = cursor.fetchone()['count']

        # Реферальный код
        cursor.execute('''
            SELECT referral_code FROM users WHERE user_id = ?
        ''', (user_id,))
        result = cursor.fetchone()
        referral_code = result['referral_code'] if result else None

        conn.close()

        return {
            "referral_code": referral_code,
            "invited_count": invited_count
        }

    # === МЕТОДЫ ДЛЯ АДМИНКИ ===

    async def get_all_users(self) -> List[int]:
        """Получает список всех пользователей для рассылки"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT user_id FROM users ORDER BY created_at')
        users = [row['user_id'] for row in cursor.fetchall()]

        conn.close()
        return users

    async def increment_daily_stat(self, stat_type: str, value: int = 1):
        """Увеличивает ежедневную статистику"""
        today = datetime.now().date()

        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Создаем запись на сегодня если её нет
            cursor.execute('''
                INSERT OR IGNORE INTO daily_stats (date) VALUES (?)
            ''', (today,))

            # Обновляем статистику
            cursor.execute(f'''
                UPDATE daily_stats SET {stat_type} = {stat_type} + ? WHERE date = ?
            ''', (value, today))

            conn.commit()
        except Exception as e:
            logging.error(f"Ошибка обновления статистики {stat_type}: {e}")
        finally:
            conn.close()

    async def get_bot_statistics(self) -> Dict[str, int]:
        """Получает полную статистику бота для админки"""
        conn = self.get_connection()
        cursor = conn.cursor()
        today = datetime.now().date()

        try:
            # Общая статистика пользователей
            cursor.execute('SELECT COUNT(*) as total FROM users')
            total_users = cursor.fetchone()['total']

            cursor.execute('''
                SELECT COUNT(*) as premium FROM users 
                WHERE subscription_type = 'premium' 
                AND (subscription_expires IS NULL OR subscription_expires > datetime('now'))
            ''')
            premium_users = cursor.fetchone()['premium']

            free_users = total_users - premium_users

            # Статистика за сегодня
            cursor.execute('''
                SELECT 
                    COALESCE(new_users, 0) as new_users_today,
                    COALESCE(text_requests, 0) as text_requests_today,
                    COALESCE(image_analysis, 0) as image_analysis_today,
                    COALESCE(image_generation, 0) as image_generation_today,
                    COALESCE(payments_count, 0) as payments_today,
                    COALESCE(revenue_stars, 0) as revenue_today
                FROM daily_stats WHERE date = ?
            ''', (today,))

            today_stats = cursor.fetchone()
            if not today_stats:
                today_stats = {
                    'new_users_today': 0,
                    'text_requests_today': 0,
                    'image_analysis_today': 0,
                    'image_generation_today': 0,
                    'payments_today': 0,
                    'revenue_today': 0
                }

            # Статистика рефералов
            cursor.execute('SELECT COUNT(*) as total FROM referrals')
            total_referrals = cursor.fetchone()['total']

            cursor.execute('SELECT COUNT(*) as given FROM referrals WHERE bonus_given = TRUE')
            referral_bonuses_given = cursor.fetchone()['given']

            stats = {
                'total_users': total_users,
                'premium_users': premium_users,
                'free_users': free_users,
                'new_users_today': today_stats['new_users_today'],
                'text_requests_today': today_stats['text_requests_today'],
                'image_analysis_today': today_stats['image_analysis_today'],
                'image_generation_today': today_stats['image_generation_today'],
                'payments_today': today_stats['payments_today'],
                'revenue_today': today_stats['revenue_today'],
                'total_referrals': total_referrals,
                'referral_bonuses_given': referral_bonuses_given
            }

            return stats

        except Exception as e:
            logging.error(f"Ошибка получения статистики: {e}")
            return {}
        finally:
            conn.close()

    async def check_referral_bonus_used(self, user_id: int) -> bool:
        """Проверяет, использовал ли пользователь уже реферальный бонус"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Проверяем, есть ли записи в таблице рефералов где пользователь был приглашен
        cursor.execute('''
            SELECT COUNT(*) as count FROM referrals WHERE invited_id = ?
        ''', (user_id,))

        result = cursor.fetchone()
        conn.close()

        return result['count'] > 0

    async def apply_referral_bonus_to_existing_user(self, user_id: int, invited_by: int):
        """Применяет реферальный бонус к существующему пользователю"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Добавляем запись в таблицу рефералов
            cursor.execute('''
                INSERT INTO referrals (inviter_id, invited_id, bonus_given)
                VALUES (?, ?, TRUE)
            ''', (invited_by, user_id))

            # Даем бонус приглашенному (удвоенные лимиты на день)
            referral_bonus_expires = datetime.now() + timedelta(days=1)
            cursor.execute('''
                UPDATE users SET referral_bonus_expires = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE user_id = ?
            ''', (referral_bonus_expires, user_id))

            # Даем премиум на день приглашающему
            inviter_premium_expires = datetime.now() + timedelta(days=1)
            cursor.execute('''
                UPDATE users SET 
                    subscription_type = CASE 
                        WHEN subscription_type = 'free' THEN 'premium'
                        ELSE subscription_type 
                    END,
                    subscription_expires = CASE 
                        WHEN subscription_type = 'free' THEN ?
                        WHEN subscription_expires IS NULL OR subscription_expires < ? THEN ?
                        ELSE datetime(subscription_expires, '+1 day')
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            ''', (inviter_premium_expires, inviter_premium_expires, inviter_premium_expires, invited_by))

            conn.commit()
            logging.info(f"Реферальный бонус применен к существующему пользователю {user_id} от {invited_by}")

        except sqlite3.IntegrityError as e:
            logging.warning(f"Пользователь {user_id} уже получал бонус от {invited_by}: {e}")
        finally:
            conn.close()

    async def reset_user_referral_status(self, user_id: int):
        """Сбрасывает реферальный статус пользователя (для тестирования)"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Удаляем записи о рефералах
            cursor.execute('DELETE FROM referrals WHERE invited_id = ?', (user_id,))

            # Убираем реферальный бонус
            cursor.execute('''
                UPDATE users SET referral_bonus_expires = NULL, updated_at = CURRENT_TIMESTAMP 
                WHERE user_id = ?
            ''', (user_id,))

            conn.commit()
            logging.info(f"Реферальный статус пользователя {user_id} сброшен")

        except Exception as e:
            logging.error(f"Ошибка сброса реферального статуса: {e}")
        finally:
            conn.close()

    async def check_user_activity_before_referral(self, user_id: int) -> bool:
        """Проверяет, была ли активность пользователя до реферальной ссылки"""
        from config import BotConfig
        settings = BotConfig.REFERRAL_SETTINGS

        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Проверяем использование лимитов (исключая маркер активности)
            cursor.execute('''
                SELECT COUNT(*) as count FROM usage_limits 
                WHERE user_id = ? AND limit_type != 'bot_activity_marker'
            ''', (user_id,))
            usage_count = cursor.fetchone()['count']

            if usage_count > 0:
                return True

            # Проверяем последнюю активность по времени
            cursor.execute('''
                SELECT MAX(updated_at) as last_activity FROM usage_limits 
                WHERE user_id = ? AND limit_type != 'bot_activity_marker'
            ''', (user_id,))
            result = cursor.fetchone()

            if result and result['last_activity']:
                from datetime import datetime, timedelta
                last_activity = datetime.fromisoformat(result['last_activity'])
                threshold = timedelta(hours=settings["activity_threshold_hours"])

                if datetime.now() - last_activity < threshold:
                    return True

            return False

        except Exception as e:
            logging.error(f"Ошибка проверки активности пользователя: {e}")
            return True  # В случае ошибки считаем что пользователь активен
        finally:
            conn.close()

    async def mark_user_as_active(self, user_id: int):
        """Отмечает пользователя как активного (для отслеживания)"""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Добавляем специальную запись об активности
            cursor.execute('''
                INSERT OR IGNORE INTO usage_limits 
                (user_id, limit_type, period_start, period_end, usage_count, period_type)
                VALUES (?, 'bot_activity_marker', date('now'), date('now'), 1, 'lifetime')
            ''', (user_id,))

            conn.commit()
        except Exception as e:
            logging.error(f"Ошибка отметки активности пользователя: {e}")
        finally:
            conn.close()

    async def get_referral_debug_info(self, user_id: int) -> Dict[str, Any]:
        """Получает отладочную информацию о реферальном статусе пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Информация о пользователе
        cursor.execute('''
            SELECT user_id, username, first_name, invited_by, referral_bonus_expires, created_at
            FROM users WHERE user_id = ?
        ''', (user_id,))
        user_info = cursor.fetchone()

        # Информация о рефералах (кого пригласил)
        cursor.execute('''
            SELECT invited_id, created_at, bonus_given FROM referrals WHERE inviter_id = ?
        ''', (user_id,))
        invited_users = cursor.fetchall()

        # Информация о том, кто пригласил этого пользователя
        cursor.execute('''
            SELECT inviter_id, created_at, bonus_given FROM referrals WHERE invited_id = ?
        ''', (user_id,))
        invited_by_info = cursor.fetchone()

        conn.close()

        return {
            "user_info": dict(user_info) if user_info else None,
            "invited_users": [dict(row) for row in invited_users],
            "invited_by_info": dict(invited_by_info) if invited_by_info else None,
            "has_used_referral": invited_by_info is not None
        }

    async def is_eligible_for_referral_bonus(self, user_id: int) -> tuple[bool, str]:
        """
        Проверяет, может ли пользователь получить реферальный бонус
        Возвращает (можно_ли, причина)
        """
        from config import BotConfig
        settings = BotConfig.REFERRAL_SETTINGS

        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # 1. Проверяем, уже ли получал реферальный бонус
            if not settings["allow_multiple_referral_bonuses"]:
                cursor.execute('''
                    SELECT COUNT(*) as count FROM referrals WHERE invited_id = ?
                ''', (user_id,))
                referral_count = cursor.fetchone()['count']

                if referral_count > 0:
                    return False, "already_used"

            # 2. Проверяем активность до реферала (если настройка включена)
            if not settings["allow_bonus_for_active_users"]:
                has_activity = await self.check_user_activity_before_referral(user_id)
                if has_activity:
                    return False, "too_active"

            # 3. Проверяем дату регистрации
            cursor.execute('''
                SELECT created_at FROM users WHERE user_id = ?
            ''', (user_id,))
            result = cursor.fetchone()

            if result:
                from datetime import datetime, timedelta
                created_at = datetime.fromisoformat(result['created_at'])
                max_age = timedelta(hours=settings["max_registration_age_hours"])

                if datetime.now() - created_at > max_age:
                    return False, "too_old"

            return True, "eligible"

        except Exception as e:
            logging.error(f"Ошибка проверки права на реферальный бонус: {e}")
            return False, f"error: {e}"
        finally:
            conn.close()