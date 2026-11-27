import { Anchor } from '@mantine/core';
import { notifications } from '@mantine/notifications';

import { api, type Item } from './api';

const ref = (id: number) => `#${String(id).padStart(4, '0')}`;

/** Follows an item after Send / Go live, even after the page has moved on, and says how it went.
 *  Failures stay on screen until closed, so they can't be missed. */
export function watchSend(itemId: number, open: (path: string) => void) {
  const key = `send-${itemId}`;
  notifications.show({ id: key, loading: true, autoClose: false, withCloseButton: false, message: `${ref(itemId)}: sending to Tradera…` });
  const started = Date.now();
  const tick = async () => {
    let item: Item | null = null;
    try { item = await api<Item>(`/api/items/${itemId}`); } catch { /* try again */ }
    if (item && item.status !== 'publishing') {
      const link = (
        <Anchor size="sm" component="button" onClick={() => { notifications.hide(key); open(`/items/${itemId}`); }}>Open {ref(itemId)}</Anchor>
      );
      if (item.status === 'error') {
        notifications.update({ id: key, loading: false, color: 'red', autoClose: false, withCloseButton: true,
          title: `${ref(itemId)} was not sent`, message: <>{item.message} {link}</> });
      } else if (item.status === 'published') {
        notifications.update({ id: key, loading: false, color: 'green', autoClose: 8000, withCloseButton: true,
          title: `${ref(itemId)} is live on Tradera`,
          message: item.tradera_item_id ? <Anchor size="sm" href={`https://www.tradera.com/item/${item.tradera_item_id}`} target="_blank">Open on Tradera</Anchor> : link });
      } else if (item.status === 'sent') {
        notifications.update({ id: key, loading: false, color: 'grape', autoClose: 8000, withCloseButton: true,
          title: `${ref(itemId)} is on Tradera, not live`, message: item.message || 'Press Go live on the item to publish it.' });
      } else {
        notifications.update({ id: key, loading: false, color: 'blue', autoClose: 8000, withCloseButton: true,
          title: `${ref(itemId)}`, message: item.message || 'Done.' });
      }
      return;
    }
    if (Date.now() - started > 5 * 60_000) {
      notifications.update({ id: key, loading: false, color: 'yellow', autoClose: false, withCloseButton: true,
        message: `${ref(itemId)} is still being sent. Check the item in a while.` });
      return;
    }
    setTimeout(tick, 2000);
  };
  setTimeout(tick, 1500);
}
