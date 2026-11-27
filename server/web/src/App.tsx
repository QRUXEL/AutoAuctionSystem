import { AppShell, Badge, Burger, Group, NavLink, Stack, Text, Title, Tooltip } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconAlertTriangle, IconChecklist, IconCloudUpload, IconInbox, IconLayoutGrid, IconSettings, IconSparkles,
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { api, type Info, type Status } from './api';
import { ItemPage } from './pages/ItemPage';
import { ItemsPage, VIEWS } from './pages/ItemsPage';
import { SettingsPage } from './pages/SettingsPage';

const ICONS = { review: IconChecklist, incoming: IconInbox, published: IconCloudUpload, problems: IconAlertTriangle, all: IconLayoutGrid };

export function useInfo() {
  return useQuery({ queryKey: ['info'], queryFn: () => api<Info>('/api/info'), refetchInterval: 5000 });
}

export function App() {
  const [opened, { toggle, close }] = useDisclosure();
  const { pathname } = useLocation();
  const info = useInfo().data;

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 230, breakpoint: 'sm', collapsed: { mobile: !opened } }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Title order={4}>Auction station</Title>
          </Group>
          {info && (
            <Group gap="xs">
              <Tooltip label={info.ai_ready ? 'Claude writes the descriptions' : 'Add a Claude API key in Settings'}>
                <Badge variant="light" color={info.ai_ready ? 'teal' : 'gray'} leftSection={<IconSparkles size={12} />}>
                  {info.ai_ready ? 'AI on' : 'AI off'}
                </Badge>
              </Tooltip>
              <Tooltip label={info.dry_run ? 'Publishing only saves the request; nothing is sent to Tradera' : 'Publish sends listings live to Tradera'}>
                <Badge variant="filled" color={info.dry_run ? 'yellow' : 'green'}>{info.dry_run ? 'Dry run' : 'Live'}</Badge>
              </Tooltip>
            </Group>
          )}
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        <Stack gap={2}>
          {VIEWS.map((v) => {
            const Icon = ICONS[v.key as keyof typeof ICONS];
            const n = v.statuses.reduce((sum, s) => sum + (info?.counts[s as Status] ?? 0), 0);
            return (
              <NavLink
                key={v.key} component={Link} to={`/${v.key}`} onClick={close}
                active={pathname === `/${v.key}`} label={v.label} leftSection={<Icon size={18} />}
                rightSection={n ? <Badge size="sm" variant={v.key === 'problems' ? 'filled' : 'light'} color={v.key === 'problems' ? 'red' : undefined}>{n}</Badge> : null}
              />
            );
          })}
        </Stack>
        <Stack gap={2} mt="auto">
          <NavLink component={Link} to="/settings" onClick={close} active={pathname === '/settings'} label="Settings" leftSection={<IconSettings size={18} />} />
          <Text size="xs" c="dimmed" px="sm" py={4}>Photos arrive from the camera by themselves.</Text>
        </Stack>
      </AppShell.Navbar>

      <AppShell.Main>
        <Routes>
          <Route path="/" element={<Navigate to="/review" replace />} />
          {VIEWS.map((v) => <Route key={v.key} path={`/${v.key}`} element={<ItemsPage view={v} />} />)}
          <Route path="/items/:id" element={<ItemPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/review" replace />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}
