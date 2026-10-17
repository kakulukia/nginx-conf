import argparse
import ipaddress
import sys
from collections.abc import Iterable

HEADER = "# Managed by nginx-conf admin allowlist workflow."


def normalize_allowlist_entry(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("Allowlist entry must not be empty")
    try:
        network = ipaddress.ip_network(stripped, strict=False)
    except ValueError as error:
        raise ValueError(f"Invalid IP address or network: {value}") from error
    return str(network)


def render_allowlist(entries: Iterable[str]) -> str:
    normalized: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        network = normalize_allowlist_entry(entry)
        if network not in seen:
            normalized.append(network)
            seen.add(network)
    if not normalized:
        raise ValueError("At least one allowlist entry is required")

    allow_lines = "\n".join(f"allow {network};" for network in normalized)
    return f"{HEADER}\n{allow_lines}\ndeny all;\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render Nginx admin IP allowlist snippet")
    parser.add_argument("entries", nargs="+", help="Allowed IP addresses or CIDR networks")
    args = parser.parse_args(argv)
    try:
        sys.stdout.write(render_allowlist(args.entries))
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
