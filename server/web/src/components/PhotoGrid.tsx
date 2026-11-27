import { ActionIcon, Badge, Box, Center, FileButton, Group, Image, Loader, SimpleGrid, Stack, Text, Tooltip, UnstyledButton } from '@mantine/core';
import { IconAdjustmentsHorizontal, IconCrop, IconPhotoPlus, IconStar, IconTrash } from '@tabler/icons-react';
import { useState } from 'react';

import type { Photo } from '../api';

export const MAX_PHOTOS = 40;          // Tradera's limit per listing

interface Props {
  photos: Photo[];
  selected: number;
  locked: boolean;
  busy: string | null;
  onSelect: (i: number) => void;
  onMove: (from: number, to: number) => void;
  onCrop: (p: Photo) => void;
  onAdjust: (p: Photo) => void;
  onRemove: (p: Photo) => void;
  onUpload: (files: File[]) => void;
}

/** Every photo of an item as a tile: drag to reorder, the first is the main photo; actions on each tile; an Add tile for uploads. */
export function PhotoGrid({ photos, selected, locked, busy, onSelect, onMove, onCrop, onAdjust, onRemove, onUpload }: Props) {
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const [filesOver, setFilesOver] = useState(false);
  const full = photos.length >= MAX_PHOTOS;

  const dropFiles = (e: React.DragEvent) => {
    e.preventDefault();
    setFilesOver(false);
    const files = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith('image/') || /\.(heic|heif)$/i.test(f.name));
    if (files.length) onUpload(files);
  };

  return (
    <Stack gap={6}>
      <Group justify="space-between">
        <Text size="sm" fw={500}>Photos <Text span c="dimmed" size="sm">· {photos.length} of max {MAX_PHOTOS}</Text></Text>
        {!locked && photos.length > 1 && <Text size="xs" c="dimmed">Drag to reorder. The first is the main photo and Tradera thumbnail.</Text>}
      </Group>
      <SimpleGrid cols={{ base: 3, xs: 4, sm: 5, lg: 6 }} spacing={8}>
        {photos.map((p, i) => (
          <Box key={p.id} pos="relative" draggable={!locked}
            onDragStart={(e) => { setDragFrom(i); e.dataTransfer.effectAllowed = 'move'; }}
            onDragOver={(e) => { if (dragFrom !== null) { e.preventDefault(); setOver(i); } }}
            onDragLeave={() => setOver((o) => (o === i ? null : o))}
            onDrop={(e) => {
              e.preventDefault();
              if (dragFrom !== null) onMove(dragFrom, i);
              else dropFiles(e);
              setDragFrom(null); setOver(null);
            }}
            onDragEnd={() => { setDragFrom(null); setOver(null); }}
            style={{
              aspectRatio: '1', borderRadius: 8, overflow: 'hidden', cursor: locked ? 'pointer' : 'grab',
              opacity: dragFrom === i ? 0.35 : 1, background: 'var(--mantine-color-dark-8)',
              outline: over === i && dragFrom !== i ? '2px dashed var(--mantine-color-yellow-5)'
                : i === selected ? '2px solid var(--mantine-primary-color-filled)' : '1px solid var(--mantine-color-dark-4)',
            }}
            className="photo-tile">
            <UnstyledButton onClick={() => onSelect(i)} w="100%" h="100%">
              {p.framed ? <Image src={p.framed} w="100%" h="100%" fit="contain" alt="" draggable={false} /> : <Center h="100%"><Loader size="xs" /></Center>}
            </UnstyledButton>
            <Group gap={4} pos="absolute" top={4} left={4} style={{ pointerEvents: 'none' }}>
              {i === 0 ? <Badge size="xs" color="yellow" variant="filled">Main</Badge> : <Badge size="xs" color="dark" variant="filled">{i + 1}</Badge>}
              {p.manual_crop && <Badge size="xs" color="violet" variant="filled" px={4}><IconCrop size={10} /></Badge>}
            </Group>
            {!locked && (
              <Group gap={2} pos="absolute" bottom={4} right={4} className="photo-tile-actions">
                <Tooltip label="Crop"><ActionIcon size="sm" variant="default" onClick={() => onCrop(p)} aria-label="Crop"><IconCrop size={14} /></ActionIcon></Tooltip>
                <Tooltip label="Brightness and contrast"><ActionIcon size="sm" variant="default" onClick={() => onAdjust(p)} aria-label="Adjust"><IconAdjustmentsHorizontal size={14} /></ActionIcon></Tooltip>
                {i > 0 && <Tooltip label="Make main photo"><ActionIcon size="sm" variant="default" onClick={() => onMove(i, 0)} aria-label="Make main"><IconStar size={14} /></ActionIcon></Tooltip>}
                {photos.length > 1 && (
                  <Tooltip label="Remove (goes to Loose shots)">
                    <ActionIcon size="sm" variant="filled" color="red" loading={busy === `detach-${p.seq}`} onClick={() => onRemove(p)} aria-label="Remove"><IconTrash size={14} /></ActionIcon>
                  </Tooltip>
                )}
              </Group>
            )}
          </Box>
        ))}
        {!locked && !full && (
          <FileButton onChange={(files) => files.length && onUpload(files)} accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif" multiple>
            {(props) => (
              <UnstyledButton {...props}
                onDragOver={(e) => { if (dragFrom === null) { e.preventDefault(); setFilesOver(true); } }}
                onDragLeave={() => setFilesOver(false)} onDrop={dropFiles}
                style={{ aspectRatio: '1', borderRadius: 8, border: `2px dashed var(--mantine-color-${filesOver ? 'blue-5' : 'dark-3'})` }}>
                <Center h="100%">
                  {busy === 'upload' ? <Loader size="sm" /> : (
                    <Stack gap={2} align="center">
                      <IconPhotoPlus size={22} />
                      <Text size="xs" ta="center">Add photos</Text>
                      <Text size="10px" c="dimmed" ta="center">or drop them here</Text>
                    </Stack>
                  )}
                </Center>
              </UnstyledButton>
            )}
          </FileButton>
        )}
      </SimpleGrid>
      {full && !locked && <Text size="xs" c="yellow.5">Tradera takes at most {MAX_PHOTOS} photos. Remove one to add another.</Text>}
    </Stack>
  );
}
