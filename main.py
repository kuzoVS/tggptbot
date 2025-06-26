import asyncio
import logging
import re
import base64
import aiohttp
import sys
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (InlineKeyboardMarkup, InlineKeyboardButton,
                           ReplyKeyboardMarkup, KeyboardButton, LabeledPrice)
from openai import AsyncOpenAI
import g4f
from g4f.client import Client
from deep_translator import GoogleTranslator

# Импорты наших модулей
from config import BotConfig
from database import DatabaseManager

# Инициализация
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

bot = Bot(token=BotConfig.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db_manager = DatabaseManager()

# Клиенты AI
text_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=BotConfig.OPENAPI,
)
img_client = Client()

# Константы
MAX_HISTORY = 10
TIMEOUT = 30
PROCESSING_INTERVAL = 2


# === MIDDLEWARE ===
class UserUpdateMiddleware(BaseMiddleware):
    """Middleware для автоматического обновления информации о пользователе"""

    async def __call__(
            self,
            handler: Callable[[types.Update, Dict[str, Any]], Awaitable[Any]],
            event: types.Update,
            data: Dict[str, Any]
    ) -> Any:
        user = None
        if hasattr(event, 'message') and event.message and event.message.from_user:
            user = event.message.from_user
        elif hasattr(event, 'callback_query') and event.callback_query and event.callback_query.from_user:
            user = event.callback_query.from_user

        if user:
            try:
                await db_manager.update_user_info(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name
                )
            except Exception as e:
                logging.error(f"Ошибка обновления пользователя {user.id}: {e}")

        return await handler(event, data)


class SubscriptionCheckMiddleware(BaseMiddleware):
    """Middleware для проверки подписки на канал"""

    ALLOWED_COMMANDS = {'/start', '/referral'}
    ALLOWED_CALLBACKS = {'check_subscription'}

    async def __call__(
            self,
            handler: Callable[[types.Update, Dict[str, Any]], Awaitable[Any]],
            event: types.Update,
            data: Dict[str, Any]
    ) -> Any:
        user = None
        is_command = False
        is_callback = False
        command_text = None
        callback_data = None

        if hasattr(event, 'message') and event.message and event.message.from_user:
            user = event.message.from_user
            if event.message.text and event.message.text.startswith('/'):
                is_command = True
                command_text = event.message.text.split()[0]
        elif hasattr(event, 'callback_query') and event.callback_query and event.callback_query.from_user:
            user = event.callback_query.from_user
            is_callback = True
            callback_data = event.callback_query.data

        if not user:
            return await handler(event, data)

        # Проверяем разрешенные команды
        if is_command and command_text in self.ALLOWED_COMMANDS:
            return await handler(event, data)

        if is_callback and callback_data in self.ALLOWED_CALLBACKS:
            return await handler(event, data)

        # Проверяем подписку
        if not await check_user_subscription(user.id):
            if is_callback:
                await event.callback_query.answer(
                    "❌ Сначала подпишитесь на канал!",
                    show_alert=True
                )
                return

            if hasattr(event, 'message'):
                await send_subscription_request(event.message)
                return

        return await handler(event, data)


# === ФУНКЦИИ КЛАВИАТУР ===
def create_main_menu():
    """Создает главное меню"""
    keyboard = [
        [
            KeyboardButton(text="🤖 Выбрать модель"),
            KeyboardButton(text="📊 Мои лимиты")
        ],
        [
            KeyboardButton(text="🎨 Генерация"),
            KeyboardButton(text="👥 Рефералы")
        ],
        [
            KeyboardButton(text="💎 Подписка"),
            KeyboardButton(text="ℹ️ Помощь")
        ]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def create_subscription_keyboard():
    """Создает клавиатуру для проверки подписки"""
    keyboard = [
        [InlineKeyboardButton(text=f"📢 Подписаться на {BotConfig.CHANNEL_NAME}", url=BotConfig.CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_subscription")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_model_keyboard(current_model: str = None, is_premium: bool = False):
    """Создает клавиатуру для выбора модели"""
    keyboard = []

    # Группируем модели по типам
    text_free_models = []
    text_premium_models = []
    image_free_models = []
    image_premium_models = []

    for model_key, model_info in BotConfig.MODELS.items():
        if model_info["model_type"] == "text":
            if model_info["is_premium"]:
                text_premium_models.append((model_key, model_info))
            else:
                text_free_models.append((model_key, model_info))
        elif model_info["model_type"] == "image":
            if model_info["is_premium"]:
                image_premium_models.append((model_key, model_info))
            else:
                image_free_models.append((model_key, model_info))

    # Добавляем бесплатные текстовые модели
    if text_free_models:
        for model_key, model_info in text_free_models:
            name = BotConfig.MODEL_NAMES[model_key]
            if model_key == current_model:
                name = "✅ " + name
            keyboard.append([InlineKeyboardButton(text=name, callback_data=f"model_{model_key}")])

    # Добавляем премиум текстовые модели
    if text_premium_models:
        for model_key, model_info in text_premium_models:
            name = BotConfig.MODEL_NAMES[model_key]
            if not is_premium:
                name = "🔒 " + name
            elif model_key == current_model:
                name = "✅ " + name
            keyboard.append([InlineKeyboardButton(text=name, callback_data=f"model_{model_key}")])

    # Добавляем бесплатные модели генерации
    if image_free_models:
        for model_key, model_info in image_free_models:
            name = BotConfig.MODEL_NAMES[model_key]
            if model_key == current_model:
                name = "✅ " + name
            keyboard.append([InlineKeyboardButton(text=name, callback_data=f"model_{model_key}")])

    # Добавляем премиум модели генерации
    if image_premium_models:
        for model_key, model_info in image_premium_models:
            name = BotConfig.MODEL_NAMES[model_key]
            if not is_premium:
                name = "🔒 " + name
            elif model_key == current_model:
                name = "✅ " + name
            keyboard.append([InlineKeyboardButton(text=name, callback_data=f"model_{model_key}")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_generation_keyboard():
    """Создает клавиатуру для генерации"""
    keyboard = [
        [InlineKeyboardButton(text="🎨 Flux", callback_data="gen_flux")],
        [InlineKeyboardButton(text="🎭 Midjourney", callback_data="gen_midjourney")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_subscription_plans_keyboard():
    """Создает клавиатуру с планами подписки"""
    keyboard = [
        [InlineKeyboardButton(text="🔥 Пробная неделя - 1⭐", callback_data="buy_week_trial")],
        [InlineKeyboardButton(text="📅 Месяц - 555⭐", callback_data="buy_month")],
        [InlineKeyboardButton(text="💰 3 месяца - 1111⭐", callback_data="buy_3months")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
async def check_user_subscription(user_id: int) -> bool:
    """Проверяет подписку пользователя на канал"""
    try:
        member = await bot.get_chat_member(BotConfig.REQUIRED_CHANNEL_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.error(f"Ошибка проверки подписки для пользователя {user_id}: {e}")
        return False


async def send_subscription_request(message: types.Message):
    """Отправляет запрос на подписку"""
    subscription_text = (
        "❤️ Я — помощник в успехе, который ответит на любой вопрос, поддержит тебя, "
        "сделает за тебя задание, выполнит любую работу или нарисует картину.\n\n"
        "Для дальнейшего использования бота, пожалуйста, подпишитесь на наш канал.\n"
        f"• [{BotConfig.CHANNEL_NAME}]({BotConfig.CHANNEL_URL})\n\n"
        "⭐️ Мы просим так сделать для защиты от ботов и за это мы дарим вам "
        "дополнительные запросы в нейросети."
    )

    await message.answer(
        subscription_text,
        reply_markup=create_subscription_keyboard(),
        parse_mode="Markdown"
    )


def get_system_message():
    """Возвращает системное сообщение для AI"""
    return {
        "role": "system",
        "content": (
            "Тебя зовут Помощник. Ты умный и дружелюбный ассистент, который помогает пользователям с любыми вопросами. "
            "Ты отлично анализируешь изображения, решаешь математические задачи, читаешь текст с картинок, "
            "объясняешь схемы и диаграммы, помогаешь с программированием.\n\n"
            "ВАЖНЫЕ ПРАВИЛА ФОРМАТИРОВАНИЯ ДЛЯ TELEGRAM:\n"
            "1. Используй Markdown, но НИКОГДА не используй LaTeX формулы \\[...\\] или \\(...\\)\n"
            "2. Для математических формул используй Unicode символы: ÷, ×, ≈, ², ³, ≤, ≥, π, √, ∞\n"
            "3. Код в бэктиках для формул: `x² + y² = z²`\n"
            "4. Блоки кода для сложных выражений\n"
            "5. Пошаговые решения оформляй с заголовками\n"
            "6. Выделяй важные результаты **жирным** текстом\n\n"
            "Если на изображении есть текст задачи - внимательно прочитай его и реши пошагово."
        )
    }


def clean_markdown_for_telegram(text):
    """Очищает текст от проблемных символов для корректного парсинга Markdown в Telegram"""

    def replace_math_symbols(formula):
        replacements = {
            r'\\frac\{([^}]+)\}\{([^}]+)\}': r'\1/\2',
            r'\\cdot': '·', r'\\times': '×', r'\\div': '÷', r'\\pm': '±',
            r'\\approx': '≈', r'\\neq': '≠', r'\\leq': '≤', r'\\geq': '≥',
            r'\\infty': '∞', r'\\sum': '∑', r'\\sqrt': '√', r'\\pi': 'π'
        }

        for pattern, replacement in replacements.items():
            formula = re.sub(pattern, replacement, formula)
        return formula.strip()

    # Обрабатываем LaTeX формулы
    text = re.sub(r'\\\[(.*?)\\\]', lambda m: f"\n```\n{replace_math_symbols(m.group(1))}\n```\n", text,
                  flags=re.DOTALL)
    text = re.sub(r'\\\((.*?)\\\)', lambda m: f"`{replace_math_symbols(m.group(1))}`", text, flags=re.DOTALL)

    # Убираем проблемные символы
    text = re.sub(r'\\(?![*_`\[\]()])', '', text)
    text = re.sub(r'###\s*([^\n]+)', r'\n\1\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{4,}', '\n\n', text)

    return text.strip()


def detect_and_translate_to_english(text: str) -> tuple[str, bool]:
    """Простой переводчик как fallback"""
    try:
        cyrillic_chars = sum(1 for char in text if 'а' <= char.lower() <= 'я' or char.lower() in 'ё')
        total_letters = sum(1 for char in text if char.isalpha())

        if total_letters > 0 and (cyrillic_chars / total_letters) > 0.3:
            translator = GoogleTranslator(source='ru', target='en')
            translated = translator.translate(text)
            return translated, True
        else:
            return text, False

    except Exception as e:
        logging.error(f"Ошибка простого перевода: {e}")
        return text, False


async def translate_with_ai(text: str) -> tuple[str, bool]:
    """Переводит текст на английский с помощью AI если нужно"""
    try:
        # Проверяем, нужен ли перевод
        cyrillic_chars = sum(1 for char in text if 'а' <= char.lower() <= 'я' or char.lower() in 'ё')
        total_letters = sum(1 for char in text if char.isalpha())

        # Если текст уже на английском или мало букв
        if total_letters == 0 or (cyrillic_chars / total_letters) < 0.3:
            return text, False

        # Переводим с помощью AI
        translate_prompt = f"""Переведи следующий текст с русского на английский. 
Это описание для генерации изображения, поэтому перевод должен быть точным и подходящим для AI генерации.
Отвечай ТОЛЬКО переводом, без дополнительных слов.

Текст для перевода: {text}"""

        history = [
            {
                "role": "system",
                "content": "Ты профессиональный переводчик. Переводи точно."
            },
            {
                "role": "user",
                "content": translate_prompt
            }
        ]

        # Используем модель для перевода
        completion = await asyncio.wait_for(
            text_client.chat.completions.create(
                extra_headers={
                    "HTTP-Referer": "https://kuzotgpro.com",
                    "X-Title": "Kuzo telegram gpt",
                },
                model=BotConfig.MODELS["gemma3"]["api_name"],
                messages=history,
                max_tokens=200,
                temperature=0.3
            ),
            timeout=TIMEOUT
        )

        translated = completion.choices[0].message.content.strip()

        # Проверяем что получили нормальный перевод
        if translated and len(translated) > 0:
            # Убираем лишние кавычки если есть
            translated = translated.strip('"').strip("'")
            return translated, True
        else:
            # Fallback на простой переводчик
            return detect_and_translate_to_english(text)

    except Exception as e:
        logging.error(f"Ошибка AI перевода: {e}")
        # Fallback на простой переводчик
        return detect_and_translate_to_english(text)


async def download_image_as_base64(file_id: str) -> tuple[str, str]:
    """Скачивает изображение из Telegram и конвертирует в base64"""
    try:
        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path

        if file_path.lower().endswith('.jpg') or file_path.lower().endswith('.jpeg'):
            mime_type = "image/jpeg"
        elif file_path.lower().endswith('.png'):
            mime_type = "image/png"
        elif file_path.lower().endswith('.gif'):
            mime_type = "image/gif"
        elif file_path.lower().endswith('.webp'):
            mime_type = "image/webp"
        else:
            mime_type = "image/jpeg"

        file_url = f"https://api.telegram.org/file/bot{BotConfig.BOT_TOKEN}/{file_path}"

        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as response:
                if response.status == 200:
                    image_data = await response.read()
                    if len(image_data) > 20 * 1024 * 1024:
                        raise Exception("Изображение слишком большое (более 20MB)")

                    base64_image = base64.b64encode(image_data).decode('utf-8')
                    return base64_image, mime_type
                else:
                    raise Exception(f"Не удалось скачать изображение: {response.status}")
    except Exception as e:
        logging.error(f"Ошибка при скачивании изображения: {e}")
        raise


async def process_message_with_ai(history: list, processing_msg: types.Message, user_model: str = None):
    """Обрабатывает сообщение с помощью AI"""
    try:
        has_images = any(
            isinstance(msg.get("content"), list) and
            any(item.get("type") == "image_url" for item in msg.get("content", []))
            for msg in history if msg.get("role") == "user"
        )

        model_info = BotConfig.MODELS.get(user_model, BotConfig.MODELS[BotConfig.DEFAULT_MODEL])

        # Если это модель генерации изображений, используем дефолтную текстовую модель
        if model_info["model_type"] == "image":
            model_info = BotConfig.MODELS[BotConfig.DEFAULT_MODEL]

        # Если есть изображения и модель не поддерживает vision, используем GPT-4o Mini
        if has_images and not model_info["supports_vision"]:
            model_info = BotConfig.MODELS["gpt-4o-mini"]

        completion = await asyncio.wait_for(
            text_client.chat.completions.create(
                extra_headers={
                    "HTTP-Referer": "https://kuzotgpro.com",
                    "X-Title": "Kuzo telegram gpt",
                },
                model=model_info["api_name"],
                messages=history
            ),
            timeout=TIMEOUT
        )

        response_text = completion.choices[0].message.content

        if not response_text or len(response_text.strip()) < 3:
            raise RuntimeError("Получен пустой ответ от AI")

        return response_text

    except asyncio.TimeoutError:
        raise RuntimeError(f"Превышен лимит времени ({TIMEOUT}s)")
    except Exception as e:
        raise e


async def generate_image(prompt: str, model: str = "flux") -> tuple[str, str, bool]:
    """Генерирует изображение и возвращает URL, финальный промпт и флаг перевода"""
    english_prompt, was_translated = await translate_with_ai(prompt)

    response = await img_client.images.async_generate(
        model=model,
        prompt=english_prompt,
        response_format="url"
    )
    return response.data[0].url, english_prompt, was_translated


async def send_long_message(message: types.Message, text: str, parse_mode: str = "Markdown"):
    """Отправляет длинное сообщение, разбивая его на части если нужно"""
    MAX_MESSAGE_LENGTH = 4000

    if len(text) <= MAX_MESSAGE_LENGTH:
        try:
            await message.answer(text, parse_mode=parse_mode)
        except Exception:
            await message.answer(text)
        return

    # Разбиваем длинное сообщение на части
    parts = []
    current_part = ""
    lines = text.split('\n')

    for line in lines:
        if len(line) > MAX_MESSAGE_LENGTH:
            if current_part:
                parts.append(current_part.strip())
                current_part = ""

            words = line.split(' ')
            temp_line = ""

            for word in words:
                if len(temp_line + " " + word) > MAX_MESSAGE_LENGTH:
                    if temp_line:
                        parts.append(temp_line.strip())
                    temp_line = word
                else:
                    temp_line = temp_line + " " + word if temp_line else word

            if temp_line:
                current_part = temp_line
        else:
            if len(current_part + "\n" + line) > MAX_MESSAGE_LENGTH:
                parts.append(current_part.strip())
                current_part = line
            else:
                current_part = current_part + "\n" + line if current_part else line

    if current_part:
        parts.append(current_part.strip())

    for i, part in enumerate(parts):
        if not part.strip():
            continue

        if len(parts) > 1:
            part_header = f"📄 **Часть {i + 1}/{len(parts)}**\n\n"
            part = part_header + part

        try:
            await message.answer(part, parse_mode=parse_mode)
        except Exception:
            await message.answer(part)

        if i < len(parts) - 1:
            await asyncio.sleep(0.5)


def get_limit_type_for_model(model_key: str) -> str:
    """Определяет тип лимита для модели"""
    model_info = BotConfig.MODELS.get(model_key)
    if not model_info:
        return "free_text_requests"

    # Для моделей генерации изображений возвращаем соответствующие лимиты
    if model_info["model_type"] == "image":
        if model_key == "flux":
            return "flux_generation"
        elif model_key == "midjourney":
            return "midjourney_generation"

    # Для текстовых моделей
    return "premium_text_requests" if model_info["is_premium"] else "free_text_requests"


async def get_user_by_identifier(identifier: str) -> tuple[int, str]:
    """Получает ID пользователя по username или ID"""
    # Проверяем, является ли identifier числом (ID)
    if identifier.isdigit():
        user_id = int(identifier)
        try:
            # Проверяем существование пользователя в БД
            if await db_manager.user_exists(user_id):
                status = await db_manager.get_user_status(user_id)
                username = status.get('username')
                display_name = f"@{username}" if username else f"ID: {user_id}"
                return user_id, display_name
            else:
                return None, "Пользователь не найден в базе данных"
        except Exception as e:
            return None, f"Ошибка при проверке пользователя: {e}"

    # Убираем @ если есть
    if identifier.startswith('@'):
        identifier = identifier[1:]

    # Ищем по username в базе
    try:
        user_id = await db_manager.get_user_by_username(identifier)
        if user_id:
            return user_id, f"@{identifier}"
        else:
            return None, f"Пользователь @{identifier} не найден в базе данных"
    except Exception as e:
        return None, f"Ошибка при поиске пользователя: {e}"


# === КОМАНДЫ ===
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    """Команда /start с обработкой реферальных ссылок"""
    await state.clear()
    user_id = message.from_user.id

    # Проверяем реферальную ссылку
    args = message.text.split()
    invited_by = None
    bonus_text = ""

    # Проверяем существование пользователя ДО обработки реферальной ссылки
    user_exists = await db_manager.user_exists(user_id)

    if len(args) > 1:
        referral_code = args[1]

        if BotConfig.REFERRAL_SETTINGS["log_referral_attempts"]:
            logging.info(f"Попытка использования реферальной ссылки: {referral_code} пользователем {user_id}")

        # Ищем пользователя по реферальному коду
        invited_by = await db_manager.get_user_by_referral_code(referral_code)

        if invited_by and invited_by != user_id:
            if BotConfig.REFERRAL_SETTINGS["log_referral_attempts"]:
                logging.info(f"Найден приглашающий пользователь: {invited_by}")

            # Проверяем право на получение реферального бонуса
            eligible, reason = await db_manager.is_eligible_for_referral_bonus(user_id)

            if eligible:
                if not user_exists:
                    # Создаем нового пользователя с реферальной ссылкой
                    await db_manager.create_user(
                        user_id=user_id,
                        username=message.from_user.username,
                        first_name=message.from_user.first_name,
                        last_name=message.from_user.last_name,
                        invited_by=invited_by
                    )
                else:
                    # Применяем бонус к существующему, но неактивному пользователю
                    await db_manager.apply_referral_bonus_to_existing_user(user_id, invited_by)

                bonus_text = BotConfig.REFERRAL_MESSAGES["bonus_activated"]

                # Уведомляем приглашающего
                try:
                    inviter_name = message.from_user.first_name or "Пользователь"
                    notification_text = BotConfig.REFERRAL_MESSAGES["inviter_notification"].format(
                        inviter_name=inviter_name
                    )

                    await bot.send_message(
                        invited_by,
                        notification_text,
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logging.error(f"Не удалось отправить уведомление пользователю {invited_by}: {e}")

            else:
                if BotConfig.REFERRAL_SETTINGS["log_referral_attempts"]:
                    logging.info(f"Пользователь {user_id} не может получить реферальный бонус: {reason}")

                # Получаем соответствующее сообщение
                bonus_text = BotConfig.REFERRAL_MESSAGES.get(reason, f"\n❌ {reason}")

        else:
            if invited_by == user_id:
                logging.warning(f"Пользователь {user_id} пытается использовать свою же реферальную ссылку")
                bonus_text = BotConfig.REFERRAL_MESSAGES["own_link"]
            else:
                logging.warning(f"Не найден пользователь с реферальным кодом: {referral_code}")
                bonus_text = BotConfig.REFERRAL_MESSAGES["invalid_link"]

    # Создаем обычного пользователя если он не существует и не было реферальной ссылки
    if not user_exists and not invited_by:
        await db_manager.create_user(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

    # Если пользователь существует, обновляем его информацию
    if user_exists:
        await db_manager.update_user_info(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )

    # Отмечаем пользователя как активного (для будущих проверок рефералов)
    await db_manager.mark_user_as_active(user_id)

    # Проверяем подписку
    if not await check_user_subscription(user_id):
        await send_subscription_request(message)
        return

    try:
        status = await db_manager.get_user_status(user_id)
        subscription_type = status["subscription_type"].title()

        await message.answer(
            "👋 Привет! Меня зовут Помощник. Я использую различные AI модели для ответов и запоминаю контекст.\n\n"
            "🤖 **Что я умею:**\n"
            "• Отвечать на любые текстовые вопросы\n"
            "• Анализировать изображения и решать задачи с картинок\n"
            "• Генерировать изображения по описанию\n"
            "• Помогать с программированием и математикой\n\n"
            "💬 Используйте меню ниже для навигации или просто напишите мне сообщение!\n"
            f"💎 Ваш тариф: **{subscription_type}**"
            f"{bonus_text}",
            reply_markup=create_main_menu(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Ошибка в команде /start для пользователя {user_id}: {e}")
        await message.answer(
            "❌ Произошла ошибка при инициализации. Попробуйте через несколько секунд.",
            reply_markup=create_main_menu()
        )

@dp.callback_query(F.data == "check_subscription")
async def handle_check_subscription(callback_query: types.CallbackQuery):
    """Обработчик проверки подписки"""
    user_id = callback_query.from_user.id

    if await check_user_subscription(user_id):
        await callback_query.message.delete()

        await callback_query.message.answer(
            "✅ Отлично! Подписка подтверждена.\n\n"
            "👋 Добро пожаловать! Используйте меню ниже для навигации.",
            reply_markup=create_main_menu(),
            parse_mode="Markdown"
        )
        await callback_query.answer("✅ Подписка подтверждена!")
    else:
        await callback_query.answer(
            f"❌ Подписка не найдена! Убедитесь, что вы подписались на канал {BotConfig.CHANNEL_NAME}",
            show_alert=True
        )


# === ОБРАБОТЧИКИ МЕНЮ ===
@dp.message(F.text == "🤖 Выбрать модель")
async def handle_model_menu(message: types.Message, state: FSMContext):
    """Обработчик меню выбора модели"""
    data = await state.get_data()
    current_model = data.get("current_model", BotConfig.DEFAULT_MODEL)

    # Проверяем подписку пользователя
    status = await db_manager.get_user_status(message.from_user.id)
    is_premium = status["subscription_type"] == "premium"

    await message.answer(
        f"🤖 **Выбор AI модели**\n\n"
        f"Текущая модель: **{BotConfig.MODEL_NAMES[current_model]}**\n\n"
        f"Выберите модель из списка ниже:",
        reply_markup=create_model_keyboard(current_model, is_premium),
        parse_mode="Markdown"
    )


@dp.message(F.text == "📊 Мои лимиты")
async def handle_limits_menu(message: types.Message):
    """Обработчик меню лимитов"""
    user_id = message.from_user.id

    try:
        status = await db_manager.get_user_status(user_id)
        subscription_type = status["subscription_type"].title()
        username = status.get("username")
        display_name = f"@{username}" if username else f"ID: {user_id}"

        limits_text = f"📊 **Ваши лимиты** ({display_name})\n\n"
        limits_text += f"💎 Тариф: **{subscription_type}**\n"

        if status["subscription_expires"]:
            expires = datetime.fromisoformat(status["subscription_expires"])
            limits_text += f"📅 Действует до: {expires.strftime('%d.%m.%Y')}\n"

        if status["referral_bonus_expires"]:
            bonus_expires = datetime.fromisoformat(status["referral_bonus_expires"])
            limits_text += f"🎁 Реферальный бонус до: {bonus_expires.strftime('%d.%m.%Y')}\n"

        limits_text += f"\n📈 **Использование:**\n\n"

        limit_names = {
            "free_text_requests": "🆓 Бесплатные нейросети (день)",
            "premium_text_requests": "💎 Премиум нейросети (день)",
            "photo_analysis": "🖼 Анализ изображений (день)",
            "flux_generation": "🎨 Генерация Flux (неделя)",
            "midjourney_generation": "🎭 Генерация Midjourney"
        }

        for limit_type, limit_info in status["limits"].items():
            if limit_type in limit_names:
                name = limit_names[limit_type]
                used = limit_info["used"]
                limit = limit_info["limit"]
                remaining = limit_info["remaining"]
                period = limit_info["period_type"]

                if limit >= 999999:
                    limits_text += f"{name}: {used} (безлимит)\n"
                else:
                    period_text = ""
                    if limit_type == "midjourney_generation":
                        period_text = f" ({period})"

                    percentage = (used / limit * 100) if limit > 0 else 0
                    bar = "🟩" * min(10, int(percentage / 10)) + "⬜" * max(0, 10 - int(percentage / 10))
                    limits_text += f"{name}{period_text}: {used}/{limit}\n{bar}\n\n"

        if status["subscription_type"] == "free":
            limits_text += "\n💎 **Хотите больше возможностей?**\n"
            limits_text += "Используйте кнопку '💎 Подписка' в меню!"

        limits_text += f"\n🔄 Лимиты обновляются каждый день в 00:00"

        await message.answer(limits_text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка в меню лимитов для пользователя {user_id}: {e}")
        await message.answer("❌ Произошла ошибка при получении лимитов.")


@dp.message(F.text == "🎨 Генерация")
async def handle_generation_menu(message: types.Message):
    """Обработчик меню генерации"""
    await message.answer(
        "🎨 **Генерация изображений**\n\n"
        "Выберите сервис для генерации:",
        reply_markup=create_generation_keyboard(),
        parse_mode="Markdown"
    )


@dp.message(F.text == "👥 Рефералы")
async def handle_referral_menu(message: types.Message):
    """Обработчик меню рефералов"""
    user_id = message.from_user.id

    try:
        referral_stats = await db_manager.get_referral_stats(user_id)
        referral_code = referral_stats["referral_code"]
        invited_count = referral_stats["invited_count"]

        bot_username = (await bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start={referral_code}"

        referral_text = (
            "👥 **Реферальная программа**\n\n"
            f"🔗 Ваша реферальная ссылка:\n"
            f"`{referral_link}`\n\n"
            f"👨‍👩‍👧‍👦 Приглашено друзей: **{invited_count}**\n\n"
            "🎁 **Бонусы за приглашение:**\n"
            "• Друг получает удвоенные лимиты на 1 день\n"
            "• Вы получаете 1 день премиума\n\n"
            "📤 Поделитесь ссылкой с друзьями и получайте бонусы!"
        )

        await message.answer(referral_text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка в меню рефералов для пользователя {user_id}: {e}")
        await message.answer("❌ Произошла ошибка при получении информации о рефералах.")


@dp.message(F.text == "💎 Подписка")
async def handle_subscription_menu(message: types.Message):
    """Обработчик меню подписки"""
    user_id = message.from_user.id

    try:
        status = await db_manager.get_user_status(user_id)
        subscription_type = status["subscription_type"].title()

        subscription_text = f"💎 **Подписка**\n\n"
        subscription_text += f"Текущий тариф: **{subscription_type}**\n"

        if status["subscription_expires"]:
            expires = datetime.fromisoformat(status["subscription_expires"])
            subscription_text += f"📅 Действует до: {expires.strftime('%d.%m.%Y %H:%M')}\n"

        subscription_text += "\n🚀 **Преимущества Premium:**\n"
        subscription_text += "• Доступ к премиум моделям (Gemini, Gemma, Kimi)\n"
        subscription_text += "• Увеличенные лимиты на все функции\n"
        subscription_text += "• Приоритетная обработка запросов\n\n"

        if status["subscription_type"] == "free":
            subscription_text += "Выберите план подписки:"

            await message.answer(
                subscription_text,
                reply_markup=create_subscription_plans_keyboard(),
                parse_mode="Markdown"
            )
        else:
            subscription_text += "Спасибо за использование Premium! 🙏"
            await message.answer(subscription_text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка в меню подписки для пользователя {user_id}: {e}")
        await message.answer("❌ Произошла ошибка при получении информации о подписке.")


@dp.message(F.text == "ℹ️ Помощь")
async def handle_help_menu(message: types.Message):
    """Обработчик меню помощи"""
    help_text = (
        "ℹ️ **Справка по боту**\n\n"
        "🤖 **Доступные AI модели:**\n"
        "• Бесплатные: GPT-4o Mini, Mistral, DeepSeek\n"
        "• Премиум: Gemini Pro 2.5, Gemma 3, Kimi Dev\n\n"
        "📝 **Что я умею:**\n"
        "• Отвечать на любые текстовые вопросы\n"
        "• Анализировать изображения и решать задачи с картинок\n"
        "• Генерировать изображения (Flux, Midjourney)\n"
        "• Помогать с программированием и математикой\n"
        "• Объяснять схемы, графики и диаграммы\n\n"
        "💬 **Как пользоваться:**\n"
        "• Используйте меню для быстрого доступа к функциям\n"
        "• Просто напишите сообщение или отправьте картинку\n"
        "• Для генерации изображений используйте меню 'Генерация'\n\n"
        "🔗 **Полезные команды:**\n"
        "• /new - Начать новый диалог (очистить контекст)\n"
        "• /start - Перезапустить бота\n\n"
        "❓ Если возникли вопросы - обратитесь к администратору."
    )

    await message.answer(help_text, parse_mode="Markdown")


# === ОБРАБОТЧИКИ CALLBACK QUERIES ===
@dp.callback_query(F.data.startswith("model_"))
async def handle_model_selection(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик выбора модели"""
    model_key = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id

    if model_key not in BotConfig.MODELS:
        await callback_query.answer("❌ Неизвестная модель", show_alert=True)
        return

    model_info = BotConfig.MODELS[model_key]

    # Проверяем доступ к премиум модели
    if model_info["is_premium"]:
        status = await db_manager.get_user_status(user_id)
        if status["subscription_type"] != "premium":
            await callback_query.answer(
                "🔒 Эта модель доступна только с Premium подпиской!\nИспользуйте меню 'Подписка' для получения доступа.",
                show_alert=True
            )
            return

    await state.update_data(current_model=model_key)
    model_name = BotConfig.MODEL_NAMES[model_key]

    # Если это модель генерации изображений
    if model_info["model_type"] == "image":
        if model_key == "flux":
            await state.update_data(waiting_for_flux_prompt=True)
            await callback_query.message.edit_text(
                f"🎨 **Выбрана модель: {model_name}**\n\n"
                f"✨ Теперь напишите описание изображения, которое хотите создать.\n\n"
                f"💡 **Советы:**\n"
                f"• Описывайте детально (стиль, цвета, композицию)\n"
                f"• Можно писать на русском - я автоматически переведу\n"
                f"• Пример: 'Космический корабль в стиле киберпанк, неоновые огни, темное небо'\n\n"
                f"📊 **Ваши лимиты:**\n"
                f"🎨 Flux: недельный лимит",
                parse_mode="Markdown"
            )
        elif model_key == "midjourney":
            await state.update_data(waiting_for_mj_prompt=True)
            await callback_query.message.edit_text(
                f"🎭 **Выбрана модель: {model_name}**\n\n"
                f"✨ Теперь напишите описание изображения, которое хотите создать.\n\n"
                f"💡 **Советы для Midjourney:**\n"
                f"• Используйте художественные термины\n"
                f"• Указывайте стиль (фотореализм, арт, аниме и т.д.)\n"
                f"• Можно писать на русском - я автоматически переведу\n"
                f"• Пример: 'Портрет девушки в стиле ренессанс, масляная живопись'\n\n"
                f"📊 **Ваши лимиты:**\n"
                f"🎭 Midjourney: {'дневной' if model_info['is_premium'] else 'недельный'} лимит",
                parse_mode="Markdown"
            )
    else:
        # Обычная текстовая модель
        await callback_query.message.edit_text(
            f"✅ **Модель изменена**\n\n"
            f"Выбрана модель: **{model_name}**\n\n"
            f"Теперь все ваши текстовые сообщения будут обрабатываться этой моделью.\n"
            f"Просто напишите свой вопрос или отправьте изображение для анализа.",
            parse_mode="Markdown"
        )

    await callback_query.answer(f"Выбрана модель: {model_name}")


@dp.callback_query(F.data.startswith("gen_"))
async def handle_generation_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик callback'ов генерации"""
    generation_type = callback_query.data.split("_", 1)[1]

    if generation_type == "flux":
        await state.update_data(waiting_for_flux_prompt=True)
        await callback_query.message.edit_text(
            "🎨 **Генерация Flux**\n\n"
            "Опишите изображение, которое хотите создать:",
            parse_mode="Markdown"
        )
    elif generation_type == "midjourney":
        await state.update_data(waiting_for_mj_prompt=True)
        await callback_query.message.edit_text(
            "🎭 **Генерация Midjourney**\n\n"
            "Опишите изображение, которое хотите создать:",
            parse_mode="Markdown"
        )

    await callback_query.answer()


@dp.callback_query(F.data.startswith("buy_"))
async def handle_subscription_purchase(callback_query: types.CallbackQuery):
    """Обработчик покупки подписки через Telegram Stars"""
    subscription_type = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id

    # Проверяем валидность типа подписки
    if subscription_type not in BotConfig.SUBSCRIPTION_PRICES:
        await callback_query.answer("❌ Неизвестный тип подписки", show_alert=True)
        return

    amount = BotConfig.SUBSCRIPTION_PRICES[subscription_type]

    prices = {
        "week_trial": "1⭐ (пробная неделя)",
        "month": "555⭐ (месяц)",
        "3months": "1111⭐ (3 месяца)"
    }

    # Создаем инвойс для Telegram Stars
    try:
        title = f"Premium подписка - {prices.get(subscription_type, 'План')}"
        description = f"Premium подписка на {subscription_type.replace('_', ' ')}"

        # Создаем LabeledPrice для Telegram Stars
        labeled_price = LabeledPrice(label=title, amount=amount)

        await bot.send_invoice(
            chat_id=user_id,
            title=title,
            description=description,
            payload=f"premium_{subscription_type}_{user_id}",
            provider_token="",  # Пустой токен для Telegram Stars
            currency="XTR",  # Валюта для Telegram Stars
            prices=[labeled_price],
            start_parameter=f"premium_{subscription_type}"
        )

        await callback_query.message.edit_text(
            f"💳 **Оплата через Telegram Stars**\n\n"
            f"Выбран план: **{prices.get(subscription_type, 'Неизвестный')}**\n\n"
            f"🚀 **Что входит в Premium:**\n"
            f"• Доступ к премиум моделям (Gemini, Gemma, Kimi)\n"
            f"• Увеличенные лимиты на все функции\n"
            f"• Приоритетная обработка запросов\n\n"
            f"⭐ Оплата происходит через Telegram Stars\n"
            f"Инвойс отправлен вам в личные сообщения.",
            parse_mode="Markdown"
        )

        await callback_query.answer("Инвойс отправлен!")

    except Exception as e:
        logging.error(f"Ошибка создания инвойса: {e}")
        await callback_query.answer("❌ Ошибка создания платежа", show_alert=True)


@dp.pre_checkout_query()
async def handle_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    """Обработчик pre-checkout запроса"""
    # Проверяем корректность payload
    payload = pre_checkout_query.invoice_payload
    if payload.startswith("premium_"):
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    else:
        await bot.answer_pre_checkout_query(
            pre_checkout_query.id,
            ok=False,
            error_message="Неверный платеж"
        )


@dp.message(F.successful_payment)
async def handle_successful_payment(message: types.Message):
    """Обработчик успешного платежа"""
    payment = message.successful_payment
    payload = payment.invoice_payload

    try:
        # Парсим payload: premium_subscription_type_user_id
        parts = payload.split("_")
        if len(parts) >= 3 and parts[0] == "premium":
            subscription_type = parts[1]
            user_id = int(parts[2])

            # Определяем количество дней подписки
            days_map = {
                "week": 7,
                "trial": 7,
                "month": 30,
                "3months": 90
            }

            days = days_map.get(subscription_type, 30)
            if subscription_type == "week_trial":
                days = 7

            # Активируем подписку
            await db_manager.set_subscription(user_id, "premium", days)

            await message.answer(
                f"✅ **Платеж успешно обработан!**\n\n"
                f"💎 Premium подписка активирована на {days} дней\n"
                f"🎉 Спасибо за покупку!\n\n"
                f"Теперь вам доступны все премиум функции бота.",
                parse_mode="Markdown"
            )

            logging.info(f"Успешный платеж от пользователя {user_id}, подписка {subscription_type} на {days} дней")

        else:
            logging.error(f"Неверный формат payload: {payload}")
            await message.answer("❌ Ошибка обработки платежа. Обратитесь к администратору.")

    except Exception as e:
        logging.error(f"Ошибка обработки успешного платежа: {e}")
        await message.answer("❌ Ошибка обработки платежа. Обратитесь к администратору.")


@dp.callback_query(F.data == "back_subscription")
async def handle_back_to_subscription(callback_query: types.CallbackQuery):
    """Возврат к меню подписки"""
    user_id = callback_query.from_user.id

    try:
        status = await db_manager.get_user_status(user_id)
        subscription_type = status["subscription_type"].title()

        subscription_text = f"💎 **Подписка**\n\n"
        subscription_text += f"Текущий тариф: **{subscription_type}**\n"

        if status["subscription_expires"]:
            expires = datetime.fromisoformat(status["subscription_expires"])
            subscription_text += f"📅 Действует до: {expires.strftime('%d.%m.%Y %H:%M')}\n"

        subscription_text += "\n🚀 **Преимущества Premium:**\n"
        subscription_text += "• Доступ к премиум моделям (Gemini, Gemma, Kimi)\n"
        subscription_text += "• Увеличенные лимиты на все функции\n"
        subscription_text += "• Приоритетная обработка запросов\n\n"

        if status["subscription_type"] == "free":
            subscription_text += "Выберите план подписки:"

            await callback_query.message.edit_text(
                subscription_text,
                reply_markup=create_subscription_plans_keyboard(),
                parse_mode="Markdown"
            )
        else:
            subscription_text += "Спасибо за использование Premium! 🙏"
            await callback_query.message.edit_text(subscription_text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка в меню подписки: {e}")
        await callback_query.answer("❌ Ошибка", show_alert=True)


@dp.callback_query(F.data == "back_main")
async def handle_back_to_main(callback_query: types.CallbackQuery):
    """Обработчик возврата в главное меню"""
    await callback_query.message.delete()
    await callback_query.answer()


# === ОБРАБОТЧИКИ МЕДИА ===
@dp.message(F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    """Обработчик изображений"""
    user_id = message.from_user.id

    # Проверяем лимит на анализ изображений
    limit_check = await db_manager.check_limit(user_id, "photo_analysis")

    if not limit_check["allowed"]:
        await message.answer(
            f"❌ **Лимит превышен**\n\n"
            f"🖼 Анализ изображений: {limit_check['used']}/{limit_check['limit']}\n"
            f"💎 Для увеличения лимитов используйте меню 'Подписка'",
            parse_mode="Markdown"
        )
        return

    # Используем лимит
    if not await db_manager.use_limit(user_id, "photo_analysis"):
        await message.answer("❌ Не удалось использовать лимит. Попробуйте позже.")
        return

    remaining = limit_check["remaining"] - 1
    processing_text = f"🖼 Анализирую изображение... (осталось: {remaining}/{limit_check['limit']})"
    processing_msg = await message.answer(processing_text)

    try:
        photo = message.photo[-1]
        base64_image, mime_type = await download_image_as_base64(photo.file_id)

        data = await state.get_data()
        history = data.get("history", [])
        current_model = data.get("current_model", BotConfig.DEFAULT_MODEL)

        if not history:
            history.append(get_system_message())

        user_message = {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_image}",
                        "detail": "high"
                    }
                }
            ]
        }

        if message.caption:
            user_message["content"].append({"type": "text", "text": message.caption})
        else:
            user_message["content"].append({
                "type": "text",
                "text": "Проанализируй это изображение подробно. Если это задача или содержит текст - прочитай и реши."
            })

        history.append(user_message)

        if len(history) > MAX_HISTORY * 2 + 1:
            system_msg = history[0] if history[0]["role"] == "system" else None
            recent_history = history[-(MAX_HISTORY * 2):]
            if system_msg:
                history = [system_msg] + recent_history
            else:
                history = recent_history

        response_text = await process_message_with_ai(history, processing_msg, current_model)

        history.append({"role": "assistant", "content": response_text})
        await state.update_data(history=history)

        try:
            await bot.delete_message(message.chat.id, processing_msg.message_id)
        except Exception:
            pass

        model_name = BotConfig.MODEL_NAMES[current_model]
        status = await db_manager.get_user_status(user_id)
        remaining_now = status["limits"]["photo_analysis"]["remaining"]

        full_response = f"🤖 {model_name}\n📊 Анализ изображений: {remaining_now}/{limit_check['limit']}\n\n" + clean_markdown_for_telegram(
            response_text)
        await send_long_message(message, full_response)

    except Exception as e:
        try:
            await bot.delete_message(message.chat.id, processing_msg.message_id)
        except Exception:
            pass

        logging.error(f"Ошибка при обработке изображения: {e}")
        await message.answer(
            f"❌ Не удалось проанализировать изображение\n"
            f"💡 Возможные решения:\n"
            f"• Попробуйте отправить изображение в лучшем качестве\n"
            f"• Убедитесь, что изображение не слишком большое\n"
            f"• Используйте /new для очистки контекста"
        )


@dp.message(F.document)
async def handle_document(message: types.Message, state: FSMContext):
    """Обработчик документов (изображений в виде файлов)"""
    document = message.document

    if document.mime_type and document.mime_type.startswith('image/'):
        await handle_photo(message, state)
    else:
        await message.answer(
            "📄 Я могу анализировать только изображения.\n"
            "Отправьте изображение как фото или как файл в формате JPG, PNG, GIF или WebP."
        )


# === ОБРАБОТЧИКИ ТЕКСТА ===
@dp.message(F.text & ~F.text.startswith('/') & ~F.text.in_([
    "🤖 Выбрать модель", "📊 Мои лимиты", "🎨 Генерация",
    "👥 Рефералы", "💎 Подписка", "ℹ️ Помощь"
]))
async def handle_text(message: types.Message, state: FSMContext):
    """Обработчик обычного текста"""
    user_text = message.text
    user_id = message.from_user.id
    data = await state.get_data()
    await db_manager.mark_user_as_active(user_id)
    # Проверяем, ждем ли мы промпт для генерации
    if data.get("waiting_for_flux_prompt"):
        await state.update_data(waiting_for_flux_prompt=False)
        await handle_flux_generation(message, user_text)
        return

    if data.get("waiting_for_mj_prompt"):
        await state.update_data(waiting_for_mj_prompt=False)
        await handle_midjourney_generation(message, user_text)
        return

    # Обычная обработка текста
    logging.info(f"Пользователь {user_id}: {user_text[:50]}...")

    current_model = data.get("current_model", BotConfig.DEFAULT_MODEL)

    # Проверяем тип модели
    model_info = BotConfig.MODELS.get(current_model, BotConfig.MODELS[BotConfig.DEFAULT_MODEL])

    # Если выбрана модель генерации изображений, направляем пользователя
    if model_info["model_type"] == "image":
        if current_model == "flux":
            await state.update_data(waiting_for_flux_prompt=True)
            await message.answer(
                f"🎨 **У вас выбрана модель Flux для генерации изображений**\n\n"
                f"Обрабатываю ваш запрос как промпт для генерации...",
                parse_mode="Markdown"
            )
            await handle_flux_generation(message, user_text)
        elif current_model == "midjourney":
            await state.update_data(waiting_for_mj_prompt=True)
            await message.answer(
                f"🎭 **У вас выбрана модель Midjourney для генерации изображений**\n\n"
                f"Обрабатываю ваш запрос как промпт для генерации...",
                parse_mode="Markdown"
            )
            await handle_midjourney_generation(message, user_text)
        return

    # Обычная обработка для текстовых моделей
    limit_type = get_limit_type_for_model(current_model)

    # Проверяем лимит
    limit_check = await db_manager.check_limit(user_id, limit_type)
    if not limit_check["allowed"]:
        if model_info["is_premium"]:
            limit_text = "💎 Премиум модели"
        else:
            limit_text = "🆓 Бесплатные модели"

        await message.answer(
            f"❌ **Лимит превышен**\n\n"
            f"{limit_text}: {limit_check['used']}/{limit_check['limit']}\n"
            f"💎 Для увеличения лимитов используйте меню 'Подписка'",
            parse_mode="Markdown"
        )
        return

    processing_msg = await message.answer("🧠 Помощник обрабатывает сообщение...")

    try:
        history = data.get("history", [])

        if not history:
            history.append(get_system_message())

        history.append({"role": "user", "content": user_text})

        if len(history) > MAX_HISTORY * 2 + 1:
            system_msg = history[0] if history[0]["role"] == "system" else None
            recent_history = history[-(MAX_HISTORY * 2):]
            if system_msg:
                history = [system_msg] + recent_history
            else:
                history = recent_history

        response_text = await process_message_with_ai(history, processing_msg, current_model)

        history.append({"role": "assistant", "content": response_text})
        await state.update_data(history=history)

        # Используем лимит
        await db_manager.use_limit(user_id, limit_type)

        try:
            await bot.delete_message(message.chat.id, processing_msg.message_id)
        except Exception:
            pass

        model_name = BotConfig.MODEL_NAMES[current_model]
        full_response = f"🤖 {model_name}\n\n" + clean_markdown_for_telegram(response_text)
        await send_long_message(message, full_response)

    except Exception as e:
        try:
            await bot.delete_message(message.chat.id, processing_msg.message_id)
        except Exception:
            pass

        logging.error(f"Ошибка при запросе к AI: {e}")
        await message.answer(
            f"❌ Не удалось получить ответ от AI\n"
            f"💡 Возможные решения:\n"
            f"• Подождите немного и повторите\n"
            f"• Сократите длину сообщения\n"
            f"• Используйте /new для очистки контекста"
        )


async def handle_flux_generation(message: types.Message, prompt: str):
    """Обработчик генерации Flux"""
    user_id = message.from_user.id

    # Проверяем лимит
    limit_check = await db_manager.check_limit(user_id, "flux_generation")

    if not limit_check["allowed"]:
        await message.answer(
            f"❌ **Лимит превышен**\n\n"
            f"🎨 Генерация Flux: {limit_check['used']}/{limit_check['limit']} (неделя)\n"
            f"💎 Для увеличения лимитов используйте меню 'Подписка'",
            parse_mode="Markdown"
        )
        return

    # Используем лимит
    if not await db_manager.use_limit(user_id, "flux_generation"):
        await message.answer("❌ Не удалось использовать лимит. Попробуйте позже.")
        return

    # Показываем процесс перевода и генерации
    translation_msg = await message.answer("🔄 Подготавливаю промпт для генерации...")

    try:
        # Переводим промпт
        english_prompt, was_translated = await translate_with_ai(prompt)

        await bot.edit_message_text(
            f"🎨 Генерирую изображение...\n"
            f"{'🌍 Промпт переведен с помощью AI' if was_translated else '✅ Промпт на английском'}",
            chat_id=translation_msg.chat.id,
            message_id=translation_msg.message_id
        )

        await bot.send_chat_action(message.chat.id, "upload_photo")

        url, final_prompt, _ = await generate_image(prompt, "flux")

        status = await db_manager.get_user_status(user_id)
        remaining = status["limits"]["flux_generation"]["remaining"]
        limit_total = status["limits"]["flux_generation"]["limit"]

        # Удаляем сообщение о процессе
        try:
            await bot.delete_message(translation_msg.chat.id, translation_msg.message_id)
        except:
            pass

        caption = f"🖼 **Flux генерация**\n\n"
        if was_translated:
            caption += f"📝 Ваш запрос: `{prompt}`\n"
            caption += f"🌍 AI перевод: `{final_prompt}`\n\n"
        else:
            caption += f"📝 Промпт: `{prompt}`\n\n"

        caption += f"🎨 Flux: {remaining}/{limit_total} осталось (неделя)"

        await message.answer_photo(url, caption=caption, parse_mode="Markdown")

    except Exception as e:
        try:
            await bot.delete_message(translation_msg.chat.id, translation_msg.message_id)
        except:
            pass
        logging.error(f"Ошибка генерации Flux: {e}")
        await message.answer("⚠️ Не удалось сгенерировать картинку. Попробуйте позже или измените промпт.")


async def handle_midjourney_generation(message: types.Message, prompt: str):
    """Обработчик генерации Midjourney"""
    user_id = message.from_user.id

    # Проверяем лимит
    limit_check = await db_manager.check_limit(user_id, "midjourney_generation")

    if not limit_check["allowed"]:
        period_text = "день" if limit_check["period_type"] == "daily" else "неделя"
        await message.answer(
            f"❌ **Лимит превышен**\n\n"
            f"🎭 Midjourney: {limit_check['used']}/{limit_check['limit']} ({period_text})\n"
            f"💎 Для увеличения лимитов используйте меню 'Подписка'",
            parse_mode="Markdown"
        )
        return

    # Используем лимит
    if not await db_manager.use_limit(user_id, "midjourney_generation"):
        await message.answer("❌ Не удалось использовать лимит. Попробуйте позже.")
        return

    # Показываем процесс перевода и генерации
    translation_msg = await message.answer("🔄 Подготавливаю промпт для Midjourney...")

    try:
        # Переводим промпт
        english_prompt, was_translated = await translate_with_ai(prompt)

        await bot.edit_message_text(
            f"🎭 Midjourney генерирует изображение...\n"
            f"{'🌍 Промпт переведен с помощью AI' if was_translated else '✅ Промпт на английском'}\n\n"
            f"⏳ Это может занять до 60 секунд...",
            chat_id=translation_msg.chat.id,
            message_id=translation_msg.message_id
        )

        url, final_prompt, _ = await generate_image(prompt, "midjourney-6.0")

        # Удаляем сообщение о процессе
        try:
            await bot.delete_message(translation_msg.chat.id, translation_msg.message_id)
        except:
            pass

        status = await db_manager.get_user_status(user_id)
        remaining = status["limits"]["midjourney_generation"]["remaining"]
        limit_total = status["limits"]["midjourney_generation"]["limit"]
        period_text = "день" if status["limits"]["midjourney_generation"]["period_type"] == "daily" else "неделя"

        caption = f"🖼 **Midjourney 6.0 генерация**\n\n"
        if was_translated:
            caption += f"📝 Ваш запрос: `{prompt}`\n"
            caption += f"🌍 AI перевод: `{final_prompt}`\n\n"
        else:
            caption += f"📝 Промпт: `{prompt}`\n\n"

        caption += f"🎭 MJ: {remaining}/{limit_total} осталось ({period_text})"

        await message.answer_photo(url, caption=caption, parse_mode="Markdown")

    except Exception as e:
        try:
            await bot.delete_message(translation_msg.chat.id, translation_msg.message_id)
        except:
            pass
        logging.error(f"Ошибка генерации Midjourney: {e}")
        await message.answer("⚠️ Не удалось сгенерировать изображение. Попробуйте позже или измените промпт.")


# === КОМАНДЫ ===
@dp.message(Command("new"))
async def new_chat_cmd(message: types.Message, state: FSMContext):
    """Команда для начала нового чата"""
    data = await state.get_data()
    current_model = data.get("current_model", BotConfig.DEFAULT_MODEL)

    await state.clear()
    await state.update_data(current_model=current_model)

    model_name = BotConfig.MODEL_NAMES[current_model]
    await message.answer(
        f"🆕 Начинаем новый чат!\n"
        f"🤖 Модель: **{model_name}**\n\n"
        f"Напишите мне что-нибудь!",
        parse_mode="Markdown"
    )


# === АДМИНСКИЕ КОМАНДЫ ===
@dp.message(Command("admin"))
async def admin_cmd(message: types.Message):
    """Админская панель"""
    if message.from_user.id not in BotConfig.ADMIN_IDS:
        await message.answer("❌ У вас нет прав для выполнения этой команды")
        return
    await message.answer(
        "🔧 Админская панель\n\n"
        "Доступные команды:\n"
        "• /admin_stats - Статистика бота\n"
        "• /admin_user [user_id/@username] - Информация о пользователе\n"
        "• /admin_premium [user_id/@username] [days] - Выдать премиум\n"
        "• /admin_reset [user_id/@username] - Сбросить подписку\n"
        "• /admin_broadcast [текст] - Рассылка сообщения\n\n"
        "Можно использовать как ID пользователя, так и @username",
    )


@dp.message(Command("admin_stats"))
async def admin_stats_cmd(message: types.Message):
    """Админская статистика"""
    if message.from_user.id not in BotConfig.ADMIN_IDS:
        return

    try:
        stats = await db_manager.get_bot_statistics()

        # Безопасно форматируем все числа
        def safe_format(value):
            return str(value) if value is not None else "0"

        stats_text = "📊 *Статистика бота*\n\n"
        stats_text += f"👥 Всего пользователей: *{safe_format(stats.get('total_users', 0))}*\n"
        stats_text += f"💎 Premium пользователей: *{safe_format(stats.get('premium_users', 0))}*\n"
        stats_text += f"🆓 Бесплатных пользователей: *{safe_format(stats.get('free_users', 0))}*\n\n"

        stats_text += f"📈 *Активность за сегодня:*\n"
        stats_text += f"🆕 Новых пользователей: *{safe_format(stats.get('new_users_today', 0))}*\n"
        stats_text += f"💬 Текстовых запросов: *{safe_format(stats.get('text_requests_today', 0))}*\n"
        stats_text += f"🖼 Анализов изображений: *{safe_format(stats.get('image_analysis_today', 0))}*\n"
        stats_text += f"🎨 Генераций изображений: *{safe_format(stats.get('image_generation_today', 0))}*\n\n"

        stats_text += f"👥 *Рефералы:*\n"
        stats_text += f"🔗 Всего приглашений: *{safe_format(stats.get('total_referrals', 0))}*\n"
        stats_text += f"🎁 Выданных бонусов: *{safe_format(stats.get('referral_bonuses_given', 0))}*\n\n"

        stats_text += f"💰 *Подписки:*\n"
        stats_text += f"⭐ Платежей за сегодня: *{safe_format(stats.get('payments_today', 0))}*\n"
        stats_text += f"💵 Доход за сегодня: *{safe_format(stats.get('revenue_today', 0))}⭐*"

        try:
            await message.answer(stats_text, parse_mode="Markdown")
        except Exception as markdown_error:
            logging.warning(f"Ошибка парсинга Markdown в статистике: {markdown_error}")
            # Отправляем без форматирования
            plain_text = stats_text.replace('*', '').replace('_', '')
            await message.answer(plain_text)

    except Exception as e:
        logging.error(f"Ошибка получения статистики: {e}")
        await message.answer("❌ Ошибка получения статистики")


@dp.message(Command("admin_user"))
async def admin_user_cmd(message: types.Message):
    """Информация о пользователе"""
    if message.from_user.id not in BotConfig.ADMIN_IDS:
        return

    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        await message.answer("Использование: /admin_user <user_id/@username>")
        return

    try:
        identifier = args[1]
        user_id, display_name = await get_user_by_identifier(identifier)

        if not user_id:
            await message.answer(f"❌ {display_name}")
            return

        status = await db_manager.get_user_status(user_id)
        referral_stats = await db_manager.get_referral_stats(user_id)

        # Безопасно экранируем все данные для Markdown
        def escape_markdown(text):
            if text is None:
                return "Не указано"
            # Экранируем специальные символы Markdown
            special_chars = ['_', '*', '`', '[', ']', '(', ')', '~', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
            escaped = str(text)
            for char in special_chars:
                escaped = escaped.replace(char, f'\\{char}')
            return escaped

        # Форматируем данные безопасно
        user_id_safe = escape_markdown(user_id)
        display_name_safe = escape_markdown(display_name)
        first_name_safe = escape_markdown(status.get('first_name', 'Не указано'))
        username_safe = escape_markdown(status.get('username', 'Нет'))
        subscription_type_safe = escape_markdown(status['subscription_type'])
        referral_code_safe = escape_markdown(referral_stats['referral_code'])
        invited_count_safe = escape_markdown(referral_stats['invited_count'])

        info_text = f"👤 *Пользователь {display_name_safe}*\n\n"
        info_text += f"🆔 ID: `{user_id_safe}`\n"
        info_text += f"👤 Имя: {first_name_safe}\n"
        info_text += f"📧 Username: @{username_safe}\n"
        info_text += f"💎 Тариф: {subscription_type_safe}\n"

        if status['subscription_expires']:
            try:
                expires = datetime.fromisoformat(status['subscription_expires'])
                expires_safe = escape_markdown(expires.strftime('%d.%m.%Y %H:%M'))
                info_text += f"📅 Подписка до: {expires_safe}\n"
            except:
                info_text += f"📅 Подписка до: данные повреждены\n"

        if status['referral_bonus_expires']:
            try:
                bonus_expires = datetime.fromisoformat(status['referral_bonus_expires'])
                bonus_expires_safe = escape_markdown(bonus_expires.strftime('%d.%m.%Y %H:%M'))
                info_text += f"🎁 Реф\\. бонус до: {bonus_expires_safe}\n"
            except:
                info_text += f"🎁 Реф\\. бонус до: данные повреждены\n"

        info_text += f"\n👥 *Рефералы:*\n"
        info_text += f"🔗 Код: `{referral_code_safe}`\n"
        info_text += f"👨‍👩‍👧‍👦 Приглашено: {invited_count_safe}\n"

        info_text += f"\n📊 *Лимиты:*\n"

        limit_names = {
            "free_text_requests": "Бесплатные запросы",
            "premium_text_requests": "Премиум запросы",
            "photo_analysis": "Анализ изображений",
            "flux_generation": "Flux генерация",
            "midjourney_generation": "Midjourney генерация"
        }

        for limit_type, limit_info in status["limits"].items():
            if limit_type in limit_names:
                limit_name = limit_names[limit_type]
                used_safe = escape_markdown(limit_info['used'])
                limit_safe = escape_markdown(limit_info['limit'])
                info_text += f"• {limit_name}: {used_safe}/{limit_safe}\n"

        # Отправляем с Markdown v2 или без парсинга в случае ошибки
        try:
            await message.answer(info_text, parse_mode="Markdown")
        except Exception as markdown_error:
            logging.warning(f"Ошибка парсинга Markdown: {markdown_error}")
            # Отправляем без форматирования
            plain_text = info_text.replace('*', '').replace('`', '').replace('\\', '')
            await message.answer(plain_text)

    except Exception as e:
        logging.error(f"Ошибка получения информации о пользователе: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")

@dp.message(Command("admin_premium"))
async def admin_premium_cmd(message: types.Message):
    """Выдача премиума"""
    if message.from_user.id not in BotConfig.ADMIN_IDS:
        return

    args = message.text.split()
    if len(args) != 3:
        await message.answer("Использование: /admin_premium <user_id/@username> <days>")
        return

    try:
        identifier = args[1]
        days = int(args[2])

        user_id, display_name = await get_user_by_identifier(identifier)

        if not user_id:
            await message.answer(f"❌ {display_name}")
            return

        await db_manager.set_subscription(user_id, "premium", days)
        await message.answer(f"✅ Пользователю {display_name} выдан премиум на {days} дней")

        # Уведомляем пользователя
        try:
            await bot.send_message(
                user_id,
                f"🎉 **Поздравляем!**\n\n"
                f"Вам была выдана Premium подписка на {days} дней!\n"
                f"Теперь вам доступны все премиум функции бота.\n\n"
                f"Спасибо за использование нашего сервиса! ❤️",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    except ValueError:
        await message.answer("❌ Неверное количество дней")
    except Exception as e:
        logging.error(f"Ошибка выдачи премиума: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("admin_reset"))
async def admin_reset_cmd(message: types.Message):
    """Сброс подписки"""
    """Сброс подписки"""
    if message.from_user.id not in BotConfig.ADMIN_IDS:
        return

    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        await message.answer("Использование: /admin_reset <user_id/@username>")
        return

    try:
        identifier = args[1]
        user_id, display_name = await get_user_by_identifier(identifier)

        if not user_id:
            await message.answer(f"❌ {display_name}")
            return

        await db_manager.reset_subscription(user_id)
        await message.answer(f"✅ Подписка пользователя {display_name} сброшена на бесплатную")

        # Уведомляем пользователя
        try:
            await bot.send_message(
                user_id,
                f"ℹ️ **Уведомление**\n\n"
                f"Ваша подписка была сброшена администратором.\n"
                f"Теперь у вас бесплатный тариф.\n\n"
                f"Для получения Premium используйте меню 'Подписка'.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    except Exception as e:
        logging.error(f"Ошибка сброса подписки: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("admin_broadcast"))
async def admin_broadcast_cmd(message: types.Message):
    """Рассылка сообщения всем пользователям"""
    if message.from_user.id not in BotConfig.ADMIN_IDS:
        return

    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        await message.answer("Использование: /admin_broadcast <текст сообщения>")
        return

    broadcast_text = args[1]

    try:
        users = await db_manager.get_all_users()
        total_users = len(users)
        sent_count = 0
        failed_count = 0

        status_msg = await message.answer(f"📤 Начинаю рассылку для {total_users} пользователей...")

        for user_id in users:
            try:
                # Отправляем без parse_mode чтобы избежать ошибок форматирования
                await bot.send_message(user_id, broadcast_text)
                sent_count += 1

                # Обновляем статус каждые 10 отправленных сообщений
                if sent_count % 10 == 0:
                    try:
                        await bot.edit_message_text(
                            f"📤 Рассылка: {sent_count}/{total_users} отправлено...",
                            chat_id=status_msg.chat.id,
                            message_id=status_msg.message_id
                        )
                    except:
                        pass  # Игнорируем ошибки редактирования статуса

                # Небольшая задержка чтобы не превысить лимиты
                await asyncio.sleep(0.05)

            except Exception as e:
                failed_count += 1
                logging.warning(f"Не удалось отправить сообщение пользователю {user_id}: {e}")

        try:
            await bot.edit_message_text(
                f"✅ Рассылка завершена!\n\n"
                f"📤 Отправлено: {sent_count}\n"
                f"❌ Не удалось: {failed_count}\n"
                f"👥 Всего пользователей: {total_users}",
                chat_id=status_msg.chat.id,
                message_id=status_msg.message_id
            )
        except:
            # Если не получается отредактировать, отправляем новое сообщение
            await message.answer(
                f"✅ Рассылка завершена!\n\n"
                f"📤 Отправлено: {sent_count}\n"
                f"❌ Не удалось: {failed_count}\n"
                f"👥 Всего пользователей: {total_users}"
            )

    except Exception as e:
        logging.error(f"Ошибка рассылки: {e}")
        await message.answer(f"❌ Ошибка рассылки: {e}")

# === ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ===
@dp.message()
async def handle_all_other(message: types.Message):
    """Обработчик всех остальных типов сообщений"""
    await message.answer(
        "🤔 Я получил ваше сообщение, но не знаю как его обработать.\n"
        "💬 Попробуйте:\n"
        "• Написать текстом\n"
        "• Отправить изображение\n"
        "• Использовать меню ниже",
        reply_markup=create_main_menu()
    )


async def on_startup():
    """Функция, выполняемая при запуске бота"""
    logging.info("Инициализация базы данных...")
    await db_manager.init_database()
    logging.info("База данных инициализирована")
    logging.info("Бот запущен и готов к работе!")


async def main():
    """Основная функция запуска бота"""
    logging.info("Запуск бота...")

    # Инициализация при запуске
    await on_startup()

    # Добавляем middleware
    dp.update.middleware(SubscriptionCheckMiddleware())
    dp.update.middleware(UserUpdateMiddleware())

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Начинаем polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())