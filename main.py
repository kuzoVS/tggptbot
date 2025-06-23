import asyncio
import logging
import re
import base64
import aiohttp
import os
import sys
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import OpenAI, AsyncOpenAI
import g4f
from g4f.client import Client
from deep_translator import GoogleTranslator

# Импорты наших модулей
from config import BotConfig
from database import DatabaseManager, DatabaseConfig

# Глобальная переменная для менеджера БД
db_manager = None

# Настройка логирования с поддержкой UTF-8
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

# Клиент OpenAI для текстовых запросов через OpenRouter
text_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=BotConfig.OPENAPI,
)

# Клиент для генерации картинок
img_client = Client()
MAX_HISTORY = 10

# Параметры для запросов
TIMEOUT = 30
PROCESSING_INTERVAL = 2

# ID администраторов (замените на свои)
ADMIN_IDS = {
    768902323,
    1374423290,
    # 987654321,
}

REQUIRED_CHANNEL_ID = "@technoloka"  # Замените на ваш канал
CHANNEL_URL = "https://t.me/technoloka"
CHANNEL_NAME = "Техноложка"



# === MIDDLEWARE ===

class UserUpdateMiddleware(BaseMiddleware):
    """Middleware для автоматического обновления информации о пользователе"""

    async def __call__(
            self,
            handler: Callable[[types.Update, Dict[str, Any]], Awaitable[Any]],
            event: types.Update,
            data: Dict[str, Any]
    ) -> Any:
        global db_manager

        # Получаем пользователя из события
        user = None
        if hasattr(event, 'message') and event.message and event.message.from_user:
            user = event.message.from_user
        elif hasattr(event, 'callback_query') and event.callback_query and event.callback_query.from_user:
            user = event.callback_query.from_user
        elif hasattr(event, 'from_user'):
            user = event.from_user

        # Если пользователь найден и db_manager инициализирован, обновляем информацию ПЕРЕД обработкой
        if user and db_manager:
            try:
                # Проверяем существует ли пользователь
                user_exists = await db_manager.user_exists(user.id)

                # Обновляем только если есть реальные данные
                update_needed = False
                username = first_name = last_name = None

                if hasattr(user, 'username') and user.username:
                    username = user.username
                    update_needed = True

                if hasattr(user, 'first_name') and user.first_name:
                    first_name = user.first_name
                    update_needed = True

                if hasattr(user, 'last_name') and user.last_name:
                    last_name = user.last_name
                    update_needed = True

                # Обновляем только если есть что обновлять или пользователь новый
                if update_needed or not user_exists:
                    await db_manager.update_user_info_selective(
                        user_id=user.id,
                        username=username,
                        first_name=first_name,
                        last_name=last_name
                    )

                    action = "Создан новый" if not user_exists else "Обновлена информация"
                    logging.debug(
                        f"{action} пользователь {user.id}: @{username}, {first_name} {last_name}")

            except Exception as e:
                logging.error(f"Ошибка обновления информации пользователя {user.id}: {e}")
                # Не прерываем выполнение, если не удалось обновить пользователя

        # Продолжаем обработку
        return await handler(event, data)


class SubscriptionCheckMiddleware(BaseMiddleware):
    """Middleware для проверки подписки на канал"""

    # Команды, которые можно выполнять без подписки
    ALLOWED_COMMANDS = {'/start'}
    ALLOWED_CALLBACKS = {'check_subscription'}

    async def __call__(
            self,
            handler: Callable[[types.Update, Dict[str, Any]], Awaitable[Any]],
            event: types.Update,
            data: Dict[str, Any]
    ) -> Any:
        # Получаем пользователя и тип события
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

        # Если пользователь не найден, пропускаем проверку
        if not user:
            return await handler(event, data)

        # Проверяем, разрешена ли команда без подписки
        if is_command and command_text in self.ALLOWED_COMMANDS:
            return await handler(event, data)

        if is_callback and callback_data in self.ALLOWED_CALLBACKS:
            return await handler(event, data)

        # Проверяем подписку
        if not await check_user_subscription(user.id):
            # Если это callback query, отвечаем через callback
            if is_callback:
                await event.callback_query.answer(
                    "❌ Сначала подпишитесь на канал!",
                    show_alert=True
                )
                return

            # Если это обычное сообщение, отправляем запрос на подписку
            if hasattr(event, 'message'):
                await send_subscription_request(event.message)
                return

        # Если подписка есть, продолжаем обработку
        return await handler(event, data)

class LoggingMiddleware(BaseMiddleware):
    """Middleware для логирования"""

    async def __call__(
            self,
            handler: Callable[[types.Update, Dict[str, Any]], Awaitable[Any]],
            event: types.Update,
            data: Dict[str, Any]
    ) -> Any:
        logging.info(f"=== ПОЛУЧЕНО ОБНОВЛЕНИЕ ===")
        logging.info(f"Тип события: {type(event).__name__}")

        if hasattr(event, 'message') and event.message:
            msg = event.message
            logging.info(f"Тип контента: {msg.content_type}")
            logging.info(f"Chat ID: {msg.chat.id}")
            if msg.from_user:
                logging.info(f"User ID: {msg.from_user.id}")
                logging.info(f"Username: @{msg.from_user.username or 'None'}")
                logging.info(f"Name: {msg.from_user.first_name or ''} {msg.from_user.last_name or ''}")
            if hasattr(msg, 'text') and msg.text:
                logging.info(f"Текст сообщения: {msg.text[:100]}...")

        try:
            result = await handler(event, data)
            logging.info("Обновление успешно обработано")
            return result
        except Exception as e:
            logging.error(f"Ошибка при обработке обновления: {str(e)}")
            raise


# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def create_subscription_keyboard():
    """Создает клавиатуру для проверки подписки"""
    keyboard = [
        [InlineKeyboardButton(text=f"📢 Подписаться на {CHANNEL_NAME}", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_subscription")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def check_user_subscription(user_id: int) -> bool:
    """Проверяет подписку пользователя на канал"""
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL_ID, user_id)
        # Проверяем статус: member, administrator, creator
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.error(f"Ошибка проверки подписки для пользователя {user_id}: {e}")
        return False


async def check_user_subscription(user_id: int) -> bool:
    """Проверяет подписку пользователя на канал"""
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL_ID, user_id)
        # Проверяем статус: member, administrator, creator
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.error(f"Ошибка проверки подписки для пользователя {user_id}: {e}")
        return False


async def send_subscription_request(message: types.Message):
    subscription_text = (
        "❤️ Я — помощник в успехе, который ответит на любой вопрос, поддержит тебя, "
        "сделает за тебя задание, выполнит любую работу или нарисует картину.\n\n"
        "Для дальнейшего использования бота, пожалуйста, подпишитесь на наши каналы.\n"
        f"• [{CHANNEL_NAME}]({CHANNEL_URL})\n\n"
        "⭐️ Мы просим так сделать для защиты от ботов и за это мы дарим вам "
        "по 5 дополнительных запросов в бесплатные нейросети."
    )

    await message.answer(
        subscription_text,
        reply_markup=create_subscription_keyboard(),
        parse_mode="Markdown"
    )

def create_model_keyboard():
    """Создает клавиатуру для выбора модели"""
    keyboard = []
    models = list(BotConfig.MODEL_NAMES.items())

    # Создаем кнопки по 2 в ряд
    for i in range(0, len(models), 2):
        row = []
        for j in range(2):
            if i + j < len(models):
                model_key, model_name = models[i + j]
                row.append(InlineKeyboardButton(
                    text=model_name,
                    callback_data=f"model_{model_key}"
                ))
        keyboard.append(row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_admin_keyboard():
    """Создает клавиатуру для админских функций"""
    keyboard = [
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")
        ],
        [
            InlineKeyboardButton(text="💎 Установить VIP", callback_data="admin_set_vip"),
            InlineKeyboardButton(text="⭐ Установить Premium", callback_data="admin_set_premium")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def send_long_message(message: types.Message, text: str, parse_mode: str = "Markdown"):
    """
    Отправляет длинное сообщение, разбивая его на части если нужно
    """
    MAX_MESSAGE_LENGTH = 4000  # Оставляем небольшой запас от лимита 4096

    if len(text) <= MAX_MESSAGE_LENGTH:
        try:
            await message.answer(text, parse_mode=parse_mode)
        except Exception as e:
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
        except Exception as e:
            await message.answer(part)

        if i < len(parts) - 1:
            await asyncio.sleep(0.5)


def detect_and_translate_to_english(text: str) -> tuple[str, bool]:
    """
    Определяет язык текста и переводит на английский если нужно
    """
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
        logging.error(f"Ошибка перевода: {e}")
        return text, False


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


async def update_processing_message(chat_id: int, message_id: int, dots_count: int,
                                    base_text: str = "🧠 Помощник обрабатывает сообщение"):
    """Обновляет сообщение с анимацией точек"""
    dots = "." * (dots_count % 4)
    try:
        await bot.edit_message_text(
            f"{base_text}{dots}",
            chat_id=chat_id,
            message_id=message_id
        )
    except Exception:
        pass


async def show_processing_animation(chat_id: int, message_id: int, duration: float,
                                    base_text: str = "🧠 Помощник обрабатывает сообщение"):
    """Показывает анимацию обработки"""
    start_time = asyncio.get_event_loop().time()
    dots_count = 0

    while asyncio.get_event_loop().time() - start_time < duration:
        await update_processing_message(chat_id, message_id, dots_count, base_text)
        dots_count += 1
        await asyncio.sleep(PROCESSING_INTERVAL)


async def process_message_with_ai(history: list, processing_msg: types.Message, user_model: str = None):
    """Обрабатывает сообщение с помощью AI"""
    animation_task = asyncio.create_task(
        show_processing_animation(
            processing_msg.chat.id,
            processing_msg.message_id,
            TIMEOUT
        )
    )

    try:
        has_images = any(
            isinstance(msg.get("content"), list) and
            any(item.get("type") == "image_url" for item in msg.get("content", []))
            for msg in history if msg.get("role") == "user"
        )

        if user_model and user_model in BotConfig.MODELS:
            if has_images and user_model not in BotConfig.VISION_MODELS:
                model = BotConfig.MODELS["gpt-4o-mini"]
            else:
                model = BotConfig.MODELS[user_model]
        else:
            model = BotConfig.MODELS[BotConfig.DEFAULT_MODEL]

        completion = await asyncio.wait_for(
            text_client.chat.completions.create(
                extra_headers={
                    "HTTP-Referer": "https://kuzotgpro.com",
                    "X-Title": "Kuzo telegram gpt",
                },
                model=model,
                messages=history
            ),
            timeout=TIMEOUT
        )

        animation_task.cancel()
        response_text = completion.choices[0].message.content

        if not response_text or len(response_text.strip()) < 3:
            raise RuntimeError("Получен пустой ответ от AI")

        return response_text

    except asyncio.TimeoutError:
        animation_task.cancel()
        raise RuntimeError(f"Превышен лимит времени ({TIMEOUT}s)")
    except Exception as e:
        animation_task.cancel()
        raise e


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


async def generate_image(prompt: str, model: str = "flux") -> str:
    """Генерирует изображение и возвращает URL"""
    english_prompt, was_translated = detect_and_translate_to_english(prompt)

    response = await img_client.images.async_generate(
        model=model,
        prompt=english_prompt,
        response_format="url"
    )
    return response.data[0].url, english_prompt, was_translated


async def handle_image_message(message: types.Message, state: FSMContext, file_id: str, caption: str = None):
    """Универсальная функция для обработки изображений с проверкой лимитов"""
    global db_manager

    if db_manager is None:
        await message.answer("⚠️ Система лимитов инициализируется, попробуйте через несколько секунд...")
        return

    user_id = message.from_user.id

    # Проверяем лимит на анализ изображений
    limit_check = await db_manager.check_limit(user_id, "photo_analysis")

    if not limit_check["allowed"]:
        limit_message = db_manager.get_limit_message(user_id, "photo_analysis", limit_check)
        await message.answer(limit_message, parse_mode="Markdown")
        return

    # Используем лимит
    if not await db_manager.use_limit(user_id, "photo_analysis"):
        await message.answer("❌ Не удалось использовать лимит. Попробуйте позже.")
        return

    remaining = limit_check["remaining"] - 1
    processing_text = f"🖼 Анализирую изображение... (осталось: {remaining}/{limit_check['limit']})"
    processing_msg = await message.answer(processing_text)

    try:
        base64_image, mime_type = await download_image_as_base64(file_id)

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

        if caption:
            user_message["content"].append({"type": "text", "text": caption})
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


# === КОМАНДЫ ===
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    global db_manager

    await state.clear()
    user_id = message.from_user.id

    # Проверяем, что db_manager инициализирован
    if db_manager is None:
        await message.answer("⚠️ Бот еще инициализируется, попробуйте через несколько секунд...")
        return

    # Проверяем подписку
    if not await check_user_subscription(user_id):
        await send_subscription_request(message)
        return

    try:
        status = await db_manager.get_user_status(user_id)
        subscription_type = status["subscription_type"].title()

        # Даем бонус за подписку (если пользователь новый или это первая проверка)
        #bonus_given = await db_manager.give_subscription_bonus(user_id)
        #bonus_text = "\n🎁 За подписку на канал вы получили +5 запросов к нейросетям!" if bonus_given else ""

        await message.answer(
            "👋 Привет! Меня зовут Помощник. Я использую различные AI модели для ответов и запоминаю контекст.\n\n"
            "🤖 **Доступные команды:**\n"
            "• `/model` - Выбрать AI модель\n"
            "• `/new` - Начать новый чат\n"
            "• `/image <описание>` - Сгенерировать картинку\n"
            "• `/mj <описание>` - Сгенерировать картинку Midjourney 6.0\n"
            "• `/limits` - Посмотреть свои лимиты\n"
            "• `/help` - Подробная справка\n\n"
            "💬 Напиши что-нибудь или отправь изображение для анализа!\n"
            f"📱 Текущая модель: **{BotConfig.MODEL_NAMES[BotConfig.DEFAULT_MODEL]}**\n"
            f"💎 Ваш тариф: **{subscription_type}**",
            #f"{bonus_text}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Ошибка в команде /start для пользователя {user_id}: {e}")
        await message.answer(
            "❌ Произошла ошибка при инициализации.\n"
            "Попробуйте через несколько секунд."
        )


@dp.callback_query(F.data == "check_subscription")
async def handle_check_subscription(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик проверки подписки"""
    user_id = callback_query.from_user.id

    if await check_user_subscription(user_id):
        await callback_query.message.delete()

        # Даем бонус за подписку
        if db_manager:
           #bonus_given = await db_manager.give_subscription_bonus(user_id)
           bonus_text = "\n🎁 За подписку на канал вы получили +5 запросов к нейросетям!" #if bonus_given else ""
        else:
            bonus_text = ""

        await callback_query.message.answer(
            "✅ Отлично! Подписка подтверждена.\n\n"
            "👋 Привет! Меня зовут Помощник. Я использую различные AI модели для ответов и запоминаю контекст.\n\n"
            "🤖 **Доступные команды:**\n"
            "• `/model` - Выбрать AI модель\n"
            "• `/new` - Начать новый чат\n"
            "• `/image <описание>` - Сгенерировать картинку\n"
            "• `/mj <описание>` - Сгенерировать картинку Midjourney 6.0\n"
            "• `/limits` - Посмотреть свои лимиты\n"
            "• `/help` - Подробная справка\n\n"
            "💬 Напиши что-нибудь или отправь изображение для анализа!\n"
            f"📱 Текущая модель: **{BotConfig.MODEL_NAMES[BotConfig.DEFAULT_MODEL]}**"
            f"{bonus_text}",
            parse_mode="Markdown"
        )
        await callback_query.answer("✅ Подписка подтверждена!")
    else:
        await callback_query.answer(
            f"❌ Подписка не найдена! Убедитесь, что вы подписались на канал {CHANNEL_NAME}",
            show_alert=True
        )

@dp.message(Command("limits"))
async def limits_cmd(message: types.Message):
    """Команда для просмотра лимитов пользователя"""
    global db_manager

    if db_manager is None:
        await message.answer("⚠️ Система лимитов инициализируется, попробуйте через несколько секунд...")
        return

    user_id = message.from_user.id

    try:
        # Убеждаемся, что пользователь существует в БД
        if not await db_manager.user_exists(user_id):
            # Если пользователя нет, создаем его
            await db_manager.update_user_info_selective(
                user_id=user_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name
            )
            logging.info(f"Создан новый пользователь {user_id} при вызове /limits")

        status = await db_manager.get_user_status(user_id)

        subscription_type = status["subscription_type"].title()
        username = status.get("username")
        display_name = f"@{username}" if username else f"ID: {user_id}"

        limits_text = f"📊 **Ваши лимиты** ({display_name})\n\n"
        limits_text += f"💎 Тариф: **{subscription_type}**\n"

        if status["subscription_expires"]:
            from datetime import datetime
            expires = datetime.fromisoformat(status["subscription_expires"])
            limits_text += f"📅 Действует до: {expires.strftime('%d.%m.%Y')}\n"

        limits_text += f"\n📈 **Использование за сегодня:**\n\n"

        limit_names = {
            "photo_analysis": "🖼 Анализ изображений",
            "flux_generation": "🎨 Генерация Flux",
            "midjourney_generation": "🎭 Генерация Midjourney",
            "text_requests": "💬 Текстовые запросы"
        }

        for limit_type, limit_info in status["limits"].items():
            if limit_type in limit_names:
                name = limit_names[limit_type]
                used = limit_info["used"]
                limit = limit_info["limit"]
                remaining = limit_info["remaining"]

                if limit >= 999999:
                    limits_text += f"{name}: {used} ♾️\n"
                else:
                    percentage = (used / limit * 100) if limit > 0 else 0
                    bar = "🟩" * (int(percentage / 10)) + "⬜" * (10 - int(percentage / 10))
                    limits_text += f"{name}: {used}/{limit} ({remaining} осталось)\n{bar}\n\n"

        if status["subscription_type"] == "free":
            limits_text += "\n💎 **Хотите больше возможностей?**\n"
            limits_text += "• Premium: увеличенные лимиты\n"
            limits_text += "• VIP: без ограничений\n\n"
            limits_text += "📞 Обратитесь к администратору для подключения подписки"

        limits_text += f"\n🔄 Лимиты обновляются каждый день в 00:00"

        await message.answer(limits_text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка в команде /limits для пользователя {user_id}: {e}")
        await message.answer(
            "❌ Произошла ошибка при получении лимитов.\n"
            "Попробуйте выполнить команду /start для инициализации."
        )


@dp.message(Command("model"))
async def model_cmd(message: types.Message, state: FSMContext):
    """Команда для выбора модели AI"""
    data = await state.get_data()
    current_model = data.get("current_model", BotConfig.DEFAULT_MODEL)
    current_model_name = BotConfig.MODEL_NAMES[current_model]

    await message.answer(
        f"🤖 **Выбор AI модели**\n\n"
        f"Текущая модель: **{current_model_name}**\n\n"
        f"Выберите модель из списка ниже:",
        reply_markup=create_model_keyboard(),
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("model_"))
async def handle_model_selection(callback_query: types.CallbackQuery, state: FSMContext):
    """Обработчик выбора модели"""
    model_key = callback_query.data.split("_", 1)[1]

    if model_key in BotConfig.MODELS:
        await state.update_data(current_model=model_key)
        model_name = BotConfig.MODEL_NAMES[model_key]

        await callback_query.message.edit_text(
            f"✅ **Модель изменена**\n\n"
            f"Выбрана модель: **{model_name}**\n\n"
            f"Теперь все ваши сообщения будут обрабатываться этой моделью.",
            parse_mode="Markdown"
        )

        await callback_query.answer(f"Выбрана модель: {model_name}")
    else:
        await callback_query.answer("❌ Неизвестная модель", show_alert=True)


@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    global db_manager

    user_id = message.from_user.id

    if db_manager is not None:
        try:
            status = await db_manager.get_user_status(user_id)
            subscription_type = status["subscription_type"].title()
        except Exception as e:
            logging.error(f"Ошибка получения статуса в /help для {user_id}: {e}")
            subscription_type = "Free"
    else:
        subscription_type = "Free"

    await message.answer(
        "🤖 **Команды бота:**\n\n"
        "🔹 `/start` - Начать работу с ботом\n"
        "🔹 `/model` - Выбрать AI модель\n"
        "🔹 `/new` или `/newchat` - Начать новый диалог\n"
        "🔹 `/image <описание>` - Сгенерировать изображение\n"
        "🔹 `/mj <описание>` - Сгенерировать изображение Midjourney 6.0\n"
        "🔹 `/limits` - Посмотреть свои лимиты\n"
        "🔹 `/help` - Показать эту справку\n\n"
        "🤖 **Доступные AI модели:**\n\n" +
        "\n".join([f"• {name}" for name in BotConfig.MODEL_NAMES.values()]) +
        f"\n\n💎 **Ваш тариф: {subscription_type}**\n\n"
        "📝 **Что я умею:**\n\n"
        "💬 Отвечать на любые текстовые вопросы\n"
        "📸 Анализировать изображения и решать задачи с картинок\n"
        "🧮 Решать математические задачи пошагово\n"
        "📊 Объяснять схемы, графики и диаграммы\n"
        "💻 Помогать с программированием\n"
        "🎨 Генерировать изображения по описанию\n\n"
        "**Просто напишите мне или отправьте картинку!**",
        parse_mode="Markdown"
    )


@dp.message(Command("image"))
async def image_cmd(message: types.Message):
    global db_manager

    text = message.text or ""
    args = text[len("/image"):].strip()
    if not args:
        return await message.answer(
            "⚠️ Пожалуйста, укажи описание картинки:\n`/image <описание>`",
            parse_mode="Markdown"
        )

    if db_manager is None:
        await message.answer("⚠️ Система лимитов инициализируется, попробуйте через несколько секунд...")
        return

    user_id = message.from_user.id

    try:
        limit_check = await db_manager.check_limit(user_id, "flux_generation")

        if not limit_check["allowed"]:
            limit_message = db_manager.get_limit_message(user_id, "flux_generation", limit_check)
            await message.answer(limit_message, parse_mode="Markdown")
            return

        if not await db_manager.use_limit(user_id, "flux_generation"):
            await message.answer("❌ Не удалось использовать лимит. Попробуйте позже.")
            return

        await bot.send_chat_action(message.chat.id, "upload_photo")

        url, english_prompt, was_translated = await generate_image(args)

        status = await db_manager.get_user_status(user_id)
        remaining = status["limits"]["flux_generation"]["remaining"]
        limit_total = status["limits"]["flux_generation"]["limit"]

        if was_translated:
            caption = (
                f"🖼 Вот твоя картинка по запросу:\n"
                f"🇷🇺 `{args}`\n"
                f"🇺🇸 `{english_prompt}`\n"
                f"🎨 Flux: {remaining}/{limit_total} осталось"
            )
        else:
            caption = (
                f"🖼 Вот твоя картинка по запросу:\n`{args}`\n"
                f"🎨 Flux: {remaining}/{limit_total} осталось"
            )

        await message.answer_photo(url, caption=caption, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка генерации картинки: {e}")
        await message.answer("⚠️ Не удалось сгенерировать картинку. Попробуй позже.")


@dp.message(Command("mj"))
async def mj_cmd(message: types.Message):
    global db_manager

    text = message.text or ""
    user_prompt = text[len("/mj"):].strip()
    if not user_prompt:
        return await message.answer(
            "⚠️ Укажи описание для Midjourney 6.0:\n`/mj <описание>`",
            parse_mode="Markdown"
        )

    if db_manager is None:
        await message.answer("⚠️ Система лимитов инициализируется, попробуйте через несколько секунд...")
        return

    user_id = message.from_user.id

    try:
        limit_check = await db_manager.check_limit(user_id, "midjourney_generation")

        if not limit_check["allowed"]:
            limit_message = db_manager.get_limit_message(user_id, "midjourney_generation", limit_check)
            await message.answer(limit_message, parse_mode="Markdown")
            return

        if not await db_manager.use_limit(user_id, "midjourney_generation"):
            await message.answer("❌ Не удалось использовать лимит. Попробуйте позже.")
            return

        remaining = limit_check["remaining"] - 1
        processing_msg = await message.answer(
            f"🎨 Midjourney 6.0 генерирует изображение... (осталось: {remaining}/{limit_check['limit']})")

        animation_task = asyncio.create_task(
            show_processing_animation(
                chat_id=processing_msg.chat.id,
                message_id=processing_msg.message_id,
                duration=TIMEOUT,
                base_text="🎨 Midjourney 6.0 генерирует изображение"
            )
        )

        url = None
        english_prompt = user_prompt
        was_translated = False

        try:
            url, english_prompt, was_translated = await generate_image(user_prompt, "midjourney-6.0")
        except Exception as e:
            logging.error(f"Midjourney 6.0 error: {e}")
        finally:
            animation_task.cancel()
            try:
                await bot.delete_message(processing_msg.chat.id, processing_msg.message_id)
            except:
                pass

        if not url:
            return await message.answer("⚠️ Не удалось сгенерировать изображение Midjourney. Попробуйте позже.")

        status = await db_manager.get_user_status(user_id)
        remaining_now = status["limits"]["midjourney_generation"]["remaining"]
        limit_total = status["limits"]["midjourney_generation"]["limit"]

        await message.answer_photo(
            url,
            caption=(
                f"🖼 Midjourney 6.0 по запросу:\n"
                f"• исходный: `{user_prompt}`\n"
                f"• англ.: `{english_prompt}`\n"
                f"🎭 MJ: {remaining_now}/{limit_total} осталось"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Ошибка в команде /mj для пользователя {user_id}: {e}")
        await message.answer("⚠️ Не удалось сгенерировать изображение. Попробуйте позже.")


@dp.message(Command(commands=["new", "newchat"]))
async def new_chat_cmd(message: types.Message, state: FSMContext):
    data = await state.get_data()
    current_model = data.get("current_model", BotConfig.DEFAULT_MODEL)

    await state.clear()
    await state.update_data(current_model=current_model)

    model_name = BotConfig.MODEL_NAMES[current_model]
    await message.answer(
        f"🆕 Начинаем новый чат!\n"
        f"🤖 Модель: **{model_name}**\n\n"
        f"Напиши мне что-нибудь!",
        parse_mode="Markdown"
    )


# === АДМИНСКИЕ КОМАНДЫ ===
@dp.message(Command("admin"))
async def admin_cmd(message: types.Message):
    """Команда для админской панели"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для выполнения этой команды")
        return

    await message.answer(
        "🔧 **Админская панель**\n\n"
        "Выберите действие:",
        reply_markup=create_admin_keyboard(),
        parse_mode="Markdown"
    )


@dp.message(Command("admin_stats"))
async def admin_stats_cmd(message: types.Message):
    """Команда для просмотра статистики"""
    global db_manager

    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для выполнения этой команды")
        return

    if db_manager is None:
        await message.answer("⚠️ Система БД не инициализирована")
        return

    try:
        stats = await db_manager.get_statistics(days=7)

        stats_text = f"📊 **Статистика за 7 дней**\n\n"
        stats_text += f"👥 Всего пользователей: **{stats['total_users']}**\n\n"

        stats_text += "💎 **По типам подписки:**\n"
        for sub_type, count in stats['subscription_stats'].items():
            stats_text += f"• {sub_type.title()}: {count}\n"

        stats_text += "\n🎯 **Популярные функции:**\n"
        for feature in stats['feature_usage'][:5]:  # Топ 5
            stats_text += f"• {feature['limit_type']}: {feature['total_usage']} использований\n"

        stats_text += "\n📈 **Активность по дням:**\n"
        for day in stats['daily_activity'][-7:]:  # Последние 7 дней
            date = day['date']
            usage = day['total_usage']
            stats_text += f"• {date}: {usage} действий\n"

        await message.answer(stats_text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка получения статистики: {e}")
        await message.answer("❌ Ошибка получения статистики")


@dp.message(Command("admin_set_vip"))
async def admin_set_vip_cmd(message: types.Message):
    """Команда для установки VIP статуса"""
    global db_manager

    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для выполнения этой команды")
        return

    if db_manager is None:
        await message.answer("⚠️ Система БД не инициализирована")
        return

    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("Использование: /admin_set_vip <user_id>")
            return

        target_user_id = int(args[1])
        await db_manager.add_vip_user(target_user_id)

        await message.answer(f"✅ Пользователь {target_user_id} получил VIP статус")
    except ValueError:
        await message.answer("❌ Неверный ID пользователя")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("admin_set_premium"))
async def admin_set_premium_cmd(message: types.Message):
    """Команда для установки Premium подписки"""
    global db_manager

    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для выполнения этой команды")
        return

    if db_manager is None:
        await message.answer("⚠️ Система БД не инициализирована")
        return

    try:
        args = message.text.split()
        if len(args) != 3:
            await message.answer("Использование: /admin_set_premium <user_id> <days>")
            return

        target_user_id = int(args[1])
        days = int(args[2])

        await db_manager.set_subscription(target_user_id, "premium", days)

        await message.answer(f"✅ Пользователь {target_user_id} получил Premium подписку на {days} дней")
    except ValueError:
        await message.answer("❌ Неверные параметры")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("admin_user_info"))
async def admin_user_info_cmd(message: types.Message):
    """Команда для получения информации о пользователе"""
    global db_manager

    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для выполнения этой команды")
        return

    if db_manager is None:
        await message.answer("⚠️ Система БД не инициализирована")
        return

    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("Использование: /admin_user_info <user_id>")
            return

        target_user_id = int(args[1])
        status = await db_manager.get_user_status(target_user_id)

        username = status.get("username")
        first_name = status.get("first_name", "")
        last_name = status.get("last_name", "")

        display_name = f"@{username}" if username else "Нет username"
        full_name = f"{first_name} {last_name}".strip() or "Имя не указано"

        info_text = f"👤 **Информация о пользователе**\n\n"
        info_text += f"🆔 ID: `{target_user_id}`\n"
        info_text += f"👤 Username: {display_name}\n"
        info_text += f"📝 Имя: {full_name}\n"
        info_text += f"💎 Тариф: **{status['subscription_type'].title()}**\n"

        if status['subscription_expires']:
            expires = datetime.fromisoformat(status['subscription_expires'])
            info_text += f"📅 Действует до: {expires.strftime('%d.%m.%Y %H:%M')}\n"

        info_text += f"\n📊 **Использование за сегодня:**\n"

        limit_names = {
            "photo_analysis": "🖼 Анализ изображений",
            "flux_generation": "🎨 Генерация Flux",
            "midjourney_generation": "🎭 Генерация Midjourney",
            "text_requests": "💬 Текстовые запросы"
        }

        for limit_type, limit_info in status['limits'].items():
            if limit_type in limit_names:
                name = limit_names[limit_type]
                used = limit_info['used']
                limit = limit_info['limit']
                info_text += f"• {name}: {used}/{limit}\n"

        await message.answer(info_text, parse_mode="Markdown")

    except ValueError:
        await message.answer("❌ Неверный ID пользователя")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# === CALLBACK HANDLERS ===
@dp.callback_query(F.data == "admin_stats")
async def handle_admin_stats_callback(callback_query: types.CallbackQuery):
    """Обработчик кнопки статистики"""
    global db_manager

    if callback_query.from_user.id not in ADMIN_IDS:
        await callback_query.answer("❌ Нет доступа", show_alert=True)
        return

    if db_manager is None:
        await callback_query.answer("⚠️ Система БД не инициализирована", show_alert=True)
        return

    try:
        stats = await db_manager.get_statistics(days=7)

        stats_text = f"📊 **Статистика за 7 дней**\n\n"
        stats_text += f"👥 Всего пользователей: **{stats['total_users']}**\n\n"

        stats_text += "💎 **По типам подписки:**\n"
        for sub_type, count in stats['subscription_stats'].items():
            stats_text += f"• {sub_type.title()}: {count}\n"

        stats_text += "\n🎯 **Популярные функции:**\n"
        for feature in stats['feature_usage'][:3]:
            stats_text += f"• {feature['limit_type']}: {feature['total_usage']}\n"

        await callback_query.message.edit_text(stats_text, parse_mode="Markdown")
        await callback_query.answer()

    except Exception as e:
        await callback_query.answer("❌ Ошибка получения статистики", show_alert=True)


# === МЕДИА КОНТЕНТ ===
@dp.message(F.photo)
async def handle_photo(message: types.Message, state: FSMContext):
    """Обработчик изображений"""
    photo = message.photo[-1]
    await handle_image_message(message, state, photo.file_id, message.caption)


@dp.message(F.document)
async def handle_document(message: types.Message, state: FSMContext):
    """Обработчик документов (изображений в виде файлов)"""
    document = message.document

    if document.mime_type and document.mime_type.startswith('image/'):
        await handle_image_message(message, state, document.file_id, message.caption)
    else:
        await message.answer(
            "📄 Я могу анализировать только изображения.\n"
            "Отправьте изображение как фото или как файл в формате JPG, PNG, GIF или WebP."
        )


@dp.message(F.video)
async def handle_video(message: types.Message):
    """Обработчик видео"""
    await message.answer(
        "🎥 Я пока не умею анализировать видео.\n"
        "📸 Отправьте скриншот из видео, если нужно проанализировать изображение."
    )


@dp.message(F.voice)
async def handle_voice(message: types.Message):
    """Обработчик голосовых сообщений"""
    await message.answer(
        "🎤 Я пока не умею обрабатывать голосовые сообщения.\n"
        "💬 Напишите текстом или отправьте изображение для анализа."
    )


@dp.message(F.sticker)
async def handle_sticker(message: types.Message):
    """Обработчик стикеров"""
    await message.answer("😊 Симпатичный стикер! Чем могу помочь?")


# === ТЕКСТОВЫЕ СООБЩЕНИЯ ===
@dp.message(F.text.startswith('/'))
async def handle_unknown_command(message: types.Message):
    """Обработчик неизвестных команд"""
    command = message.text.split()[0]
    await message.answer(
        f"❓ Неизвестная команда: `{command}`\n\n"
        f"📋 Доступные команды:\n"
        f"• `/start` - Начать работу\n"
        f"• `/help` - Справка\n"
        f"• `/model` - Выбор AI модели\n"
        f"• `/new` - Новый диалог\n"
        f"• `/limits` - Ваши лимиты\n"
        f"• `/image <описание>` - Генерация изображения\n"
        f"• `/mj <описание>` - Генерация изображения Midjourney 6.0\n\n"
        f"💬 Или просто напишите сообщение без команды!",
        parse_mode="Markdown"
    )


@dp.message(F.text & ~F.text.startswith('/'))
async def handle_text(message: types.Message, state: FSMContext):
    """Обработчик обычного текста (не команд)"""
    global db_manager

    user_text = message.text
    user_id = message.from_user.id

    logging.info(f"Пользователь {user_id}: {user_text[:50]}...")

    if db_manager is None:
        await message.answer("⚠️ Система лимитов инициализируется, попробуйте через несколько секунд...")
        return

    try:
        limit_check = await db_manager.check_limit(user_id, "text_requests")
        if not limit_check["allowed"]:
            limit_message = db_manager.get_limit_message(user_id, "text_requests", limit_check)
            await message.answer(limit_message, parse_mode="Markdown")
            return

        processing_msg = await message.answer("🧠 Помощник обрабатывает сообщение...")

        data = await state.get_data()
        history = data.get("history", [])
        current_model = data.get("current_model", BotConfig.DEFAULT_MODEL)

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

        # Используем лимит на текстовые запросы (если включен)
        await db_manager.use_limit(user_id, "text_requests")

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

        logging.error(f"Ошибка при запросе к Помощнику: {e}")
        await message.answer(
            f"❌ Не удалось получить ответ от Помощника\n"
            f"💡 Возможные решения:\n"
            f"• Подождите немного и повторите\n"
            f"• Сократите длину сообщения\n"
            f"• Используйте /new для очистки контекста"
        )


# === ОБРАБОТЧИКИ ДРУГИХ СОБЫТИЙ ===
@dp.callback_query()
async def handle_callback_query(callback_query: types.CallbackQuery):
    """Обработчик callback queries (нажатия на inline кнопки)"""
    if not callback_query.data.startswith(("model_", "admin_")):
        logging.info(f"Получен неизвестный callback query: {callback_query.data}")
        await callback_query.answer("Функция пока не реализована")


@dp.message()
async def handle_all_other(message: types.Message):
    """Обработчик всех остальных типов сообщений"""
    logging.info(f"Получено необработанное сообщение типа: {message.content_type}")
    await message.answer(
        "🤔 Я получил ваше сообщение, но не знаю как его обработать.\n"
        "💬 Попробуйте:\n"
        "• Написать текстом\n"
        "• Отправить изображение\n"
        "• Использовать команды /start или /help"
    )


async def on_startup():
    """Функция, выполняемая при запуске бота"""
    global db_manager

    logging.info("Инициализация базы данных...")

    # Получаем конфигурацию БД
    config = DatabaseConfig.get_config_for_environment()

    # Создаем менеджер БД
    db_manager = DatabaseManager(**config)

    # Инициализируем БД
    await db_manager.init_database()

    logging.info("База данных инициализирована")
    logging.info("Бот запущен и готов к работе!")


async def main():
    """Основная функция запуска бота"""
    logging.info("Запуск бота...")

    # Инициализация при запуске
    await on_startup()

    # ВАЖНО: Middleware должен быть добавлен в правильном порядке
    # 1. Проверка подписки (самый первый)
    dp.update.middleware(SubscriptionCheckMiddleware())

    # 2. Обновление пользователей
    dp.update.middleware(UserUpdateMiddleware())

    # 3. Логирование (последний)
    dp.update.middleware(LoggingMiddleware())

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Начинаем polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())