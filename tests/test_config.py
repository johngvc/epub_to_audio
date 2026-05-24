from pathlib import Path

import pytest

from audiobook.config import AppConfig, load_config


def test_loads_repo_default(repo_root: Path) -> None:
    cfg = load_config(repo_root / "config.toml")
    assert isinstance(cfg, AppConfig)
    # Repo default is "api" — unattended LM Studio path. See README.
    assert cfg.adapt.mode == "api"
    assert cfg.adapt.concurrency == 8
    assert cfg.chunk.max_chars == 400
    assert cfg.render.workers == 2
    assert cfg.adapt.api.base_url == "http://localhost:1234/v1"
    assert cfg.adapt.api.model != ""  # must be set to a real model


def test_rejects_unknown_section(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[bogus]\nx = 1\n")
    with pytest.raises(ValueError):
        load_config(bad)


def test_adapt_api_block_defaults(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("""
[adapt]
mode = "api"

[adapt.api]
base_url = "http://localhost:1234/v1"
model = "qwen2.5-14b-instruct"
""")
    from audiobook.config import load_config
    cfg = load_config(cfg_path)
    assert cfg.adapt.mode == "api"
    assert cfg.adapt.api.base_url == "http://localhost:1234/v1"
    assert cfg.adapt.api.model == "qwen2.5-14b-instruct"
    # documented defaults
    assert cfg.adapt.api.api_key == "lm-studio"
    assert cfg.adapt.api.context_window == 16384
    assert cfg.adapt.api.temperature == 0.3
    assert cfg.adapt.api.max_output_tokens == 8192
    assert cfg.adapt.api.request_timeout_s == 600


def test_adapt_api_env_overrides(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("""
[adapt]
mode = "api"

[adapt.api]
base_url = "http://localhost:1234/v1"
model = "configured-model"
api_key = "configured-key"
""")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://other:9999/v1")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    from audiobook.config import load_config, resolve_adapt_api
    cfg = load_config(cfg_path)
    resolved = resolve_adapt_api(cfg.adapt.api)
    assert resolved.base_url == "http://other:9999/v1"
    assert resolved.model == "env-model"
    assert resolved.api_key == "env-key"


def test_adapt_api_env_empty_string_is_ignored(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("""
[adapt]
mode = "api"

[adapt.api]
base_url = "http://localhost:1234/v1"
model = "configured-model"
""")
    monkeypatch.setenv("OPENAI_MODEL", "")  # empty must NOT override
    from audiobook.config import load_config, resolve_adapt_api
    cfg = load_config(cfg_path)
    resolved = resolve_adapt_api(cfg.adapt.api)
    assert resolved.model == "configured-model"
