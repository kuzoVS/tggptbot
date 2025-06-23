class BotConfig:
    BOT_TOKEN = "mytoken"
    OPENAPI = "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"

    # Доступные модели AI
    MODELS = {
        "gpt-4o-mini": "openai/gpt-4o-mini",
        #"gpt-4.1": "openai/gpt-4.1",  # Заменяем на доступную модель
        #"gpt-4.1-mini": "openai/gpt-4.1-mini",    # Можно использовать gpt-4o-mini
        #"o4-mini": "openai/o4-mini",
        "mistral": "mistralai/mistral-small-3.1-24b-instruct:free",
        "kimidev": "moonshotai/kimi-dev-72b:free",
        "deepseek-v3": "deepseek/deepseek-chat:free",
        "gemma3": "google/gemma-3-27b-it:free",
        "qwen3": "qwen/qwen3-32b:free",
        #"qwenqwq": "qwen/qwen/qwq-32b:free",
        "gemminipro25": "google/gemini-2.5-pro-exp-03-25",
    }

    # Названия моделей для пользователя
    MODEL_NAMES = {
        "gpt-4o-mini": "GPT-4o Mini 🚀",
        #"gpt-4.1": "GPT-4.1 🧠 ❌",
        #"gpt-4.1-mini": "GPT-4.1 Mini ⚡❌",
        #"o4-mini": "O4 Mini 🎯 ❌",
        "kimidev": "Kimi Dev 🧑‍💻",
        "mistral": "Mistral 🪶",
        "deepseek-v3": "DeepSeek V3 🔬",
        "gemma3": "Gemma 3 Google 🔍",
        "qwen3": "Qwen3 👨‍🔬",
        #"qwenqwq": "Qwen QWQ",
        "gemminipro25": "Gemini Pro 2.5 🧠",
    }

    # Модель по умолчанию
    DEFAULT_MODEL = "gpt-4o-mini"

    # Модели с поддержкой vision (изображений)
    VISION_MODELS = [
        "gpt-4o-mini",
        "gpt-4.1",
        "claude",
        "gemini"
    ]