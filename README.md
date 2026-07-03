# ITcampusWorker

Парсер образовательной платформы IT campus (https://edu.firpo.ru/campus).  
Автоматизирует сбор данных об упражнениях через браузерную автоматизацию Playwright.

## Установка

### Ubuntu LTS

```bash
# Python и зависимости
sudo apt update && sudo apt install -y python3 python3-venv python3-pip postgresql postgresql-client libpq-dev

# Клонирование
git clone <repo-url> && cd ITcampusWorker

# Виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate

# Установка пакетов
pip install -r requirements.txt

# Chromium для Playwright (системный)
sudo apt install -y chromium-browser

# Или через сам Playwright:
# python -m playwright install chromium
```

### Windows 10

```powershell
# Python — скачайте с https://www.python.org/downloads/ (галка "Add to PATH")

# PostgreSQL — скачайте с https://www.postgresql.org/download/windows/

# Клонирование
git clone <repo-url> && cd ITcampusWorker

# Виртуальное окружение
python -m venv .venv
.venv\Scripts\activate

# Установка пакетов
pip install -r requirements.txt

# Chromium для Playwright
python -m playwright install chromium
```

## Настройка

### 1. Переменные окружения

Скопируйте `.env.example` в `.env` и заполните:

```ini
FIRPO_LOGIN=ваш-логин
FIRPO_PASSWORD=ваш-пароль

DB_NAME=firpocampus
DB_USER=postgres
DB_PASSWORD=пароль-бд
DB_HOST=localhost
DB_PORT=5432

DJANGO_SECRET_KEY=сгенерируйте-через-генератор
```

### 2. База данных

```bash
# Ubuntu
sudo -u postgres createdb firpocampus
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'your-password';"

# Windows (в psql от администратора)
createdb firpocampus
ALTER USER postgres PASSWORD 'your-password';
```

### 3. Миграции

```bash
source .venv/bin/activate      # Ubuntu
# .venv\Scripts\activate       # Windows

python manage.py migrate
```

## Запуск

```bash
# headless-режим (по умолчанию)
python manage.py collect_exercises

# с видимым браузером (для отладки)
python manage.py collect_exercises --no-headless
```

## Структура проекта

```
ITcampusWorker/
├── ITcampusWorker/          # Настройки Django
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── parser/                  # Django-приложение
│   ├── models.py            # ExerciseRecord — модель записи упражнения
│   ├── management/
│   │   └── commands/
│   │       └── collect_exercises.py   # Команда парсинга
│   └── migrations/
├── php/                     # Директория для JSON-дампов (опционально)
├── .env                     # Переменные окружения
├── manage.py                # Точка входа Django
└── requirements.txt         # Зависимости
```

## Как это работает

1. **Авторизация** — Playwright открывает страницу входа, заполняет логин/пароль из `.env` и подтверждает вход.
2. **Переход** — после редиректа в админ-панель браузер переходит на страницу упражнений.
3. **Перехват** — все POST-ответы от `api/query.php` сохраняются в список.
4. **Пагинация** — на каждой странице собираются записи с ключами `exerciseTitle`, `taskTitle`, `userName`, затем скрипт кликает «Следующая страница» и повторяет цикл, пока пагинация не закончится.
5. **Сохранение** — найденные записи сохраняются в БД (таблица `parser_exerciserecord`).

## Команды Django

| Команда | Описание |
|---|---|
| `python manage.py collect_exercises` | Запуск парсера |
| `python manage.py showmigrations` | Статус миграций |
| `python manage.py migrate` | Применить миграции |
| `python manage.py makemigrations` | Создать миграции |

## Возможные проблемы

**Playwright не запускается на Ubuntu:**
```bash
# Установите недостающие системные библиотеки
npx playwright install-deps chromium
```

**SynchronousOnlyOperation при сохранении:**
Используется `sync_to_async` из `asgiref.sync` — все ORM-вызовы обёрнуты корректно.

**Не находится кнопка пагинации:**
Скрипт сначала ищет `button[aria-label="Следующая страница"]` на главной странице, затем во всех фреймах. Если селектор изменился — обновите в `collect_exercises.py`.
