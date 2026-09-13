import 'dotenv/config';
import { SuiClient, getFullnodeUrl } from '@mysten/sui.js/client';

const NODE = process.env.SUI_RPC || getFullnodeUrl('localnet');
const client = new SuiClient({ url: NODE });

(async () => {
  console.log('[oracle] ready at', NODE);
  // Phase-1 stub: nothing to push on-chain. Registration done via script.
})();
