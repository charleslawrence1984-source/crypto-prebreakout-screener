from meme_relevance import classify_meme_relevance


def pair(symbol, name):
    return {
        "baseToken": {
            "symbol": symbol,
            "name": name,
        }
    }


def test_excludes_tokenized_stock_and_spv_examples():
    broadcom = classify_meme_relevance(
        pair("AVGOB", "Broadcom Tokenized bStocks"),
        {},
    )
    paimon = classify_meme_relevance(
        pair("pPOLY", "Paimon Polymarket SPV Token"),
        {},
    )

    assert broadcom["status"] == "EXCLUDE"
    assert broadcom["reason"] == "Tokenized stock/equity"
    assert paimon["status"] == "EXCLUDE"
    assert paimon["reason"] == "SPV / securities token"


def test_excludes_stables_wrapped_majors_and_staking_assets():
    assert classify_meme_relevance(pair("USDC", "USD Coin"), {})["status"] == "EXCLUDE"
    assert classify_meme_relevance(pair("WETH", "Wrapped Ether"), {})["status"] == "EXCLUDE"
    assert classify_meme_relevance(pair("stETH", "Lido Staked ETH"), {})["status"] == "EXCLUDE"


def test_keeps_known_meme_style_assets():
    assert classify_meme_relevance(pair("PEPE", "Pepe"), {})["status"] == "KEEP"
    assert classify_meme_relevance(pair("BONK", "Bonk"), {})["status"] == "KEEP"
    assert classify_meme_relevance(pair("DOGWIF", "dogwifhat"), {})["status"] == "KEEP"


def test_ambiguous_brand_like_token_is_not_auto_excluded():
    # A name alone is not enough evidence that a token is a stock/RWA wrapper.
    # Ambiguous assets stay in discovery instead of creating false negatives.
    result = classify_meme_relevance(pair("APPLE", "Apple Token (Official)"), {})
    assert result["status"] == "KEEP"


def test_profile_description_can_confirm_explicit_rwa_identity():
    result = classify_meme_relevance(
        pair("ABC", "ABC"),
        {"profile_description": "Tokenized treasury token backed by short-term government debt"},
    )
    assert result["status"] == "EXCLUDE"
    assert result["reason"] == "RWA / bond / treasury token"
