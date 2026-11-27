import { Loader, Select } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api } from '../api';

interface Props {
  value: { id: number | null; path: string };
  onChange: (v: { id: number | null; path: string }) => void;
  disabled?: boolean;
  traderaReady: boolean;
}

// Searches Tradera's category tree on the server; the list is filtered there, not here.
export function CategorySelect({ value, onChange, disabled, traderaReady }: Props) {
  const [search, setSearch] = useState('');
  const [debounced] = useDebouncedValue(search, 250);
  const typing = debounced.trim() && debounced !== value.path;
  const { data, isFetching } = useQuery({
    queryKey: ['categories', debounced],
    queryFn: () => api<{ id: number; path: string }[]>(`/api/categories?q=${encodeURIComponent(debounced)}`),
    enabled: !!typing,
  });
  const options = [
    ...(value.id ? [{ value: String(value.id), label: value.path }] : []),
    ...(typing ? (data ?? []).filter((c) => c.id !== value.id).map((c) => ({ value: String(c.id), label: c.path })) : []),
  ];

  return (
    <Select
      label="Category"
      placeholder="Search Tradera categories"
      searchable
      clearable
      disabled={disabled}
      data={options}
      value={value.id ? String(value.id) : null}
      searchValue={search}
      onSearchChange={setSearch}
      filter={({ options: o }) => o}
      onChange={(id, option) => onChange({ id: id ? Number(id) : null, path: option?.label ?? '' })}
      rightSection={isFetching ? <Loader size="xs" /> : undefined}
      nothingFoundMessage={
        !traderaReady && !value.id ? 'Add the Tradera app keys in Settings to search categories'
          : typing && !isFetching ? 'No matching category' : 'Type to search'
      }
      comboboxProps={{ withinPortal: true }}
    />
  );
}
