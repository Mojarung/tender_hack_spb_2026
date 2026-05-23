# n8n workflows (as code)

Workflow живут как JSON-файлы рядом — это single source of truth.

Экспорт из работающего n8n:
```bash
docker compose exec n8n n8n export:workflow --all --separate --pretty \
  --output=/home/node/.n8n/workflows
```

(перезапишет файлы; коммитим в git).

Импорт после клонирования:
```bash
docker compose exec n8n n8n import:workflow --separate \
  --input=/home/node/.n8n/workflows
```

## Базовые workflow

1. **smoke-test.json** — Schedule `*/5 * * * *` → 4 параллельных HTTP к `api:8000/scrape/{source}` → IF → Telegram alert.
2. **live-demo-board.json** — Webhook → 4 параллельных HTTP → Merge → Respond to Webhook. Открывается на демо как живой canvas.

## MCP

Чтобы Claude Code мог редактировать workflow напрямую:
```bash
npx -y @czlonkowski/n8n-mcp
```
Подключить в `.mcp.json` корня репозитория.
