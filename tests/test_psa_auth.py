from psa_sniper.psa_auth import normalize_psa_access_token


def test_psa_token_normalization_removes_common_copy_wrappers():
    assert normalize_psa_access_token(" secret-token ") == "secret-token"
    assert normalize_psa_access_token("Bearer secret-token") == "secret-token"
    assert normalize_psa_access_token(" bearer   secret-token ") == "secret-token"
    assert normalize_psa_access_token("Authorization: bearer secret-token") == "secret-token"
    assert normalize_psa_access_token(" authorization : Bearer   secret-token ") == "secret-token"
    assert normalize_psa_access_token('"Bearer secret-token"') == "secret-token"
    assert normalize_psa_access_token("'secret-token'") == "secret-token"
    assert normalize_psa_access_token("   ") is None
    assert normalize_psa_access_token(None) is None
