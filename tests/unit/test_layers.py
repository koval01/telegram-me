"""Every module of the package has a place in the import-linter layers."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "telegram_me"


def test_every_top_level_module_is_in_a_layer() -> None:
    # import-linter checks only the modules a layer names: a new module left
    # out could import anything.
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]
    layers = next(c for c in config["contracts"] if c["type"] == "layers")["layers"]
    named = {name.strip() for layer in layers for name in layer.replace(":", "|").split("|")}
    modules = {
        path.stem if path.is_file() else path.name
        for path in PACKAGE.iterdir()
        if (path.suffix == ".py" and path.stem != "__init__") or (path / "__init__.py").is_file()
    }
    assert modules - named == set(), "add them to [tool.importlinter] layers in pyproject.toml"
