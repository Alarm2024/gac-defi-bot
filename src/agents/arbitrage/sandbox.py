class ShadowSandbox:
    def __init__(self, config):
        self.config = config
    def update_context(self, snapshot):
        pass
    def simulate(self, strategy):
        class Result:
            def __init__(self):
                self.profit = 0
                self.gas_cost = 0
        return Result()
