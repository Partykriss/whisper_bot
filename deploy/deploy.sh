#!/bin/bash

# --- CONFIGURATION ---
APP_NAME="whisper_bot"
APP_DIR="/opt/${APP_NAME}"
SSH_USER="root"
SSH_PORT="22"
HOST=""
# Local server env file name
SERVER_ENV_LOCAL=".env.server"

# --- PARSE ARGUMENTS ---
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --user) SSH_USER="$2"; shift ;;
        --host) HOST="$2"; shift ;;
        --port) SSH_PORT="$2"; shift ;;
        *) echo "❌ Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

# --- VALIDATION ---
if [[ -z "$HOST" ]]; then
    echo "❌ Error: --host is mandatory."
    exit 1
fi

if [[ ! -f "$SERVER_ENV_LOCAL" ]]; then
    echo "❌ Error: Local file $SERVER_ENV_LOCAL not found!"
    exit 1
fi

echo "🚀 Starting deployment to ${SSH_USER}@${HOST}..."

# --- STEP 1: Syncing files ---
echo "📥 Syncing code..."
rsync -avz -e "ssh -p ${SSH_PORT}" \
    --exclude '.git/' \
    --exclude 'venv/' \
    --exclude '__pycache__/' \
    --exclude 'downloads/' \
    --exclude 'postgres_data/' \
    --exclude '.env*' \
    ./ ${SSH_USER}@${HOST}:${APP_DIR}

# --- STEP 2: Syncing Server Env ---
# We take .env.server and rename it to .env on the target server
echo "🔑 Copying $SERVER_ENV_LOCAL as .env to server..."
scp -P ${SSH_PORT} ${SERVER_ENV_LOCAL} ${SSH_USER}@${HOST}:${APP_DIR}/.env

# --- STEP 3: Remote Operations ---
echo "🛠 Executing remote commands..."
ssh -p ${SSH_PORT} ${SSH_USER}@${HOST} << EOF
    cd ${APP_DIR}

    # 1. Проверка и установка Docker
    if ! [ -x "\$(command -v docker)" ]; then
        echo "📦 Installing Docker..."
        curl -fsSL https://get.docker.com -o get-docker.sh
        sh get-docker.sh
    fi

    # 2. Проверка и установка Docker Compose
    if ! [ -x "\$(command -v docker-compose)" ]; then
        echo "📦 Installing Docker Compose..."
        apt-get update && apt-get install -y docker-compose
    fi

    # 3. Чистая остановка текущих контейнеров
    # Это решает проблему "No such image", так как мы удаляем ссылки на старые образы
    echo "🛑 Stopping old containers..."
    docker-compose down --remove-orphans

    # 4. Сборка и запуск
    echo "🔄 Building and starting containers..."
    docker-compose up -d --build

    # 5. Миграции базы данных
    echo "⚙️ Running database migrations..."
    # Даем базе 5 секунд, чтобы "проснуться" после старта
    sleep 5
    # Используем имя контейнера для выполнения команды
    docker exec whisper_bot alembic upgrade head

    # 6. Очистка "висячих" образов
    # Оставляет только актуальный собранный образ, экономя место
    echo "🧹 Cleaning up unused images..."
    docker image prune -f

    echo "✅ DEPLOYMENT FINISHED SUCCESSFULLY!"
EOF