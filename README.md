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
│   │       ├── collect_exercises.py   # Основная команда парсинга
│   │       └── test_last10.py         # Диагностика: последние 10 query.php
│   └── migrations/
├── php/                     # Директория для отчётов test_last10 (txt)
├── .env                     # Переменные окружения
├── manage.py                # Точка входа Django
└── requirements.txt         # Зависимости
```

## Как это работает

1. **Авторизация** — Playwright открывает страницу входа, заполняет логин/пароль из `.env` и подтверждает вход.
2. **Переход** — после редиректа в админ-панель браузер переходит на страницу упражнений.
3. **Перехват** — каждый POST-ответ от `api/query.php` сохраняется как отдельная запись (список или словарь). Из тел ответов извлекаются только записи упражнений (наличие ключей `exerciseTitle`, `taskTitle`, `userName`).
4. **Пагинация** — на каждой странице собранные записи сохраняются в БД, затем скрипт ищет иконку `<i>chevron_right</i>` (в том числе внутри iframe) и кликает по ней. Цикл повторяется, пока кнопка «Следующая страница» не исчезнет / не станет disabled.
5. **Сохранение** — записи сохраняются через `update_or_create` по `record_id` (идемпотентно, дубли при пагинации не дублируются).

## Статус работы

Для каждой записи вычисляется `status`:

| status | Условие |
|---|---|
| `no_work` (Нет работы) | `answer` пустой **И** `files == []` |
| `has_work` (Есть работа) | `answer` не пустой **ИЛИ** в `files` есть хотя бы один файл |
| `has_mark` (Есть отметка) | флаг `passed == true` |

То есть работа считается сданной, если есть либо текстовый ответ, либо прикреплённые файлы.

## Команды Django

| Команда | Описание |
|---|---|
| `python manage.py collect_exercises` | Запуск парсера (сбор и сохранение всех страниц) |
| `python manage.py test_last10` | Диагностика: берёт последние 10 ответов `query.php` первой страницы и сохраняет отчёт по ключам в `php/test_last10_*.txt` |
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
Скрипт ищет `<i>` с текстом `chevron_right` во всех фреймах (включая iframe), затем — fallback по `button[aria-label="Следующая страница"]`. Если разметка изменилась — обновите селекторы в `collect_exercises.py`.
