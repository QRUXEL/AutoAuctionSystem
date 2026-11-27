import { Badge, Collapse, Group, Loader, MultiSelect, NumberInput, Select, Stack, Text, UnstyledButton } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconChevronDown, IconChevronRight } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';

import { api } from '../api';

export interface AttributeDef {
  id: number; name: string; key: string; description: string; type: 'term' | 'number';
  min: number; max: number; values: string[]; formatting: string; condition: boolean;
}
export type Attributes = Record<string, string[]>;

/** The category's attributes from Tradera: Skick first, then the ones the category requires, the rest under "More details". */
export function AttributeFields({ categoryId, value, onChange, disabled }: {
  categoryId: number | null; value: Attributes; onChange: (v: Attributes) => void; disabled?: boolean;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['attributes', categoryId], enabled: !!categoryId, staleTime: 3600_000, retry: false,
    queryFn: () => api<AttributeDef[]>(`/api/categories/${categoryId}/attributes`),
  });
  const [more, { toggle }] = useDisclosure(false);

  if (!categoryId) return <Text size="xs" c="dimmed">Choose a category to set the condition (Skick).</Text>;
  if (isLoading) return <Group gap="xs"><Loader size="xs" /><Text size="xs" c="dimmed">Fetching the category's fields…</Text></Group>;
  if (error) return <Text size="xs" c="yellow.5">{(error as Error).message}</Text>;
  if (!data?.length) return null;

  const set = (d: AttributeDef, values: string[]) => {
    const next = { ...value };
    if (values.length) next[String(d.id)] = values; else delete next[String(d.id)];
    onChange(next);
  };
  const field = (d: AttributeDef) => {
    const v = value[String(d.id)] ?? [];
    const label = (
      <Group gap={6} component="span">
        {d.name}
        {d.min > 0 && <Badge size="xs" color={v.length ? 'gray' : 'red'} variant="light">required</Badge>}
      </Group>
    );
    if (d.type === 'number') {
      return (
        <NumberInput key={d.id} label={label} description={d.description || undefined} disabled={disabled}
          suffix={d.formatting ? ` ${d.formatting}` : undefined} value={v[0] ?? ''}
          onChange={(n) => set(d, n === '' ? [] : [String(n)])} />
      );
    }
    if (d.max > 1) {
      return (
        <MultiSelect key={d.id} label={label} description={d.description || `Up to ${d.max}`} data={d.values} value={v} searchable
          disabled={disabled} maxValues={d.max} clearable onChange={(vals) => set(d, vals)} />
      );
    }
    return (
      <Select key={d.id} label={label} description={d.description || undefined} data={d.values} value={v[0] ?? null} searchable clearable
        disabled={disabled} placeholder={d.condition ? 'Choose the condition' : undefined} onChange={(x) => set(d, x ? [x] : [])}
        error={d.min > 0 && !v.length ? `${d.name} is needed before sending` : undefined} />
    );
  };

  const main = data.filter((d) => d.condition || d.min > 0);
  const rest = data.filter((d) => !d.condition && d.min === 0);
  const restSet = rest.filter((d) => value[String(d.id)]?.length).length;

  return (
    <Stack gap="xs">
      {main.map(field)}
      {rest.length > 0 && (
        <>
          <UnstyledButton onClick={toggle}>
            <Group gap={4}>
              {more ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
              <Text size="sm">More details for this category ({rest.length}{restSet ? `, ${restSet} set` : ''})</Text>
            </Group>
          </UnstyledButton>
          <Collapse expanded={more}><Stack gap="xs">{rest.map(field)}</Stack></Collapse>
        </>
      )}
    </Stack>
  );
}
