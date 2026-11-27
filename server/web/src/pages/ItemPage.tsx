import {
  ActionIcon, Alert, Anchor, AspectRatio, Badge, Button, Center, Code, CopyButton, Grid, Group, Image, Loader, Modal, NumberInput,
  Paper, Select, Stack, Text, Textarea, TextInput, Title, Tooltip, Switch, Checkbox } from '@mantine/core';
import { useHotkeys } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import {
  IconAdjustmentsHorizontal, IconAlertCircle, IconArrowLeft, IconCrop, IconDeviceFloppy, IconExternalLink, IconInfoCircle, IconRefresh, IconSend,
  IconSparkles, IconTrash, IconCopy, IconHeartHandshake } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';

import { api, type Crop, type Item, type Photo, ref } from '../api';
import { useInfo } from '../App';
import { CategorySelect } from '../components/CategorySelect';
import { PhotoGrid } from '../components/PhotoGrid';
import { ManualListing } from '../components/ManualListing';
import { AttributeFields, type Attributes } from '../components/AttributeFields';
import { watchSend } from '../watchSend';
import { BidderSelect, bidderName, ShippingPicker } from '../components/ShippingPicker';
import { AdjustModal } from '../components/AdjustModal';
import { CropModal } from '../components/CropModal';
import { StatusBadge } from '../components/StatusBadge';

interface Form {
  title: string;
  description: string;
  category: { id: number | null; path: string };
  start_price: number | string;
  buy_now_price: number | string;
  duration_days: string;
  end_at: string;
  shipping: Item['shipping_options'] | null;      // null = the defaults in Settings
  bidders: number | null;                          // null = the default in Settings
  attributes: Attributes;                          // category attributes, e.g. Skick
}

const toForm = (i: Item): Form => ({
  title: i.title ?? '',
  description: i.description ?? '',
  category: { id: i.category_id, path: i.category_path ?? '' },
  start_price: i.start_price ?? '',
  buy_now_price: i.buy_now_price ?? '',
  duration_days: String(i.duration_days ?? 7),
  end_at: i.end_at ?? '',
  shipping: i.shipping_override ? i.shipping_options : null,
  bidders: i.accepted_bidder_override ?? null,
  attributes: (() => { try { return JSON.parse(i.attributes || '{}') as Attributes; } catch { return {}; } })(),
});

const BUSY = ['capturing', 'processing', 'publishing'];

export function ItemPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const info = useInfo().data;
  const { data: item, error } = useQuery({
    queryKey: ['item', id],
    queryFn: () => api<Item>(`/api/items/${id}`),
    refetchInterval: (q) => (q.state.data && (BUSY.includes(q.state.data.status) || q.state.data.videos.some((v) => ['queued', 'working'].includes(v.web_status))) ? 2000 : 10000),
  });

  const [form, setForm] = useState<Form | null>(null);
  const [dirty, setDirty] = useState(false);
  const [selected, setSelected] = useState(0);
  const [cropping, setCropping] = useState<Photo | null>(null);
  const [dupOpen, setDupOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [withdrawOriginal, setWithdrawOriginal] = useState(true);
  const [adjusting, setAdjusting] = useState<Photo | null>(null);
  const [confirm, setConfirm] = useState<null | 'publish' | 'golive' | 'delete' | 'rewrite'>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Take fresh data from the server unless someone is editing.
  useEffect(() => {
    if (item && !dirty) setForm(toForm(item));
  }, [item, dirty]);
  useEffect(() => { setDirty(false); setSelected(0); }, [id]);

  const set = <K extends keyof Form>(k: K, v: Form[K]) => {
    setForm((f) => (f ? { ...f, [k]: v } : f));
    setDirty(true);
  };

  const refresh = (fresh?: Item) => {
    if (fresh) qc.setQueryData(['item', id], fresh);
    qc.invalidateQueries({ queryKey: ['items'] });
    qc.invalidateQueries({ queryKey: ['info'] });
  };

  const save = async (quiet = false) => {
    if (!form) return;
    const fresh = await api<Item>(`/api/items/${id}`, {
      method: 'PATCH',
      body: {
        title: form.title, description: form.description,
        category_id: form.category.id, category_path: form.category.path, attributes: form.attributes,
        start_price: form.start_price, buy_now_price: form.buy_now_price, duration_days: Number(form.duration_days),
        end_at: form.end_at, shipping_override: form.shipping, accepted_bidder_override: form.bidders,
      },
    });
    setDirty(false);
    refresh(fresh);
    if (!quiet) notifications.show({ color: 'green', message: 'Saved' });
  };

  const act = (name: string, fn: () => Promise<void>) => async () => {
    setBusy(name);
    try { await fn(); } catch (e) {
      notifications.show({ color: 'red', title: 'That did not work', message: (e as Error).message });
    } finally { setBusy(null); setConfirm(null); }
  };

  const publish = act('publish', async () => {
    if (dirty) await save(true);
    await api(`/api/items/${id}/publish`, { method: 'POST' });
    refresh();
    watchSend(id, navigate);          // reports how it went, even after moving on to the next item
    const next = (await api<Item[]>('/api/items?status=review')).find((i) => i.id !== id);
    navigate(next && !info?.dry_run ? `/items/${next.id}` : info?.dry_run ? `/items/${id}` : '/review');
  });
  const goLive = act('golive', async () => {
    if (dirty) await save(true);
    await api(`/api/items/${id}/go-live`, { method: 'POST' });
    refresh();
    watchSend(id, navigate);
  });
  const duplicate = async () => {
    setBusy('duplicate');
    try {
      const copy = await api<Item>(`/api/items/${id}/duplicate`, { method: 'POST',
        body: { withdraw_original: !!item && ['published', 'sent'].includes(item.status) && withdrawOriginal } });
      setDupOpen(false);
      refresh();
      notifications.show({ color: 'green', message: `${ref(copy.id)} is a copy of ${ref(id)}. Check it and send it again.` });
      navigate(`/items/${copy.id}`);
    } catch (e) {
      notifications.show({ color: 'red', title: 'Not duplicated', message: (e as Error).message });
    } finally { setBusy(null); }
  };
  const upload = async (files: File[]) => {
    const body = new FormData();
    files.forEach((f) => body.append('files', f));
    setBusy('upload');
    try {
      const updated = await api<Item>(`/api/items/${id}/photos`, { method: 'POST', body });
      refresh(updated);
      setSelected(updated.photos.length - 1);
      notifications.show({ color: 'green', message: `${files.length} photo${files.length === 1 ? '' : 's'} added` });
    } catch (e) {
      notifications.show({ color: 'red', title: 'Upload failed', message: (e as Error).message });
    } finally { setBusy(null); }
  };
  const reorder = async (order: number[] | null, select?: number) => {
    setBusy('order');
    try {
      refresh(await api<Item>(`/api/items/${id}/photos/order`, { method: 'PUT', body: { order } }));
      if (select !== undefined) setSelected(select);
    } catch (e) {
      notifications.show({ color: 'red', title: 'Could not reorder', message: (e as Error).message });
    } finally { setBusy(null); }
  };
  /** Moves the photo at index `from` to index `to` (0 = main photo). */
  const move = (from: number, to: number) => {
    if (!item || from === to || to < 0 || to >= item.photos.length) return;
    const seqs = item.photos.map((p) => p.seq);
    const [moved] = seqs.splice(from, 1);
    seqs.splice(to, 0, moved);
    void reorder(seqs, to);
  };
  const rewrite = act('rewrite', async () => {
    await api(`/api/items/${id}/regenerate`, { method: 'POST', body: { photos: false, text: true } });
    setDirty(false);
    refresh();
  });
  const reframe = act('reframe', async () => {
    await api(`/api/items/${id}/regenerate`, { method: 'POST', body: { photos: true, text: false } });
    refresh();
  });
  const detach = (kind: 'photo' | 'video', seq: number) => act('detach', async () => {
    await api(`/api/items/${id}/files/${kind}/${seq}/detach`, { method: 'POST' });
    setSelected(0);
    refresh();
    qc.invalidateQueries({ queryKey: ['item', id] });
    notifications.show({ message: `Moved to Loose shots` });
  });
  const remove = act('delete', async () => {
    await api(`/api/items/${id}`, { method: 'DELETE' });
    refresh();
    navigate('/review');
  });

  const locked = !item || BUSY.includes(item.status) || item.status === 'published' || item.status === 'withdrawn';
  useHotkeys([['mod+S', () => { if (dirty && !locked) act('save', () => save())(); }]]);

  if (error) return <Alert color="red" icon={<IconAlertCircle />}>{(error as Error).message}</Alert>;
  if (!item || !form) return <Center h={300}><Loader /></Center>;

  const photo = item.photos[Math.min(selected, item.photos.length - 1)];
  const missing = [!form.title && 'title', !form.description && 'description', !form.category.id && 'category'].filter(Boolean);

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Group gap="sm">
          <ActionIcon variant="subtle" color="gray" onClick={() => navigate(-1)} aria-label="Back"><IconArrowLeft size={18} /></ActionIcon>
          <Title order={3} ff="monospace">{ref(item.id)}</Title>
          <StatusBadge status={item.status} />
          {item.source === 'manual' && <Badge variant="outline" color="gray">Made from loose shots</Badge>}
          {item.source === 'session' && <Badge variant="outline" color="gray">Started by hand</Badge>}
          {BUSY.includes(item.status) && <Loader size="xs" />}
        </Group>
        <Group gap="sm">
          {item.copied_from && (
            <Anchor component={Link} to={`/items/${item.copied_from}`} size="sm" c="dimmed">Copy of {ref(item.copied_from)}</Anchor>
          )}
          {item.tradera_item_id && item.status !== 'withdrawn' && (
            <Anchor href={`https://www.tradera.com/item/${item.tradera_item_id}`} target="_blank">
              <Group gap={4}>On Tradera <IconExternalLink size={14} /></Group>
            </Anchor>
          )}
          {!BUSY.includes(item.status) && (
            <Tooltip label="A new item to review with the same photos, text and settings, e.g. to list again after fixing errors">
              <Button size="xs" variant="light" leftSection={<IconCopy size={14} />} onClick={() => setDupOpen(true)}>Duplicate</Button>
            </Tooltip>
          )}
        </Group>
      </Group>

      {item.message && <Alert color={item.status === 'error' ? 'red' : 'yellow'} icon={<IconAlertCircle />}>{item.message}</Alert>}

      <Grid gap="lg">
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Stack gap="sm">
            <Paper withBorder p={4} radius="md" pos="relative">
              <AspectRatio ratio={1}>
                {photo?.framed
                  ? <Center bg="dark.8"><Image src={photo.framed} radius="sm" fit="contain" h="100%" w="100%" alt="" /></Center>
                  : <Center bg="dark.6"><Text c="dimmed">No photo yet</Text></Center>}
              </AspectRatio>
              {photo && !locked && (
                <Group gap={6} pos="absolute" top={12} right={12}>
                  <Button size="xs" leftSection={<IconCrop size={14} />} onClick={() => setCropping(photo)}>Crop</Button>
                  <Button size="xs" variant="default" leftSection={<IconAdjustmentsHorizontal size={14} />} onClick={() => setAdjusting(photo)}>Adjust</Button>
                </Group>
              )}
              {photo && (
                <Group gap={6} pos="absolute" bottom={12} left={12}>
                  <Badge variant="filled" color={selected === 0 ? 'yellow' : 'dark'}>{selected === 0 ? 'Main photo' : `Photo ${selected + 1}`}</Badge>
                  <Badge variant="filled" color={photo.manual_crop ? 'violet' : 'dark'}>{photo.manual_crop ? 'Your crop' : 'Automatic crop'}</Badge>
                  {(photo.adjust.brightness !== 0 || photo.adjust.contrast !== 0) && <Badge variant="filled" color="orange">Adjusted</Badge>}
                </Group>
              )}
            </Paper>
            <PhotoGrid photos={item.photos} selected={selected} locked={locked} busy={busy}
              onSelect={setSelected} onMove={move} onCrop={setCropping} onAdjust={setAdjusting}
              onRemove={(p) => detach('photo', p.seq)()} onUpload={upload} />
            <Group justify="space-between">
              <Group gap="xs">
                {!!item.manual_order && !locked && (
                  <Anchor size="xs" c="dimmed" component="button" onClick={() => reorder(null, 0)}>Back to shooting order</Anchor>
                )}
                {item.photos.length < item.expected_photos && BUSY.includes(item.status) && (
                  <Text size="sm" c="dimmed">Waiting for {item.expected_photos - item.photos.length} more…</Text>
                )}
              </Group>
              <Tooltip label="Frame all photos again, for example after changing the logo or frame">
                <Button size="xs" variant="subtle" color="gray" leftSection={<IconRefresh size={14} />} onClick={reframe}
                  loading={busy === 'reframe'} disabled={locked}>Re-frame all</Button>
              </Tooltip>
            </Group>
            {item.videos.map((v) => (
              <Paper key={v.url} withBorder p="xs">
                <Group justify="space-between" mb={6}>
                  <Group gap="xs">
                    <Text size="sm" fw={500}>Spin video</Text>
                    <Badge size="sm" variant="light" color={{ ready: 'green', failed: 'red' }[v.web_status as string] ?? 'blue'}>
                      {{ ready: 'Web copy ready', failed: 'Web copy failed', working: 'Making web copy…', queued: 'Waiting for web copy' }[v.web_status as string] ?? 'Not prepared'}
                    </Badge>
                  </Group>
                  <Group gap="sm">
                    {!locked && <Anchor size="sm" c="dimmed" component="button" onClick={detach('video', v.seq)}>Move to loose shots</Anchor>}
                    <Anchor size="sm" href={v.url} download>Download original</Anchor>
                  </Group>
                </Group>
                {v.web_status === 'ready' && v.preview
                  ? <video src={v.preview} controls preload="metadata" style={{ width: '100%', maxHeight: 320, borderRadius: 6, background: '#000' }} />
                  : <Center h={120} bg="dark.8" style={{ borderRadius: 6 }}>
                      {v.web_status === 'failed'
                        ? <Button size="xs" variant="light" onClick={act('webcopy', async () => { await api(`/api/items/${id}/videos/${v.seq}/web-copy`, { method: 'POST' }); refresh(); qc.invalidateQueries({ queryKey: ['item', id] }); })}>Try again</Button>
                        : <Group gap="xs"><Loader size="xs" /><Text size="sm" c="dimmed">Preparing the video for the web</Text></Group>}
                    </Center>}
                {v.public_url && (
                  <Group gap={6} mt={6} wrap="nowrap">
                    <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>Public link:</Text>
                    <Anchor size="xs" href={v.public_path ?? v.public_url} target="_blank" truncate style={{ flex: 1 }}>{v.public_url}</Anchor>
                    <CopyButton value={v.public_url}>
                      {({ copied, copy }) => <Button size="compact-xs" variant="subtle" onClick={copy}>{copied ? 'Copied' : 'Copy'}</Button>}
                    </CopyButton>
                  </Group>
                )}
                {item.video_host && v.seq === 1 && (
                  <Group gap={6} mt={6} wrap="nowrap" align="flex-start">
                    <Badge size="sm" variant="light" color={item.video_host.error ? 'red' : item.video_host.video ? 'green' : 'gray'} style={{ flexShrink: 0 }}>
                      {item.video_host.error ? 'Upload failed' : item.video_host.video
                        ? (item.video_host.tradera_linked ? 'On streamhjalpen.tv, linked to Tradera' : item.video_host.poster ? 'On streamhjalpen.tv' : 'On streamhjalpen.tv, poster pending')
                        : v.web_status === 'ready' ? 'Uploading to streamhjalpen.tv…' : 'Uploaded to streamhjalpen.tv when ready'}
                    </Badge>
                    {item.video_host.error && <Text size="xs" c="red.4" style={{ flex: 1 }}>{item.video_host.error}</Text>}
                    {item.video_host.error && (
                      <Button size="compact-xs" variant="light" onClick={act('vhretry', async () => {
                        qc.setQueryData(['item', id], await api(`/api/items/${id}/video-host/retry`, { method: 'POST' }));
                      })}>Retry</Button>
                    )}
                  </Group>
                )}
                <Text size="xs" c="dimmed" mt={4}>
                  Put <Code>{'<video link>'}</Code> in the description (or the footer under Settings) where the link should go;
                  without it, a "Se videon: …" line is added at the end when you send.
                </Text>
              </Paper>
            ))}
          </Stack>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 5 }}>
          <Stack gap="sm">
            {item.ai_notes && (
              <Alert color="yellow" variant="light" icon={<IconInfoCircle />} title="Check before publishing">
                <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>{item.ai_notes}</Text>
              </Alert>
            )}
            <TextInput label="Title" maxLength={80} value={form.title} disabled={locked}
              onChange={(e) => set('title', e.currentTarget.value)}
              rightSection={<Text size="xs" c="dimmed">{form.title.length}/80</Text>} rightSectionWidth={52} />
            <Textarea label="Description" autosize minRows={8} maxRows={22} value={form.description} disabled={locked}
              onChange={(e) => set('description', e.currentTarget.value)} />
            {item.videos.length > 0 && !locked && !/<\s*(youtube\s+)?video\s+link\s*>/i.test(form.description) && (
              <Anchor size="xs" component="button" type="button" style={{ alignSelf: 'flex-start', marginTop: -6 }}
                onClick={() => set('description', `${form.description.trimEnd()}\n\nVideo: <video link>`)}>
                + Add the video link to the description
              </Anchor>
            )}
            <CategorySelect value={form.category} onChange={(v) => set('category', v)} disabled={locked} traderaReady={!!info?.tradera_ready || !info?.dry_run} />
            <AttributeFields categoryId={form.category.id} value={form.attributes} disabled={locked} onChange={(v) => set('attributes', v)} />
            <Group grow align="flex-start">
              <NumberInput label="Start price" suffix=" kr" min={1} allowDecimal={false} value={form.start_price} disabled={locked}
                onChange={(v) => set('start_price', v)} />
              <NumberInput label="Buy now" suffix=" kr" min={1} allowDecimal={false} placeholder="Optional" value={form.buy_now_price}
                disabled={locked} onChange={(v) => set('buy_now_price', v)} />
              {!form.end_at && !item.end_preview.at && (
                <Select label="Runs for" data={['1', '3', '5', '7', '10', '14'].map((d) => ({ value: d, label: `${d} days` }))}
                  value={form.duration_days} disabled={locked} onChange={(v) => v && set('duration_days', v)} allowDeselect={false} />
              )}
            </Group>
            <Group grow align="flex-end">
              <TextInput type="datetime-local" label="Ends" disabled={locked} value={form.end_at}
                onChange={(e) => set('end_at', e.currentTarget.value)}
                description={form.end_at ? 'Set on this item' : item.end_preview.at ? `${item.end_preview.text}: ${item.end_preview.how}` : 'From the number of days'} />
              {form.end_at && !locked && (
                <Button variant="subtle" size="xs" onClick={() => set('end_at', '')} style={{ flexGrow: 0 }}>Use Settings</Button>
              )}
              <BidderSelect value={form.bidders} disabled={locked} onChange={(v) => set('bidders', v)}
                defaultLabel={`Default (${bidderName(info?.accepted_bidder_id) || 'Settings'})`} />
            </Group>
            <Paper withBorder p="xs" radius="md">
              <Group justify="space-between" mb={form.shipping ? 6 : 0}>
                <Text size="sm" fw={500}>Shipping</Text>
                <Switch size="xs" label="Same as Settings" checked={form.shipping === null} disabled={locked}
                  onChange={(e) => set('shipping', e.currentTarget.checked ? null : item.shipping_options)} />
              </Group>
              {form.shipping === null ? (
                <Text size="xs" c="dimmed">
                  {item.shipping_options.length ? `From Settings: ${item.shipping_options.map((o) => (o.pickup ? 'buyer collects' : `${o.cost ? `${o.cost} kr` : 'free'}`)).join(', ')}` : 'Nothing set in Settings'}
                </Text>
              ) : (
                <ShippingPicker value={form.shipping} disabled={locked} onChange={(v) => set('shipping', v)} />
              )}
            </Paper>

            {!locked && (
              <Stack gap="xs" mt="sm">
                {item.status === 'sent' && (
                  <Text size="xs" c={item.changed_since_sent || dirty ? 'orange.4' : 'grape.3'}>
                    {item.changed_since_sent || dirty
                      ? 'Changed since it was sent. Send it again so Tradera gets this version (the earlier, unpublished copy stays unused on Tradera).'
                      : 'On Tradera but not published: nobody can see it or bid until you press Go live.'}
                  </Text>
                )}
                {info?.charity?.id ? (
                  <>
                    <Button color="pink" leftSection={<IconHeartHandshake size={16} />} disabled={missing.length > 0} onClick={() => setManualOpen(true)}>
                      List on tradera.com for {info.charity.name || 'the charity'}
                    </Button>
                    <Text size="xs" c="dimmed">
                      Only Tradera's own sell form can donate a listing to a charity; this prepares everything for it.
                      Send below makes an ordinary auction{info.campaign_code ? ` with campaign code ${info.campaign_code}` : ''}.
                    </Text>
                  </>
                ) : info?.campaign_code ? (
                  <Text size="xs" c="pink.4">Sent with campaign code {info.campaign_code}. The code is checked against this category first.</Text>
                ) : (
                  <Text size="xs" c="dimmed">No charity set, so Send makes an ordinary auction.</Text>
                )}
                <Group grow>
                  <Button variant="default" leftSection={<IconDeviceFloppy size={16} />} disabled={!dirty}
                    loading={busy === 'save'} onClick={act('save', () => save())}>Save</Button>
                  <Tooltip label={missing.length ? `Missing: ${missing.join(', ')}` : ''} disabled={!missing.length}>
                    {item.status === 'sent' && !item.changed_since_sent && !dirty ? (
                      <Button color="green" leftSection={<IconSend size={16} />} onClick={() => setConfirm('golive')}>Go live</Button>
                    ) : (
                      <Button color={item.status === 'sent' ? 'orange' : 'green'} leftSection={<IconSend size={16} />} disabled={missing.length > 0}
                        onClick={() => setConfirm('publish')}>
                        {item.status === 'sent' ? 'Send again' : info?.dry_run ? 'Send (dry run)' : info?.auto_commit ? 'Publish to Tradera' : 'Send to Tradera'}
                      </Button>
                    )}
                  </Tooltip>
                </Group>
                <Group grow>
                  <Button variant="light" leftSection={<IconSparkles size={16} />} disabled={!info?.ai_ready}
                    onClick={() => setConfirm('rewrite')}>Rewrite with AI</Button>
                  <Button variant="subtle" color="red" leftSection={<IconTrash size={16} />} onClick={() => setConfirm('delete')}>Delete</Button>
                </Group>
                {dirty && <Text size="xs" c="dimmed">Unsaved changes · Ctrl+S to save</Text>}
              </Stack>
            )}
          </Stack>
        </Grid.Col>
      </Grid>

      {item && <ManualListing item={item} opened={manualOpen} onClose={() => setManualOpen(false)}
        onDone={(updated) => { setManualOpen(false); refresh(updated); }} />}
      <Modal opened={dupOpen} onClose={() => setDupOpen(false)} title={`Duplicate ${item ? ref(item.id) : ''}`} centered>
        <Stack>
          <Text size="sm">
            Makes a new item to review with the same photos (crops and adjustments kept), videos, text, category, Skick, prices and shipping.
            Nothing is sent to Tradera until you press Send on the copy.
          </Text>
          {item && ['published', 'sent'].includes(item.status) && (
            <Checkbox checked={withdrawOriginal} onChange={(e) => setWithdrawOriginal(e.currentTarget.checked)}
              label="The original has been removed from Tradera"
              description="Marks it as removed: its bids are no longer followed and its video page is taken down. Remove it on Tradera yourself; the API can't." />
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setDupOpen(false)}>Cancel</Button>
            <Button leftSection={<IconCopy size={16} />} loading={busy === 'duplicate'} onClick={duplicate}>Duplicate</Button>
          </Group>
        </Stack>
      </Modal>
      <AdjustModal
        itemId={id}
        photo={adjusting}
        count={item.photos.length}
        onClose={() => setAdjusting(null)}
        onSave={async (adjust, all) => {
          refresh(await api<Item>(`/api/items/${id}/photos/${adjusting!.seq}/adjust`, { method: 'PUT', body: { ...adjust, all } }));
        }}
      />

      <CropModal
        photo={cropping}
        aspect={info ? info.photo_aspect : 1}
        onClose={() => setCropping(null)}
        onSave={async (crop: Crop) => {
          refresh(await api<Item>(`/api/items/${id}/photos/${cropping!.seq}/crop`, { method: 'PUT', body: crop }));
        }}
        onReset={async () => {
          refresh(await api<Item>(`/api/items/${id}/photos/${cropping!.seq}/crop`, { method: 'DELETE' }));
        }}
      />

      <Modal opened={!!confirm} onClose={() => setConfirm(null)} centered
        title={{ publish: info?.dry_run ? 'Dry run' : info?.auto_commit ? 'Publish to Tradera' : 'Send to Tradera', golive: 'Go live on Tradera',
          delete: 'Delete item', rewrite: 'Rewrite with AI' }[confirm ?? 'publish']}>
        <Text size="sm">
          {confirm === 'publish' && (info?.dry_run
            ? 'This builds the Tradera request and saves it next to the photos. Nothing is sent.'
            : info?.auto_commit ? 'The listing goes live on Tradera straight away and people can bid on it.'
            : 'The listing and photos are uploaded to Tradera but not published. Press Go live afterwards to publish it.')}
          {confirm === 'golive' && 'The listing is published on Tradera now and people can bid on it.'}
          {confirm === 'delete' && 'The item and its photos are removed from this list. Files stay on the server.'}
          {confirm === 'rewrite' && 'Claude writes a new title, description and category from the photos. Your current text is replaced.'}
        </Text>
        <Group justify="flex-end" mt="lg">
          <Button variant="default" onClick={() => setConfirm(null)}>Cancel</Button>
          <Button color={confirm === 'delete' ? 'red' : confirm === 'publish' || confirm === 'golive' ? 'green' : undefined}
            loading={!!busy} onClick={{ publish, golive: goLive, delete: remove, rewrite }[confirm ?? 'publish']}>
            {confirm === 'publish' ? (info?.dry_run ? 'Run dry run' : info?.auto_commit ? 'Publish now' : 'Send') : confirm === 'golive' ? 'Go live now'
              : confirm === 'delete' ? 'Delete' : 'Rewrite'}
          </Button>
        </Group>
      </Modal>
    </Stack>
  );
}
