import { Alert, Button, Checkbox, Group, JsonInput, Loader, NumberInput, Select, Stack, Table, Text, TextInput } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { api } from '../api';

/** A Tradera shipping option, or {pickup: true, place} for "buyer collects from us". */
export interface ShippingOption {
  shippingProviderId?: number; shippingProductId?: number | null; cost?: number; shippingWeight?: number | null;
  pickup?: boolean; place?: string;
}
interface Product { key: string; providerId: number; provider: string; productId: number; name: string; weight: number | null; price: number | null; toCountry: string }

const same = (o: ShippingOption, p: Product) => !o.pickup && o.shippingProviderId === p.providerId && (o.shippingProductId ?? null) === p.productId;
const DSV = /dsv|schenker/i;

/** "Buyer collects from us": not a Tradera product, so it is its own row; the place goes into the listing's shipping note. */
function PickupRow({ value, onChange, disabled }: { value: ShippingOption[]; onChange: (v: ShippingOption[]) => void; disabled?: boolean }) {
  const { data } = useQuery({ queryKey: ['pickup'], queryFn: () => api<{ provider_id: number | null }>('/api/tradera/pickup'), staleTime: 3600_000 });
  const pickup = value.find((o) => o.pickup);
  return (
    <Stack gap={2}>
      <Group gap="sm" wrap="nowrap" align="center">
        <Checkbox label={<Text size="sm"><b>Buyer collects</b> from us (free)</Text>} checked={!!pickup} disabled={disabled}
          onChange={(e) => onChange(e.currentTarget.checked ? [{ pickup: true, place: '' }, ...value] : value.filter((o) => !o.pickup))} />
        {pickup && (
          <TextInput size="xs" placeholder="Where, e.g. Stockholm, Södermalm" style={{ flex: 1 }} disabled={disabled} value={pickup.place ?? ''}
            onChange={(e) => { const place = e.currentTarget.value; onChange(value.map((o) => (o.pickup ? { ...o, place } : o))); }} />
        )}
      </Group>
      {pickup && (
        <Text size="xs" c="dimmed">
          {data?.provider_id != null ? 'Sent to Tradera as its pickup option, ' : 'Tradera’s lists have no pickup option for this account, so it is written '}
          {data?.provider_id != null ? 'and ' : ''}in the shipping note as “Kan hämtas hos oss{pickup.place ? ` i ${pickup.place}` : ''}.”
        </Text>
      )}
    </Stack>
  );
}

/** Tradera's own shipping products (fetched and cached by the server), as checkboxes with the buyer's price. */
export function ShippingPicker({ value, onChange, disabled }: { value: ShippingOption[]; onChange: (v: ShippingOption[]) => void; disabled?: boolean }) {
  const { data, error, isLoading } = useQuery({
    queryKey: ['shipping-options'], queryFn: () => api<Product[]>('/api/tradera/shipping-options'), staleTime: 3600_000, retry: false,
  });

  if (isLoading) return <Group gap="xs"><Loader size="xs" /><Text size="sm" c="dimmed">Fetching Tradera's shipping options…</Text></Group>;
  if (error || !data) {
    return (
      <Stack gap="xs">
        <Alert color="yellow" variant="light">{(error as Error)?.message || 'No shipping list'}. You can still enter the options by hand.</Alert>
        <JsonInput autosize minRows={2} formatOnBlur disabled={disabled} value={JSON.stringify(value, null, 2)}
          onChange={(v) => { try { const parsed = JSON.parse(v); if (Array.isArray(parsed)) onChange(parsed); } catch { /* typing */ } }} />
      </Stack>
    );
  }

  const dsv = data.filter((p) => DSV.test(p.provider) && (!p.toCountry || p.toCountry === 'SE'));
  const freeDsv = () => onChange([
    ...value.filter((o) => o.pickup || !dsv.some((p) => same(o, p))),
    ...dsv.map((p) => ({ shippingProviderId: p.providerId, shippingProductId: p.productId, cost: 0, shippingWeight: p.weight })),
  ]);
  const toggle = (p: Product, on: boolean) => onChange(on
    ? [...value, { shippingProviderId: p.providerId, shippingProductId: p.productId, cost: p.price ?? 0, shippingWeight: p.weight }]
    : value.filter((o) => !same(o, p)));
  const setCost = (p: Product, cost: number) => onChange(value.map((o) => (same(o, p) ? { ...o, cost } : o)));
  const unknown = value.filter((o) => !o.pickup && !data.some((p) => same(o, p)));
  const paid = value.filter((o) => !o.pickup && (o.cost ?? 0) > 0);

  return (
    <Stack gap={4}>
      <PickupRow value={value} onChange={onChange} disabled={disabled} />
      {!disabled && (
        <Group gap="xs">
          <Button size="compact-xs" variant="light" color="teal" disabled={!dsv.length} onClick={freeDsv}
            title={dsv.length ? 'Chooses every DSV option within Sweden at 0 kr' : 'Tradera’s list has no DSV option for this account'}>
            Free with DSV in Sweden
          </Button>
          <Button size="compact-xs" variant="subtle" color="gray" disabled={!paid.length}
            onClick={() => onChange(value.map((o) => (o.pickup ? o : { ...o, cost: 0 })))}>Make all free</Button>
          {!dsv.length && <Text size="xs" c="yellow.5">No DSV option in Tradera’s list: pick another carrier and set 0 kr.</Text>}
        </Group>
      )}
      <Table verticalSpacing={4} withRowBorders={false}>
        <Table.Tbody>
          {data.map((p, i) => {
            const chosen = value.find((o) => same(o, p));
            const firstOfProvider = i === 0 || data[i - 1].provider !== p.provider;
            return (
              <Table.Tr key={p.key} style={firstOfProvider && i ? { borderTop: '1px solid var(--mantine-color-dark-4)' } : undefined}>
                <Table.Td w={28}><Checkbox checked={!!chosen} disabled={disabled} onChange={(e) => toggle(p, e.currentTarget.checked)} /></Table.Td>
                <Table.Td><Text size="sm"><b>{p.provider}</b> {p.name}</Text></Table.Td>
                <Table.Td w={70}><Text size="xs" c="dimmed">{p.weight ? `≤ ${p.weight} kg` : ''}</Text></Table.Td>
                <Table.Td w={120}>
                  {chosen
                    ? <NumberInput size="xs" suffix=" kr" min={0} allowDecimal={false} value={chosen.cost ?? 0} disabled={disabled}
                        onChange={(v) => setCost(p, Number(v) || 0)} description={chosen.cost === 0 ? 'Free for the buyer' : undefined} />
                    : <Text size="xs" c="dimmed">{p.price != null ? `${p.price} kr` : ''}</Text>}
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
      {unknown.length > 0 && (
        <Text size="xs" c="yellow.5">
          Also set, but not in Tradera's current list: {unknown.map((o) => `provider ${o.shippingProviderId}${o.shippingProductId ? `/${o.shippingProductId}` : ''} (${o.cost} kr)`).join(', ')}.{' '}
          {!disabled && <Text span size="xs" c="blue.4" style={{ cursor: 'pointer' }} onClick={() => onChange(value.filter((o) => data.some((p) => same(o, p))))}>Remove</Text>}
        </Text>
      )}
      {!value.length && <Text size="xs" c="yellow.5">Nothing chosen: tick at least one way for the buyer to get the item.</Text>}
    </Stack>
  );
}

const FALLBACK_BIDDERS = [{ id: 1, description: 'Sverige' }, { id: 4, description: 'EU' }, { id: 3, description: 'Internationellt' }];

/** Who may bid (Tradera's accepted-bidder types). defaultLabel adds a "use the default" choice (value null). */
export function BidderSelect({ value, onChange, disabled, defaultLabel, label = 'Who may bid' }: {
  value: number | null; onChange: (v: number | null) => void; disabled?: boolean; defaultLabel?: string; label?: string;
}) {
  const { data } = useQuery({ queryKey: ['bidder-types'], queryFn: () => api<{ id: number; description: string }[]>('/api/tradera/bidder-types'), staleTime: 3600_000 });
  const types = data?.length ? data : FALLBACK_BIDDERS;
  const options = [
    ...(defaultLabel ? [{ value: '', label: defaultLabel }] : []),
    ...types.map((t) => ({ value: String(t.id), label: t.description })),
  ];
  return (
    <Select label={label} data={options} value={value == null ? '' : String(value)} disabled={disabled} allowDeselect={false}
      onChange={(v) => onChange(v ? Number(v) : null)} />
  );
}

export const bidderName = (id: number | null | undefined) => FALLBACK_BIDDERS.find((b) => b.id === id)?.description ?? (id ? `type ${id}` : '');
