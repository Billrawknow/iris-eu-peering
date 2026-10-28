import pytest

from iris_peering.config import ConfigError, load_config


def _write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text)
    return p


def test_defaults_target_staging_and_de():
    cfg = load_config(None, env={})
    assert cfg.storage.qualified_table == "iris_staging.peeringdb_ix_nodes"
    assert cfg.source.countries == ("DE",)


@pytest.mark.parametrize("schema", ["iris_core", "public", "iris_core_staging"])
def test_refuses_non_staging_schema(tmp_path, schema):
    with pytest.raises(ConfigError, match="only iris_staging"):
        load_config(_write(tmp_path, f"storage:\n  schema: {schema}\n"), env={})


def test_refuses_sql_injection_in_identifiers(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, "storage:\n  table: 'x; drop table y'\n"), env={})


def test_secrets_only_from_env_and_not_in_repr(tmp_path):
    cfg = load_config(None, env={"PEERINGDB_API_KEY": "s3cret", "DATABASE_URL": "postgresql://u:p@h/d"})
    assert cfg.source.api_key == "s3cret"
    assert "s3cret" not in repr(cfg) and "u:p@h" not in repr(cfg)


def test_unknown_keys_and_bad_country_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown keys"):
        load_config(_write(tmp_path, "source:\n  api_key: inline\n  nope: 1\n"), env={})
    with pytest.raises(ConfigError, match="alpha-2"):
        load_config(_write(tmp_path, "source:\n  countries: [Germany]\n"), env={})


def test_example_config_is_valid():
    from pathlib import Path

    cfg = load_config(Path(__file__).parents[1] / "config" / "config.example.yaml", env={})
    assert cfg.source.base_url.startswith("https://www.peeringdb.com/api")


def test_loader_refuses_core_schema_even_if_config_bypassed():
    from iris_peering.config import StorageConfig
    from iris_peering.load import StagingGuardError, StagingLoader

    with pytest.raises(StagingGuardError):
        StagingLoader(StorageConfig(schema="iris_core", database_url="postgresql://x"))
