import pytest


@pytest.fixture(autouse=True)
def no_real_embedding_models_in_unit_tests(monkeypatch, tmp_path):
    # Tests must not silently depend on downloaded local weights or the network.
    monkeypatch.setenv('LITTLE_BLUE_EMBEDDING_MODEL_DIR', str(tmp_path / 'no-model'))
