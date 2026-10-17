from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass

from nginx_conf.store import AppDatabase

DNSResolver = Callable[[str], tuple[tuple[str, ...], tuple[str, ...]]]


@dataclass(frozen=True, slots=True)
class DomainDNSResult:
    domain: str
    a_records: tuple[str, ...]
    aaaa_records: tuple[str, ...]
    ok: bool
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DNSPreflightResult:
    domains: tuple[DomainDNSResult, ...]
    configured_ipv4: tuple[str, ...]
    configured_ipv6: tuple[str, ...]
    ok: bool
    blockers: tuple[str, ...] = ()


def check_dns_preflight(
    domains: tuple[str, ...] | list[str],
    db: AppDatabase,
    *,
    resolver: DNSResolver | None = None,
) -> DNSPreflightResult:
    resolver = resolver or resolve_domain_records
    normalized_domains = tuple(domains)
    configured_ipv4, configured_ipv6 = _configured_server_ips(db)
    global_blockers: list[str] = []

    if not configured_ipv4:
        global_blockers.append("server_ipv4_missing")

    domain_results: list[DomainDNSResult] = []
    for domain in normalized_domains:
        try:
            a_records, aaaa_records = resolver(domain)
        except OSError as error:
            domain_results.append(
                DomainDNSResult(
                    domain=domain,
                    a_records=(),
                    aaaa_records=(),
                    ok=False,
                    blockers=(f"dns_error:{error}",),
                )
            )
            continue

        blockers: list[str] = []
        if not a_records:
            blockers.append("missing_a_record")
        else:
            foreign_a = tuple(address for address in a_records if address not in configured_ipv4)
            if foreign_a:
                blockers.append(f"foreign_a_record:{','.join(foreign_a)}")

        if aaaa_records:
            if not configured_ipv6:
                blockers.append("server_ipv6_missing_for_existing_aaaa")
            else:
                foreign_aaaa = tuple(address for address in aaaa_records if address not in configured_ipv6)
                if foreign_aaaa:
                    blockers.append(f"foreign_aaaa_record:{','.join(foreign_aaaa)}")

        domain_results.append(
            DomainDNSResult(
                domain=domain,
                a_records=a_records,
                aaaa_records=aaaa_records,
                ok=not blockers,
                blockers=tuple(blockers),
            )
        )

    ok = not global_blockers and all(result.ok for result in domain_results)
    return DNSPreflightResult(
        domains=tuple(domain_results),
        configured_ipv4=configured_ipv4,
        configured_ipv6=configured_ipv6,
        ok=ok,
        blockers=tuple(global_blockers),
    )


def resolve_domain_records(domain: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    ipv4: set[str] = set()
    ipv6: set[str] = set()
    for family, _type, _proto, _canonname, sockaddr in socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM):
        if family == socket.AF_INET:
            ipv4.add(sockaddr[0])
        elif family == socket.AF_INET6:
            ipv6.add(sockaddr[0])
    return tuple(sorted(ipv4)), tuple(sorted(ipv6))


def dns_preflight_to_dict(result: DNSPreflightResult) -> dict[str, object]:
    return {
        "ok": result.ok,
        "configured_ipv4": list(result.configured_ipv4),
        "configured_ipv6": list(result.configured_ipv6),
        "blockers": list(result.blockers),
        "domains": [
            {
                "domain": domain.domain,
                "a_records": list(domain.a_records),
                "aaaa_records": list(domain.aaaa_records),
                "ok": domain.ok,
                "blockers": list(domain.blockers),
            }
            for domain in result.domains
        ],
    }


def _configured_server_ips(db: AppDatabase) -> tuple[tuple[str, ...], tuple[str, ...]]:
    ipv4 = []
    ipv6 = []
    for server_ip in db.list_server_ips():
        normalized = str(ipaddress.ip_address(server_ip.address))
        if server_ip.family == "ipv4":
            ipv4.append(normalized)
        elif server_ip.family == "ipv6":
            ipv6.append(normalized)
    return tuple(sorted(ipv4)), tuple(sorted(ipv6))
