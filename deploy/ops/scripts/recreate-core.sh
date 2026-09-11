#!/usr/bin/env bash
# recreate-core.sh — force-recreate the core + core-scheduler containers so
# they pick up newly-added .env values (docker compose up/restart alone do
# NOT re-read env_file contents for already-existing containers unless the
# compose config itself changed). Invoked via ops "script" command.
set -euo pipefail
docker compose up -d --force-recreate --no-deps core core-scheduler
