from evals.run import ObservedTurn


class NoOpConversationDriver:
    """A deliberately empty public-turn driver used to prove scoring fails closed."""

    async def execute_turn(self, scenario, turn) -> ObservedTurn:
        return ObservedTurn()
