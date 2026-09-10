# STALZONE Artifact Build Optimizer

Веб-интерфейс для поиска сборок артефактов под бюджет, костюм, контейнер и обязательные границы характеристик. FastAPI возвращает только сборки, прошедшие точный пересчет всех заданных условий; React показывает параметры и найденные варианты.

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

Актуальные правила поиска, бюджета, цен +15 и личного обучения выдачи описаны в [docs/search-and-feedback.md](docs/search-and-feedback.md).

Вкладка «Шизо-варианты» ищет сборки с одной активной реакцией: электричество, горение или разрыв. Формулы и отдельный генератор `ReactionBuildGenerator` описаны в [docs/reaction-builds.md](docs/reaction-builds.md).

- `GET /api/catalog` возвращает костюмы, контейнеры и доступные характеристики.
- `POST /api/optimize` принимает фильтры и `targets` с обязательными значениями. Бюджет от 100 000 рублей без фиксированного шага; `budget: null` отключает учёт цены и допускает пустые `targets`. Генератор адаптивно заполняет промежутки между скоростным и защитным краями и сохраняет альтернативные составы. `personalize: false` отключает личное ранжирование.
- `POST /api/feedback`, `GET /api/feedback` и `DELETE /api/feedback/{id}` сохраняют, показывают и отменяют личные оценки. В Docker история хранится в отдельном постоянном томе `feedback_data`.
- `POST /api/upgrade-plans` ищет конкретные изменения, которые выполняют переданные обязательные значения при дополнительных бюджетах. Имеющиеся артефакты считаются бесплатными, стоимость смены контейнера не учитывается.
- `GET /api/simulator/health` проверяет доступность сервиса симулятора.
- `POST /api/simulator/shooting` рассчитывает ожидаемые TTK и число пуль с учётом дистанции, меткости, пулестойкости, живучести и бронепробития патрона.
- `combat_simulator.ShootingSimulator` содержит независимое от HTTP ядро расчёта и может импортироваться напрямую в другую Python-программу.
- `artcalc.FrontierBuildGenerator` является независимым от HTTP основным генератором и может встраиваться напрямую в другую программу. `artcalc.ArtifactBuildOptimizer` используется как одноцелевой математический решатель.
- `artcalc.BuildUpgradePotentialAnalyzer` отдельно оценивает переносимость артефактов между скоростными, защитными, регенерационными, выносливостными и весовыми сборками, а также проверяет перенос полного комплекта в другие и более вместительные контейнеры с точным расчетом заражений.
- `artcalc.UpgradePlanner` отдельно строит пути улучшения с перечнем сохраненных, проданных и купленных артефактов.

## Проверка

```powershell
python -m unittest discover -s tests -v
Set-Location web/frontend
npm run build
```
