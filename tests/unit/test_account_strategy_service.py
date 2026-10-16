from src.services.account_strategy_service import resolve_account_state_path


def test_resolve_account_state_path_maps_bare_account_name_to_state_file():
    resolved = resolve_account_state_path("elon", state_dir="state")

    assert resolved == "state/elon.json"


def test_resolve_account_state_path_preserves_explicit_path():
    resolved = resolve_account_state_path("state/elon.json", state_dir="state")

    assert resolved == "state/elon.json"
