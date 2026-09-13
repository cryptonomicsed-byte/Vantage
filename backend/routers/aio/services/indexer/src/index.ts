import 'dotenv/config';
import { SuiClient, getFullnodeUrl } from '@mysten/sui.js/client';

const NODE = process.env.SUI_RPC || getFullnodeUrl('localnet');
const client = new SuiClient({ url: NODE });

(async () => {
  console.log('[indexer] connecting', NODE);
  const sub = await client.subscribeEvent({
    filter: { Package: process.env.AIO_PACKAGE_ID! },
    onMessage: (ev) => {
      console.log('[event]', JSON.stringify(ev, null, 2));
    },
  });
  console.log('[indexer] subscribed');
})();
