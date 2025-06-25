import os

class BotConfig:
    BOT_TOKEN = "mytoken"
    OPENAPI = "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"
    YOOKASSA_TOKEN = "your_yookassa_token"  # Токен ЮKassa
    
    # Базовая цена подписки (в рублях)
    SUBSCRIPTION_PRICES = {
        "week_trial": 1,
        "month": 555,
        "3months": 1111,
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
    DEFAULT_MODEL = "gpt-4o-mini"

    # Лимиты для бесплатных пользователей (в день если не указано иное)
    FREE_LIMITS = {
        "free_text_requests": 75,      # Бесплатные нейросети в день
        "premium_text_requests": 0,     # Премиум нейросети
        "photo_analysis": 7,            # Анализ изображений в день
        "flux_generation": 10,          # Flux в неделю
        "midjourney_generation": 3      # Midjourney в неделю
    }

    # Лимиты для премиум пользователей
    PREMIUM_LIMITS = {
        "free_text_requests": 150,      # Бесплатные нейросети в день
        "premium_text_requests": 50,    # Премиум нейросети в день
        "photo_analysis": 25,           # Анализ изображений в день
        "flux_generation": 25,          # Flux в неделю
        "midjourney_generation": 10     # Midjourney в день (не в неделю!)
    }

    # Реферальные бонусы (в днях)
    REFERRAL_BONUS = {
        "inviter_premium_days": 1,      # Приглашающий получает 1 день премиума
        "invited_bonus_multiplier": 2   # Приглашенный получает удвоенные лимиты на 1 день
    }

    # ID администраторов
    ADMIN_IDS = {768902323, 1374423290}
    
    # Канал для подписки
    REQUIRED_CHANNEL_ID = "@cyperpyl"
    CHANNEL_URL = "https://t.me/cyperpyl"
    CHANNEL_NAME = "Цифровая пыль"