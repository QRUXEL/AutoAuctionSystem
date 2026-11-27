import { AspectRatio, Card, Center, Group, Image, Loader, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { IconPhoto } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { api, type Item, ref } from '../api';
import { StatusBadge } from '../components/StatusBadge';

export interface View { key: string; label: string; statuses: string[]; empty: string }

export const VIEWS: View[] = [
  { key: 'review', label: 'To review', statuses: ['review'], empty: 'Nothing waiting for review.' },
  { key: 'incoming', label: 'Incoming', statuses: ['capturing', 'processing'], empty: 'No item is being photographed or processed.' },
  { key: 'sent', label: 'Waiting to go live', statuses: ['publishing', 'sent'], empty: 'Nothing is waiting on Tradera.' },
  { key: 'published', label: 'Published', statuses: ['published'], empty: 'Nothing published yet.' },
  { key: 'problems', label: 'Problems', statuses: ['error'], empty: 'No problems.' },
  { key: 'all', label: 'All items', statuses: [], empty: 'No items yet. Press the button at the photo table.' },
];

export function ItemsPage({ view }: { view: View }) {
  const { data, isLoading } = useQuery({
    queryKey: ['items', view.key],
    queryFn: () => api<Item[]>(`/api/items?status=${view.statuses.join(',')}`),
    refetchInterval: 4000,
  });

  return (
    <Stack>
      <Title order={2}>{view.label}</Title>
      {isLoading && <Center h={200}><Loader /></Center>}
      {data && data.length === 0 && <Text c="dimmed">{view.empty}</Text>}
      <SimpleGrid cols={{ base: 2, sm: 3, md: 4, xl: 6 }} spacing="md">
        {data?.map((item) => {
          const cover = item.photos[0]?.framed;
          return (
            <Card key={item.id} component={Link} to={`/items/${item.id}`} padding="xs" withBorder>
              <Card.Section>
                <AspectRatio ratio={1}>
                  {cover ? <Center bg="dark.8"><Image src={cover} fit="contain" h="100%" w="100%" alt="" /></Center> : <Center bg="dark.6"><IconPhoto size={40} opacity={0.3} /></Center>}
                </AspectRatio>
              </Card.Section>
              <Group justify="space-between" mt="xs" gap={4} wrap="nowrap">
                <Text size="xs" c="dimmed" ff="monospace">{ref(item.id)}</Text>
                <StatusBadge status={item.status} />
              </Group>
              <Text size="sm" fw={500} lineClamp={2} mt={4} c={item.title ? undefined : 'dimmed'} fs={item.title ? undefined : 'italic'}>
                {item.title || (item.status === 'processing' ? 'Writing description…' : 'No title yet')}
              </Text>
            </Card>
          );
        })}
      </SimpleGrid>
    </Stack>
  );
}
