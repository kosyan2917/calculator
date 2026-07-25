# STALZONE Artifact Build Optimizer

Веб-интерфейс для поиска сборок артефактов под бюджет, костюм, контейнер и пятиуровневые приоритеты характеристик. FastAPI выполняет поиск, React показывает параметры и найденные сборки.

Production состоит из независимых сервисов:

- `gateway` — Caddy с собранной React-статикой, HTTPS и маршрутизацией;
- `artcalc-api` — текущий API расчета сборок;
- `simulator` — отдельный микросервис расчёта стрельбы.

## Production-деплой

На Linux-сервере нужны Git, Docker Engine и Docker Compose v2. После клонирования выполните один раз:

```bash
./deploy/setup.sh artifacts.example.com
```

Без готового домена можно передать публичный IPv4 сервера:

```bash
./deploy/setup.sh 203.0.113.10
```

Для домена скрипт включит автоматический HTTPS, для IP сервис будет доступен по обычному HTTP. Скрипт создаст локальный `.env`, настроит Git hook и запустит весь стек. После этого обновление выполняется обычной командой:

```bash
git pull
```

Подробные требования к DNS, firewall, обновлению и откату описаны в [DEPLOYMENT.md](DEPLOYMENT.md).

Для локального запуска production-стека скопируйте `.env.example` в `.env`, укажите адрес и выполните `docker compose up --build`.

## Локальный production-запуск

```powershell
python -m pip install -r requirements.txt
Set-Location web/frontend
npm ci
npm run build
Set-Location ../..
python -m uvicorn web.backend.app:app --host 127.0.0.1 --port 8000
```

## Разработка

Бэкенд и Vite можно запускать отдельно. Vite проксирует `/api` на порт `8000`.

```powershell
# Терминал 1
python -m uvicorn web.backend.app:app --reload --port 8000

# Терминал 2
Set-Location web/frontend
npm run dev
```

Основные точки интеграции:

- `GET /api/catalog` возвращает костюмы, контейнеры, характеристики и уровни приоритета.
- `POST /api/optimize` принимает фильтр и стратегию `best_now`, `balanced` или `upgrade`.
- `POST /api/upgrade-plans` рассчитывает конкретные покупки для выбранной сборки при дополнительных бюджетах. Имеющиеся артефакты считаются бесплатными, стоимость смены контейнера не учитывается.
- `GET /api/simulator/health` проверяет доступность сервиса симулятора.
- `POST /api/simulator/shooting` рассчитывает ожидаемые TTK и число пуль с учётом дистанции, меткости, пулестойкости, живучести и бронепробития патрона.
- `combat_simulator.ShootingSimulator` содержит независимое от HTTP ядро расчёта и может импортироваться напрямую в другую Python-программу.
- `artcalc.ArtifactBuildOptimizer` остается независимым от HTTP и может встраиваться напрямую в другую программу.
- `artcalc.BuildStrategyRanker` отдельно ранжирует готовые сборки по текущей силе и переносимости артефактов.
- `artcalc.UpgradePlanner` отдельно строит пути улучшения с перечнем сохраненных, проданных и купленных артефактов.

## Проверка

```powershell
python -m unittest discover -s tests -v
Set-Location web/frontend
npm run build
```
