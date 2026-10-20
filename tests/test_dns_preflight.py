from pathlib import Path

from nginx_conf.dns_preflight import check_dns_preflight, dns_preflight_to_dict
from nginx_conf.store import AppDatabase


def make_db(tmp_path: Path, *addresses: str) -> AppDatabase:
    db = AppDatabase(tmp_path / "nginx-conf.sqlite3")
    db.initialize()
    for address in addresses:
        db.upsert_server_ip(address)
    return db


def resolver(records: dict[str, tuple[tuple[str, ...], tuple[str, ...]]]):
    def resolve(domain: str):
        return records.get(domain, ((), ()))

    return resolve


def test_dns_preflight_accepts_matching_a_and_aaaa_records(tmp_path: Path) -> None:
    db = make_db(tmp_path, "203.0.113.10", "2001:db8::10")

    result = check_dns_preflight(
        ("example.com", "www.example.com"),
        db,
        resolver=resolver(
            {
                "example.com": (("203.0.113.10",), ("2001:db8::10",)),
                "www.example.com": (("203.0.113.10",), ()),
            }
        ),
    )

    assert result.ok is True
    assert [domain.ok for domain in result.domains] == [True, True]
    payload = dns_preflight_to_dict(result)
    assert payload["ok"] is True
    assert payload["configured_ipv4"] == ["203.0.113.10"]
    assert payload["domains"][0]["domain"] == "example.com"


def test_dns_preflight_blocks_missing_records(tmp_path: Path) -> None:
    db = make_db(tmp_path, "203.0.113.10")

    result = check_dns_preflight(("example.com",), db, resolver=resolver({"example.com": ((), ())}))

    assert result.ok is False
    assert result.domains[0].blockers == ("missing_a_record",)


def test_dns_preflight_blocks_foreign_a_records(tmp_path: Path) -> None:
    db = make_db(tmp_path, "203.0.113.10")

    result = check_dns_preflight(
        ("example.com",),
        db,
        resolver=resolver({"example.com": (("198.51.100.20",), ())}),
    )

    assert result.ok is False
    assert result.domains[0].blockers == ("foreign_a_record:198.51.100.20",)


def test_dns_preflight_blocks_existing_aaaa_without_configured_ipv6(tmp_path: Path) -> None:
    db = make_db(tmp_path, "203.0.113.10")

    result = check_dns_preflight(
        ("example.com",),
        db,
        resolver=resolver({"example.com": (("203.0.113.10",), ("2001:db8::10",))}),
    )

    assert result.ok is False
    assert result.domains[0].blockers == ("server_ipv6_missing_for_existing_aaaa",)


def test_dns_preflight_blocks_foreign_aaaa_records(tmp_path: Path) -> None:
    db = make_db(tmp_path, "203.0.113.10", "2001:db8::10")

    result = check_dns_preflight(
        ("example.com",),
        db,
        resolver=resolver({"example.com": (("203.0.113.10",), ("2001:db8::20",))}),
    )

    assert result.ok is False
    assert result.domains[0].blockers == ("foreign_aaaa_record:2001:db8::20",)


def test_dns_preflight_blocks_missing_server_ip_configuration(tmp_path: Path) -> None:
    db = make_db(tmp_path)

    result = check_dns_preflight(
        ("example.com",),
        db,
        resolver=resolver({"example.com": (("203.0.113.10",), ())}),
    )

    assert result.ok is False
    assert result.blockers == ("server_ipv4_missing",)
    assert result.domains[0].blockers == ("foreign_a_record:203.0.113.10",)


def test_dns_preflight_reports_mixed_group_results(tmp_path: Path) -> None:
    db = make_db(tmp_path, "203.0.113.10")

    result = check_dns_preflight(
        ("ok.example.com", "bad.example.com"),
        db,
        resolver=resolver(
            {
                "ok.example.com": (("203.0.113.10",), ()),
                "bad.example.com": (("198.51.100.20",), ()),
            }
        ),
    )

    assert result.ok is False
    assert result.domains[0].ok is True
    assert result.domains[1].ok is False
