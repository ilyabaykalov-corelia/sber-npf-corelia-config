# Конфигурация Corelia для СберНПФ

Это один внешний customer configuration package Corelia Schema V3. Он содержит
два вида документов — Договор ПДС и КИД ОПС — и не содержит DataSpace GraphQL,
Platform V `ac.json`, operation permissions или platform packaging scripts.

## Структура

- `documents/` — атрибуты, presentation, UI, search и storage policy;
- `workflows/` — process key, BPMN resource, start actions и task actions;
- `permissions/` — ссылки на нейтральные Corelia permissions и lifecycle policy;
- `bpmn/` — BPMN definitions Flowable;
- `branding/` — logo, favicon и legacy-compatible web branding assets.

`configuration.json` содержит permission grants для ролей проверенного JWT.
Текущая поставка использует только существующие роли `app_owner` и
`document_operator`; изменение этих grants — отдельное изменение доступа и
должно проверяться на согласованном стенде.

## Сборка runtime package

Из родительской папки:

```bash
cd corelia
mkdir -p .local/config-releases
bash scripts/compile-config.sh ../sber-npf-corelia-config .local/config-releases/sber-release
```

Выходной каталог должен отсутствовать. Получившийся
`.local/config-releases/sber-release/corelia` задаётся как
`CORELIA_CUSTOMER_CONFIG` для Docker Compose либо `CORELIA_CONFIG_PATH` для
локального запуска сервисов. Сборка не публикует BPMN, не мигрирует документы
и не обращается к Platform V.

Перед production deployment проверьте создание ПДС и КИД, выдачу задач
Flowable, права `app_owner` и `document_operator`, загрузку вложений и
отображение branding в web-клиенте.
