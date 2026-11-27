import { Button, Group, Modal, Slider, Stack, Text } from '@mantine/core';
import { IconRestore } from '@tabler/icons-react';
import { useState } from 'react';
import Cropper, { type Area } from 'react-easy-crop';

import type { Crop, Photo } from '../api';

interface Props {
  photo: Photo | null;
  aspect: number;
  onClose: () => void;
  onSave: (crop: Crop) => Promise<void>;
  onReset: () => Promise<void>;
}

// The crop is stored as fractions of the upright photo, so the preview's size doesn't matter.
export function CropModal({ photo, aspect, onClose, onSave, onReset }: Props) {
  return (
    <Modal opened={!!photo} onClose={onClose} size="xl" title={photo ? `Crop photo ${photo.seq}` : ''} centered>
      {photo && <CropEditor key={photo.seq} photo={photo} aspect={aspect} onClose={onClose} onSave={onSave} onReset={onReset} />}
    </Modal>
  );
}

function CropEditor({ photo, aspect, onClose, onSave, onReset }: Props & { photo: Photo }) {
  const [crop, setCrop] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [area, setArea] = useState<Area | null>(null);
  const [busy, setBusy] = useState<'save' | 'reset' | null>(null);
  const initial = photo.crop
    ? { x: photo.crop.x * 100, y: photo.crop.y * 100, width: photo.crop.w * 100, height: photo.crop.h * 100 }
    : undefined;

  const run = async (kind: 'save' | 'reset', fn: () => Promise<void>) => {
    setBusy(kind);
    try { await fn(); onClose(); } finally { setBusy(null); }
  };

  return (
    <Stack>
      <div style={{ position: 'relative', height: '60vh', background: 'var(--mantine-color-dark-8)', borderRadius: 8, overflow: 'hidden' }}>
        <Cropper
          image={photo.preview}
          crop={crop}
          zoom={zoom}
          aspect={aspect}
          minZoom={1}
          maxZoom={6}
          zoomSpeed={0.2}
          initialCroppedAreaPercentages={initial}
          onCropChange={setCrop}
          onZoomChange={setZoom}
          onCropComplete={(pct) => setArea(pct)}
          showGrid
        />
      </div>
      <Group gap="sm" align="center">
        <Text size="sm" w={50}>Zoom</Text>
        <Slider style={{ flex: 1 }} min={1} max={6} step={0.01} value={zoom} onChange={setZoom} label={(v) => `${v.toFixed(1)}×`} />
      </Group>
      <Text size="xs" c="dimmed">Drag the photo to move it, scroll or use the slider to zoom. The box has the shape of the photo area in the frame.</Text>
      <Group justify="space-between">
        <Button variant="subtle" color="gray" leftSection={<IconRestore size={16} />} disabled={!photo.manual_crop || !!busy}
          loading={busy === 'reset'} onClick={() => run('reset', onReset)}>
          Back to automatic crop
        </Button>
        <Group>
          <Button variant="default" onClick={onClose} disabled={!!busy}>Cancel</Button>
          <Button loading={busy === 'save'} disabled={!area || busy === 'reset'}
            onClick={() => area && run('save', () => onSave({ x: area.x / 100, y: area.y / 100, w: area.width / 100, h: area.height / 100 }))}>
            Save crop
          </Button>
        </Group>
      </Group>
    </Stack>
  );
}
