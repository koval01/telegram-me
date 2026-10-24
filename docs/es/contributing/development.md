# Desarrollo

Prepara el proyecto como se describe en
[Desarrollo local](../getting-started/local-development.md) y después:

## Flujo de trabajo { #workflow }

1. Crea una rama a partir de `main`.
2. Haz el cambio, con pruebas.
3. `make check`: lint, tipos y pruebas, exactamente lo que la CI ejecuta
   primero.
4. Si cambiaste a propósito lo que devuelve un endpoint, ejecuta
   `make snapshots` y revisa `git diff tests/snapshots` (consulta
   [Pruebas](testing.md)).
5. Añade una entrada en **Unreleased** de `CHANGELOG.md` para los cambios
   visibles para los usuarios.
6. Abre una pull request. La CI ejecuta lint, mypy, las pruebas en Python 3.13
   y 3.14, construye la imagen y la somete a una prueba de humo contra t.me
   real.

## Estilo de código { #code-style }

- Formato y lint: **ruff** (longitud de línea 100, un conjunto amplio de reglas
  que incluye bugbear, pyupgrade, bandit y pydocstyle con la convención de
  Google). `make format` corrige la mayoría de los hallazgos.
- Tipos: **mypy --strict** sobre `src` y `tests`, con el plugin de Pydantic.
  Las funciones públicas están completamente anotadas; `Any` se reserva para
  JSON sin procesar.
- Docstrings: en cada módulo, clase y función públicos, con el estilo de
  Google.
- Capas: `api` → `services` → `cache` → `telegram`; nada por debajo de `api`
  importa FastAPI. import-linter comprueba ambas cosas (`[tool.importlinter]`
  en `pyproject.toml`). Conecta los nuevos objetos de larga duración en
  `container.py`.
- Rust: nada de `unwrap()` en código de producción (un `expect("...")`
  enuncia el invariante), y en `src/parse/` nada de indexación ni cortes que
  puedan entrar en pánico: usa `get`, `split_once`, `strip_prefix`. La
  extensión aborta ante un pánico, lo que tumba el worker. Consulta
  [Comprobaciones estáticas](testing.md#static-checks) para cada herramienta.
- Errores: lanza los errores de dominio de `telegram/errors.py`; la API los
  traduce a códigos de estado en un único lugar (`api/errors.py`).
- Mantén estable el contrato de `/v1`. Los campos nuevos están bien; eliminar o
  renombrar campos, o cambiar sus tipos, requiere una nueva versión de la API.

## Dependencias { #dependencies }

Las dependencias se declaran en `pyproject.toml` y se fijan en `uv.lock`:

```bash
uv add <package>             # runtime dependency
uv add --dev <package>       # development tool
uv lock --upgrade            # refresh everything to the newest versions
```

Dependabot abre actualizaciones semanales agrupadas para los paquetes de
Python, las GitHub Actions y las imágenes base de Docker, para versiones con
al menos una semana (las actualizaciones de seguridad llegan enseguida).

## Pre-commit { #pre-commit }

`make install` instala hooks de git que se ejecutan en cada commit: ruff (con
corrección automática), ruff format, mypy, import-linter, vulture, typos,
zizmor, gitleaks sobre los cambios preparados, cargo fmt y clippy y, cuando
cambian sus archivos, hadolint, cargo-deny y cargo-machete. Las herramientas
de Python vienen de `uv.lock`; `make tools` instala las demás. Ejecútalos
cuando quieras con `uv run pre-commit run --all-files`.

## Documentación { #documentation }

El sitio se escribe en inglés en `docs/`, con traducciones al español y al
ruso en `docs/es/` y `docs/ru/` que replican su estructura de archivos. Cada
idioma es una compilación independiente: `zensical.toml` compila el inglés en
`site/`, y `zensical.es.toml` / `zensical.ru.toml` compilan las traducciones
en `site/es/` y `site/ru/`. El selector de idioma de la cabecera mantiene al
lector en la misma página y sección (`overrides/main.html`).

```bash
make docs-serve                  # live preview of English on :8001
make docs-serve DOCS_LANG=ru     # live preview of the Russian pages
make docs-preview                # build all languages and serve site/ with the switcher
```

Cuando cambies una página, actualiza sus traducciones en la misma pull request
o abre un issue para ellas. Los encabezados traducidos conservan el ancla en
inglés con un id explícito (`## Ejemplo { #example }`), de modo que los enlaces
y el selector llegan a la misma sección en todos los idiomas. El código, los
comandos, los nombres de los ajustes y las claves JSON no se traducen.
`tests/unit/test_docs_config.py` comprueba que cada página tenga su traducción
y que los tres archivos de configuración sigan sincronizados.
