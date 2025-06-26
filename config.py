import os


class BotConfig:
    BOT_TOKEN = "token"
    OPENAPI = "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"

    # Цены подписки в Telegram Stars
    SUBSCRIPTION_PRICES = {
        "week_trial": 1,  # 1 звезда за пробную неделю
        "month": 555,  # 555 звезд за месяц
        "3months": 1111,  # 1111 звезд за 3 месяца
    }

    # Доступные модели AI
    MODELS = {
        # Бесплатные модели
        "gpt-4o-mini": {
            "api_name": "openai/gpt-4o-mini",
            "is_premium": False,
            "supports_vision": True,
            "model_type": "text"
        },
        "mistral": {
            "api_name": "mistralai/mistral-small-3.1-24b-instruct:free",
            "is_premium": False,
            "supports_vision": False,
            "model_type": "text"
        },
        "deepseek-v3": {
            "api_name": "deepseek/deepseek-chat:free",
            "is_premium": False,
            "supports_vision": False,
            "model_type": "text"
        },

        # Премиум модели
        "gemma3": {
            "api_name": "google/gemma-3-27b-it:free",
            "is_premium": True,
            "supports_vision": False,
            "model_type": "text"
        },
        "gemini-pro-25": {
            "api_name": "google/gemini-2.5-pro-exp-03-25",
            "is_premium": True,
            "supports_vision": True,
            "model_type": "text"
        },
        "kimidev": {
            "api_name": "moonshotai/kimi-dev-72b:free",
            "is_premium": True,
            "supports_vision": False,
            "model_type": "text"
        },

        # Модели генерации изображений
        "flux": {
            "api_name": "flux",
            "is_premium": False,
            "supports_vision": False,
            "model_type": "image"
        },
        "midjourney": {
            "api_name": "midjourney-6.0",
            "is_premium": True,
            "supports_vision": False,
            "model_type": "image"
        }
    }

    # Названия моделей для пользователя
    MODEL_NAMES = {
        "gpt-4o-mini": "GPT-4o Mini 🚀",
        "mistral": "Mistral 🪶",
        "deepseek-v3": "DeepSeek V3 🔬",
        "gemma3": "Gemma 3 Google 🔍",
        "gemini-pro-25": "Gemini Pro 2.5 🧠",
        "kimidev": "Kimi Dev 🧑‍💻",
        "flux": "Flux (генерация) 🎨",
        "midjourney": "Midjourney (генерация) 🎭"
    }

    # Модель по умолчанию
    DEFAULT_MODEL = "deepseek-v3"

    # Лимиты для бесплатных пользователей (в день если не указано иное)
    FREE_LIMITS = {
        "free_text_requests": 75,  # Бесплатные нейросети в день
        "premium_text_requests": 0,  # Премиум нейросети
        "photo_analysis": 7,  # Анализ изображений в день
        "flux_generation": 10,  # Flux в неделю
        "midjourney_generation": 3,  # Midjourney в неделю
        "voice_processing": 0,  # Голосовые сообщения - НЕТ для бесплатных
        "document_processing": 0  # Документы - НЕТ для бесплатных

    }

    # Лимиты для премиум пользователей
    PREMIUM_LIMITS = {
        "free_text_requests": 150,  # Бесплатные нейросети в день
        "premium_text_requests": 50,  # Премиум нейросети в день
        "photo_analysis": 25,  # Анализ изображений в день
        "flux_generation": 25,  # Flux в неделю
        "midjourney_generation": 10,  # Midjourney в день (не в неделю!)
        "voice_processing": 20,  # Голосовые сообщения в день
        "document_processing": 15,  # Документы в день
    }

    # Реферальные бонусы (в днях)
    REFERRAL_BONUS = {
        "inviter_premium_days": 1,  # Приглашающий получает 1 день премиума
        "invited_bonus_multiplier": 2  # Приглашенный получает удвоенные лимиты на 1 день
    }

    REFERRAL_SETTINGS = {
        # Максимальное время с момента регистрации, когда еще можно получить реферальный бонус (в часах)
        "max_registration_age_hours": 24,  # 24 часа

        # Разрешить ли бонус для пользователей с предыдущей активностью
        "allow_bonus_for_active_users": False,

        # Разрешить ли повторные реферальные бонусы (обычно False)
        "allow_multiple_referral_bonuses": False,

        # Минимальный интервал между использованием лимитов для считания пользователя "активным" (в часах)
        "activity_threshold_hours": 1,

        # Логировать ли все попытки получения реферальных бонусов
        "log_referral_attempts": True,

        # Отправлять ли уведомления администраторам о подозрительной активности
        "notify_admins_suspicious_activity": False
    }

    # Сообщения для реферальной системы
    REFERRAL_MESSAGES = {
        "bonus_activated": (
            "\n🎉 **Реферальный бонус активирован!**\n"
            "• Вы получили удвоенные лимиты на 1 день\n"
            "• Пригласившему вас пользователю выдан 1 день премиума"
        ),
        "already_used": "\n💡 Реферальный бонус можно получить только один раз",
        "too_active": "\n⚠️ Реферальный бонус доступен только для новых пользователей",
        "too_old": "\n⚠️ Реферальная ссылка действительна только для новых пользователей",
        "own_link": "\n⚠️ Нельзя использовать собственную реферальную ссылку",
        "invalid_link": "\n❌ Неверная реферальная ссылка",
        "inviter_notification": (
            "🎉 **Новый реферал!**\n\n"
            "Пользователь {inviter_name} присоединился по вашей ссылке!\n"
            "🎁 Вы получили 1 день Premium подписки\n\n"
            "👥 Продолжайте приглашать друзей и получайте больше бонусов!"
        )
    }

    # ID администраторов
    ADMIN_IDS = {768902323, 1374423290}

    # Канал для подписки
    REQUIRED_CHANNEL_ID = "@cyperpyl"
    CHANNEL_URL = "https://t.me/cyperpyl"
    CHANNEL_NAME = "Цифровая пыль"