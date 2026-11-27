import { Box, Button, Center, Checkbox, Group, Image, Loader, Modal, Slider, Stack, Text } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { IconRestore } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';

import type { Photo } from '../api';

export interface Adjust { brightness: number; contrast: number }

interface Props {
  itemId: number;
  photo: Photo | null;
  count: number;
  onClose: () => void;
  onSave: (adjust: Adjust, all: boolean) => Promise<void>;
}

export function AdjustModal({ itemId, photo, count, onClose, onSave }: Props) {
  return (
    <Modal opened={!!photo} onClose={onClose} size="xl" title={photo ? `Adjust photo ${photo.seq}` : ''} centered>
      {photo && <Editor key={photo.seq} itemId={itemId} photo={photo} count={count} onClose={onClose} onSave={onSave} />}
    </Modal>
  );
}

function Editor({ itemId, photo, count, onClose, onSave }: Props & { photo: Photo }) {
  const [adj, setAdj] = useState<Adjust>(photo.adjust ?? { brightness: 0, contrast: 0 });
  const [all, setAll] = useState(false);
  const [saving, setSaving] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [debounced] = useDebouncedValue(adj, 250);
  const last = useRef<string | null>(null);

  // The preview is made by the server with the same code as the real photo.
  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    fetch(`/api/items/${itemId}/photos/${photo.seq}/adjust-preview`, {
      method: 'POST', signal: ctrl.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(debounced),
    })
      .then((r) => r.blob())
      .then((b) => {
        if (last.current) URL.revokeObjectURL(last.current);
        last.current = URL.createObjectURL(b);
        setUrl(last.current);
      })
      .catch(() => {})
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false); });
    return () => ctrl.abort();
  }, [debounced, itemId, photo.seq]);

  const slider = (key: keyof Adjust, label: string) => (
    <Group gap="sm" align="center" wrap="nowrap">
      <Text size="sm" w={80}>{label}</Text>
      <Slider style={{ flex: 1 }} min={-60} max={60} step={1} value={adj[key]} onChange={(v) => setAdj({ ...adj, [key]: v })}
        marks={[{ value: 0 }]} label={(v) => (v > 0 ? `+${v}` : `${v}`)} />
      <Text size="sm" w={40} ta="right" ff="monospace">{adj[key] > 0 ? `+${adj[key]}` : adj[key]}</Text>
    </Group>
  );

  return (
    <Stack>
      <Box pos="relative">
        <Center bg="dark.8" h="58vh" style={{ borderRadius: 8 }}>
          {url ? <Image src={url} mah="58vh" maw="100%" w="auto" h="auto" fit="contain" alt="" /> : <Loader />}
        </Center>
        {loading && url && <Loader size="xs" pos="absolute" top={10} right={10} />}
      </Box>
      {slider('brightness', 'Brightness')}
      {slider('contrast', 'Contrast')}
      <Text size="xs" c="dimmed">Only the photo changes, not the frame, logo or texts. A default for all photos can be set on the Photo layout page.</Text>
      <Group justify="space-between">
        <Group>
          <Button variant="subtle" color="gray" leftSection={<IconRestore size={16} />} disabled={!adj.brightness && !adj.contrast}
            onClick={() => setAdj({ brightness: 0, contrast: 0 })}>Reset</Button>
          {count > 1 && <Checkbox label={`Apply to all ${count} photos of this item`} checked={all} onChange={(e) => setAll(e.currentTarget.checked)} />}
        </Group>
        <Group>
          <Button variant="default" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button loading={saving} onClick={async () => {
            setSaving(true);
            try { await onSave(adj, all); onClose(); } finally { setSaving(false); }
          }}>Save</Button>
        </Group>
      </Group>
    </Stack>
  );
}
