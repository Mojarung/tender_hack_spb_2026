# Grafana dashboards

В этой папке лежат JSON-дашборды, провижионируемые Grafana при старте.

Базовые, которые стоит скачать перед хакатоном и положить рядом:

| ID | Название | URL |
|---|---|---|
| 16110 | FastAPI Observability | https://grafana.com/grafana/dashboards/16110-fastapi-observability/ |
| 1860  | Node Exporter Full    | https://grafana.com/grafana/dashboards/1860-node-exporter-full/ |
| 14282 | cAdvisor exporter     | https://grafana.com/grafana/dashboards/14282-cadvisor-exporter/ |
| 24474 | n8n System Health     | https://grafana.com/grafana/dashboards/24474-n8n-system-health-overview/ |
| 24475 | n8n Execution Analytics | https://grafana.com/grafana/dashboards/24475-n8n-workflow-execution-analytics/ |

Скачать:
```bash
curl -sSL https://grafana.com/api/dashboards/16110/revisions/latest/download \
  -o monitoring/grafana/dashboards/fastapi-observability.json
```

Свой кастомный `live-scraping.json` — см. описание в [../../../docs/anti-bot.md §7.1](../../../docs/anti-bot.md).
