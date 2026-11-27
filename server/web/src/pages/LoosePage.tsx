import {
  Alert, AspectRatio, Button, Card, Center, Checkbox, Group, Image, Loader, Modal, Paper, Select, SimpleGrid, Stack, Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconFolderPlus, IconInfoCircle, IconLink, IconMovie, IconTrash } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api, clock, type Item, type LooseFile, ref } from '../api';
import { useInfo } from '../App';

export function LoosePage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const info = useInfo().data;
  const { data, isLoading } = useQuery({ queryKey: ['loose'], queryFn: () => api<LooseFile[]>('/api/loose'), refetchInterval: 5000 });
  const { data: targets } = useQuery({
    queryKey: ['items', 'attach-targets'],
    queryFn: () => api<Item[]>('/api/items?status=review,error'),
  });
  const [picked, setPicked] = useState<number[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [target, setTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const live = picked.filter((id) => data?.some((f) => f.id === id));
  const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const done = () => {
    setPicked([]);
    qc.invalidateQueries({ queryKey: ['loose'] });
    qc.invalidateQueries({ queryKey: ['items'] });
    qc.invalidateQueries({ queryKey: ['info'] });
  };
  const run = (name: string, fn: () => Promise<void>) => async () => {
    setBusy(name);
    try { await fn(); } catch (e) {
      notifications.show({ color: 'red', title: 'That did not work', message: (e as Error).message });
    } finally { setBusy(null); setConfirmDelete(false); }
  };

  const makeItem = run('make', async () => {
    const r = await api<{ id: number }>('/api/loose/make-item', { method: 'POST', body: { ids: live } });
    done();
    navigate(`/items/${r.id}`);
  });
  const attach = run('attach', async () => {
    await api('/api/loose/attach', { method: 'POST', body: { ids: live, item_id: Number(target) } });
    done();
    notifications.show({ color: 'green', message: `Added to ${ref(Number(target))}` });
  });
  const remove = run('delete', async () => {
    const r = await api<{ deleted: number }>('/api/loose/delete', { method: 'POST', body: { ids: live } });
    done();
    notifications.show({ message: `Deleted ${r.deleted} file${r.deleted === 1 ? '' : 's'}` });
  });

  const offset = info?.camera_offset;
  const hasPhoto = data?.some((f) => live.includes(f.id) && f.kind === 'photo');

  return (
    <Stack pb={90}>
      <Title order={2}>Loose shots</Title>
      <Text c="dimmed" size="sm">
        Photos and videos taken outside a capture started with the button, such as test shots. Delete them, add them to an
        item, or turn a few into a new item.
      </Text>
      {offset != null && Math.abs(offset) > 5 && (
        <Alert variant="light" color="gray" icon={<IconInfoCircle />}>
          The camera's clock is about {formatOffset(offset)} {offset > 0 ? 'behind' : 'ahead of'} the server. That is
          corrected automatically when matching shots to captures.
        </Alert>
      )}
      {isLoading && <Center h={200}><Loader /></Center>}
      {data && data.length === 0 && <Text c="dimmed">No loose shots.</Text>}

      {data && data.length > 0 && (
        <Group gap="xs">
          <Button size="xs" variant="subtle" onClick={() => setPicked(data.map((f) => f.id))}>Select all</Button>
          {live.length > 0 && <Button size="xs" variant="subtle" color="gray" onClick={() => setPicked([])}>Clear selection</Button>}
        </Group>
      )}

      <SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 6 }} spacing="sm">
        {data?.map((f) => {
          const on = live.includes(f.id);
          return (
            <Card key={f.id} padding={6} withBorder onClick={() => toggle(f.id)} style={{
              cursor: 'pointer', outline: on ? '2px solid var(--mantine-primary-color-filled)' : undefined,
            }}>
              <Card.Section pos="relative">
                <AspectRatio ratio={4 / 3}>
                  {f.thumb ? <Image src={f.thumb} alt="" /> : <Center bg="dark.6"><IconMovie size={36} opacity={0.5} /></Center>}
                </AspectRatio>
                <Checkbox checked={on} readOnly pos="absolute" top={6} left={6} aria-label="Select" />
              </Card.Section>
              <Text size="xs" mt={6} truncate>{f.name || (f.kind === 'video' ? 'Video' : 'Photo')}</Text>
              <Text size="xs" c="dimmed">{f.shot_at ? `Shot ${clock(f.shot_at)}` : `Arrived ${clock(f.received_at)}`}</Text>
            </Card>
          );
        })}
      </SimpleGrid>

      {live.length > 0 && (
        <Paper withBorder p="sm" pos="fixed" bottom={12} left={{ base: 12, sm: 242 }} right={12} shadow="lg" style={{ zIndex: 20 }}>
          <Group justify="space-between">
            <Text size="sm">{live.length} selected</Text>
            <Group gap="xs">
              <Button size="sm" leftSection={<IconFolderPlus size={16} />} onClick={makeItem} loading={busy === 'make'} disabled={!hasPhoto}>
                Make new item
              </Button>
              <Select size="sm" w={220} placeholder="Add to item…" value={target} onChange={setTarget} searchable clearable
                data={(targets ?? []).map((i) => ({ value: String(i.id), label: `${ref(i.id)} ${i.title || ''}`.trim() }))}
                comboboxProps={{ withinPortal: true }} />
              <Button size="sm" variant="light" leftSection={<IconLink size={16} />} disabled={!target} loading={busy === 'attach'} onClick={attach}>
                Add
              </Button>
              <Button size="sm" variant="subtle" color="red" leftSection={<IconTrash size={16} />} onClick={() => setConfirmDelete(true)}>
                Delete
              </Button>
            </Group>
          </Group>
        </Paper>
      )}

      <Modal opened={confirmDelete} onClose={() => setConfirmDelete(false)} title="Delete loose shots" centered>
        <Text size="sm">The {live.length} selected file{live.length === 1 ? ' is' : 's are'} deleted from the server. This can't be undone.</Text>
        <Group justify="flex-end" mt="lg">
          <Button variant="default" onClick={() => setConfirmDelete(false)}>Cancel</Button>
          <Button color="red" loading={busy === 'delete'} onClick={remove}>Delete</Button>
        </Group>
      </Modal>
    </Stack>
  );
}

function formatOffset(s: number) {
  const a = Math.abs(s);
  if (a < 90) return `${Math.round(a)} seconds`;
  if (a < 5400) return `${Math.round(a / 60)} minutes`;
  return `${(a / 3600).toFixed(1)} hours`;
}
