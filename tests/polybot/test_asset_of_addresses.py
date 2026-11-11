from openjarvis.polybot.state import asset_of


def test_asset_of_reads_both_address_forms():
    cases = {
        "cpc-btc-updown-5m-2026-11-11-2040z": "btc",
        "btc-updown-5m-2026-11-11-2040z": "btc",
        "cpceth-eth-updown-5m-2026-11-11-2040z": "eth",
        "eth-updown-5m-2026-11-11-2040z": "eth",
        "cpc-btc-updown-15m-2026-11-11-2040z": "btc15",
        "btc-updown-15m-2026-11-11-2040z": "btc15",
        "sol-updown-1h-2026-11-11-2040z": "sol1h",
    }
    for slug, want in cases.items():
        assert asset_of(slug) == want, slug
