import {
  ActionIcon, Alert, Anchor, Autocomplete, Badge, Button, Code, CopyButton, Group, Loader, Center, NumberInput,
  Paper, PasswordInput, SegmentedControl, Select, SimpleGrid, Stack, Switch, Text, Textarea, TextInput, Title, Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconCopy, IconLogin2, IconPlugConnected, IconX } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { type ReactNode, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

const LOGIN_RESULTS: Record<string, { color: string; text: string }> = {
  connected: { color: 'green', text: 'Tradera account connected. The user ID and token are saved.' },
  rejected: { color: 'yellow', text: 'The Tradera login was cancelled, so nothing changed.' },
  missing: { color: 'red', text: 'Save the app ID, app key and public key first, then connect.' },
  expired: { color: 'red', text: 'The login took too long. Press Connect Tradera account again.' },
  nouser: { color: 'red', text: 'Tradera did not send a user ID back. Tick "Display token on return URL" in the Tradera app settings, or enter the user ID here, and connect again.' },
  failed: { color: 'red', text: 'Tradera did not hand over a token.' },
};

import { useInfo } from '../App';
import { CategorySelect } from '../components/CategorySelect';
import { BidderSelect, ShippingPicker } from '../components/ShippingPicker';
import { api, type Check, type Listing, type SecretState, type Settings } from '../api';

type SecretName = keyof Settings['secrets'];
type Draft = { values: Settings['values']; listing: Listing; shipping: string; secrets: Partial<Record<SecretName, string | null>> };

const fromSettings = (s: Settings): Draft => ({
  values: { ...s.values },
  listing: { ...s.listing },
  shipping: JSON.stringify(s.listing.shipping_options ?? [], null, 2),
  secrets: {},
});

export function SettingsPage() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const login = params.get('tradera');
  const info = useInfo().data;
  const { data } = useQuery({ queryKey: ['settings'], queryFn: () => api<Settings>('/api/settings') });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => { if (data && !dirty) setDraft(fromSettings(data)); }, [data, dirty]);
  if (!data || !draft) return <Center h={300}><Loader /></Center>;

  const setValue = <K extends keyof Settings['values']>(k: K, v: Settings['values'][K]) => {
    setDraft({ ...draft, values: { ...draft.values, [k]: v } }); setDirty(true);
  };
  const setListing = <K extends keyof Listing>(k: K, v: Listing[K]) => {
    setDraft({ ...draft, listing: { ...draft.listing, [k]: v } }); setDirty(true);
  };
  const setSecret = (k: SecretName, v: string | null | undefined) => {
    const secrets = { ...draft.secrets };
    if (v === undefined) delete secrets[k]; else secrets[k] = v;
    setDraft({ ...draft, secrets }); setDirty(true);
  };

  let shippingError: string | null = null;
  let shipping: Listing['shipping_options'] = [];
  try {
    shipping = JSON.parse(draft.shipping || '[]');
    if (!Array.isArray(shipping)) shippingError = 'Must be a list: [ {...}, {...} ]';
  } catch { shippingError = 'Not valid JSON'; }

  const save = async () => {
    setSaving(true);
    try {
      const secrets = Object.fromEntries(Object.entries(draft.secrets).filter(([, v]) => v !== ''));
      const fresh = await api<Settings>('/api/settings', {
        method: 'PUT',
        // only what was changed, so untouched fields keep following the defaults and Coolify's variables
        body: {
          values: changed(draft.values, data.values),
          secrets,
          listing: changed({ ...draft.listing, shipping_options: shipping }, data.listing),
        },
      });
      qc.setQueryData(['settings'], fresh);
      qc.invalidateQueries({ queryKey: ['info'] });
      qc.invalidateQueries({ queryKey: ['end-preview'] });
      setDirty(false);
      setDraft(fromSettings(fresh));
      notifications.show({ color: 'green', message: 'Settings saved' });
    } catch (e) {
      notifications.show({ color: 'red', title: 'Not saved', message: (e as Error).message });
    } finally { setSaving(false); }
  };

  return (
    <Stack maw={900} pb={80}>
      <Title order={2}>Settings</Title>
      <Text c="dimmed" size="sm">
        Values saved here take priority over the environment variables in Coolify. Keys are stored on the server and
        only their last four characters are shown again.
      </Text>

      <Section title="Claude (descriptions)" test="claude" dirty={dirty}>
        <SecretField label="API key" state={data.secrets.ANTHROPIC_API_KEY} draft={draft.secrets.ANTHROPIC_API_KEY}
          onChange={(v) => setSecret('ANTHROPIC_API_KEY', v)}
          help={<>Create one at <Anchor href="https://console.anthropic.com/settings/keys" target="_blank" size="xs">console.anthropic.com</Anchor>.</>} />
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <Autocomplete label="Model" data={['claude-sonnet-5-5', 'claude-opus-5-5', 'claude-haiku-5-5']}
            value={draft.values.AI_MODEL} onChange={(v) => setValue('AI_MODEL', v)} />
          <Autocomplete label="Listing language" data={['Swedish', 'English']}
            value={draft.values.LISTING_LANGUAGE} onChange={(v) => setValue('LISTING_LANGUAGE', v)} />
        </SimpleGrid>
        <Textarea label="Extra instructions" autosize minRows={2} placeholder="For example: always mention that the item comes from our charity auction."
          value={draft.values.AI_EXTRA_INSTRUCTIONS} onChange={(e) => setValue('AI_EXTRA_INSTRUCTIONS', e.currentTarget.value)} />
      </Section>

      <Section title="Tradera" test="tradera" dirty={dirty}>
        {login && LOGIN_RESULTS[login] && (
          <Alert color={LOGIN_RESULTS[login].color} variant="light" withCloseButton onClose={() => setParams({})}>
            {LOGIN_RESULTS[login].text}{params.get('reason') ? ` (${params.get('reason')})` : ''}
          </Alert>
        )}
        <Text size="sm" c="dimmed">
          1. Register an application at <Anchor href="https://api.tradera.com/register" target="_blank" size="sm">api.tradera.com</Anchor> with
          these return URLs, and tick “Display token on return URL”:
        </Text>
        <ReturnUrl label="Accept Return URL" path="/tradera/accept" />
        <ReturnUrl label="Reject Return URL" path="/tradera/reject" />
        <Text size="sm" c="dimmed">2. Paste the app ID, app key and public key below and save.</Text>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput label="App ID" value={draft.values.TRADERA_APP_ID} onChange={(e) => setValue('TRADERA_APP_ID', e.currentTarget.value)} />
          <SecretField label="App key" state={data.secrets.TRADERA_APP_KEY} draft={draft.secrets.TRADERA_APP_KEY}
            onChange={(v) => setSecret('TRADERA_APP_KEY', v)} />
          <TextInput label="Public key" description="Shown with the app in Tradera's developer center" value={draft.values.TRADERA_PUBLIC_KEY}
            onChange={(e) => setValue('TRADERA_PUBLIC_KEY', e.currentTarget.value)} />
        </SimpleGrid>
        <Text size="sm" c="dimmed">3. Log in with the selling account. Tradera sends you back here and the token is saved.</Text>
        <Group>
          <Tooltip label="Save first" disabled={!dirty}>
            <Button component="a" href="/api/tradera/connect" leftSection={<IconLogin2 size={16} />} variant="light"
              disabled={dirty || !(data.values.TRADERA_APP_ID && data.secrets.TRADERA_APP_KEY.set && data.values.TRADERA_PUBLIC_KEY)}
              onClick={(e) => { if (dirty) e.preventDefault(); }}>
              {data.secrets.TRADERA_USER_TOKEN.set ? 'Reconnect Tradera account' : 'Connect Tradera account'}
            </Button>
          </Tooltip>
          {data.values.TRADERA_TOKEN_EXPIRES && (
            <Text size="sm" c="dimmed">Token valid until {new Date(data.values.TRADERA_TOKEN_EXPIRES).toLocaleString('sv-SE')}</Text>
          )}
        </Group>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput label="User ID (selling account)" description="Filled in by the login" value={draft.values.TRADERA_USER_ID}
            onChange={(e) => setValue('TRADERA_USER_ID', e.currentTarget.value)} />
          <SecretField label="User token" state={data.secrets.TRADERA_USER_TOKEN} draft={draft.secrets.TRADERA_USER_TOKEN}
            onChange={(v) => setSecret('TRADERA_USER_TOKEN', v)} />
        </SimpleGrid>
        <Switch label="Dry run" description="When on, Send only saves the Tradera request on the server. Turn off to send to Tradera for real."
          checked={draft.values.TRADERA_DRY_RUN} onChange={(e) => setValue('TRADERA_DRY_RUN', e.currentTarget.checked)}
          color="yellow" />
        <Switch label="Auto-publish" color="green"
          description="Off: Send puts the listing on Tradera without publishing it, and it goes live when you press Go live. On: Send publishes it straight away."
          checked={draft.values.TRADERA_AUTO_COMMIT} onChange={(e) => setValue('TRADERA_AUTO_COMMIT', e.currentTarget.checked)} />
        {!draft.values.TRADERA_DRY_RUN && draft.values.TRADERA_AUTO_COMMIT && (
          <Alert color="orange" variant="light">With dry run off and auto-publish on, Send creates live listings that people can bid on.</Alert>
        )}
      </Section>

      <Section title="Video links" test="video">
        {info?.video_site?.configured ? (
          <Stack gap={4}>
            <Text size="sm">Videos are uploaded to <b>{info.video_site.base_url}</b> and listings link there
              ({info.video_site.uploaded} uploaded{info.video_site.failed ? `, ${info.video_site.failed} with errors` : ''}).</Text>
            {info.video_site.warning && <Alert color="red" variant="light">{info.video_site.warning}</Alert>}
            {info.video_site.last_error && <Text size="xs" c="red.4">Last error: {info.video_site.last_error}</Text>}
            <Text size="xs" c="dimmed">Set by AUCTION_VIDEO_BASE_URL and AUCTION_VIDEO_SECRET in Coolify. Press Test to check the address and the secret.</Text>
          </Stack>
        ) : (
          <Text size="sm" c="dimmed">AUCTION_VIDEO_SECRET is not set in Coolify, so videos are linked to this server's own pages below.</Text>
        )}
        <Text size="xs" c="dimmed">The link goes where the description or footer says {'<video link>'}; without that tag, a "Se videon: …" line is added at the end.</Text>
        {!info?.video_site?.configured && (
          <TextInput label="Public address of this site" placeholder="https://aas.qruxel.productions"
            description="Used for this server's own video pages. They need no password."
            value={draft.values.PUBLIC_BASE_URL} onChange={(e) => setValue('PUBLIC_BASE_URL', e.currentTarget.value)} />
        )}
      </Section>

      <Section title="Charity campaign (Musikhjälpen)">
        <CampaignPicker value={draft.listing.campaign_code} onChange={(v) => setListing('campaign_code', v)} traderaReady={!!info?.tradera_ready} />
      </Section>

      <Section title="When auctions end">
        <SegmentedControl data={[{ value: 'duration', label: 'After a number of days' }, { value: 'fixed', label: 'All at one time' },
          { value: 'staggered', label: 'One after another' }]}
          value={draft.listing.end_mode} onChange={(v) => setListing('end_mode', v as Listing['end_mode'])} />
        {draft.listing.end_mode === 'duration' ? (
          <Select label="Runs for" data={['1', '3', '5', '7', '10', '14'].map((d) => ({ value: d, label: `${d} day${d === '1' ? '' : 's'}` }))} w={200}
            value={String(draft.listing.duration_days)} onChange={(v) => v && setListing('duration_days', Number(v))} allowDeselect={false} />
        ) : (
          <Group align="flex-end" grow>
            <TextInput type="datetime-local" label={draft.listing.end_mode === 'fixed' ? 'Every auction ends at' : 'The first auction ends at'}
              value={draft.listing.end_at} onChange={(e) => setListing('end_at', e.currentTarget.value)} description="Swedish time" />
            {draft.listing.end_mode === 'staggered' && (
              <NumberInput label="Then one every" suffix=" min" min={0} max={240} allowDecimal={false} value={draft.listing.end_interval_min}
                description="Each next item sent ends this much after the previous one" onChange={(v) => setListing('end_interval_min', Number(v) || 0)} />
            )}
          </Group>
        )}
        <EndPreview dirty={dirty} />
        <Text size="xs" c="dimmed">An item can also get its own end time on its page. A sent item keeps its end time when it is sent again.</Text>
      </Section>

      <Section title="Shipping and buyers">
        <Text size="sm" fw={500}>How items can be sent (defaults; each item can change them)</Text>
        <ShippingPicker value={draft.listing.shipping_options} onChange={(v) => { setDraft({ ...draft, listing: { ...draft.listing, shipping_options: v }, shipping: JSON.stringify(v) }); setDirty(true); }} />
        <TextInput label="Shipping note" placeholder="For example: Fri frakt inom Sverige, DSV bjuder. Skickas inom 3 dagar."
          description="Shown with the shipping options on Tradera"
          value={draft.listing.shipping_condition} onChange={(e) => setListing('shipping_condition', e.currentTarget.value)}
          rightSectionWidth={150} rightSection={!/dsv/i.test(draft.listing.shipping_condition) ? (
            <Button size="compact-xs" variant="subtle" onClick={() => setListing('shipping_condition',
              `${draft.listing.shipping_condition.trim()} Fri frakt inom Sverige, DSV bjuder.`.trim())}>+ DSV sponsors</Button>) : undefined} />
        <BidderSelect value={draft.listing.accepted_bidder_id} onChange={(v) => setListing('accepted_bidder_id', v ?? 1)} />
      </Section>

      <Section title="Listing defaults">
        <Group align="flex-end" grow>
          <Stack gap={4}>
            <Text size="sm" fw={500}>Type</Text>
            <SegmentedControl data={[{ value: '1', label: 'Auction' }, { value: '3', label: 'Fixed price' }]}
              value={String(draft.listing.item_type)} onChange={(v) => setListing('item_type', Number(v))} />
          </Stack>
          <NumberInput label="Start price" suffix=" kr" min={1} allowDecimal={false} value={draft.listing.start_price}
            onChange={(v) => setListing('start_price', Number(v) || 1)} />
        </Group>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <NumberInput label="Automatic restarts" min={0} max={5} allowDecimal={false} value={draft.listing.restarts}
            onChange={(v) => setListing('restarts', Number(v) || 0)} />
          <NumberInput label="VAT %" min={0} max={25} allowDecimal={false} placeholder="Not set" value={draft.listing.vat ?? ''}
            description="Only for business sellers" onChange={(v) => setListing('vat', v === '' ? null : Number(v))} />
        </SimpleGrid>
        <Textarea label="Text added under every description" autosize minRows={2}
          description="Can hold <video link>; the line is left out for items without a video."
          value={draft.listing.description_footer} onChange={(e) => setListing('description_footer', e.currentTarget.value)} />
      </Section>

      <Section title="Capture">
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <NumberInput label="Photos per item" min={1} max={12} allowDecimal={false} value={draft.values.EXPECTED_PHOTOS}
            onChange={(v) => setValue('EXPECTED_PHOTOS', Number(v) || 4)} />
          <NumberInput label="Videos per item" min={0} max={2} allowDecimal={false} value={draft.values.EXPECTED_VIDEOS}
            onChange={(v) => setValue('EXPECTED_VIDEOS', Number(v) || 0)} />
          <NumberInput label="Wait for missing photos" suffix=" s" min={10} max={600} allowDecimal={false} value={draft.values.GRACE_SECONDS}
            onChange={(v) => setValue('GRACE_SECONDS', Number(v) || 45)} />
        </SimpleGrid>
        <Text size="xs" c="dimmed">These must match the ESP32's sequence (PHOTOS and RECORD_VIDEO in its config).</Text>
      </Section>

      <Paper withBorder p="sm" pos="sticky" bottom={12} style={{ zIndex: 5 }} shadow="md">
        <Group justify="space-between">
          <Text size="sm" c={dirty ? undefined : 'dimmed'}>{dirty ? 'You have unsaved changes' : 'All changes saved'}</Text>
          <Group>
            <Button variant="default" disabled={!dirty || saving} onClick={() => { setDirty(false); setDraft(fromSettings(data)); }}>Discard</Button>
            <Button onClick={save} loading={saving} disabled={!dirty || !!shippingError}>Save settings</Button>
          </Group>
        </Group>
      </Paper>
    </Stack>
  );
}

function Section({ title, test, dirty, children }: { title: string; test?: 'claude' | 'tradera' | 'video'; dirty?: boolean; children: ReactNode }) {
  const [result, setResult] = useState<Record<string, Check | null> | null>(null);
  const [running, setRunning] = useState(false);
  const run = async () => {
    setRunning(true);
    try {
      const r = await api<Check | { app: Check | null; user: Check | null }>(`/api/settings/test/${test}`, { method: 'POST' });
      setResult('ok' in r ? { Claude: r as Check } : { 'App keys': r.app, 'User token': r.user });
    } catch (e) {
      setResult({ Test: { ok: false, message: (e as Error).message } });
    } finally { setRunning(false); }
  };
  return (
    <Paper withBorder p="md">
      <Group justify="space-between" mb="sm">
        <Title order={4}>{title}</Title>
        {test && (
          <Button size="xs" variant="light" leftSection={<IconPlugConnected size={14} />} loading={running} onClick={run}
            title={dirty ? 'Save first: the test uses the saved settings' : undefined}>
            Test connection{dirty ? ' (saved values)' : ''}
          </Button>
        )}
      </Group>
      <Stack gap="sm">
        {children}
        {result && Object.entries(result).map(([name, r]) => r && (
          <Alert key={name} color={r.ok ? 'green' : 'red'} variant="light" icon={r.ok ? <IconCheck size={16} /> : <IconX size={16} />}
            title={name} withCloseButton onClose={() => setResult(null)}>
            <Text size="sm" style={{ wordBreak: 'break-word' }}>{r.message}</Text>
          </Alert>
        ))}
      </Stack>
    </Paper>
  );
}

function SecretField({ label, state, draft, onChange, help }: {
  label: string; state: SecretState; draft: string | null | undefined; onChange: (v: string | null | undefined) => void; help?: ReactNode;
}) {
  const removing = draft === null;
  const status = removing
    ? <Badge color="red" variant="light">Will be removed</Badge>
    : state.set
      ? <Badge color="green" variant="light">{`Saved ${state.hint}`}{state.source === 'env' ? ' · from Coolify' : ''}</Badge>
      : <Badge color="gray" variant="light">Not set</Badge>;
  return (
    <Stack gap={4}>
      <PasswordInput
        label={<Group gap={6}>{label}{status}</Group>}
        placeholder={state.set ? 'Paste a new value to replace it' : 'Paste the value'}
        value={typeof draft === 'string' ? draft : ''}
        onChange={(e) => onChange(e.currentTarget.value || undefined)}
        description={help}
        autoComplete="off"
      />
      {state.set && state.source === 'settings' && !removing && (
        <Anchor size="xs" c="dimmed" onClick={() => onChange(null)} component="button" type="button" style={{ alignSelf: 'flex-start' }}>
          Remove saved value
        </Anchor>
      )}
    </Stack>
  );
}


function ReturnUrl({ label, path }: { label: string; path: string }) {
  const url = window.location.origin + path;
  return (
    <Group gap="xs" wrap="nowrap">
      <Text size="sm" w={150} style={{ flexShrink: 0 }}>{label}</Text>
      <Code style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{url}</Code>
      <CopyButton value={url}>
        {({ copied, copy }) => (
          <Tooltip label={copied ? 'Copied' : 'Copy'}>
            <ActionIcon variant="subtle" color={copied ? 'teal' : 'gray'} onClick={copy}>{copied ? <IconCheck size={16} /> : <IconCopy size={16} />}</ActionIcon>
          </Tooltip>
        )}
      </CopyButton>
    </Group>
  );
}

function changed<T extends object>(draft: T, saved: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(draft).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify((saved as Record<string, unknown>)[k])),
  ) as Partial<T>;
}

interface CampaignOption { code: string; ok: boolean; message: string; description: string }

/** The Tradera campaign code sent with every listing (Musikhjälpen). Tradera's API can't list the codes, so a code is
 *  checked with Tradera when it is entered, and likely codes can be tried. */
function CampaignPicker({ value, onChange, traderaReady }: { value: string; onChange: (v: string) => void; traderaReady: boolean }) {
  const [category, setCategory] = useState<{ id: number | null; path: string }>({ id: null, path: '' });
  const [result, setResult] = useState<CampaignOption | null>(null);
  const [typed, setTyped] = useState(value);
  const [busy, setBusy] = useState(false);
  const [found, setFound] = useState<CampaignOption[] | null>(null);
  const [scanning, setScanning] = useState(false);

  const check = async (code: string, cat = category.id) => {
    setResult(null);
    if (!code || !traderaReady) return;
    setBusy(true);
    try {
      const [r] = await api<CampaignOption[]>('/api/settings/campaign-codes', { method: 'POST', body: { codes: [code], only: true, category_id: cat } });
      setResult(r ?? null);
    } catch (e) { setResult({ code, ok: false, message: (e as Error).message, description: '' }); } finally { setBusy(false); }
  };
  const use = (code: string) => { setTyped(code); onChange(code); void check(code); };
  const scan = async () => {
    setScanning(true);
    try {
      setFound(await api<CampaignOption[]>('/api/settings/campaign-codes', { method: 'POST', body: { codes: [], category_id: category.id } }));
    } catch (e) { setFound([{ code: '', ok: false, message: (e as Error).message, description: '' }]); } finally { setScanning(false); }
  };

  return (
    <Stack gap="xs">
      <Group align="flex-end" wrap="nowrap">
        <TextInput style={{ flex: 1 }} label="Campaign code" placeholder="Empty: ordinary auctions" value={typed}
          description="Sent with every listing so it counts for the campaign. Get it from Tradera or Radiohjälpen."
          onChange={(e) => setTyped(e.currentTarget.value)} />
        <Button variant="light" loading={busy} disabled={typed.trim() === value && !!result} onClick={() => use(typed.trim())}>
          {typed.trim() ? 'Check and use' : 'Use none'}</Button>
      </Group>
      {!traderaReady && <Text size="xs" c="yellow.5">Connect Tradera to check codes.</Text>}
      {result && (
        <Alert color={result.ok ? 'green' : 'red'} variant="light" icon={result.ok ? <IconCheck size={16} /> : <IconX size={16} />}>
          {result.ok ? `Tradera accepts ${result.code}: ${result.message}` : `${result.code}: ${result.message}.`}
          {!result.ok && ' Listings are not sent while the code is rejected. Clear it to send ordinary auctions.'}
        </Alert>
      )}
      {!result && value && <Text size="xs" c="dimmed">Saved code: {value}. Press Check and use to ask Tradera about it.</Text>}
      <Group gap="xs" align="center">
        <Button size="xs" variant="subtle" loading={scanning} disabled={!traderaReady} onClick={scan}>Try likely Musikhjälpen codes</Button>
        {found && !found.some((f) => f.ok) && <Text size="xs" c="yellow.5">Tradera accepted none of: {found.map((f) => f.code).join(', ')}</Text>}
      </Group>
      {found?.filter((f) => f.ok).map((f) => (
        <Group key={f.code} gap="xs">
          <Badge color="green" variant="light">{f.code}</Badge>
          <Text size="sm">{f.description || f.message}</Text>
          <Button size="compact-xs" onClick={() => { use(f.code); setResult(f); }}>Use</Button>
        </Group>
      ))}
      <Group align="flex-end" wrap="nowrap">
        <div style={{ flex: 1 }}>
          <Text size="sm" fw={500} mb={4}>Check against category (optional)</Text>
          <CategorySelect value={category} traderaReady={traderaReady} onChange={(v) => { setCategory(v); if (value) void check(value, v.id); }} />
        </div>
      </Group>
      <Text size="xs" c="dimmed">
        Tradera may limit a campaign to some product categories. Without a category, the check uses the one your items use most.
        The charity numbers on tradera.com (e.g. Musikhjälpen 3164) are not campaign codes. Musikhjälpen used MH 20 (private) and MHF 20 (companies) in 2020, so the likely codes include MH 26 and MHF 26.
      </Text>
    </Stack>
  );
}

/** When the next item sent would end, with the saved settings. */
function EndPreview({ dirty }: { dirty: boolean }) {
  const { data } = useQuery({ queryKey: ['end-preview'], queryFn: () => api<{ at: string | null; text: string; how: string }>('/api/settings/end-preview') });
  if (!data) return null;
  return (
    <Text size="sm" c={data.at ? 'teal.4' : 'dimmed'}>
      {data.at ? `The next item sent ends ${data.text} (${data.how}).` : `Items run for the chosen number of days.`}
      {dirty && ' Save to update.'}
    </Text>
  );
}
