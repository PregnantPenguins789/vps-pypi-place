import tomllib
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.toml"
_cache = None

def load() -> dict:
    global _cache
    if _cache is None:
        with open(_CONFIG_PATH, "rb") as f:
            _cache = tomllib.load(f)
    return _cache

def get(section: str) -> dict:
    return load()[section]
