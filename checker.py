"""
Модуль проверки доступности TikTok юзернеймов.

Этот модуль содержит класс TikTokChecker для асинхронной проверки
статуса юзернеймов TikTok: доступен, занят или недоступен (забанен).
"""

import asyncio
import logging
import re
from enum import Enum
from dataclasses import dataclass
from typing import Optional

import aiohttp

# Настройка логирования
logger = logging.getLogger(__name__)


class UsernameStatus(Enum):
    """Статусы юзернейма TikTok."""
    AVAILABLE = "✅ Доступен"
    TAKEN = "❌ Занят"
    UNAVAILABLE = "⚠️ Недоступен (забанен/недействителен)"
    ERROR = "🔴 Ошибка проверки"


@dataclass
class CheckResult:
    """Результат проверки юзернейма."""
    username: str
    status: UsernameStatus
    message: Optional[str] = None


class TikTokChecker:
    """
    Класс для проверки доступности TikTok юзернеймов.
    
    Использует асинхронные HTTP-запросы для определения статуса юзернейма.
    Поддерживает bulk-проверку с ограничением concurrent запросов.
    """
    
    # URL для проверки профиля TikTok
    TIKTOK_USER_URL = "https://www.tiktok.com/@{username}"
    
    # Регулярное выражение для валидации юзернейма
    # TikTok юзернеймы: 2-24 символа, буквы, цифры, точки и подчёркивания
    USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_.]{2,24}$')
    
    # Максимальное количество одновременных запросов
    DEFAULT_CONCURRENT_LIMIT = 10
    
    # Таймаут для HTTP-запросов (секунды)
    REQUEST_TIMEOUT = 15
    
    # Количество попыток при ошибке
    MAX_RETRIES = 3
    
    # Задержка между попытками (секунды)
    RETRY_DELAY = 2
    
    def __init__(self, concurrent_limit: int = DEFAULT_CONCURRENT_LIMIT):
        """
        Инициализация чекера.
        
        Args:
            concurrent_limit: Максимальное количество одновременных запросов.
        """
        self._semaphore = asyncio.Semaphore(concurrent_limit)
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Получение или создание HTTP-сессии."""
        if self._session is None or self._session.closed:
            # Заголовки для имитации браузера
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
            
            timeout = aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT)
            self._session = aiohttp.ClientSession(headers=headers, timeout=timeout)
        
        return self._session
    
    async def close(self) -> None:
        """Закрытие HTTP-сессии."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    def validate_username(self, username: str) -> bool:
        """
        Валидация формата юзернейма.
        
        Args:
            username: Юзернейм для проверки.
            
        Returns:
            True если формат валидный, иначе False.
        """
        if not username:
            return False
        
        # Удаляем @ в начале если есть
        clean_username = username.lstrip('@').strip()
        
        return bool(self.USERNAME_PATTERN.match(clean_username))
    
    def clean_username(self, username: str) -> str:
        """
        Очистка юзернейма от лишних символов.
        
        Args:
            username: Исходный юзернейм.
            
        Returns:
            Очищенный юзернейм.
        """
        return username.lstrip('@').strip().lower()
    
    async def check_username(self, username: str) -> CheckResult:
        """
        Проверка одного юзернейма.
        
        Args:
            username: Юзернейм для проверки.
            
        Returns:
            Результат проверки с статусом.
        """
        clean_name = self.clean_username(username)
        
        # Валидация формата
        if not self.validate_username(clean_name):
            logger.warning(f"Невалидный формат юзернейма: {username}")
            return CheckResult(
                username=username,
                status=UsernameStatus.UNAVAILABLE,
                message="Неверный формат юзернейма (2-24 символа, буквы, цифры, _ и .)"
            )
        
        async with self._semaphore:
            return await self._check_with_retry(clean_name)
    
    async def _check_with_retry(self, username: str) -> CheckResult:
        """
        Проверка юзернейма с retry-логикой.
        
        Args:
            username: Очищенный юзернейм.
            
        Returns:
            Результат проверки.
        """
        last_error = None
        
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                return await self._perform_check(username)
            except aiohttp.ClientError as e:
                last_error = e
                logger.warning(
                    f"Попытка {attempt}/{self.MAX_RETRIES} для @{username} не удалась: {e}"
                )
                
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self.RETRY_DELAY * attempt)
            except asyncio.TimeoutError:
                last_error = "Таймаут запроса"
                logger.warning(
                    f"Таймаут при проверке @{username}, попытка {attempt}/{self.MAX_RETRIES}"
                )
                
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self.RETRY_DELAY * attempt)
        
        logger.error(f"Все попытки проверки @{username} исчерпаны: {last_error}")
        return CheckResult(
            username=username,
            status=UsernameStatus.ERROR,
            message=f"Ошибка после {self.MAX_RETRIES} попыток: {last_error}"
        )
    
    async def _perform_check(self, username: str) -> CheckResult:
        """
        Выполнение проверки юзернейма.
        
        Сначала пробуем API, затем fallback на HTML парсинг.
        
        Args:
            username: Очищенный юзернейм.
            
        Returns:
            Результат проверки.
        """
        logger.debug(f"Проверка юзернейма: @{username}")
        
        # Сначала пробуем API проверку (более надёжная)
        api_result = await self._check_via_api(username)
        if api_result is not None:
            return api_result
        
        # Fallback на HTML парсинг
        logger.debug(f"@{username}: API недоступен, используем HTML парсинг")
        
        session = await self._get_session()
        url = self.TIKTOK_USER_URL.format(username=username)
        
        async with session.get(url, allow_redirects=True) as response:
            status_code = response.status
            
            logger.debug(f"@{username}: HTTP статус {status_code}")
            
            # Получаем контент для анализа
            content = await response.text()
            
            # Анализируем ответ
            return self._analyze_response(username, status_code, content)
    
    async def _check_via_api(self, username: str) -> Optional[CheckResult]:
        """
        Проверка юзернейма через TikTok API endpoint.
        
        Этот метод более надёжен чем парсинг HTML страницы.
        
        Args:
            username: Очищенный юзернейм.
            
        Returns:
            Результат проверки или None если API недоступен.
        """
        session = await self._get_session()
        
        # TikTok API endpoint для проверки пользователя
        api_url = f"https://www.tiktok.com/api/user/detail/?uniqueId={username}&secUid="
        
        try:
            async with session.get(api_url, allow_redirects=True) as response:
                if response.status == 200:
                    try:
                        data = await response.json()
                        
                        # Проверяем статус код в ответе API
                        status_code = data.get("statusCode", data.get("status_code", 0))
                        
                        # statusCode 0 = успех, пользователь существует
                        if status_code == 0:
                            user_info = data.get("userInfo", {})
                            user = user_info.get("user", {})
                            if user.get("uniqueId", "").lower() == username.lower():
                                logger.info(f"@{username}: Занят (подтверждено через API)")
                                return CheckResult(
                                    username=username,
                                    status=UsernameStatus.TAKEN,
                                    message="Юзернейм занят (подтверждено через API)"
                                )
                        
                        # statusCode 10202 = пользователь не существует
                        if status_code == 10202:
                            logger.info(f"@{username}: Доступен (подтверждено через API)")
                            return CheckResult(
                                username=username,
                                status=UsernameStatus.AVAILABLE,
                                message="Юзернейм свободен (подтверждено через API)"
                            )
                        
                        # statusCode 10101 = аккаунт забанен
                        if status_code == 10101:
                            logger.info(f"@{username}: Забанен (подтверждено через API)")
                            return CheckResult(
                                username=username,
                                status=UsernameStatus.UNAVAILABLE,
                                message="Аккаунт забанен"
                            )
                            
                    except Exception as e:
                        logger.debug(f"Ошибка парсинга API ответа для @{username}: {e}")
                        
        except Exception as e:
            logger.debug(f"API проверка не удалась для @{username}: {e}")
        
        return None
    
    def _analyze_response(
        self, 
        username: str, 
        status_code: int, 
        content: str
    ) -> CheckResult:
        """
        Анализ HTTP-ответа для определения статуса юзернейма.
        
        ВАЖНО: По умолчанию считаем юзернейм ЗАНЯТЫМ, если не доказано обратное.
        Это обеспечивает более точные результаты (меньше ложных "доступен").
        
        Args:
            username: Проверяемый юзернейм.
            status_code: HTTP статус код.
            content: Содержимое ответа.
            
        Returns:
            Результат с определённым статусом.
        """
        content_lower = content.lower()
        username_lower = username.lower()
        
        # 404 - профиль не существует, юзернейм свободен
        if status_code == 404:
            logger.info(f"@{username}: Доступен (404)")
            return CheckResult(
                username=username,
                status=UsernameStatus.AVAILABLE,
                message="Юзернейм свободен для регистрации"
            )
        
        # 200 - страница загружена, проверяем содержимое
        if status_code == 200:
            # === ПРОВЕРКА НА "ПОЛЬЗОВАТЕЛЬ НЕ НАЙДЕН" ===
            # Эти признаки ТОЧНО означают что юзернейм свободен
            definite_not_found = [
                '"statuscode":10202',  # TikTok API код: пользователь не найден
                '"statuscode": 10202',
                '"status_code":10202',
                '"status_code": 10202',
                '"statusmsg":"user not exist"',
                '"statusmsg": "user not exist"',
                '"statusmsg":"user doesn\'t exist"',
                '"errormsg":"user not exist"',
            ]
            
            for indicator in definite_not_found:
                if indicator.lower() in content_lower:
                    logger.info(f"@{username}: Доступен (API код не найден: '{indicator}')")
                    return CheckResult(
                        username=username,
                        status=UsernameStatus.AVAILABLE,
                        message="Юзернейм свободен для регистрации"
                    )
            
            # === ПРОВЕРКА НА СУЩЕСТВОВАНИЕ ПРОФИЛЯ ===
            # Ищем uniqueId в JSON-данных страницы
            # Паттерны для поиска uniqueId
            uniqueid_patterns = [
                f'"uniqueid":"{username_lower}"',
                f'"uniqueid": "{username_lower}"',
                f'"unique_id":"{username_lower}"',
                f'"unique_id": "{username_lower}"',
                f'"uniqueId":"{username_lower}"',
                f'"uniqueId": "{username_lower}"',
            ]
            
            for pattern in uniqueid_patterns:
                if pattern.lower() in content_lower:
                    logger.info(f"@{username}: Занят (найден uniqueId в JSON)")
                    return CheckResult(
                        username=username,
                        status=UsernameStatus.TAKEN,
                        message="Юзернейм уже занят другим пользователем"
                    )
            
            # Проверяем наличие данных профиля в JSON
            profile_indicators = [
                '"followercount"',
                '"followingcount"', 
                '"heartcount"',
                '"videocount"',
                '"diggcount"',
                '"follower_count"',
                '"following_count"',
                '"heart_count"',
            ]
            
            profile_score = sum(1 for ind in profile_indicators if ind in content_lower)
            
            # Если найдено 2+ признака профиля - аккаунт занят
            if profile_score >= 2:
                logger.info(f"@{username}: Занят (найдено {profile_score} признаков профиля)")
                return CheckResult(
                    username=username,
                    status=UsernameStatus.TAKEN,
                    message="Юзернейм уже занят другим пользователем"
                )
            
            # === ПРОВЕРКА НА ЗАБАНЕННЫЙ АККАУНТ ===
            banned_indicators = [
                "this account has been banned",
                "account suspended",
                "this account is suspended",
                "this account was banned",
                "account has been suspended",
                "violates our community guidelines",
                '"statuscode":10101',
                '"status_code":10101',
            ]
            
            for indicator in banned_indicators:
                if indicator.lower() in content_lower:
                    logger.info(f"@{username}: Недоступен (забанен)")
                    return CheckResult(
                        username=username,
                        status=UsernameStatus.UNAVAILABLE,
                        message="Аккаунт забанен (юзернейм может стать доступен позже)"
                    )
            
            # === ПРОВЕРКА ТЕКСТОВЫХ ПРИЗНАКОВ "НЕ НАЙДЕН" ===
            # Эти менее надёжные, проверяем в конце
            text_not_found = [
                "couldn't find this account",
                "couldn't find this page",
                "user not found",
                "page not found",
                "this account doesn't exist",
                "user doesn't exist",
            ]
            
            for indicator in text_not_found:
                if indicator.lower() in content_lower:
                    logger.info(f"@{username}: Доступен (текст не найден: '{indicator}')")
                    return CheckResult(
                        username=username,
                        status=UsernameStatus.AVAILABLE,
                        message="Юзернейм свободен для регистрации"
                    )
            
            # === ПО УМОЛЧАНИЮ: СЧИТАЕМ ЗАНЯТЫМ ===
            # Если нет явных признаков что юзернейм свободен - считаем занятым
            # Это ВАЖНОЕ изменение! Раньше по умолчанию считалось "доступен"
            logger.info(f"@{username}: Предположительно занят (нет подтверждения свободности)")
            return CheckResult(
                username=username,
                status=UsernameStatus.TAKEN,
                message="Юзернейм предположительно занят (требуется ручная проверка)"
            )
        
        # Другие статусы
        if status_code == 403:
            logger.warning(f"@{username}: Доступ запрещён (403)")
            return CheckResult(
                username=username,
                status=UsernameStatus.ERROR,
                message="Доступ запрещён (возможно rate-limit)"
            )
        
        if status_code >= 500:
            logger.error(f"@{username}: Ошибка сервера TikTok ({status_code})")
            return CheckResult(
                username=username,
                status=UsernameStatus.ERROR,
                message=f"Ошибка сервера TikTok: {status_code}"
            )
        
        # Неизвестный статус - считаем занятым для безопасности
        logger.warning(f"@{username}: Неопределённый статус ({status_code}) - считаем занятым")
        return CheckResult(
            username=username,
            status=UsernameStatus.TAKEN,
            message=f"Неопределённый статус (HTTP {status_code}) - предположительно занят"
        )
    
    async def check_bulk(self, usernames: list[str]) -> list[CheckResult]:
        """
        Массовая проверка списка юзернеймов.
        
        Использует последовательную проверку с задержкой для избежания
        rate-limiting от TikTok.
        
        Args:
            usernames: Список юзернеймов для проверки.
            
        Returns:
            Список результатов проверки.
        """
        if not usernames:
            return []
        
        logger.info(f"Начинаем массовую проверку {len(usernames)} юзернеймов")
        
        results = []
        
        # Последовательная проверка с задержкой между запросами
        # чтобы избежать rate-limiting от TikTok
        for i, username in enumerate(usernames):
            try:
                result = await self.check_username(username)
                results.append(result)
                
                # Логируем прогресс каждые 10 юзернеймов
                if (i + 1) % 10 == 0:
                    logger.info(f"Проверено {i + 1}/{len(usernames)} юзернеймов")
                
                # Задержка между запросами для избежания rate-limiting
                # 0.5 секунды достаточно для избежания блокировки
                if i < len(usernames) - 1:
                    await asyncio.sleep(0.5)
                    
            except Exception as e:
                logger.error(f"Исключение при проверке {username}: {e}")
                results.append(CheckResult(
                    username=username,
                    status=UsernameStatus.ERROR,
                    message=str(e)
                ))
        
        logger.info(f"Массовая проверка завершена: {len(results)} результатов")
        
        return results
    
    @staticmethod
    def format_result(result: CheckResult) -> str:
        """
        Форматирование результата для отображения.
        
        Args:
            result: Результат проверки.
            
        Returns:
            Отформатированная строка.
        """
        return f"@{result.username}: {result.status.value}"
    
    @staticmethod
    def format_results_report(results: list[CheckResult]) -> str:
        """
        Форматирование списка результатов в текстовый отчёт.
        
        Args:
            results: Список результатов проверки.
            
        Returns:
            Отформатированный отчёт.
        """
        if not results:
            return "Нет результатов для отображения."
        
        # Группируем результаты по статусу
        available = [r for r in results if r.status == UsernameStatus.AVAILABLE]
        taken = [r for r in results if r.status == UsernameStatus.TAKEN]
        unavailable = [r for r in results if r.status == UsernameStatus.UNAVAILABLE]
        errors = [r for r in results if r.status == UsernameStatus.ERROR]
        
        lines = [
            "═" * 40,
            "📊 ОТЧЁТ О ПРОВЕРКЕ ЮЗЕРНЕЙМОВ TIKTOK",
            "═" * 40,
            "",
            f"📈 Всего проверено: {len(results)}",
            f"✅ Доступных: {len(available)}",
            f"❌ Занятых: {len(taken)}",
            f"⚠️ Недоступных: {len(unavailable)}",
            f"🔴 Ошибок: {len(errors)}",
            "",
        ]
        
        if available:
            lines.append("─" * 40)
            lines.append("✅ ДОСТУПНЫЕ ЮЗЕРНЕЙМЫ:")
            lines.append("─" * 40)
            for r in available:
                lines.append(f"  • @{r.username}")
            lines.append("")
        
        if taken:
            lines.append("─" * 40)
            lines.append("❌ ЗАНЯТЫЕ ЮЗЕРНЕЙМЫ:")
            lines.append("─" * 40)
            for r in taken:
                lines.append(f"  • @{r.username}")
            lines.append("")
        
        if unavailable:
            lines.append("─" * 40)
            lines.append("⚠️ НЕДОСТУПНЫЕ ЮЗЕРНЕЙМЫ:")
            lines.append("─" * 40)
            for r in unavailable:
                lines.append(f"  • @{r.username} - {r.message or 'Забанен/недействителен'}")
            lines.append("")
        
        if errors:
            lines.append("─" * 40)
            lines.append("🔴 ОШИБКИ ПРОВЕРКИ:")
            lines.append("─" * 40)
            for r in errors:
                lines.append(f"  • @{r.username} - {r.message or 'Неизвестная ошибка'}")
            lines.append("")
        
        lines.append("═" * 40)
        lines.append("Конец отчёта")
        lines.append("═" * 40)
        
        return "\n".join(lines)


async def main():
    """Тестирование модуля."""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    checker = TikTokChecker()
    
    try:
        # Тестовые юзернеймы
        test_usernames = ["tiktok", "test_available_name_12345", "a"]
        
        print("Тестирование TikTok Checker...")
        
        for username in test_usernames:
            result = await checker.check_username(username)
            print(TikTokChecker.format_result(result))
        
    finally:
        await checker.close()


if __name__ == "__main__":
    asyncio.run(main())
