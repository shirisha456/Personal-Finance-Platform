class Topics:
    """Kafka topic names, centralized so a producer or consumer never
    hand-types a topic string. Every topic here is both produced and
    consumed by real code somewhere in this repo — see docs/phase7.md
    for why unused topics aren't defined speculatively here."""

    TRANSACTIONS_INGESTED = "transactions.ingested"
    TRANSACTIONS_ENRICHED = "transactions.enriched"
    ALERTS_RAISED = "alerts.raised"
    INSIGHTS_GENERATED = "insights.generated"

    @classmethod
    def all(cls) -> list[str]:
        return [
            cls.TRANSACTIONS_INGESTED,
            cls.TRANSACTIONS_ENRICHED,
            cls.ALERTS_RAISED,
            cls.INSIGHTS_GENERATED,
        ]
