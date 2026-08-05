import pytest


@pytest.fixture(autouse=True)
def _no_background_audit(monkeypatch):
    """`add` spawns a detached codex audit by default. Tests must not."""
    monkeypatch.setenv("LOOPGRAPH_AUDIT", "0")


@pytest.fixture(autouse=True)
def _no_operator_sensitive_config(monkeypatch, tmp_path):
    """The redaction classifier reads the operator's own term list.

    Left alone, the suite's result would depend on whose machine it runs on --
    passing here and failing in CI, or worse, passing in CI because the file
    is absent while the mechanism is broken. Point it at nothing; the tests
    that exercise operator terms write their own config and say so.
    """
    monkeypatch.setenv("LOOPGRAPH_SENSITIVE_CONFIG", str(tmp_path / "absent.toml"))
