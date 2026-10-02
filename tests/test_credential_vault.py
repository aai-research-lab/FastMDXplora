import os

import pytest

from fastmdxplora.agent.credential_vault import CredentialVault, VaultError, _dpapi


@pytest.mark.skipif(os.name != "nt", reason="Current-user Windows DPAPI")
def test_native_dpapi_protects_fixture_credentials(tmp_path):
    vault = CredentialVault(tmp_path / "provider")
    record = {"version": 1, "accounts": [{"access_token": "synthetic-test-token"}], "active": None}
    with vault.locked():
        host = vault.host_id()
        vault.write(record)
        assert vault.read() == record
    encrypted = (vault.root / "accounts.bin").read_bytes()
    assert b"synthetic-test-token" not in encrypted
    with vault.locked():
        assert vault.host_id() == host
    assert _dpapi(encrypted, decrypt=True)


def test_corrupt_protected_record_does_not_reset_accounts(tmp_path):
    vault = CredentialVault(tmp_path, protect=lambda x: x[::-1], unprotect=lambda x: x[::-1])
    with vault.locked():
        vault.write({"version": 1, "accounts": [], "active": None})
        (tmp_path / "accounts.bin").write_bytes(b"invalid")
        with pytest.raises(VaultError, match="could not be read"):
            vault.read()
    assert (tmp_path / "accounts.bin").read_bytes() == b"invalid"


def test_existing_malformed_host_is_not_replaced(tmp_path):
    vault = CredentialVault(tmp_path)
    (tmp_path / "host.json").write_text('{"host_id":"broken"}')
    with vault.locked(), pytest.raises(VaultError, match="host record"):
        vault.host_id()
    assert (tmp_path / "host.json").read_text() == '{"host_id":"broken"}'


def test_storage_size_bound(tmp_path):
    vault = CredentialVault(tmp_path, protect=lambda x: x, unprotect=lambda x: x)
    with vault.locked(), pytest.raises(VaultError, match="limit"):
        vault.write({"value": "x" * 1_000_001})
    assert not (tmp_path / "accounts.bin").exists()
