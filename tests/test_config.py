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
    assert cfg.render.voice == ""   # default is empty; resolver picks the right file
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


def test_parse_config_defaults_and_override(tmp_path) -> None:
    from audiobook.config import load_config

    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text(
        '[book]\ntitle = "T"\nauthor = "A"\n'
        '[parse]\nparser = "pymupdf"\nfootnote_policy = "endnote"\nchapter_level = 2\n'
    )
    cfg = load_config(cfg_path)
    assert cfg.parse.parser == "pymupdf"
    assert cfg.parse.footnote_policy == "endnote"
    assert cfg.parse.chapter_level == 2


def test_parse_config_defaults_when_absent(tmp_path) -> None:
    from audiobook.config import load_config

    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text('[book]\ntitle = "T"\nauthor = "A"\n')
    cfg = load_config(cfg_path)
    assert cfg.parse.parser == "auto"
    assert cfg.parse.footnote_policy == "skip"
    assert cfg.parse.chapter_level is None

def test_chunk_config_pause_defaults() -> None:
    from audiobook.config import ChunkConfig

    c = ChunkConfig()
    assert c.sentence_silence_ms == 300
    assert c.beat_silence_ms == 600
    assert c.title_silence_ms == 800


def test_assemble_out_dir_default() -> None:
    from audiobook.config import AssembleConfig

    assert AssembleConfig().out_dir == "./out"


def test_local_overlay_deep_merges_and_wins(tmp_path: Path) -> None:
    from audiobook.config import load_config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '[book]\ntitle = "Committed"\nauthor = "A"\n'
        '[assemble]\naudio_bitrate_kbps = 64\nout_dir = "./out"\n'
    )
    (tmp_path / "config.local.toml").write_text('[assemble]\nout_dir = "/local/path"\n')
    cfg = load_config(cfg_path)
    assert cfg.assemble.out_dir == "/local/path"   # local wins
    assert cfg.assemble.audio_bitrate_kbps == 64    # sibling untouched
    assert cfg.book.title == "Committed"            # other sections untouched


def test_safe_filename_strips_illegal_chars() -> None:
    from audiobook.config import safe_filename

    assert safe_filename("Learning Domain-Driven Design") == "Learning Domain-Driven Design"
    assert safe_filename('A/B: C? "x"') == "AB C x"
    assert safe_filename("   ") == "book"
    assert safe_filename("...") == "book"


def test_resolve_out_path_explicit_wins(tmp_path: Path) -> None:
    from audiobook.config import AppConfig, resolve_out_path

    cfg = AppConfig()
    explicit = tmp_path / "custom.m4b"
    assert resolve_out_path(cfg, explicit, "Some Title") == explicit


def test_resolve_out_path_from_dir_and_title() -> None:
    from audiobook.config import AppConfig, resolve_out_path

    cfg = AppConfig()
    cfg.assemble.out_dir = "/books"
    out = resolve_out_path(cfg, None, "Learning DDD: A Book")
    assert out == Path("/books/Learning DDD A Book.m4b")


def test_resolve_out_path_env_overrides_dir(monkeypatch) -> None:
    from audiobook.config import AppConfig, resolve_out_path

    cfg = AppConfig()
    cfg.assemble.out_dir = "/books"
    monkeypatch.setenv("AUDIOBOOK_OUT_DIR", "/env/dir")
    assert resolve_out_path(cfg, None, "T") == Path("/env/dir/T.m4b")
    monkeypatch.setenv("AUDIOBOOK_OUT_DIR", "")  # empty must NOT override
    assert resolve_out_path(cfg, None, "T") == Path("/books/T.m4b")
