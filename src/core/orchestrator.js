import { KV_KEYS } from '../config/constants.js';
export class Orchestrator {
  constructor(services) {
    this.services = services;
    this.env = services.env;
    this.kv = services.kv;
    this.circuitBreaker = services.circuitBreaker;
    this.log = console;
  }

  async run() {
    const { circuitBreaker, price, gasOracle, guardian, sentinel, strategist, executor, tradeLogger } = this.services;

    const locked = await circuitBreaker.acquireLock();
    if (!locked) return { outcome: 'SKIPPED_LOCK' };

    try {
      if (await circuitBreaker.isOpen()) {
        const state = await circuitBreaker.getState();
        await tradeLogger.logFailure('circuit_breaker_open', { pauseReason: state.pauseReason });
        return { outcome: 'CB_OPEN', state };
      }

      const state = (await this.kv.getJSON(KV_KEYS.BOT_STATE)) || { cycle: 0, status: 'idle' };
      state.cycle = (state.cycle || 0) + 1;
      state.status = 'scanning';
      await this.kv.putJSON(KV_KEYS.BOT_STATE, state);

      const priceData = await price.fetch();
      const gasData = await gasOracle.fetchForChain('BSC', priceData.BNBUSDT?.price || 580);
      const strategistRes = await strategist.decide(priceData, gasData);

      state.lastSignal = strategistRes.signal;
      state.lastChain = 'BSC';
      state.lastNet = strategistRes.netAfterFee?.toFixed(2) || null;

      let executorRes = { executed: false, reason: 'no_signal' };

      if (strategistRes.signal === 'BUY') {
        try {
          executorRes = await executor.execute(strategistRes, 'BSC');
          if (executorRes.executed) {
            await circuitBreaker.recordSuccess();
            state.totalBuy = (state.totalBuy || 0) + 1;
          }
        } catch (err) {
          const reason = err.message.slice(0, 200);
          await tradeLogger.logFailure(reason, { chain: 'BSC', gwei: gasData.gwei });
          await circuitBreaker.recordFailure(reason, false);
          executorRes = { executed: false, reason };
          this.log.error('Execution failed', { reason });
        }
      } else {
        await tradeLogger.logFailure(strategistRes.holdReason || 'signal_hold', {
          spread: strategistRes.grossReturn / strategistRes.loanAmount,
          netAfterFee: strategistRes.netAfterFee,
          chain: 'BSC'
        });
      }

      state.status = 'idle';
      await this.kv.putJSON(KV_KEYS.BOT_STATE, state);
      return { outcome: 'CYCLE_COMPLETE', cycle: state.cycle, targetChain: 'BSC', executor: executorRes };
    } finally {
      await circuitBreaker.releaseLock();
    }
  }
}