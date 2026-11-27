import { Badge, Button, Group, Indicator, Popover, Stack, Text, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCamera, IconCircleFilled, IconPlayerPlay, IconPlayerStop, IconRotate360, IconSquareFilled } from '@tabler/icons-react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api } from '../api';

export interface StationView {
  online: boolean;
  last_seen_seconds: number | null;
  pending: 'capture' | 'abort' | null;
  ip: string;
  session: { item_id: number; seconds: number; photos: number; videos: number } | null;
  status: {
    busy?: boolean; camera?: boolean; table?: boolean; camera_note?: string; table_note?: string;
    table_battery?: number; last?: string; last_item?: number; wifi_rssi?: number; version?: string;
  };
}

/** Start/stop a capture and see the station's state. The ESP32 picks commands up within about a second. */
export function StationControl() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ['station'], queryFn: () => api<StationView>('/api/station'), refetchInterval: 1500 });
  const [sending, setSending] = useState(false);
  if (!data) return null;
  const s = data.status;
  const busy = !!s.busy || data.pending === 'capture';
  const ready = data.online && s.camera && s.table;

  const send = async (command: 'capture' | 'abort') => {
    setSending(true);
    try {
      qc.setQueryData(['station'], await api<StationView>('/api/station/command', { method: 'POST', body: { command } }));
      notifications.show({ message: command === 'capture' ? 'Starting the capture…' : 'Stopping the capture…' });
    } catch (e) {
      notifications.show({ color: 'red', title: 'Not sent', message: (e as Error).message });
    } finally { setSending(false); }
  };

  const session = data.session;
  const toggleSession = async () => {
    setSending(true);
    try {
      const r = await api<{ item_id: number; session: StationView['session'] }>(session ? '/api/session/stop' : '/api/session/start', { method: 'POST' });
      qc.setQueryData(['station'], { ...data, session: r.session });
      qc.invalidateQueries({ queryKey: ['info'] });
      notifications.show({ message: session
        ? `Item #${String(r.item_id).padStart(4, '0')} stopped. It is processed once the last uploads are in.`
        : `Item #${String(r.item_id).padStart(4, '0')} started. Everything you shoot now goes into it, until Stop item.` });
    } catch (e) {
      notifications.show({ color: 'red', title: 'Did not work', message: (e as Error).message });
    } finally { setSending(false); }
  };
  const elapsed = session ? `${Math.floor(session.seconds / 60)}:${String(Math.floor(session.seconds % 60)).padStart(2, '0')}` : '';

  const color = !data.online ? 'gray' : busy ? 'blue' : ready ? 'green' : 'yellow';
  const label = !data.online ? 'Station offline' : busy ? 'Capturing' : ready ? 'Station ready' : 'Station not ready';

  return (
    <Group gap="xs" wrap="nowrap">
      {session ? (
        <Group gap={6} wrap="nowrap">
          <Badge component={Link} to={`/items/${session.item_id}`} color="red" variant="light" style={{ cursor: 'pointer' }}
            leftSection={<IconCircleFilled size={8} />}>
            #{String(session.item_id).padStart(4, '0')} · {session.photos} photo{session.photos === 1 ? '' : 's'}
            {session.videos ? ` · ${session.videos} video${session.videos === 1 ? '' : 's'}` : ''} · {elapsed}
          </Badge>
          <Button size="xs" color="red" leftSection={<IconSquareFilled size={12} />} loading={sending} onClick={toggleSession}>Stop item</Button>
        </Group>
      ) : (
        <Tooltip label="Opens one item: every photo and video shot until Stop item goes into it, by hand or with turntable runs">
          <Button size="xs" variant="light" color="red" leftSection={<IconCircleFilled size={10} />} loading={sending} onClick={toggleSession}>Start item</Button>
        </Tooltip>
      )}
      <Popover width={300} position="bottom-end" withArrow shadow="md">
        <Popover.Target>
          <Badge component="button" variant="light" color={color} style={{ cursor: 'pointer' }}
            leftSection={<Indicator color={color} size={7} processing={busy} position="middle-center" inline><span /></Indicator>}>
            <Text span size="xs" visibleFrom="sm" fw={700}>{label}</Text>
          </Badge>
        </Popover.Target>
        <Popover.Dropdown>
          <Stack gap={6}>
            <Text fw={600} size="sm">{label}</Text>
            {!data.online && (
              <Text size="xs" c="dimmed">
                {data.last_seen_seconds === null ? 'The ESP32 has not checked in since the server started.'
                  : `Last seen ${Math.round(data.last_seen_seconds)} s ago.`} It needs firmware with the check-in, Wi-Fi, and the right server address and key.
              </Text>
            )}
            {data.online && (
              <>
                <Row icon={<IconCamera size={14} />} ok={!!s.camera} label="Camera" note={s.camera ? 'connected' : s.camera_note} />
                <Row icon={<IconRotate360 size={14} />} ok={!!s.table} label="Turntable"
                  note={s.table ? `connected${s.table_battery != null && s.table_battery >= 0 ? `, battery ${s.table_battery}%` : ''}` : s.table_note} />
                {s.last && <Text size="xs" c={s.last.startsWith('error') ? 'red' : 'dimmed'}>Last run: {s.last}</Text>}
                <Text size="xs" c="dimmed">{data.ip}{s.wifi_rssi ? ` · Wi-Fi ${s.wifi_rssi} dBm` : ''}{s.version ? ` · firmware ${s.version}` : ''}</Text>
              </>
            )}
          </Stack>
        </Popover.Dropdown>
      </Popover>
      {busy ? (
        <Button size="xs" color="red" variant="light" leftSection={<IconPlayerStop size={14} />} loading={sending || data.pending === 'abort'}
          onClick={() => send('abort')}>Stop</Button>
      ) : (
        <Tooltip label={!data.online ? 'The station is offline' : !ready ? 'Camera or turntable not connected'
          : session ? `A turntable run, shot into the open item #${String(session.item_id).padStart(4, '0')}` : 'Photographs and films the item on the turntable'}>
          <Button size="xs" leftSection={<IconPlayerPlay size={14} />} disabled={!ready} loading={sending}
            onClick={() => send('capture')}>Start capture</Button>
        </Tooltip>
      )}
    </Group>
  );
}

function Row({ icon, ok, label, note }: { icon: React.ReactNode; ok: boolean; label: string; note?: string }) {
  return (
    <Group gap={6} wrap="nowrap" align="flex-start">
      <Badge size="xs" circle color={ok ? 'green' : 'red'} mt={3}>{''}</Badge>
      {icon}
      <Text size="xs"><b>{label}:</b> {note || (ok ? 'ok' : 'not connected')}</Text>
    </Group>
  );
}
