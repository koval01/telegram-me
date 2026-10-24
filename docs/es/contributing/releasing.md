# Publicar versiones

Las versiones siguen [Semantic Versioning](https://semver.org/). La versión se
define en `pyproject.toml` y se expone en el documento OpenAPI y mediante
`telegram-me version`.

## Pasos { #steps }

1. Mueve las entradas de **Unreleased** de `CHANGELOG.md` a un nuevo
   encabezado `## [X.Y.Z] - YYYY-MM-DD`.
2. Incrementa `version` en `pyproject.toml` y ejecuta `uv lock`.
3. Haz commit en `main` (`Release X.Y.Z`) y haz push: esto ya despliega el
   commit en producción como `sha-…`.
4. Crea la etiqueta y súbela:

    ```bash
    git tag -a vX.Y.Z -m "X.Y.Z"
    git push origin vX.Y.Z
    ```

La etiqueta construye y publica las etiquetas de imagen `X.Y.Z`, `X.Y` y `X`
en GHCR y Docker Hub, y crea una release de GitHub con la sección del registro
de cambios como notas.

Un pull request que cambia el contrato REST o GraphQL de forma incompatible
hace fallar el workflow **Guards**
([Cambios incompatibles](testing.md#breaking-changes)). Un cambio así necesita
una versión mayor: etiqueta el pull request con `breaking-change` y describe el
cambio en **Changed** de `CHANGELOG.md`.

## Reversión urgente { #hotfix-rollback }

Si una versión se comporta mal, ejecuta **Actions → Deploy** con la etiqueta
`sha-…` anterior; consulta
[Producción → Revertir](../deployment/production.md#rolling-back).
