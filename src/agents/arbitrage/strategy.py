class Strategy:
    def __init__(self, id):
        self.id = id
        self.fitness = 0.0

class StrategyPool:
    def __init__(self):
        self.strategies = []
    def best(self):
        return Strategy("best")
    def __len__(self):
        return 0
    def serialize(self):
        return {}
