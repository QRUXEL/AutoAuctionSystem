import { Badge } from '@mantine/core';

import type { Status } from '../api';

const LOOK: Record<Status, { color: string; label: string }> = {
  capturing: { color: 'blue', label: 'Capturing' },
  processing: { color: 'indigo', label: 'Processing' },
  review: { color: 'yellow', label: 'To review' },
  publishing: { color: 'cyan', label: 'Publishing' },
  published: { color: 'green', label: 'Published' },
  error: { color: 'red', label: 'Problem' },
};

export function StatusBadge({ status }: { status: Status }) {
  const look = LOOK[status];
  return <Badge color={look.color} variant="light">{look.label}</Badge>;
}
