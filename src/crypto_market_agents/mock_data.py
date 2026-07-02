"""Official mock data for CLI demos without external API calls."""

from __future__ import annotations

from crypto_market_agents.schemas import (
    AgentReport,
    AgentStatus,
    Finding,
    ImpactDirection,
    RiskLevel,
    SentimentLabel,
    Source,
)


MOCK_SOURCE = Source(
    name="Scenario mock fictif - aucune API externe",
    provider="crypto-market-agents",
)


def build_mock_agent_reports(
    risk_level: RiskLevel | str = RiskLevel.MEDIUM,
) -> tuple[AgentReport, ...]:
    """Build coherent fictitious AgentReport objects for a demo scenario."""

    scenario = RiskLevel(risk_level)
    return (
        _price_market_report(scenario),
        _volatility_risk_report(scenario),
        _news_sentiment_report(scenario),
        _onchain_fundamental_report(scenario),
    )


def _price_market_report(scenario: RiskLevel) -> AgentReport:
    risk_by_scenario = {
        RiskLevel.LOW: RiskLevel.LOW,
        RiskLevel.MEDIUM: RiskLevel.MEDIUM,
        RiskLevel.HIGH: RiskLevel.MEDIUM,
        RiskLevel.CRITICAL: RiskLevel.HIGH,
    }
    summary_by_scenario = {
        RiskLevel.LOW: "Scenario fictif: prix stables avec volumes reguliers sur BTC et ETH.",
        RiskLevel.MEDIUM: "Scenario fictif: BTC et ETH bougent moderement avec volumes corrects.",
        RiskLevel.HIGH: "Scenario fictif: marche hesitant, BTC recule pendant que le volume augmente.",
        RiskLevel.CRITICAL: "Scenario fictif: forte baisse de BTC et ETH accompagnee de volumes extremes.",
    }
    finding_by_scenario = {
        RiskLevel.LOW: Finding(
            title="Marche calme fictif",
            description="BTC et ETH restent proches de leurs prix moyens avec une variation limitee.",
            impact=ImpactDirection.NEUTRAL,
            symbols=("btc", "eth"),
            confidence_score=0.86,
            data={"assets": ["bitcoin", "ethereum"], "mock": True},
        ),
        RiskLevel.MEDIUM: Finding(
            title="Variation moderee fictive",
            description="BTC progresse legerement alors que ETH consolide dans une zone normale.",
            impact=ImpactDirection.MIXED,
            symbols=("btc", "eth"),
            confidence_score=0.82,
            data={"assets": ["bitcoin", "ethereum"], "mock": True},
        ),
        RiskLevel.HIGH: Finding(
            title="Pression marche fictive",
            description="BTC recule nettement sur 24h avec un volume superieur a la moyenne.",
            impact=ImpactDirection.BEARISH,
            symbols=("btc",),
            confidence_score=0.80,
            data={"assets": ["bitcoin"], "mock": True},
        ),
        RiskLevel.CRITICAL: Finding(
            title="Choc prix fictif",
            description="BTC et ETH affichent une baisse tres rapide dans ce scenario de demo.",
            impact=ImpactDirection.BEARISH,
            symbols=("btc", "eth"),
            confidence_score=0.78,
            data={"assets": ["bitcoin", "ethereum"], "mock": True},
        ),
    }

    return AgentReport(
        agent_name="price_market_agent",
        status=AgentStatus.SUCCESS,
        summary=summary_by_scenario[scenario],
        risk_level=risk_by_scenario[scenario],
        confidence=_confidence_for(scenario),
        findings=(finding_by_scenario[scenario],),
        sources=(MOCK_SOURCE,),
        data={"mock": True, "scenario": scenario.value, "vs_currency": "usd"},
    )


def _volatility_risk_report(scenario: RiskLevel) -> AgentReport:
    risk_by_scenario = {
        RiskLevel.LOW: RiskLevel.LOW,
        RiskLevel.MEDIUM: RiskLevel.LOW,
        RiskLevel.HIGH: RiskLevel.HIGH,
        RiskLevel.CRITICAL: RiskLevel.CRITICAL,
    }
    summary_by_scenario = {
        RiskLevel.LOW: "Scenario fictif: volatilite basse et amplitude 24h limitee.",
        RiskLevel.MEDIUM: "Scenario fictif: volatilite surveillee mais sans signal dangereux.",
        RiskLevel.HIGH: "Scenario fictif: amplitude 24h elevee et mouvement brutal sur BTC.",
        RiskLevel.CRITICAL: "Scenario fictif: volatilite extreme et accumulation de signaux dangereux.",
    }
    finding_by_scenario = {
        RiskLevel.LOW: Finding(
            title="Volatilite basse fictive",
            description="Amplitude 24h contenue et ratio volume/capitalisation normal.",
            impact=ImpactDirection.NEUTRAL,
            symbols=("btc", "eth"),
            confidence_score=0.88,
            data={"amplitude_24h_pct": 2.4, "mock": True},
        ),
        RiskLevel.MEDIUM: Finding(
            title="Volatilite moderee fictive",
            description="Amplitude 24h legerement elevee mais sans mouvement extreme.",
            impact=ImpactDirection.MIXED,
            symbols=("btc",),
            confidence_score=0.83,
            data={"amplitude_24h_pct": 6.2, "mock": True},
        ),
        RiskLevel.HIGH: Finding(
            title="Volatilite elevee fictive",
            description="BTC affiche une amplitude 24h importante et un mouvement rapide sur 1h.",
            impact=ImpactDirection.BEARISH,
            symbols=("btc",),
            confidence_score=0.82,
            data={"amplitude_24h_pct": 14.8, "variation_1h_pct": -5.4, "mock": True},
        ),
        RiskLevel.CRITICAL: Finding(
            title="Volatilite extreme fictive",
            description="Le scenario simule un mouvement violent et des volumes anormalement eleves.",
            impact=ImpactDirection.BEARISH,
            symbols=("btc", "eth"),
            confidence_score=0.80,
            data={"amplitude_24h_pct": 31.5, "variation_1h_pct": -11.2, "mock": True},
        ),
    }

    return AgentReport(
        agent_name="volatility_risk_agent",
        status=AgentStatus.SUCCESS,
        summary=summary_by_scenario[scenario],
        risk_level=risk_by_scenario[scenario],
        confidence=_confidence_for(scenario),
        findings=(finding_by_scenario[scenario],),
        sources=(MOCK_SOURCE,),
        data={"mock": True, "scenario": scenario.value},
    )


def _news_sentiment_report(scenario: RiskLevel) -> AgentReport:
    risk_by_scenario = {
        RiskLevel.LOW: RiskLevel.LOW,
        RiskLevel.MEDIUM: RiskLevel.LOW,
        RiskLevel.HIGH: RiskLevel.MEDIUM,
        RiskLevel.CRITICAL: RiskLevel.HIGH,
    }
    sentiment_by_scenario = {
        RiskLevel.LOW: SentimentLabel.POSITIVE,
        RiskLevel.MEDIUM: SentimentLabel.MIXED,
        RiskLevel.HIGH: SentimentLabel.NEGATIVE,
        RiskLevel.CRITICAL: SentimentLabel.NEGATIVE,
    }
    summary_by_scenario = {
        RiskLevel.LOW: "Scenario fictif: actualites constructives et sentiment global positif.",
        RiskLevel.MEDIUM: "Scenario fictif: sentiment mixte avec quelques titres prudents.",
        RiskLevel.HIGH: "Scenario fictif: actualites negatives et prudence accrue du marche.",
        RiskLevel.CRITICAL: "Scenario fictif: alerte hack/exploit fictive et sentiment tres negatif.",
    }
    finding_by_scenario = {
        RiskLevel.LOW: Finding(
            title="News positives fictives",
            description="Des annonces de developpement et d'adoption soutiennent le sentiment.",
            impact=ImpactDirection.BULLISH,
            symbols=("btc", "eth"),
            confidence_score=0.80,
            data={"assets": ["bitcoin", "ethereum"], "mock": True},
        ),
        RiskLevel.MEDIUM: Finding(
            title="Sentiment mixte fictif",
            description="Les articles de demonstration melangent adoption et prudence macro.",
            impact=ImpactDirection.MIXED,
            symbols=("btc", "eth"),
            confidence_score=0.76,
            data={"assets": ["bitcoin", "ethereum"], "mock": True},
        ),
        RiskLevel.HIGH: Finding(
            title="News negatives fictives",
            description="Des titres fictifs evoquent liquidations, regulation et nervosite du marche.",
            impact=ImpactDirection.BEARISH,
            symbols=("btc", "eth"),
            confidence_score=0.74,
            data={"assets": ["bitcoin", "ethereum"], "mock": True},
        ),
        RiskLevel.CRITICAL: Finding(
            title="Exploit fictif important",
            description="Le scenario simule un hack/exploit fictif sur un protocole DeFi majeur.",
            impact=ImpactDirection.BEARISH,
            symbols=("ethereum", "aave"),
            confidence_score=0.72,
            data={"assets": ["ethereum", "aave"], "mock": True},
        ),
    }

    sentiment = sentiment_by_scenario[scenario]
    return AgentReport(
        agent_name="news_sentiment_agent",
        status=AgentStatus.SUCCESS,
        summary=summary_by_scenario[scenario],
        risk_level=risk_by_scenario[scenario],
        confidence=max(_confidence_for(scenario) - 0.04, 0.65),
        findings=(finding_by_scenario[scenario],),
        sources=(MOCK_SOURCE,),
        data={"mock": True, "scenario": scenario.value, "sentiment": sentiment.value},
    )


def _onchain_fundamental_report(scenario: RiskLevel) -> AgentReport:
    risk_by_scenario = {
        RiskLevel.LOW: RiskLevel.LOW,
        RiskLevel.MEDIUM: RiskLevel.LOW,
        RiskLevel.HIGH: RiskLevel.LOW,
        RiskLevel.CRITICAL: RiskLevel.MEDIUM,
    }
    summary_by_scenario = {
        RiskLevel.LOW: "Scenario fictif: fondamentaux DeFi solides et TVL stable.",
        RiskLevel.MEDIUM: "Scenario fictif: fondamentaux corrects avec activite a surveiller.",
        RiskLevel.HIGH: "Scenario fictif: fondamentaux encore corrects malgre la volatilite.",
        RiskLevel.CRITICAL: "Scenario fictif: TVL en baisse rapide sur un protocole de demonstration.",
    }
    finding_by_scenario = {
        RiskLevel.LOW: Finding(
            title="TVL stable fictive",
            description="Uniswap et Aave affichent une activite stable dans le scenario mock.",
            impact=ImpactDirection.NEUTRAL,
            symbols=("uniswap", "aave"),
            confidence_score=0.84,
            data={"protocols": ["uniswap", "aave"], "mock": True},
        ),
        RiskLevel.MEDIUM: Finding(
            title="Activite DeFi surveillee fictive",
            description="La TVL reste correcte mais les volumes de demonstration augmentent.",
            impact=ImpactDirection.MIXED,
            symbols=("uniswap", "aave"),
            confidence_score=0.78,
            data={"protocols": ["uniswap", "aave"], "mock": True},
        ),
        RiskLevel.HIGH: Finding(
            title="Fondamentaux resilients fictifs",
            description="Les donnees on-chain mockees restent solides malgre le stress de marche.",
            impact=ImpactDirection.NEUTRAL,
            symbols=("uniswap", "aave"),
            confidence_score=0.76,
            data={"protocols": ["uniswap", "aave"], "mock": True},
        ),
        RiskLevel.CRITICAL: Finding(
            title="TVL en recul fictif",
            description="Aave affiche une baisse de TVL fictive dans le scenario de stress.",
            impact=ImpactDirection.BEARISH,
            symbols=("aave",),
            confidence_score=0.72,
            data={"protocols": ["aave"], "mock": True},
        ),
    }

    return AgentReport(
        agent_name="onchain_fundamental_agent",
        status=AgentStatus.SUCCESS,
        summary=summary_by_scenario[scenario],
        risk_level=risk_by_scenario[scenario],
        confidence=max(_confidence_for(scenario) - 0.02, 0.68),
        findings=(finding_by_scenario[scenario],),
        sources=(MOCK_SOURCE,),
        data={
            "mock": True,
            "scenario": scenario.value,
            "protocols": [
                {"slug": "uniswap", "name": "Uniswap"},
                {"slug": "aave", "name": "Aave"},
            ],
        },
    )


def _confidence_for(scenario: RiskLevel) -> float:
    return {
        RiskLevel.LOW: 0.88,
        RiskLevel.MEDIUM: 0.84,
        RiskLevel.HIGH: 0.80,
        RiskLevel.CRITICAL: 0.76,
    }[scenario]
