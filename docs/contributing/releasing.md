# Releasing

Versions follow [Semantic Versioning](https://semver.org/). The version lives
in `pyproject.toml` and is exposed in the OpenAPI document and by
`telegram-me version`.

## Steps

1. Move the **Unreleased** entries in `CHANGELOG.md` under a new heading
   `## [X.Y.Z] - YYYY-MM-DD`.
2. Bump `version` in `pyproject.toml` and run `uv lock`.
3. Commit on `main` (`Release X.Y.Z`) and push — this already deploys the
   commit to production as `sha-…`.
4. Tag and push the tag:

    ```bash
    git tag -a vX.Y.Z -m "X.Y.Z"
    git push origin vX.Y.Z
    ```

The tag builds and pushes `X.Y.Z`, `X.Y` and `X` image tags to GHCR and
Docker Hub and creates a GitHub release with the changelog section as notes.

A pull request that changes the REST or GraphQL contract incompatibly fails
the **Guards** workflow ([Breaking changes](testing.md#breaking-changes)). Such
a change needs a major version: label the pull request `breaking-change` and
describe the change under **Changed** in `CHANGELOG.md`.

## Hotfix rollback

If a release misbehaves, run **Actions → Deploy** with the previous `sha-…`
tag; see [Production → Rolling back](../deployment/production.md#rolling-back).
