# TikTok Username Checker Bot
# Базовый образ Python 3.11 (slim для меньшего размера)
FROM python:3.11-slim

# Метаданные образа
LABEL maintainer="TikTok Checker Bot"
LABEL description="Telegram бот для проверки доступности юзернеймов TikTok"

# Переменные окружения
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Рабочая директория
WORKDIR /app

# Копируем файл зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY checker.py .
COPY main.py .

# Создаём непривилегированного пользователя для безопасности
RUN adduser --disabled-password --gecos '' botuser && \
    chown -R botuser:botuser /app

# Переключаемся на непривилегированного пользователя
USER botuser

# Запуск бота
CMD ["python", "main.py"]
