"""
TikTok Username Checker Telegram Bot.

Бот для проверки доступности юзернеймов TikTok.
Поддерживает одиночную проверку и массовую проверку из файла.

Использует aiogram 3.24.0 с Router/Dispatcher архитектурой.
"""

import asyncio
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Загрузка переменных из .env файла
from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, FSInputFile
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from checker import TikTokChecker, UsernameStatus

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# Получаем токен бота из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    logger.error("❌ Переменная окружения BOT_TOKEN не установлена!")
    sys.exit(1)

# Создаём роутер для обработки сообщений
router = Router(name="main")

# Глобальный экземпляр чекера
checker: TikTokChecker | None = None


# Текстовые сообщения бота (на русском)
MESSAGES = {
    "start": """
🔍 <b>TikTok Username Checker Bot</b>

Добро пожаловать! Этот бот проверяет доступность юзернеймов в TikTok.

<b>📝 Как пользоваться:</b>

1️⃣ <b>Одиночная проверка:</b>
   Просто отправьте юзернейм (с @ или без).
   Пример: <code>username123</code> или <code>@username123</code>

2️⃣ <b>Массовая проверка:</b>
   Загрузите .txt файл со списком юзернеймов (по одному на строку).
   Бот проверит все юзернеймы и вернёт отчёт.

<b>📊 Статусы:</b>
✅ Доступен - юзернейм свободен
❌ Занят - юзернейм уже используется
⚠️ Недоступен - забанен или недействителен
🔴 Ошибка - не удалось проверить

<i>Отправьте юзернейм или файл для начала проверки!</i>
""",
    
    "checking": "⏳ Проверяю юзернейм <code>@{username}</code>...",
    
    "checking_bulk": "⏳ Начинаю массовую проверку {count} юзернеймов...\nЭто может занять некоторое время.",
    
    "file_empty": "⚠️ Файл пуст или не содержит валидных юзернеймов.",
    
    "file_too_large": "⚠️ Файл слишком большой! Максимум {max_count} юзернеймов за раз.",
    
    "file_error": "❌ Ошибка при чтении файла. Убедитесь, что это текстовый .txt файл в кодировке UTF-8.",
    
    "invalid_file_type": "⚠️ Поддерживаются только .txt файлы. Пожалуйста, загрузите текстовый файл.",
    
    "error": "❌ Произошла ошибка при проверке. Попробуйте позже.",
    
    "bulk_complete": """
✅ <b>Массовая проверка завершена!</b>

📊 <b>Результаты:</b>
• Всего проверено: {total}
• Доступных: {available}
• Занятых: {taken}
• Недоступных: {unavailable}
• Ошибок: {errors}

📄 Подробный отчёт прикреплён к сообщению.
""",
}

# Максимальное количество юзернеймов для массовой проверки
MAX_BULK_COUNT = 500


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Обработчик команды /start."""
    logger.info(f"Пользователь {message.from_user.id} запустил бота")
    await message.answer(MESSAGES["start"], parse_mode=ParseMode.HTML)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Обработчик команды /help."""
    await message.answer(MESSAGES["start"], parse_mode=ParseMode.HTML)


@router.message(F.document)
async def handle_document(message: Message) -> None:
    """Обработчик загруженных файлов."""
    global checker
    
    document = message.document
    
    # Проверяем тип файла
    if not document.file_name or not document.file_name.endswith('.txt'):
        await message.answer(MESSAGES["invalid_file_type"])
        return
    
    logger.info(
        f"Пользователь {message.from_user.id} загрузил файл: {document.file_name}"
    )
    
    try:
        # Скачиваем файл
        bot: Bot = message.bot
        file = await bot.get_file(document.file_id)
        
        # Читаем содержимое файла
        file_bytes = await bot.download_file(file.file_path)
        content = file_bytes.read().decode('utf-8', errors='ignore')
        
        # Парсим юзернеймы (один на строку)
        usernames = []
        for line in content.splitlines():
            username = line.strip()
            if username and not username.startswith('#'):
                # Удаляем @ в начале если есть
                clean_name = username.lstrip('@').strip()
                if clean_name and len(clean_name) >= 2:
                    usernames.append(clean_name)
        
        # Убираем дубликаты, сохраняя порядок
        seen = set()
        unique_usernames = []
        for u in usernames:
            lower_u = u.lower()
            if lower_u not in seen:
                seen.add(lower_u)
                unique_usernames.append(u)
        
        usernames = unique_usernames
        
        if not usernames:
            await message.answer(MESSAGES["file_empty"])
            return
        
        if len(usernames) > MAX_BULK_COUNT:
            await message.answer(
                MESSAGES["file_too_large"].format(max_count=MAX_BULK_COUNT)
            )
            return
        
        # Отправляем уведомление о начале проверки
        status_message = await message.answer(
            MESSAGES["checking_bulk"].format(count=len(usernames)),
            parse_mode=ParseMode.HTML
        )
        
        # Выполняем массовую проверку
        if checker is None:
            checker = TikTokChecker()
        
        results = await checker.check_bulk(usernames)
        
        # Подсчитываем статистику
        available_count = sum(1 for r in results if r.status == UsernameStatus.AVAILABLE)
        taken_count = sum(1 for r in results if r.status == UsernameStatus.TAKEN)
        unavailable_count = sum(1 for r in results if r.status == UsernameStatus.UNAVAILABLE)
        error_count = sum(1 for r in results if r.status == UsernameStatus.ERROR)
        
        # Генерируем отчёт
        report = TikTokChecker.format_results_report(results)
        
        # Сохраняем отчёт во временный файл
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_filename = f"tiktok_report_{timestamp}.txt"
        
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.txt',
            delete=False,
            encoding='utf-8'
        ) as tmp_file:
            tmp_file.write(report)
            tmp_path = tmp_file.name
        
        try:
            # Отправляем результаты
            await status_message.edit_text(
                MESSAGES["bulk_complete"].format(
                    total=len(results),
                    available=available_count,
                    taken=taken_count,
                    unavailable=unavailable_count,
                    errors=error_count
                ),
                parse_mode=ParseMode.HTML
            )
            
            # Отправляем файл с отчётом
            report_file = FSInputFile(tmp_path, filename=report_filename)
            await message.answer_document(
                report_file,
                caption="📄 Подробный отчёт о проверке юзернеймов"
            )
            
        finally:
            # Удаляем временный файл
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        
        logger.info(
            f"Массовая проверка для пользователя {message.from_user.id} завершена: "
            f"{len(results)} юзернеймов"
        )
        
    except UnicodeDecodeError:
        logger.error(f"Ошибка декодирования файла от пользователя {message.from_user.id}")
        await message.answer(MESSAGES["file_error"])
    except Exception as e:
        logger.exception(f"Ошибка при обработке файла: {e}")
        await message.answer(MESSAGES["error"])


@router.message(F.text)
async def handle_text(message: Message) -> None:
    """Обработчик текстовых сообщений (одиночная проверка юзернейма)."""
    global checker
    
    text = message.text.strip()
    
    # Пропускаем команды
    if text.startswith('/'):
        return
    
    # Извлекаем юзернейм
    username = text.lstrip('@').strip()
    
    if not username:
        return
    
    # Если несколько слов - берём первое
    if ' ' in username:
        username = username.split()[0]
    
    logger.info(f"Пользователь {message.from_user.id} проверяет: @{username}")
    
    # Отправляем уведомление о начале проверки
    status_message = await message.answer(
        MESSAGES["checking"].format(username=username),
        parse_mode=ParseMode.HTML
    )
    
    try:
        # Создаём чекер если ещё не создан
        if checker is None:
            checker = TikTokChecker()
        
        # Проверяем юзернейм
        result = await checker.check_username(username)
        
        # Формируем ответ
        status_emoji = {
            UsernameStatus.AVAILABLE: "✅",
            UsernameStatus.TAKEN: "❌",
            UsernameStatus.UNAVAILABLE: "⚠️",
            UsernameStatus.ERROR: "🔴",
        }
        
        emoji = status_emoji.get(result.status, "❓")
        
        response = f"""
{emoji} <b>Результат проверки</b>

👤 <b>Юзернейм:</b> <code>@{result.username}</code>
📊 <b>Статус:</b> {result.status.value}
"""
        
        if result.message:
            response += f"\n💬 <b>Детали:</b> {result.message}"
        
        await status_message.edit_text(response, parse_mode=ParseMode.HTML)
        
        logger.info(f"Результат для @{username}: {result.status.name}")
        
    except Exception as e:
        logger.exception(f"Ошибка при проверке {username}: {e}")
        await status_message.edit_text(MESSAGES["error"])


async def on_startup(bot: Bot) -> None:
    """Действия при запуске бота."""
    me = await bot.get_me()
    logger.info(f"✅ Бот запущен: @{me.username} (ID: {me.id})")


async def on_shutdown(bot: Bot) -> None:
    """Действия при остановке бота."""
    global checker
    
    if checker:
        await checker.close()
        checker = None
    
    logger.info("🛑 Бот остановлен")


async def main() -> None:
    """Главная функция запуска бота."""
    logger.info("🚀 Запуск TikTok Username Checker Bot...")
    
    # Создаём бота с настройками по умолчанию
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Создаём диспетчер
    dp = Dispatcher()
    
    # Регистрируем роутер
    dp.include_router(router)
    
    # Регистрируем обработчики запуска и остановки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    try:
        # Удаляем webhook если есть и запускаем polling
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        sys.exit(1)
