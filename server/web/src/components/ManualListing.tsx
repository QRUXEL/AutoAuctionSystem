import { ActionIcon, Alert, Anchor, Button, CopyButton, Group, Image, Loader, Modal, ScrollArea, SimpleGrid, Stack, Text, TextInput, Textarea, ThemeIcon, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconCopy, IconDownload, IconExternalLink, IconHeartHandshake } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { api, type Item } from '../api';

interface Prepared {
  sell_url: string; charity: string; charity_id: number | null;
  photos: number; photos_zip: string; photo_urls: string[];
  fields: { name: string; value: string; long?: boolean }[];
}

function Copy({ value }: { value: string }) {
  return (
    <CopyButton value={value} timeout={1500}>
      {({ copied, copy }) => (
        <Tooltip label={copied ? 'Copied' : 'Copy'}>
          <ActionIcon variant={copied ? 'filled' : 'light'} color={copied ? 'teal' : 'gray'} onClick={copy}>
            {copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
          </ActionIcon>
        </Tooltip>
      )}
    </CopyButton>
  );
}

const Step = ({ n, title, children }: { n: number; title: string; children: React.ReactNode }) => (
  <Group align="flex-start" wrap="nowrap" gap="sm">
    <ThemeIcon radius="xl" size="md" variant="light">{n}</ThemeIcon>
    <Stack gap={6} style={{ flex: 1, minWidth: 0 }}>
      <Text fw={600}>{title}</Text>
      {children}
    </Stack>
  </Group>
);

/** Lists the item on tradera.com's own sell form, where the charity can be chosen (the API can't), then links it back. */
export function ManualListing({ item, opened, onClose, onDone }: { item: Item; opened: boolean; onClose: () => void; onDone: (i: Item) => void }) {
  const [data, setData] = useState<Prepared | null>(null);
  const [error, setError] = useState('');
  const [link, setLink] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!opened) return;
    setData(null); setError('');
    api<Prepared>(`/api/items/${item.id}/manual-listing`, { method: 'POST' }).then(setData).catch((e) => setError((e as Error).message));
  }, [opened, item.id]);

  const save = async () => {
    setSaving(true);
    try {
      const updated = await api<Item>(`/api/items/${item.id}/manual-listing/link`, { method: 'POST', body: { url: link } });
      notifications.show({ color: 'green', message: 'Linked. Its bids are followed on the Auctions page.' });
      onDone(updated);
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message });
    } finally { setSaving(false); }
  };

  return (
    <Modal opened={opened} onClose={onClose} size="xl" scrollAreaComponent={ScrollArea.Autosize}
      title={<Group gap="xs"><IconHeartHandshake size={20} /><Text fw={600}>List on tradera.com{data?.charity ? ` for ${data.charity}` : ''}</Text></Group>}>
      {error && <Alert color="red" variant="light">{error}</Alert>}
      {!data && !error && <Group gap="xs"><Loader size="sm" /><Text size="sm">Preparing (uploading the video if needed)…</Text></Group>}
      {data && (
        <Stack gap="lg">
          <Text size="sm" c="dimmed">
            Tradera's API can't make a listing that donates to a charity; only its own sell form can. Make the listing there with the
            details below, then paste its link back here so the bids and the video page are followed as usual.
          </Text>

          <Step n={1} title={`Open Tradera's sell form${data.charity ? ` with ${data.charity} chosen` : ''}`}>
            <Group>
              <Button component="a" href={data.sell_url} target="_blank" rightSection={<IconExternalLink size={16} />}>Open sell form</Button>
              {data.charity && <Text size="xs" c="dimmed">Check that “{data.charity}” is selected under donation (Tradera's charity id {data.charity_id}).</Text>}
            </Group>
          </Step>

          <Step n={2} title={`Upload the ${data.photos} photos in this order`}>
            <Group>
              <Button component="a" href={data.photos_zip} variant="light" leftSection={<IconDownload size={16} />}>Download all (zip)</Button>
              <Text size="xs" c="dimmed">01-huvudbild.jpg is the main photo. Tradera takes up to 40.</Text>
            </Group>
            <SimpleGrid cols={{ base: 5, sm: 8 }} spacing={6}>
              {data.photo_urls.map((u, i) => (
                <Anchor key={u} href={u} download={`${String(i + 1).padStart(2, '0')}.jpg`} title={`Download photo ${i + 1}`}>
                  <Image src={u} radius="sm" style={{ aspectRatio: '1' }} fit="cover" alt="" />
                </Anchor>
              ))}
            </SimpleGrid>
          </Step>

          <Step n={3} title="Fill in the form">
            <Stack gap={8}>
              {data.fields.map((f) => (
                <Group key={f.name} align="flex-start" wrap="nowrap" gap="xs">
                  <Text size="sm" w={110} fw={500} style={{ flexShrink: 0 }} pt={6}>{f.name}</Text>
                  {f.long
                    ? <Textarea style={{ flex: 1 }} value={f.value} readOnly autosize minRows={2} maxRows={10} />
                    : <TextInput style={{ flex: 1 }} value={f.value} readOnly />}
                  <Copy value={f.value} />
                </Group>
              ))}
            </Stack>
          </Step>

          <Step n={4} title="Publish on Tradera, then paste the listing's link here">
            <Group align="flex-end" wrap="nowrap">
              <TextInput style={{ flex: 1 }} placeholder="https://www.tradera.com/item/…" value={link} onChange={(e) => setLink(e.currentTarget.value)} />
              <Button onClick={save} loading={saving} disabled={!link.trim()}>Link listing</Button>
            </Group>
            <Text size="xs" c="dimmed">The item then counts as published: the Auctions page follows its bids and the video page links to it.</Text>
          </Step>
        </Stack>
      )}
    </Modal>
  );
}
