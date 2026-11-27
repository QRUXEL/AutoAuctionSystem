import { Alert, Anchor, Badge, Button, Card, Group, Image, Stack, Table, Text, TextInput, Title, Tooltip, UnstyledButton } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconChevronDown, IconChevronUp, IconExternalLink, IconRefresh, IconSearch, IconSelector } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../api';

interface Auction {
  id: number; title: string | null; tradera_item_id: number; link: string; image: string | null;
  bid: number | null; bids: number; next_bid: number | null; start_price: number | null;
  last_bid_at: number | null; bidder: string; winner: string; end: number | null; ended: boolean;
  checked_at: number | null; error: string;
}
interface Response { auctions: Auction[]; now: number }

type Key = 'title' | 'bid' | 'last_bid_at' | 'bidder' | 'end';
const kr = (n: number | null | undefined) => (n == null ? '' : `${n.toLocaleString('sv-SE')} kr`);

function left(end: number | null, now: number): string {
  if (!end) return '';
  let s = Math.max(0, Math.round(end - now));
  if (s === 0) return 'ended';
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function ago(t: number | null, now: number): string {
  if (!t) return '';
  const s = Math.max(0, now - t);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleString('sv-SE', { dateStyle: 'short', timeStyle: 'short' });
}

/** Published listings with live bidding, sortable by any column. */
export function AuctionsPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['auctions'], queryFn: () => api<Response>('/api/auctions'), refetchInterval: 15_000 });
  const [sort, setSort] = useState<{ key: Key; desc: boolean }>({ key: 'end', desc: false });
  const [filter, setFilter] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  // a ticking clock, so the time left counts down between refreshes
  const [offset, setOffset] = useState(0);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { if (data) setOffset(data.now - Date.now() / 1000); }, [data]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(t); }, []);
  const serverNow = now + offset;

  const rows = useMemo(() => {
    const list = (data?.auctions ?? []).filter((a) => !filter || `${a.title} ${a.bidder} ${a.winner} #${a.id}`.toLowerCase().includes(filter.toLowerCase()));
    const val = (a: Auction): number | string => {
      switch (sort.key) {
        case 'title': return (a.title ?? '').toLowerCase();
        case 'bid': return a.bid ?? -1;
        case 'last_bid_at': return a.last_bid_at ?? 0;
        case 'bidder': return (a.winner || a.bidder || '').toLowerCase();
        default: return a.ended ? Number.MAX_SAFE_INTEGER - (a.end ?? 0) : a.end ?? Number.MAX_SAFE_INTEGER;
      }
    };
    return [...list].sort((x, y) => {
      const a = val(x), b = val(y);
      const c = a < b ? -1 : a > b ? 1 : 0;
      return sort.desc ? -c : c;
    });
  }, [data, sort, filter]);

  const total = (data?.auctions ?? []).reduce((sum, a) => sum + (a.bid ?? 0), 0);
  const withBids = (data?.auctions ?? []).filter((a) => a.bids > 0).length;

  const refresh = async () => {
    setRefreshing(true);
    try {
      qc.setQueryData(['auctions'], await api<Response>('/api/auctions/refresh', { method: 'POST' }));
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message });
    } finally { setRefreshing(false); }
  };

  const Th = ({ k, children, w }: { k: Key; children: React.ReactNode; w?: number }) => {
    const active = sort.key === k;
    const Icon = active ? (sort.desc ? IconChevronDown : IconChevronUp) : IconSelector;
    return (
      <Table.Th w={w}>
        <UnstyledButton onClick={() => setSort({ key: k, desc: active ? !sort.desc : k === 'bid' || k === 'last_bid_at' })}>
          <Group gap={4} wrap="nowrap"><Text size="sm" fw={600}>{children}</Text><Icon size={14} /></Group>
        </UnstyledButton>
      </Table.Th>
    );
  };

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Auctions</Title>
          <Text c="dimmed" size="sm">
            {data?.auctions.length ?? 0} live or ended listings · {withBids} with bids · <b>{kr(total)}</b> in current bids
          </Text>
        </div>
        <Group>
          <TextInput placeholder="Filter" leftSection={<IconSearch size={14} />} value={filter} onChange={(e) => setFilter(e.currentTarget.value)} />
          <Button variant="light" leftSection={<IconRefresh size={16} />} loading={refreshing} onClick={refresh}>Check now</Button>
        </Group>
      </Group>
      <Text size="xs" c="dimmed">
        Checked on Tradera every 15 minutes, every 5 in the last day, every minute in the last hour and every 20 seconds in the last 10 minutes.
        Tradera doesn&apos;t give bid times, so “Last bid” is when the server first saw the new bid.
      </Text>

      {!isLoading && !data?.auctions.length && <Alert variant="light">No published listings yet. They show up here once they are live on Tradera.</Alert>}

      {!!rows.length && (
        <Card withBorder p={0}>
          <Table.ScrollContainer minWidth={760}>
            <Table highlightOnHover verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th w={64} />
                  <Th k="title">Item</Th>
                  <Th k="bid" w={130}>Current bid</Th>
                  <Th k="last_bid_at" w={120}>Last bid</Th>
                  <Th k="bidder" w={150}>Bidder / winner</Th>
                  <Th k="end" w={140}>Time left</Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((a) => {
                  const remaining = a.ended ? 'ended' : left(a.end, serverNow);
                  const soon = !a.ended && a.end != null && a.end - serverNow < 3600;
                  return (
                    <Table.Tr key={a.id}>
                      <Table.Td>
                        {a.image ? <Image src={a.image} w={52} h={52} fit="cover" radius="sm" alt="" /> : null}
                      </Table.Td>
                      <Table.Td>
                        <Stack gap={0}>
                          <Anchor component={Link} to={`/items/${a.id}`} size="sm" fw={500} lineClamp={2}>{a.title || 'Untitled'}</Anchor>
                          <Group gap={6}>
                            <Text size="xs" c="dimmed">#{String(a.id).padStart(4, '0')}</Text>
                            <Anchor href={a.link} target="_blank" size="xs">Tradera <IconExternalLink size={10} /></Anchor>
                            {a.error && <Tooltip label={a.error}><Badge size="xs" color="red" variant="light">check failed</Badge></Tooltip>}
                          </Group>
                        </Stack>
                      </Table.Td>
                      <Table.Td>
                        {a.bids ? (
                          <Stack gap={0}>
                            <Text size="sm" fw={700}>{kr(a.bid)}</Text>
                            <Text size="xs" c="dimmed">{a.bids} bid{a.bids === 1 ? '' : 's'}</Text>
                          </Stack>
                        ) : (
                          <Stack gap={0}>
                            <Text size="sm" c="dimmed">No bids</Text>
                            {a.start_price ? <Text size="xs" c="dimmed">from {kr(a.start_price)}</Text> : null}
                          </Stack>
                        )}
                      </Table.Td>
                      <Table.Td><Text size="sm">{ago(a.last_bid_at, serverNow)}</Text></Table.Td>
                      <Table.Td>
                        {a.winner
                          ? <Group gap={4}><Badge size="xs" color="green">Winner</Badge><Text size="sm">{a.winner}</Text></Group>
                          : <Text size="sm">{a.bidder}</Text>}
                      </Table.Td>
                      <Table.Td>
                        {a.ended
                          ? <Badge color={a.bids ? 'green' : 'gray'} variant="light">{a.bids ? 'Sold' : 'Ended, no bids'}</Badge>
                          : <Text size="sm" fw={soon ? 700 : 400} c={soon ? 'orange.4' : undefined} ff="monospace">{remaining}</Text>}
                        {a.end && <Text size="xs" c="dimmed">{new Date(a.end * 1000).toLocaleString('sv-SE', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</Text>}
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      )}
    </Stack>
  );
}
