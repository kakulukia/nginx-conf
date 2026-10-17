import pytest

from nginx_conf.admin_allowlist import normalize_allowlist_entry, render_allowlist


def test_normalize_allowlist_entry_accepts_addresses_and_networks() -> None:
    assert normalize_allowlist_entry("203.0.113.10") == "203.0.113.10/32"
    assert normalize_allowlist_entry("2001:db8::10") == "2001:db8::10/128"
    assert normalize_allowlist_entry("203.0.113.0/24") == "203.0.113.0/24"


def test_render_allowlist_deduplicates_and_denies_everything_else() -> None:
    rendered = render_allowlist(["203.0.113.10", "203.0.113.10/32", "2001:db8::10"])

    assert rendered == (
        "# Managed by nginx-conf admin allowlist workflow.\nallow 203.0.113.10/32;\nallow 2001:db8::10/128;\ndeny all;\n"
    )


@pytest.mark.parametrize("entry", ["", "not-an-ip", "203.0.113.999"])
def test_normalize_allowlist_entry_rejects_invalid_values(entry: str) -> None:
    with pytest.raises(ValueError):
        normalize_allowlist_entry(entry)


def test_render_allowlist_requires_at_least_one_entry() -> None:
    with pytest.raises(ValueError):
        render_allowlist([])
