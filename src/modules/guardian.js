import { CHAIN_REGISTRY } from '../config/constants.js';
export class GuardianModule {
  constructor(blockchain, env) { this.blockchain = blockchain; this.env = env; }
  async checkAll() {
    return { chains: { ETH: { approved: true, gwei: 10 } }, bestChain: { key: 'ETH', approved: true }, anyApproved: true };
  }
}
