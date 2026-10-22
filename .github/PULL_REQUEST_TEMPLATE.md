<!-- English or 繁體中文 are both fine. Security problems: never here, see SECURITY.md. -->

## What and why / 變更內容與原因



## How I tested it / 測試方式

<!-- Commands you ran, and anything you checked by hand. -->

## Checklist / 檢查清單

- [ ] One change per pull request, with tests for it. / 每個 PR 只做一件事，並附上測試。
- [ ] `python manage.py test` and, for panel changes, `npm test` pass. / 測試通過。
- [ ] API changed? `api_contract --write` and `npm run api:types` were run, and both generated files are committed. / 若修改 API，已重新產生規格與型別。
- [ ] Dependencies changed? `python docker/third_party_notices.py --write` was run, and the licenses are acceptable (CONTRIBUTING.md, section 8). / 若修改相依套件，已更新第三方授權聲明。
- [ ] User-visible change? A line was added under `[Unreleased]` in CHANGELOG.md. / 使用者看得到的變更已寫入變更紀錄。
- [ ] README or CONTRIBUTING changed? Both languages were updated, or I've asked for help with the translation. / 已同步更新中英文文件，或已請求協助翻譯。
- [ ] No secrets or real data: no App Passwords, webhook URLs, `.env`, databases, backups or real problem reports. / 沒有任何機密資訊或真實資料。
