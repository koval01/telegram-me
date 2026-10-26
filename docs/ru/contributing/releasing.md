# Выпуск релизов

Версии следуют [Semantic Versioning](https://semver.org/). Версия хранится в
`pyproject.toml` и доступна в документе OpenAPI и через
`telegram-me version`.

## Шаги { #steps }

1. Перенесите записи из **Unreleased** в `CHANGELOG.md` под новый заголовок
   `## [X.Y.Z] - YYYY-MM-DD`.
2. Увеличьте `version` в `pyproject.toml` и выполните `uv lock`.
3. Сделайте коммит (`Release X.Y.Z`) в ветке и откройте pull request: `main`
   защищена и принимает изменения только через pull request с пройденными
   проверками. Его слияние уже развернёт коммит в продакшене как `sha-…`.
4. Когда release-прогон этого слияния в `main` пройдёт, поставьте тег на
   merge-коммит и отправьте его:

    ```bash
    git tag -a vX.Y.Z -m "X.Y.Z"
    git push origin vX.Y.Z
    ```

Тег собирает и публикует теги образа `X.Y.Z`, `X.Y` и `X` в GHCR и Docker Hub
и создаёт релиз на GitHub с разделом истории изменений в качестве заметок.

Pull request, который несовместимо меняет контракт REST или GraphQL, роняет
workflow **Guards** ([Несовместимые изменения](testing.md#breaking-changes)).
Такое изменение требует мажорной версии: поставьте pull request метку
`breaking-change` и опишите изменение в разделе **Changed** файла
`CHANGELOG.md`.

## Срочный откат { #hotfix-rollback }

Если релиз ведёт себя плохо, запустите **Actions → Deploy** с предыдущим тегом
`sha-…`; см. [Продакшен → Откат](../deployment/production.md#rolling-back).
