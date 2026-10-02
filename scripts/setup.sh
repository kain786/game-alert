#!/bin/sh
set -eu
TASK_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$TASK_DIR"
umask 077
if [ ! -e .env ]; then cp .env.example .env; fi
if [ ! -e .discord.env ]; then cp .discord.env.example .discord.env; fi
mkdir -p data
printf '%s\n' 'Settings prepared. Edit .env and .discord.env. Existing settings/data were preserved.'
