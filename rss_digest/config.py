import tomllib
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass
class FreshRSSConf:
    url: str
    user: str
    api_password: str


@dataclass
class WallabagConf:
    url: str
    client_id: str
    client_secret: str
    username: str
    password: str


@dataclass
class NextcloudConf:
    url: str
    user: str
    app_password: str
    vault_path: str


@dataclass
class LLMConf:
    base_url: str
    api_key: str
    cheap_model: str
    writer_model: str
    max_tokens_per_run: int = 200000
    wiki_api_key: str = ""  # optional: a second key so the provider reports wiki and digest usage apart


@dataclass
class DigestConf:
    top_n: int = 15
    also_notable: int = 10
    explore: int = 5  # random titles from below the cut-off, against the ranking's own bubble


@dataclass
class Config:
    freshrss: FreshRSSConf
    wallabag: WallabagConf
    nextcloud: NextcloudConf
    llm: LLMConf
    state_path: Path
    digest: DigestConf = field(default_factory=DigestConf)


def _section(raw: dict, name: str, cls):
    data = raw.get(name, {})
    if not isinstance(data, dict):
        raise ConfigError(f"[{name}] must be a table")
    for f in fields(cls):
        if f.name not in data and f.default is MISSING and f.default_factory is MISSING:
            raise ConfigError(f"missing config key: {name}.{f.name}")
    return cls(**{f.name: data[f.name] for f in fields(cls) if f.name in data})


def load_config(path: Path) -> Config:
    try:
        raw = tomllib.loads(Path(path).read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e
    if "state_path" not in raw:
        raise ConfigError("missing config key: state_path")
    return Config(
        freshrss=_section(raw, "freshrss", FreshRSSConf),
        wallabag=_section(raw, "wallabag", WallabagConf),
        nextcloud=_section(raw, "nextcloud", NextcloudConf),
        llm=_section(raw, "llm", LLMConf),
        digest=_section(raw, "digest", DigestConf),
        state_path=Path(raw["state_path"]),
    )
