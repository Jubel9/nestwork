from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_files(tmp_path, monkeypatch):
    """Never touch the real ~/.claude (shared CLAUDE.md / memory.md) or the real database."""
    monkeypatch.setenv("NESTWORK_SHARED_DIR", str(tmp_path / "shared"))
    monkeypatch.setenv("NESTWORK_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("NESTWORK_AUTO_BACKUP", "0")   # tests that want it turn it back on
    monkeypatch.setenv("NESTWORK_TRUSTED_LOCAL", "testclient,testserver")   # TestClient counts as this computer


TEST_CHAIN = Path(__file__).with_name("fixtures") / "c2pa_test_chain.pem"


@pytest.fixture(autouse=True)
def trusted_test_signer(monkeypatch):
    """Trust the throwaway test chain's intermediate as a C2PA trust anchor, only inside the tests, so photos
    signed by the test signer count as verified. Tests of unverified signers turn it off with untrusted_signer."""
    from nestwork import provenance
    pem = TEST_CHAIN.read_text()
    monkeypatch.setattr(provenance, "trust_anchors", lambda: pem[pem.index("-----BEGIN", 1):])   # the intermediate


@pytest.fixture()
def untrusted_signer(monkeypatch):
    """No trust anchors: the test signer is then a valid but unverified signer, like a self-signed generator."""
    from nestwork import provenance
    monkeypatch.setattr(provenance, "trust_anchors", lambda: "")
