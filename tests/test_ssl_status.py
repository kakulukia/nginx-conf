from datetime import UTC, datetime
from pathlib import Path

from nginx_conf.configs import ProjectInventory
from nginx_conf.ssl_status import (
    discover_certbot_lineages,
    discover_ssl_inventory,
    ssl_inventory_to_dict,
)

NOW = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def write_renewal(root: Path, name: str, domains: str) -> Path:
    renewal_dir = root / "renewal"
    renewal_dir.mkdir(parents=True, exist_ok=True)
    cert_path = root / "live" / name / "cert.pem"
    fullchain_path = root / "live" / name / "fullchain.pem"
    chain_path = root / "live" / name / "chain.pem"
    privkey_path = root / "live" / name / "privkey.pem"
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_text("fake cert", encoding="utf-8")
    config_path = renewal_dir / f"{name}.conf"
    config_path.write_text(
        f"""
        cert = {cert_path}
        fullchain = {fullchain_path}
        chain = {chain_path}
        privkey = {privkey_path}
        domains = {domains}
        [renewalparams]
        account = ignored
        """,
        encoding="utf-8",
    )
    return cert_path


def fake_decoder(expiries: dict[str, str], sans: dict[str, tuple[str, ...]] | None = None):
    sans = sans or {}

    def decode(path: Path):
        return {
            "notAfter": expiries[str(path)],
            "subjectAltName": tuple(("DNS", domain) for domain in sans.get(str(path), ())),
        }

    return decode


def project(domains: tuple[str, ...]) -> ProjectInventory:
    return ProjectInventory(
        project_name="demo-app",
        project_path=Path("/opt/www/demo-app"),
        configs=(),
        domains=domains,
    )


def test_discover_certbot_lineages_reads_paths_domains_and_expiry(tmp_path: Path) -> None:
    cert_path = write_renewal(tmp_path, "demo.example.com", "demo.example.com,www.demo.example.com")

    lineages = discover_certbot_lineages(
        tmp_path,
        decoder=fake_decoder({str(cert_path): "Jul 11 12:00:00 2026 GMT"}),
    )

    assert len(lineages) == 1
    lineage = lineages[0]
    assert lineage.name == "demo.example.com"
    assert lineage.cert_path == cert_path
    assert lineage.fullchain_path == tmp_path / "live/demo.example.com/fullchain.pem"
    assert lineage.chain_path == tmp_path / "live/demo.example.com/chain.pem"
    assert lineage.privkey_path == tmp_path / "live/demo.example.com/privkey.pem"
    assert lineage.domains == ("demo.example.com", "www.demo.example.com")
    assert lineage.expires_at == datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_decoded_certificate_san_overrides_renewal_domains(tmp_path: Path) -> None:
    cert_path = write_renewal(tmp_path, "demo.example.com", "stale.example.com")

    lineages = discover_certbot_lineages(
        tmp_path,
        decoder=fake_decoder(
            {str(cert_path): "Jul 11 12:00:00 2026 GMT"},
            {str(cert_path): ("demo.example.com", "www.demo.example.com")},
        ),
    )

    assert lineages[0].domains == ("demo.example.com", "www.demo.example.com")


def test_ssl_inventory_reports_all_warning_states_and_missing_certificates(tmp_path: Path) -> None:
    expiries = {}
    domains = (
        ("expired.example.com", "Jun 10 12:00:00 2026 GMT"),
        ("critical.example.com", "Jun 17 12:00:00 2026 GMT"),
        ("red.example.com", "Jun 24 12:00:00 2026 GMT"),
        ("yellow.example.com", "Jul 10 12:00:00 2026 GMT"),
        ("valid.example.com", "Jul 11 12:00:00 2026 GMT"),
    )
    for domain, expiry in domains:
        cert_path = write_renewal(tmp_path, domain, domain)
        expiries[str(cert_path)] = expiry

    inventory = discover_ssl_inventory(
        [
            project(
                (
                    "expired.example.com",
                    "critical.example.com",
                    "red.example.com",
                    "yellow.example.com",
                    "valid.example.com",
                    "missing.example.com",
                )
            )
        ],
        letsencrypt_root=tmp_path,
        now=NOW,
        decoder=fake_decoder(expiries),
    )

    statuses = {status.domain: status for status in inventory.projects[0].domains}
    assert statuses["expired.example.com"].state == "expired"
    assert statuses["expired.example.com"].relative_expiry == "seit 1 Tagen abgelaufen"
    assert statuses["critical.example.com"].state == "critical"
    assert statuses["red.example.com"].state == "red"
    assert statuses["yellow.example.com"].state == "yellow"
    assert statuses["valid.example.com"].state == "valid"
    assert statuses["missing.example.com"].state == "missing"
    assert statuses["valid.example.com"].cert_path == tmp_path / "live/valid.example.com/cert.pem"
    serialized = {status["domain"]: status for status in ssl_inventory_to_dict(inventory)["projects"][0]["domains"]}
    assert serialized["valid.example.com"]["expires_at"] == "2026-07-11T12:00:00+00:00"
    assert serialized["valid.example.com"]["cert_path"] == str(statuses["valid.example.com"].cert_path)


def test_ssl_inventory_reports_unassigned_certbot_lineages(tmp_path: Path) -> None:
    cert_path = write_renewal(tmp_path, "orphan.example.com", "orphan.example.com")

    inventory = discover_ssl_inventory(
        [project(("demo.example.com",))],
        letsencrypt_root=tmp_path,
        now=NOW,
        decoder=fake_decoder({str(cert_path): "Jul 11 12:00:00 2026 GMT"}),
    )

    assert [lineage.name for lineage in inventory.unassigned_lineages] == ["orphan.example.com"]
    assert inventory.projects[0].domains[0].state == "missing"


def test_admin_only_certificate_is_not_an_unassigned_project_certificate(tmp_path):
    names_and_domains = {
        "config.pepperz.de": "config.pepperz.de",
        "config.pepperz.de-0001": "config.pepperz.de",
        "orphan.example.com": "orphan.example.com",
        "mixed": "config.pepperz.de,foreign.example.com",
    }
    paths = [write_renewal(tmp_path, name, domains) for name, domains in names_and_domains.items()]
    inventory = discover_ssl_inventory(
        [],
        letsencrypt_root=tmp_path,
        now=NOW,
        decoder=fake_decoder({str(path): "Jul 11 12:00:00 2026 GMT" for path in paths}),
    )
    assert {lineage.name for lineage in inventory.unassigned_lineages} == {"orphan.example.com", "mixed"}
