"""The trading, music, NFT and screenwriting roles: researched with web_search, files only, nothing that spends, trades, posts or signs."""

from openjarvis.server.crew import engine as eng_mod

FORBIDDEN = {"use_connection", "marnyx_post", "repo_commit", "request_merge", "check_site", "make_book", "get_weather"}


def _names(role):
    return [t["function"]["name"] for t in eng_mod.tools_for(role)]


def test_the_four_roles_exist_with_a_search_and_no_way_to_act_outside_the_crew():
    for role, who in (("trader", "Quant"), ("composer", "Lyra"), ("nft", "Mint"), ("screenwriter", "Reel")):
        names = _names(role)
        assert eng_mod.ROLES[role]["name"] == who
        assert {"web_search", "save_output", "finish"} <= set(names)
        assert len(names) == len(set(names))
        assert not set(names) & FORBIDDEN


def test_only_mint_makes_images_and_quant_cannot_trade():
    assert "generate_image" in _names("nft")
    assert all("generate_image" not in _names(r) for r in ("trader", "composer", "screenwriter"))
    assert "Not a licensed advisor" in eng_mod.ROLES["trader"]["about"] and "Place any order" in eng_mod.ROLES["trader"]["cannot"]
