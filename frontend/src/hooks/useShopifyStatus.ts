import { useContext } from 'react';

import { ShopifyStatusContext } from '../contexts/ShopifyStatusContext';
import type { ShopifyStatus } from '../contexts/ShopifyStatusContext';

export function useShopifyStatus(): ShopifyStatus {
  const context = useContext(ShopifyStatusContext);
  if (!context) throw new Error('useShopifyStatus must be used inside <ShopifyStatusProvider>');
  return context;
}
