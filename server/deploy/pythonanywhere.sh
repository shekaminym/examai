#!/bin/sh
set -eu
umask 077
cd "$(dirname "$0")/../.."

set -a
. "${EXAMAI_DEPLOY_CONFIG:-$HOME/.config/examai/backend.env}"
set +a

: "${EXAMAI_PUBLIC_URL:?Set the public HTTPS origin}"
: "${EXAMAI_SERVER_DB:?Use an absolute path on persistent storage}"
: "${DOMAIN_SOCKET:?PythonAnywhere must supply the domain socket}"

exec "${EXAMAI_DEPLOY_PYTHON:-.venv/bin/python}" -m uvicorn server.api:create_app \
    --factory --uds "$DOMAIN_SOCKET" --workers 1 --no-access-log
