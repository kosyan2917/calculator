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
4. Собирает приложение, запускает Caddy и ждет успешного healthcheck.

Caddy автоматически запросит и будет обновлять TLS-сертификат. FastAPI не публикует порт `8000` на хосте и доступен только внутри Docker-сети.

Проверка после запуска:

```bash
docker compose ps
curl -I https://artifacts.example.com/
curl https://artifacts.example.com/api/health
```

## Обновление

В этом же clone достаточно выполнить:

```bash
git pull
```

Git вызывает `.githooks/post-merge`, который запускает `deploy/deploy.sh`. Новый image сначала полностью собирается, затем Compose переключает контейнеры и ждет healthcheck. При неуспешном переключении скрипт повторно помечает предыдущий application image и запускает его.

Одновременные деплои блокируются каталогом `.deploy.lock`. Hook срабатывает для обычного fast-forward `git pull`; обновление через `git reset`, `git checkout` или ручную замену файлов нужно завершать командой:

```bash
./deploy/deploy.sh
```

Это автоматический in-place deployment с коротким перезапуском FastAPI, а не полноценный zero-downtime blue/green deployment.

## Управление

```bash
# Состояние и healthcheck
docker compose ps

# Логи всех сервисов
docker compose logs -f --tail=200

# Только приложение
docker compose logs -f --tail=200 artcalc

# Ручная повторная сборка и запуск
./deploy/deploy.sh

# Остановка без удаления TLS volumes
docker compose down
```

Чтобы изменить домен или число Uvicorn workers, отредактируйте `.env` и запустите `./deploy/deploy.sh`. Не коммитьте `.env`.

## Если обновление не прошло

Git уже может находиться на новом commit, даже если post-merge hook вернул ошибку. Работающий контейнер при ошибке сборки не затрагивается; при ошибке healthcheck выполняется попытка возврата предыдущего image.

Сначала изучите состояние и логи:

```bash
docker compose ps
docker compose logs --tail=300 artcalc caddy
```

После устранения причины повторите `./deploy/deploy.sh`. Не удаляйте volumes `caddy_data` и `caddy_config`, если хотите сохранить состояние сертификатов.
