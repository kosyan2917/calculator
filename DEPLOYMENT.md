# Production deployment

## Требования к серверу

- Linux x86_64, Git, Docker Engine и Docker Compose v2 с поддержкой `docker compose up --wait`.
- Рекомендуемый размер для нерегулярной аудитории около 500 человек: 2 vCPU, 4 GB RAM и 20 GB SSD.
- Порты TCP `80`, TCP `443` и UDP `443` должны быть открыты во внешнем firewall и не заняты другим reverse proxy.
- Пользователь деплоя должен иметь доступ к Docker без интерактивного `sudo`.

Приложение не использует базу данных. Постоянные Docker volumes содержат только состояние Caddy и TLS-сертификаты.

## DNS

До первого запуска создайте у DNS-провайдера запись:

```text
A  artifacts.example.com  <публичный IPv4 сервера>
```

Добавляйте `AAAA` только если IPv6 действительно настроен и доступен на сервере. Для домена с кириллицей передавайте setup-скрипту ASCII/punycode-форму. Значение должно быть доменом без `https://`, порта и пути.

Проверьте, что домен уже разрешается в IP сервера:

```bash
getent ahosts artifacts.example.com
```

## Первый запуск

```bash
git clone <repository-url> /opt/stalzone-artcalc
cd /opt/stalzone-artcalc
./deploy/setup.sh artifacts.example.com
```

`setup.sh` выполняет следующие действия:

1. Проверяет Git, Docker и Compose v2.
2. Создает закрытый от Git файл `.env` и ограничивает его права до `600`.
3. Настраивает для этого clone `core.hooksPath=.githooks` и fast-forward pull.
4. Собирает `gateway`, `artcalc-api` и `simulator`, затем ждет успешных healthcheck.

`gateway` содержит production-сборку React и Caddy, который автоматически запросит и будет обновлять TLS-сертификат. Оба FastAPI-сервиса доступны только внутри Docker-сети. Gateway направляет обычные `/api/*` запросы в `artcalc-api`, а `/api/simulator/*` — в `simulator`.

Проверка после запуска:

```bash
docker compose ps
curl -I https://artifacts.example.com/
curl https://artifacts.example.com/api/health
curl https://artifacts.example.com/api/simulator/health
```

## Обновление

В этом же clone достаточно выполнить:

```bash
git pull
```

Git вызывает `.githooks/post-merge`, который запускает `deploy/deploy.sh`. Новые images сначала полностью собираются, затем Compose переключает контейнеры и ждет healthcheck. При неуспешном переключении скрипт возвращает предыдущий согласованный набор images для всех трех сервисов.

Одновременные деплои блокируются каталогом `.deploy.lock`. Hook срабатывает для обычного fast-forward `git pull`; обновление через `git reset`, `git checkout` или ручную замену файлов нужно завершать командой:

```bash
./deploy/deploy.sh
```

Это автоматический in-place deployment с коротким перезапуском контейнеров, а не полноценный zero-downtime blue/green deployment.

## Управление

```bash
# Состояние и healthcheck
docker compose ps

# Логи всех сервисов
docker compose logs -f --tail=200

# Только API калькулятора
docker compose logs -f --tail=200 artcalc-api

# Новый сервис симулятора
docker compose logs -f --tail=200 simulator

# Ручная повторная сборка и запуск
./deploy/deploy.sh

# Остановка без удаления TLS volumes
docker compose down
```

Чтобы изменить домен или число Uvicorn workers, отредактируйте `.env` и запустите `./deploy/deploy.sh`. Не коммитьте `.env`.

## Если обновление не прошло

Git уже может находиться на новом commit, даже если post-merge hook вернул ошибку. Работающие контейнеры при ошибке сборки не затрагиваются; при ошибке healthcheck выполняется попытка возврата предыдущих images.

Сначала изучите состояние и логи:

```bash
docker compose ps
docker compose logs --tail=300 gateway artcalc-api simulator
```

После устранения причины повторите `./deploy/deploy.sh`. Не удаляйте volumes `caddy_data` и `caddy_config`, если хотите сохранить состояние сертификатов.
