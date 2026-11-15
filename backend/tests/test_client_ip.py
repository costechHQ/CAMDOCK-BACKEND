import pytest
from starlette.requests import Request

from app.core.client_ip import client_ip, parse_networks

PROXY = parse_networks("10.0.0.0/8")


def request(host="203.0.113.9", forwarded=None):
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    scope = {
        "type": "http",
        "headers": headers,
        "client": None if host is None else (host, 1234),
    }
    return Request(scope)


def test_the_connection_address_is_used_without_trusted_proxies():
    assert client_ip(request(forwarded="198.51.100.1")) == "203.0.113.9"


def test_a_forwarded_header_is_ignored_from_an_untrusted_connection():
    assert client_ip(request(forwarded="198.51.100.1"), PROXY) == "203.0.113.9"


def test_a_trusted_proxy_passes_the_real_client_through():
    found = client_ip(request("10.1.2.3", "198.51.100.1"), PROXY)

    assert found == "198.51.100.1"


def test_a_spoofed_leftmost_value_is_ignored():
    found = client_ip(request("10.1.2.3", "6.6.6.6, 198.51.100.1"), PROXY)

    assert found == "198.51.100.1"


def test_a_chain_of_trusted_proxies_is_skipped_from_the_right():
    found = client_ip(request("10.1.2.3", "198.51.100.1, 10.9.9.9"), PROXY)

    assert found == "198.51.100.1"


def test_a_trusted_connection_without_the_header_uses_its_own_address():
    assert client_ip(request("10.1.2.3"), PROXY) == "10.1.2.3"


def test_a_malformed_header_is_not_believed():
    assert client_ip(request("10.1.2.3", "not-an-ip"), PROXY) == "10.1.2.3"


def test_a_header_made_only_of_trusted_hops_gives_the_first():
    assert client_ip(request("10.1.2.3", "10.5.5.5, 10.6.6.6"), PROXY) == "10.5.5.5"


def test_ipv6_clients_are_supported():
    proxies = parse_networks("::1")

    assert client_ip(request("::1", "2001:db8::7"), proxies) == "2001:db8::7"


def test_a_missing_client_is_reported_as_unknown():
    assert client_ip(request(None)) == "unknown"


def test_parsing_accepts_addresses_ranges_and_blanks():
    networks = parse_networks(" 10.0.0.0/8 , 192.168.1.5 ,, ")

    assert [str(n) for n in networks] == ["10.0.0.0/8", "192.168.1.5/32"]
    assert parse_networks("") == ()


@pytest.mark.parametrize("value", ["nope", "10.0.0.0/99", "10.0.0.1, x"])
def test_parsing_rejects_bad_entries(value):
    with pytest.raises(ValueError):
        parse_networks(value)
