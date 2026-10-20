from __future__ import annotations

import ssl
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nginx_conf.configs import ProjectInventory

LETSENCRYPT_ROOT = Path("/etc/letsencrypt")
ADMIN_DOMAIN = "config.pepperz.de"
CERTBOT_WARNING_YELLOW_DAYS = 30
CERTBOT_WARNING_RED_DAYS = 14
CERTBOT_WARNING_CRITICAL_DAYS = 7

CertDecoder = Callable[[Path], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class CertificateLineage:
    name: str
    renewal_config_path: Path
    cert_path: Path
    fullchain_path: Path | None
    chain_path: Path | None
    privkey_path: Path | None
    domains: tuple[str, ...]
    expires_at: datetime | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DomainCertificateStatus:
    domain: str
    state: str
    certificate_name: str | None
    expires_at: datetime | None
    days_remaining: int | None
    relative_expiry: str
    cert_path: Path | None
    fullchain_path: Path | None
    chain_path: Path | None
    privkey_path: Path | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectSSLStatus:
    project_name: str
    domains: tuple[DomainCertificateStatus, ...]


@dataclass(frozen=True, slots=True)
class SSLInventory:
    projects: tuple[ProjectSSLStatus, ...]
    unassigned_lineages: tuple[CertificateLineage, ...]


def discover_ssl_inventory(
    projects: Iterable[ProjectInventory],
    *,
    letsencrypt_root: Path = LETSENCRYPT_ROOT,
    now: datetime | None = None,
    decoder: CertDecoder | None = None,
) -> SSLInventory:
    now = _as_utc(now or datetime.now(UTC))
    lineages = discover_certbot_lineages(letsencrypt_root, decoder=decoder)
    lineage_by_domain: dict[str, CertificateLineage] = {}
    for lineage in lineages:
        for domain in lineage.domains:
            lineage_by_domain.setdefault(domain, lineage)

    project_statuses: list[ProjectSSLStatus] = []
    assigned_domains: set[str] = set()
    for project in projects:
        domain_statuses = []
        for domain in project.domains:
            lineage = lineage_by_domain.get(domain)
            if lineage is not None:
                assigned_domains.update(lineage.domains)
            domain_statuses.append(_status_for_domain(domain, lineage, now))
        project_statuses.append(ProjectSSLStatus(project.project_name, tuple(domain_statuses)))

    unassigned_lineages = tuple(
        lineage
        for lineage in lineages
        if lineage.domains
        and set(lineage.domains) != {ADMIN_DOMAIN}
        and not set(lineage.domains).intersection(assigned_domains)
    )
    return SSLInventory(projects=tuple(project_statuses), unassigned_lineages=unassigned_lineages)


def discover_certbot_lineages(
    letsencrypt_root: Path = LETSENCRYPT_ROOT,
    *,
    decoder: CertDecoder | None = None,
) -> tuple[CertificateLineage, ...]:
    renewal_dir = letsencrypt_root / "renewal"
    if not renewal_dir.is_dir():
        return ()

    decoder = decoder or decode_certificate
    lineages: list[CertificateLineage] = []
    for renewal_config_path in sorted(renewal_dir.glob("*.conf")):
        values = _parse_renewal_config(renewal_config_path)
        name = renewal_config_path.stem
        cert_path = _path_from_config(values, "cert", letsencrypt_root / "live" / name / "cert.pem")
        fullchain_path = _optional_path_from_config(values, "fullchain")
        chain_path = _optional_path_from_config(values, "chain")
        privkey_path = _optional_path_from_config(values, "privkey")
        configured_domains = _domains_from_config(values)
        domains: tuple[str, ...] = configured_domains
        expires_at: datetime | None = None
        error: str | None = None

        try:
            decoded = decoder(cert_path)
            expires_at = _parse_not_after(decoded.get("notAfter"))
            decoded_domains = _domains_from_decoded_cert(decoded)
            if decoded_domains:
                domains = decoded_domains
        except Exception as exc:
            error = str(exc)

        lineages.append(
            CertificateLineage(
                name=name,
                renewal_config_path=renewal_config_path,
                cert_path=cert_path,
                fullchain_path=fullchain_path,
                chain_path=chain_path,
                privkey_path=privkey_path,
                domains=domains,
                expires_at=expires_at,
                error=error,
            )
        )

    return tuple(lineages)


def decode_certificate(cert_path: Path) -> dict[str, Any]:
    return ssl._ssl._test_decode_cert(str(cert_path))  # type: ignore[attr-defined]


def ssl_inventory_to_dict(inventory: SSLInventory) -> dict[str, object]:
    return {
        "projects": [
            {
                "project_name": project.project_name,
                "domains": [domain_ssl_status_to_dict(status) for status in project.domains],
            }
            for project in inventory.projects
        ],
        "unassigned_lineages": [certificate_lineage_to_dict(lineage) for lineage in inventory.unassigned_lineages],
    }


def domain_ssl_status_to_dict(status: DomainCertificateStatus) -> dict[str, object]:
    return {
        "domain": status.domain,
        "state": status.state,
        "certificate_name": status.certificate_name,
        "expires_at": status.expires_at.isoformat() if status.expires_at else None,
        "days_remaining": status.days_remaining,
        "relative_expiry": status.relative_expiry,
        "cert_path": _path_to_str(status.cert_path),
        "fullchain_path": _path_to_str(status.fullchain_path),
        "chain_path": _path_to_str(status.chain_path),
        "privkey_path": _path_to_str(status.privkey_path),
        "error": status.error,
    }


def certificate_lineage_to_dict(lineage: CertificateLineage) -> dict[str, object]:
    return {
        "name": lineage.name,
        "renewal_config_path": str(lineage.renewal_config_path),
        "cert_path": str(lineage.cert_path),
        "fullchain_path": _path_to_str(lineage.fullchain_path),
        "chain_path": _path_to_str(lineage.chain_path),
        "privkey_path": _path_to_str(lineage.privkey_path),
        "domains": list(lineage.domains),
        "expires_at": lineage.expires_at.isoformat() if lineage.expires_at else None,
        "error": lineage.error,
    }


def _status_for_domain(domain: str, lineage: CertificateLineage | None, now: datetime) -> DomainCertificateStatus:
    if lineage is None:
        return DomainCertificateStatus(
            domain=domain,
            state="missing",
            certificate_name=None,
            expires_at=None,
            days_remaining=None,
            relative_expiry="kein Zertifikat",
            cert_path=None,
            fullchain_path=None,
            chain_path=None,
            privkey_path=None,
        )

    if lineage.error is not None:
        return DomainCertificateStatus(
            domain=domain,
            state="error",
            certificate_name=lineage.name,
            expires_at=lineage.expires_at,
            days_remaining=None,
            relative_expiry="nicht lesbar",
            cert_path=lineage.cert_path,
            fullchain_path=lineage.fullchain_path,
            chain_path=lineage.chain_path,
            privkey_path=lineage.privkey_path,
            error=lineage.error,
        )

    days_remaining = None
    if lineage.expires_at is not None:
        seconds_remaining = (lineage.expires_at - now).total_seconds()
        days_remaining = int(seconds_remaining // 86_400)

    return DomainCertificateStatus(
        domain=domain,
        state=_warning_state(days_remaining),
        certificate_name=lineage.name,
        expires_at=lineage.expires_at,
        days_remaining=days_remaining,
        relative_expiry=_relative_expiry(days_remaining),
        cert_path=lineage.cert_path,
        fullchain_path=lineage.fullchain_path,
        chain_path=lineage.chain_path,
        privkey_path=lineage.privkey_path,
    )


def _warning_state(days_remaining: int | None) -> str:
    if days_remaining is None:
        return "unknown"
    if days_remaining < 0:
        return "expired"
    if days_remaining < CERTBOT_WARNING_CRITICAL_DAYS:
        return "critical"
    if days_remaining < CERTBOT_WARNING_RED_DAYS:
        return "red"
    if days_remaining < CERTBOT_WARNING_YELLOW_DAYS:
        return "yellow"
    return "valid"


def _relative_expiry(days_remaining: int | None) -> str:
    if days_remaining is None:
        return "Ablauf unbekannt"
    if days_remaining < 0:
        return f"seit {abs(days_remaining)} Tagen abgelaufen"
    if days_remaining == 0:
        return "heute"
    if days_remaining == 1:
        return "in 1 Tag"
    return f"in {days_remaining} Tagen"


def _parse_renewal_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("[") or "=" not in stripped:
            continue
        key, value = stripped.split("=", maxsplit=1)
        values[key.strip()] = value.strip()
    return values


def _domains_from_config(values: dict[str, str]) -> tuple[str, ...]:
    domains = values.get("domains")
    if not domains:
        return ()
    return tuple(domain.strip() for domain in domains.split(",") if domain.strip())


def _domains_from_decoded_cert(decoded: dict[str, Any]) -> tuple[str, ...]:
    domains: list[str] = []
    for kind, value in decoded.get("subjectAltName", ()):
        if kind == "DNS" and value not in domains:
            domains.append(value)
    if domains:
        return tuple(domains)

    for subject_part in decoded.get("subject", ()):
        for key, value in subject_part:
            if key == "commonName" and value:
                return (value,)
    return ()


def _parse_not_after(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)


def _path_from_config(values: dict[str, str], key: str, fallback: Path) -> Path:
    value = values.get(key)
    return Path(value) if value else fallback


def _optional_path_from_config(values: dict[str, str], key: str) -> Path | None:
    value = values.get(key)
    return Path(value) if value else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _path_to_str(path: Path | None) -> str | None:
    return None if path is None else str(path)
