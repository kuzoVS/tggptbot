import logging
import uuid
from typing import Optional, Dict
from yookassa import Configuration, Payment
from config import BotConfig
from database import DatabaseManager

# Настройка ЮKassa
Configuration.account_id = "your_shop_id"  # Замените на ваш shop_id
Configuration.secret_key = "your_secret_key"  # Замените на ваш секретный ключ


class PaymentManager:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager

        # Маппинг типов подписки на цены и описания
        self.subscription_config = {
            "week_trial": {
                "price": 100,  # 1 рубль = 100 копеек
                "days": 7,
                "description": "Пробная неделя Premium подписки"
            },
            "month": {
                "price": 55500,  # 555 рублей = 55500 копеек
                "days": 30,
                "description": "Premium подписка на 1 месяц"
            },
            "3months": {
                "price": 111100,  # 1111 рублей = 111100 копеек
                "days": 90,
                "description": "Premium подписка на 3 месяца"
            }
        }

    async def create_payment(self, user_id: int, subscription_type: str,
                             return_url: str = None) -> Optional[Dict]:
        """Создает платеж через ЮKassa"""

        if subscription_type not in self.subscription_config:
            logging.error(f"Неизвестный тип подписки: {subscription_type}")
            return None

        config = self.subscription_config[subscription_type]
        payment_id = str(uuid.uuid4())

        # Сохраняем информацию о платеже в БД
        success = await self.db_manager.create_payment(
            user_id=user_id,
            payment_id=payment_id,
            amount=config["price"] // 100,  # Сохраняем в рублях
            subscription_type=subscription_type
        )

        if not success:
            logging.error(f"Не удалось создать запись о платеже для пользователя {user_id}")
            return None

        try:
            # Создаем платеж в ЮKassa
            payment = Payment.create({
                "amount": {
                    "value": f"{config['price'] / 100:.2f}",
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url or "https://t.me/your_bot_username"
                },
                "capture": True,
                "description": config["description"],
                "metadata": {
                    "user_id": str(user_id),
                    "subscription_type": subscription_type,
                    "bot_payment_id": payment_id
                }
            }, payment_id)

            return {
                "payment_id": payment_id,
                "yookassa_id": payment.id,
                "confirmation_url": payment.confirmation.confirmation_url,
                "status": payment.status,
                "amount": config["price"] // 100,
                "currency": "RUB",
                "description": config["description"]
            }

        except Exception as e:
            logging.error(f"Ошибка создания платежа в ЮKassa: {e}")
            return None

    async def check_payment_status(self, payment_id: str) -> Optional[Dict]:
        """Проверяет статус платежа"""
        try:
            payment = Payment.find_one(payment_id)

            return {
                "payment_id": payment_id,
                "status": payment.status,
                "paid": payment.paid,
                "amount": payment.amount.value,
                "currency": payment.amount.currency,
                "metadata": payment.metadata
            }

        except Exception as e:
            logging.error(f"Ошибка проверки статуса платежа {payment_id}: {e}")
            return None

    async def handle_payment_notification(self, notification_data: Dict) -> bool:
        """Обрабатывает уведомление о платеже от ЮKassa"""
        try:
            payment_object = notification_data.get("object")
            if not payment_object:
                return False

            yookassa_payment_id = payment_object.get("id")
            status = payment_object.get("status")
            metadata = payment_object.get("metadata", {})

            bot_payment_id = metadata.get("bot_payment_id")
            user_id = metadata.get("user_id")
            subscription_type = metadata.get("subscription_type")

            if not all([bot_payment_id, user_id, subscription_type]):
                logging.error("Неполные данные в уведомлении о платеже")
                return False

            # Если платеж успешно завершен
            if status == "succeeded":
                # Подтверждаем платеж в нашей БД и активируем подписку
                payment_info = await self.db_manager.confirm_payment(bot_payment_id)

                if payment_info:
                    logging.info(f"Платеж {bot_payment_id} подтвержден для пользователя {user_id}")
                    return True
                else:
                    logging.error(f"Не удалось подтвердить платеж {bot_payment_id}")
                    return False

            return True

        except Exception as e:
            logging.error(f"Ошибка обработки уведомления о платеже: {e}")
            return False

    def get_subscription_info(self, subscription_type: str) -> Optional[Dict]:
        """Возвращает информацию о подписке"""
        return self.subscription_config.get(subscription_type)

    async def get_user_payments(self, user_id: int) -> list:
        """Получает список платежей пользователя"""
        # Эта функция требует дополнительной реализации в DatabaseManager
        # Пока возвращаем пустой список
        return []