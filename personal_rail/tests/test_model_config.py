import pytest
from personal_rail import service


def test_environment_config_and_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "ROOT", tmp_path / "personal_rail")
    (tmp_path / ".env").write_text("RAIL_MODEL_NAME=file-choice\n")
    monkeypatch.setenv("RAIL_MODEL_BASE_URL", "https://example.invalid/v1/")
    monkeypatch.setenv("RAIL_MODEL_API_KEY", "synthetic-test-value")
    monkeypatch.setenv("RAIL_MODEL_NAME", "environment-choice")
    assert service.model_config() == {
        "url": "https://example.invalid/v1",
        "key": "synthetic-test-value",
        "model": "environment-choice",
    }


def test_no_configuration_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "ROOT", tmp_path / "personal_rail")
    for name in ("RAIL_MODEL_BASE_URL", "RAIL_MODEL_API_KEY", "RAIL_MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError):
        service.model_config()
