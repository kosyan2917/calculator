# STALZONE Artifact Build Optimizer

Веб-интерфейс для поиска сборок артефактов под бюджет, костюм, контейнер и пятиуровневые приоритеты характеристик. FastAPI выполняет поиск, React показывает параметры и найденные сборки. В production FastAPI также раздает собранный фронтенд.

## Production-деплой

На Linux-сервере нужны Git, Docker Engine и Docker Compose v2. После клонирования выполните один раз:

```bash
./deploy/setup.sh artifacts.example.com
```

Скрипт создаст локальный `.env`, настроит Git hook и запустит Caddy с автоматическим HTTPS. После этого обновление выполняется обычной командой:

```bash
git pull
```

Подробные требования к DNS, firewall, обновлению и откату описаны в [DEPLOYMENT.md](DEPLOYMENT.md).

Для локального запуска production-стека скопируйте `.env.example` в `.env`, укажите домен и выполните `docker compose up --build`.

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
- `artcalc.ArtifactBuildOptimizer` остается независимым от HTTP и может встраиваться напрямую в другую программу.
- `artcalc.BuildStrategyRanker` отдельно ранжирует готовые сборки по текущей силе и переносимости артефактов.
- `artcalc.UpgradePlanner` отдельно строит пути улучшения с перечнем сохраненных, проданных и купленных артефактов.

## Проверка

```powershell
python -m unittest discover -s tests -v
Set-Location web/frontend
npm run build
```
