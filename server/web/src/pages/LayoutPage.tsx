import {
  ActionIcon, Alert, AspectRatio, Badge, Box, Button, Center, ColorInput, FileButton, Grid, Group, Image, Loader, Menu, NumberInput,
  Paper, SegmentedControl, Select, SimpleGrid, Slider, Stack, Switch, Text, Textarea, TextInput, Title, Tooltip, UnstyledButton,
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import {
  IconArrowDown, IconArrowUp, IconEye, IconEyeOff, IconLayoutBottombar, IconPhoto, IconPlus, IconTextSize, IconTrash, IconUpload,
} from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { api } from '../api';

type Anchor = 'top-left' | 'top' | 'top-right' | 'left' | 'center' | 'right' | 'bottom-left' | 'bottom' | 'bottom-right';
type BoxName = 'canvas' | 'photo' | 'margin-top' | 'margin-bottom' | 'margin-left' | 'margin-right';

interface Base { id: string; name: string; visible: boolean; box: BoxName; opacity: number }
interface TextEl extends Base { type: 'text'; text: string; anchor: Anchor; x: number; y: number; size: number; color: string; bold: boolean; shadow: boolean; font: string }
interface ImageEl extends Base { type: 'image'; asset: string; anchor: Anchor; x: number; y: number; height: number }
interface BarEl extends Base { type: 'bar'; edge: 'top' | 'bottom' | 'left' | 'right'; thickness: number; color: string }
type El = TextEl | ImageEl | BarEl;

interface Layout {
  canvas: { shape: string; long_side: number; background: string };
  margins: { top: number; right: number; bottom: number; left: number; color: string };
  crop: { auto: boolean; padding: number };
  elements: El[];
}
interface Asset { name: string; kind: 'image' | 'font'; size: number }

const SHAPES = [
  { value: 'photo', label: "Keep each photo's shape" },
  { value: '1:1', label: 'Square 1:1' },
  { value: '4:3', label: 'Landscape 4:3' },
  { value: '3:2', label: 'Landscape 3:2' },
  { value: '16:9', label: 'Wide 16:9' },
  { value: '3:4', label: 'Portrait 3:4' },
  { value: '2:3', label: 'Portrait 2:3' },
];
const BOXES = [
  { value: 'photo', label: 'The photo' },
  { value: 'canvas', label: 'Whole image' },
  { value: 'margin-bottom', label: 'Bottom margin' },
  { value: 'margin-top', label: 'Top margin' },
  { value: 'margin-left', label: 'Left margin' },
  { value: 'margin-right', label: 'Right margin' },
];
const ANCHORS: Anchor[] = ['top-left', 'top', 'top-right', 'left', 'center', 'right', 'bottom-left', 'bottom', 'bottom-right'];
const TESTS = [
  { key: 'landscape', label: 'Landscape photo', ratio: 3 / 2 },
  { key: 'portrait', label: 'Portrait photo', ratio: 2 / 3 },
  { key: 'square', label: 'Square photo', ratio: 1 },
];
const ICON = { text: IconTextSize, image: IconPhoto, bar: IconLayoutBottombar };

let counter = 0;
const newId = (t: string) => `${t}-${Date.now().toString(36)}${counter++}`;

export function LayoutPage() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ['layout'], queryFn: () => api<{ layout: Layout; assets: Asset[] }>('/api/layout') });
  const { data: samples } = useQuery({
    queryKey: ['layout-samples'],
    queryFn: () => api<{ id: number; label: string; thumb: string }[]>('/api/layout/samples'),
  });
  const [draft, setDraft] = useState<Layout | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [dirty, setDirty] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [sample, setSample] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedOnce, setSavedOnce] = useState(false);

  useEffect(() => {
    if (data && !dirty) { setDraft(data.layout); setAssets(data.assets); }
  }, [data, dirty]);
  useEffect(() => { if (!sample && samples?.length) setSample(String(samples[0].id)); }, [samples, sample]);

  if (!draft) return <Center h={300}><Loader /></Center>;

  const update = (fn: (l: Layout) => Layout) => { setDraft((l) => (l ? fn(structuredClone(l)) : l)); setDirty(true); };
  const el = draft.elements.find((e) => e.id === selected) ?? null;
  const setEl = (patch: Partial<El>) => update((l) => ({ ...l, elements: l.elements.map((e) => (e.id === selected ? { ...e, ...patch } as El : e)) }));
  const move = (id: string, dir: -1 | 1) => update((l) => {
    const i = l.elements.findIndex((e) => e.id === id);
    const j = i + dir;
    if (j < 0 || j >= l.elements.length) return l;
    [l.elements[i], l.elements[j]] = [l.elements[j], l.elements[i]];
    return l;
  });
  const add = (type: El['type']) => {
    const id = newId(type);
    const base = { id, visible: true, opacity: 100 };
    const made: El = type === 'text'
      ? { ...base, type, name: 'Text', text: 'New text', box: 'photo', anchor: 'bottom-left', x: 3, y: 3, size: 4, color: '#ffffff', bold: true, shadow: true, font: '' }
      : type === 'image'
        ? { ...base, type, name: 'Graphic', asset: assets.find((a) => a.kind === 'image')?.name ?? '', box: 'photo', anchor: 'top-right', x: 3, y: 3, height: 10 }
        : { ...base, type, name: 'Bar', box: 'photo', edge: 'bottom', thickness: 8, color: '#000000', opacity: 50 };
    update((l) => ({ ...l, elements: [...l.elements, made] }));
    setSelected(id);
  };

  const save = async () => {
    setSaving(true);
    try {
      const r = await api<{ layout: Layout; assets: Asset[] }>('/api/layout', { method: 'PUT', body: { layout: draft } });
      qc.setQueryData(['layout'], r);
      qc.invalidateQueries({ queryKey: ['info'] });
      setDraft(r.layout); setDirty(false); setSavedOnce(true);
      notifications.show({ color: 'green', message: 'Layout saved. New photos use it from now on.' });
    } catch (e) {
      notifications.show({ color: 'red', title: 'Not saved', message: (e as Error).message });
    } finally { setSaving(false); }
  };
  const apply = async () => {
    const r = await api<{ items: number }>('/api/layout/apply', { method: 'POST' });
    qc.invalidateQueries({ queryKey: ['items'] });
    notifications.show({ message: `Re-framing ${r.items} item${r.items === 1 ? '' : 's'} that are not published yet` });
  };
  const upload = async (file: File | null) => {
    if (!file) return;
    try {
      const r = await fetch(`/api/layout/assets/${encodeURIComponent(file.name)}`, { method: 'PUT', body: file });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail ?? r.statusText);
      setAssets(body.assets);
      if (el?.type === 'image' && /\.(png|jpe?g|webp)$/i.test(body.name)) setEl({ asset: body.name });
      if (el?.type === 'text' && /\.(ttf|otf)$/i.test(body.name)) setEl({ font: body.name });
      notifications.show({ color: 'green', message: `Uploaded ${body.name}` });
    } catch (e) {
      notifications.show({ color: 'red', title: 'Upload failed', message: (e as Error).message });
    }
  };

  return (
    <Stack pb={90}>
      <Group justify="space-between">
        <Title order={2}>Photo layout</Title>
        <Badge variant="light" size="lg">Sizes are % of the photo's short side</Badge>
      </Group>
      <Text c="dimmed" size="sm">
        Pin each logo, text or bar to a corner or edge of the photo or of a margin. Because sizes follow the photo's short side, the
        same layout fits landscape, portrait and square photos. The previews on the right are made by the same code that frames the
        real photos.
      </Text>

      <Grid gap="lg">
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Stack>
            <Paper withBorder p="md">
              <Title order={5} mb="sm">Image</Title>
              <SimpleGrid cols={2}>
                <Select label="Shape" data={SHAPES} value={draft.canvas.shape} allowDeselect={false}
                  onChange={(v) => v && update((l) => ({ ...l, canvas: { ...l.canvas, shape: v } }))} />
                <NumberInput label="Long side" suffix=" px" min={400} max={4000} step={100} value={draft.canvas.long_side}
                  onChange={(v) => update((l) => ({ ...l, canvas: { ...l.canvas, long_side: Number(v) || 1600 } }))} />
              </SimpleGrid>
              <Group mt="sm" align="flex-end">
                <Switch label="Crop around the item automatically" checked={draft.crop.auto}
                  onChange={(e) => { const auto = e.currentTarget.checked; update((l) => ({ ...l, crop: { ...l.crop, auto } })); }} />
              </Group>
              {draft.crop.auto && (
                <Box mt="xs">
                  <Text size="sm">Space around the item: {draft.crop.padding}%</Text>
                  <Slider min={0} max={40} step={1} value={draft.crop.padding}
                    onChange={(v) => update((l) => ({ ...l, crop: { ...l.crop, padding: v } }))} />
                </Box>
              )}
            </Paper>

            <Paper withBorder p="md">
              <Title order={5} mb="sm">Margins around the photo</Title>
              <SimpleGrid cols={4}>
                {(['top', 'right', 'bottom', 'left'] as const).map((side) => (
                  <NumberInput key={side} label={side[0].toUpperCase() + side.slice(1)} min={0} max={60} step={0.5} decimalScale={1}
                    value={draft.margins[side]} onChange={(v) => update((l) => ({ ...l, margins: { ...l.margins, [side]: Number(v) || 0 } }))} />
                ))}
              </SimpleGrid>
              <ColorInput mt="sm" label="Margin colour" value={draft.margins.color}
                onChange={(v) => update((l) => ({ ...l, margins: { ...l.margins, color: v } }))} />
            </Paper>

            <Paper withBorder p="md">
              <Group justify="space-between" mb="sm">
                <Title order={5}>Elements</Title>
                <Menu position="bottom-end">
                  <Menu.Target><Button size="xs" leftSection={<IconPlus size={14} />}>Add</Button></Menu.Target>
                  <Menu.Dropdown>
                    <Menu.Item leftSection={<IconTextSize size={16} />} onClick={() => add('text')}>Text</Menu.Item>
                    <Menu.Item leftSection={<IconPhoto size={16} />} onClick={() => add('image')}>Logo or graphic</Menu.Item>
                    <Menu.Item leftSection={<IconLayoutBottombar size={16} />} onClick={() => add('bar')}>Colour bar</Menu.Item>
                  </Menu.Dropdown>
                </Menu>
              </Group>
              <Text size="xs" c="dimmed" mb="xs">Lower in the list is drawn on top.</Text>
              <Stack gap={4}>
                {draft.elements.map((e, i) => {
                  const Icon = ICON[e.type];
                  return (
                    <Group key={e.id} gap={6} wrap="nowrap" p={6} style={{
                      borderRadius: 8, cursor: 'pointer',
                      background: e.id === selected ? 'var(--mantine-color-dark-5)' : 'var(--mantine-color-dark-6)',
                    }} onClick={() => setSelected(e.id === selected ? null : e.id)}>
                      <Icon size={16} />
                      <Text size="sm" style={{ flex: 1 }} c={e.visible ? undefined : 'dimmed'} truncate>{e.name}</Text>
                      <ActionIcon variant="subtle" color="gray" size="sm" disabled={i === 0}
                        onClick={(ev) => { ev.stopPropagation(); move(e.id, -1); }} aria-label="Move down a layer"><IconArrowUp size={14} /></ActionIcon>
                      <ActionIcon variant="subtle" color="gray" size="sm" disabled={i === draft.elements.length - 1}
                        onClick={(ev) => { ev.stopPropagation(); move(e.id, 1); }} aria-label="Move up a layer"><IconArrowDown size={14} /></ActionIcon>
                      <ActionIcon variant="subtle" color="gray" size="sm" aria-label="Show or hide"
                        onClick={(ev) => { ev.stopPropagation(); update((l) => ({ ...l, elements: l.elements.map((x) => (x.id === e.id ? { ...x, visible: !x.visible } : x)) })); }}>
                        {e.visible ? <IconEye size={14} /> : <IconEyeOff size={14} />}
                      </ActionIcon>
                      <ActionIcon variant="subtle" color="red" size="sm" aria-label="Remove"
                        onClick={(ev) => { ev.stopPropagation(); update((l) => ({ ...l, elements: l.elements.filter((x) => x.id !== e.id) })); }}>
                        <IconTrash size={14} />
                      </ActionIcon>
                    </Group>
                  );
                })}
                {draft.elements.length === 0 && <Text size="sm" c="dimmed">No elements yet.</Text>}
              </Stack>
            </Paper>

            {el && (
              <Paper withBorder p="md">
                <Title order={5} mb="sm">{el.name}</Title>
                <Stack gap="sm">
                  <TextInput label="Name (only shown here)" value={el.name} onChange={(e) => setEl({ name: e.currentTarget.value })} />
                  <Select label="Placed in" data={BOXES} value={el.box} allowDeselect={false} onChange={(v) => v && setEl({ box: v as BoxName })} />

                  {el.type === 'bar' ? (
                    <SimpleGrid cols={2}>
                      <Select label="Along edge" data={['top', 'bottom', 'left', 'right'].map((v) => ({ value: v, label: v[0].toUpperCase() + v.slice(1) }))}
                        value={el.edge} allowDeselect={false} onChange={(v) => v && setEl({ edge: v as BarEl['edge'] })} />
                      <NumberInput label="Thickness" min={0.1} max={100} step={0.5} decimalScale={1} value={el.thickness}
                        onChange={(v) => setEl({ thickness: Number(v) || 1 })} />
                    </SimpleGrid>
                  ) : (
                    <Group align="flex-start" gap="lg">
                      <Box>
                        <Text size="sm" fw={500} mb={4}>Pinned to</Text>
                        <AnchorPicker value={el.anchor} onChange={(anchor) => setEl({ anchor })} />
                      </Box>
                      <Stack gap="xs" style={{ flex: 1 }}>
                        <NumberInput label="In from the side" min={-100} max={100} step={0.5} decimalScale={1} value={el.x}
                          onChange={(v) => setEl({ x: Number(v) || 0 })} />
                        <NumberInput label="In from top/bottom" min={-100} max={100} step={0.5} decimalScale={1} value={el.y}
                          onChange={(v) => setEl({ y: Number(v) || 0 })} />
                      </Stack>
                    </Group>
                  )}

                  {el.type === 'text' && (
                    <>
                      <Textarea label="Text" autosize minRows={1} value={el.text} onChange={(e) => setEl({ text: e.currentTarget.value })}
                        description="{ref} = item number, {date} = today's date. Long text shrinks to fit." />
                      <SimpleGrid cols={2}>
                        <NumberInput label="Size" min={0.5} max={40} step={0.2} decimalScale={1} value={el.size} onChange={(v) => setEl({ size: Number(v) || 4 })} />
                        <ColorInput label="Colour" value={el.color} onChange={(v) => setEl({ color: v })} />
                      </SimpleGrid>
                      <Group>
                        <Switch label="Bold" checked={el.bold} onChange={(e) => setEl({ bold: e.currentTarget.checked })} />
                        <Switch label="Shadow (readable on photos)" checked={el.shadow} onChange={(e) => setEl({ shadow: e.currentTarget.checked })} />
                      </Group>
                      <Group align="flex-end">
                        <Select style={{ flex: 1 }} label="Font" clearable placeholder="Built-in (DejaVu Sans)"
                          data={assets.filter((a) => a.kind === 'font').map((a) => a.name)} value={el.font || null}
                          onChange={(v) => setEl({ font: v ?? '' })} />
                        <FileButton onChange={upload} accept=".ttf,.otf">
                          {(props) => <Button variant="default" leftSection={<IconUpload size={14} />} {...props}>Upload font</Button>}
                        </FileButton>
                      </Group>
                    </>
                  )}

                  {el.type === 'image' && (
                    <>
                      <Group align="flex-end">
                        <Select style={{ flex: 1 }} label="Graphic" placeholder="Upload a PNG with transparency"
                          data={assets.filter((a) => a.kind === 'image').map((a) => a.name)} value={el.asset || null}
                          onChange={(v) => setEl({ asset: v ?? '' })} />
                        <FileButton onChange={upload} accept="image/png,image/jpeg,image/webp">
                          {(props) => <Button variant="default" leftSection={<IconUpload size={14} />} {...props}>Upload</Button>}
                        </FileButton>
                      </Group>
                      {el.asset && (
                        <Paper p="xs" bg="dark.8" style={{ backgroundImage: 'repeating-conic-gradient(#333 0 25%, #2a2a2a 0 50%)', backgroundSize: '16px 16px' }}>
                          <Image src={`/api/layout/assets/${encodeURIComponent(el.asset)}`} h={60} fit="contain" alt="" />
                        </Paper>
                      )}
                      <NumberInput label="Height" min={0.5} max={100} step={0.5} decimalScale={1} value={el.height}
                        onChange={(v) => setEl({ height: Number(v) || 5 })} />
                    </>
                  )}

                  {el.type !== 'text' && el.type !== 'image' && (
                    <ColorInput label="Colour" value={el.color} onChange={(v) => setEl({ color: v })} />
                  )}
                  <Box>
                    <Text size="sm">Opacity: {el.opacity}%</Text>
                    <Slider min={0} max={100} step={5} value={el.opacity} onChange={(v) => setEl({ opacity: v })} />
                  </Box>
                </Stack>
              </Paper>
            )}
          </Stack>
        </Grid.Col>

        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Paper withBorder p="md" pos="sticky" top={70} style={{ overflow: "hidden" }}>
            <Group justify="space-between" mb="sm">
              <Title order={5}>Preview</Title>
              <Select size="xs" w={260} placeholder="Sample photo" value={sample} onChange={setSample}
                data={(samples ?? []).map((s) => ({ value: String(s.id), label: s.label }))}
                nothingFoundMessage="No photos yet; using a placeholder" />
            </Group>
            <Previews layout={draft} fileId={sample ? Number(sample) : null} />
            {draft.canvas.shape !== 'photo' && (
              <Text size="xs" c="dimmed" mt="xs">With a fixed shape, every photo is cropped to it, so all three come out the same shape.</Text>
            )}
          </Paper>
        </Grid.Col>
      </Grid>

      {(dirty || savedOnce) && (
        <Paper withBorder p="sm" pos="fixed" bottom={12} left={{ base: 12, sm: 242 }} right={12} shadow="lg" style={{ zIndex: 20 }}>
          <Group justify="space-between">
            <Text size="sm">{dirty ? 'Unsaved layout changes' : 'Saved. Photos already framed keep the old layout until re-framed.'}</Text>
            <Group gap="xs">
              {dirty && <Button variant="default" onClick={() => { setDirty(false); }}>Discard</Button>}
              {dirty && <Button loading={saving} onClick={save}>Save layout</Button>}
              {!dirty && (
                <Tooltip label="Frames the photos of all items that are not published yet again, keeping manual crops">
                  <Button variant="light" onClick={apply}>Re-frame unpublished items</Button>
                </Tooltip>
              )}
            </Group>
          </Group>
        </Paper>
      )}
    </Stack>
  );
}

function AnchorPicker({ value, onChange }: { value: Anchor; onChange: (a: Anchor) => void }) {
  return (
    <SimpleGrid cols={3} spacing={4} w={96}>
      {ANCHORS.map((a) => (
        <Tooltip key={a} label={a.replace('-', ' ')} openDelay={400}>
          <UnstyledButton onClick={() => onChange(a)} aria-label={a} style={{
            height: 28, borderRadius: 6, border: '1px solid var(--mantine-color-dark-4)',
            background: a === value ? 'var(--mantine-primary-color-filled)' : 'var(--mantine-color-dark-6)',
          }} />
        </Tooltip>
      ))}
    </SimpleGrid>
  );
}

function Previews({ layout, fileId }: { layout: Layout; fileId: number | null }) {
  const [debounced] = useDebouncedValue(JSON.stringify({ layout, fileId }), 350);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [big, setBig] = useState('landscape');
  const current = useRef<Record<string, string>>({});

  useEffect(() => {
    const ctrl = new AbortController();
    const { layout: l, fileId: f } = JSON.parse(debounced);
    setLoading(true);
    Promise.all(TESTS.map(async (t) => {
      const r = await fetch('/api/layout/preview', {
        method: 'POST', signal: ctrl.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ layout: l, file_id: f, test: t.key, size: 900 }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? r.statusText);
      return [t.key, URL.createObjectURL(await r.blob())] as const;
    })).then((pairs) => {
      Object.values(current.current).forEach(URL.revokeObjectURL);
      current.current = Object.fromEntries(pairs);
      setUrls(current.current);
      setError(null);
    }).catch((e) => { if (e.name !== 'AbortError') setError(String(e.message ?? e)); })
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false); });
    return () => ctrl.abort();
  }, [debounced]);

  return (
    <Stack gap="sm">
      {error && <Alert color="red" variant="light">{error}</Alert>}
      <SegmentedControl fullWidth value={big} onChange={setBig} data={TESTS.map((t) => ({ value: t.key, label: t.label }))} />
      <Box pos="relative">
        <Center bg="dark.8" style={{ borderRadius: 8, minHeight: 300 }} p="xs">
          {urls[big] ? <Image src={urls[big]} mah="60vh" maw="100%" w="auto" h="auto" fit="contain" alt="" /> : <Loader />}
        </Center>
        {loading && <Loader size="xs" pos="absolute" top={10} right={10} />}
      </Box>
      <SimpleGrid cols={3}>
        {TESTS.map((t) => (
          <UnstyledButton key={t.key} onClick={() => setBig(t.key)}>
            <AspectRatio ratio={1}>
              <Center bg="dark.8" style={{ borderRadius: 6, outline: big === t.key ? '2px solid var(--mantine-primary-color-filled)' : undefined }}>
                {urls[t.key] && <Image src={urls[t.key]} mah="100%" maw="100%" fit="contain" alt="" />}
              </Center>
            </AspectRatio>
            <Text size="xs" ta="center" mt={4} c="dimmed">{t.label}</Text>
          </UnstyledButton>
        ))}
      </SimpleGrid>
    </Stack>
  );
}
