from pathlib import Path

import pytest

from audiobook.config import AppConfig, load_config


def test_loads_repo_default(repo_root: Path) -> None:
    cfg = load_config(repo_root / "config.toml")
    assert isinstance(cfg, AppConfig)
    assert cfg.adapt.mode == "agent"
    assert cfg.adapt.concurrency == 8
    assert cfg.chunk.max_chars == 400
    assert cfg.render.workers == 2


def test_rejects_unknown_section(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[bogus]\nx = 1\n")
    with pytest.raises(ValueError):
        load_config(bad)
