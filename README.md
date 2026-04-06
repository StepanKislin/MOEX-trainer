# MOEX Trainer

MOEX Trainer - учебный веб-тренажер по инвестициям на данных Московской биржи. Пользователь может собирать виртуальный портфель, покупать и продавать акции, анализировать динамику, проходить стресс-сценарии и участвовать в комнатах, созданных учителем.

## Возможности

- загрузка рыночных данных из MOEX API;
- покупка и продажа акций по лотам;
- расчет стоимости портфеля, прибыли и убытка;
- расчет коэффициента Шарпа;
- стресс-тестирование портфеля на исторических сценариях;
- учебные комнаты для соревнования учеников;
- роли `ученик`, `учитель`, `администратор`;
- управление справочником бумаг и школьными кодами.

## Роли пользователей

### Ученик

- собирает портфель;
- покупает и продает бумаги;
- проходит стресс-тесты;
- участвует в комнатах и рейтингах.

### Учитель

- создает комнаты;
- задает сценарии и ограничения по бумагам;
- следит за рейтингом учеников;
- завершает комнаты.

### Администратор

- выдает школьные коды;
- управляет списком компаний;
- управляет глобальными стресс-сценариями.

## Основные страницы

- `/` — список доступных акций;
- `/portfolio` — личный портфель;
- `/stress` — стресс-сценарии;
- `/room` — учебная комната;
- `/dashboard` — личный кабинет;
- `/admin` — административная панель;
- `/login` — вход;
- `/register` — регистрация.

## Требования

- Python 3.11+ или совместимая версия Python 3;
- MySQL 8 или MAMP с MySQL;
- доступ к интернету для получения цен и истории с MOEX.

## Установка зависимостей

Из папки проекта:

```bash
python3 -m venv .venv
./.venv/bin/python3 -m pip install -r requirements.txt
```

На Windows:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Параметры базы данных

По умолчанию проект использует такие настройки:

- `DB_HOST=localhost`
- `DB_PORT=8889`
- `DB_NAME=evriki-nto`
- `DB_USER=root`
- `DB_PASSWORD=root`

Их можно переопределить через переменные окружения.

## Локальный запуск на macOS через MAMP

1. Открой `MAMP` и нажми `Start`.
2. Перейди в папку проекта (например /Users/Downloads/MOEX-trainer_audit/MOEX-trainer):

```bash
cd "Нахождение проекта"
```

3. Создай базу данных:

```bash
/Applications/MAMP/Library/bin/mysql80/bin/mysql -h localhost -P 8889 -u root -proot -e "CREATE DATABASE IF NOT EXISTS \`evriki-nto\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

4. Импортируй таблицы:

```bash
/Applications/MAMP/Library/bin/mysql80/bin/mysql -h localhost -P 8889 -u root -proot evriki-nto < init_db.sql
```

5. Удалить старую версию

```bash
rm -rf .venv
```

6. Создать новую (убедитесь, что python3 установлен)

```bash
python3 -m venv .venv
```

7. Активируйте её

```bash
source .venv/bin/activate
```

8. Установите зависимости

```bash
pip install -r requirements.txt
```

9. Запустите приложение

```bash
python app.py
```

10. Открой в браузере:

[http://127.0.0.1:5001](http://127.0.0.1:5001)

### Повторный запуск на macOS

Если база уже создана и зависимости уже установлены:

```bash
cd "/Users/Downloads/MOEX-trainer_audit_23/MOEX-trainer"
./.venv/bin/python3 app.py
```

Перед этим нужно только включить `MAMP`.

## Локальный запуск на Windows

1. Запусти MySQL или MAMP.
2. Перейди в папку проекта:

```powershell
cd "C:\path\to\MOEX-trainer"
```

3. Создай базу данных:

```powershell
mysql -h localhost -P 8889 -u root -proot -e "CREATE DATABASE IF NOT EXISTS evriki-nto CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

4. Импортируй таблицы:

```powershell
mysql -h localhost -P 8889 -u root -proot evriki-nto < init_db.sql
```

5. Удалите старую виртуальную среду
```powershell
rm -rf .venv
```

6. Создайте новую (убедитесь, что python3 установлен)
```powershell
python3 -m venv .venv
```

7. Дайте разрешение для активации
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```

8. Активируйте её
```powershell
.\.venv\Scripts\Activate.ps1
```

9. Установите зависимости
```powershell
pip install -r requirements.txt
```

10. Запустите приложение
```powershell
python app.py
```

11. Открой в браузере:

[http://127.0.0.1:5001](http://127.0.0.1:5001)

## Полезные файлы

- [app.py](./app.py) — основной сервер;
- [database.py](./database.py) — логика базы данных;
- [moex_api.py](./moex_api.py) — запросы к MOEX;
- [init_db.sql](./init_db.sql) — схема и начальные данные.


## Ссылка на видео

[https://disk.yandex.ru/d/m2JRKMNnvhPqjA](https://disk.yandex.ru/d/m2JRKMNnvhPqjA)