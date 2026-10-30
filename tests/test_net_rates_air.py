from homebot.services.network import TailscaleError, parse_tailscale_status
from homebot.services.rates import parse_nbu
from homebot.ui.texts import render_network

TAILSCALE = {
    "BackendState": "Running",
    "Self": {
        "HostName": "myserver",
        "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"],
        "OS": "linux",
        "Online": True,
    },
    "Peer": {
        "nodekey:a": {"HostName": "phone", "TailscaleIPs": ["fd7a::2", "100.64.0.2"], "OS": "android", "Online": True},
        "nodekey:b": {
            "HostName": "laptop",
            "TailscaleIPs": ["100.64.0.3"],
            "OS": "windows",
            "Online": False,
            "LastSeen": "2026-10-12T08:00:00Z",
            "ExitNode": True,
        },
        "nodekey:c": {
            "DNSName": "old-pc.tail1234.ts.net.",
            "TailscaleIPs": [],
            "Online": False,
            "LastSeen": "0001-01-01T00:00:00Z",
        },
    },
    "Health": ["some warning"],
}


def test_tailscale_parse_and_render():
    status = parse_tailscale_status(TAILSCALE)
    names = [n.name for n in status.nodes]
    assert names[0] == "myserver" and status.nodes[0].is_self
    assert names[1] == "phone"  # онлайн выше офлайна
    assert status.nodes[1].ip == "100.64.0.2"  # предпочитаем IPv4
    assert status.online_count == 2
    old = next(n for n in status.nodes if n.name == "old-pc")
    assert old.last_seen is None and old.ip == ""
    text = render_network(tailnet=status, tailnet_error=None, external_ip="203.0.113.5", rx=1500, tx=0, total_rx=10**9, total_tx=10**8)
    assert "myserver" in text and "exit-node" in text and "203.0.113.5" in text and "some warning" in text
    try:
        parse_tailscale_status({"BackendState": "NeedsLogin"})
    except TailscaleError:
        pass
    else:
        raise AssertionError("TailscaleError expected")
    down = render_network(
        tailnet=None,
        tailnet_error="tailscale: команда не найдена",
        external_ip=None,
        rx=0,
        tx=0,
        total_rx=0,
        total_tx=0,
    )
    assert "недоступен" in down and "не определён" in down


NBU = [
    {"r030": 840, "txt": "Долар США", "rate": 41.2345, "cc": "USD", "exchangedate": "13.10.2026"},
    {"r030": 978, "txt": "Євро", "rate": 45.1, "cc": "EUR", "exchangedate": "13.10.2026"},
    {"r030": 985, "txt": "Злотий", "rate": "bad", "cc": "PLN"},
    "junk",
]


def test_nbu_parse_order_and_robustness():
    rates = parse_nbu(NBU, ["eur", "USD", "PLN", "GBP"])
    assert [r.code for r in rates] == ["EUR", "USD"]
    assert rates[1].rate == 41.2345 and rates[1].date == "13.10.2026"
    try:
        parse_nbu({"not": "a list"}, ["USD"])
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError expected")
