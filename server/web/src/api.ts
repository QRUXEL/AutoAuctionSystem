export type Status = 'capturing' | 'processing' | 'review' | 'publishing' | 'sent' | 'published' | 'error';

export interface Crop { x: number; y: number; w: number; h: number }

export interface Photo {
  id: number;
  seq: number;
  framed: string | null;
  raw: string;
  preview: string;
  crop: Crop | null;
  manual_crop: boolean;
  adjust: { brightness: number; contrast: number };
  name: string;
}

export interface Item {
  id: number;
  status: Status;
  source: 'station' | 'manual' | 'session';
  created_at: number;
  title: string;
  description: string;
  category_id: number | null;
  category_path: string;
  start_price: number | null;
  buy_now_price: number | null;
  duration_days: number | null;
  ai_notes: string;
  message: string;
  tradera_item_id: number | null;
  tradera_pending_item_id: number | null;
  sent_at: number | null;
  manual_order: number;
  end_at: string | null;
  listing_end_at: number | null;
  end_preview: { at: string | null; text: string; how: string };
  shipping_override: string;
  shipping_options: Listing['shipping_options'];
  accepted_bidder_override: number | null;
  changed_since_sent: boolean;
  published_at: number | null;
  expected_photos: number;
  photos: Photo[];
  video_host: { url: string | null; video: boolean; poster: boolean; tradera_linked: boolean; error: string | null; blocked: boolean } | null;
  videos: { seq: number; url: string; name: string; public_url: string | null; public_path: string | null; preview: string | null; web_status: '' | 'queued' | 'working' | 'ready' | 'failed' }[];
}

export interface Info {
  tradera_ready: boolean;
  dry_run: boolean;
  auto_commit: boolean;
  ai_ready: boolean;
  photo_aspect: number | null;
  counts: Record<Status, number>;
  loose: number;
  camera_offset: number | null;
  campaign_code: string;
  accepted_bidder_id: number;
  video_host: string | null;
  video_site: { configured: boolean; base_url: string; warning: string; uploaded: number; failed: number; last_error: string | null };
}

export interface SecretState { set: boolean; hint: string; source: 'settings' | 'env' | 'none' }

export interface Listing {
  item_type: number;
  duration_days: number;
  start_price: number;
  restarts: number;
  accepted_bidder_id: number;
  vat: number | null;
  shipping_condition: string;
  shipping_options: { shippingProviderId?: number; shippingProductId?: number | null; cost?: number; shippingWeight?: number | null; pickup?: boolean; place?: string }[];
  description_footer: string;
  campaign_code: string;
  end_mode: 'duration' | 'fixed' | 'staggered';
  end_at: string;
  end_interval_min: number;
}

export interface Settings {
  values: {
    AI_MODEL: string;
    LISTING_LANGUAGE: string;
    AI_EXTRA_INSTRUCTIONS: string;
    TRADERA_APP_ID: string;
    TRADERA_USER_ID: string;
    TRADERA_PUBLIC_KEY: string;
    TRADERA_TOKEN_EXPIRES: string;
    PUBLIC_BASE_URL: string;
    TRADERA_DRY_RUN: boolean;
    TRADERA_AUTO_COMMIT: boolean;
    EXPECTED_PHOTOS: number;
    EXPECTED_VIDEOS: number;
    GRACE_SECONDS: number;
  };
  secrets: Record<'ANTHROPIC_API_KEY' | 'TRADERA_APP_KEY' | 'TRADERA_USER_TOKEN', SecretState>;
  listing: Listing;
}

export interface Check { ok: boolean; message: string }

export class ApiError extends Error {}

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const r = await fetch(path, {
    method: init.method ?? 'GET',
    headers: init.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError((data as { detail?: string; error?: string }).detail ?? (data as { error?: string }).error ?? r.statusText);
  return data as T;
}

export const ref = (id: number) => `#${String(id).padStart(4, '0')}`;

export interface LooseFile {
  id: number;
  kind: 'photo' | 'video';
  name: string;
  shot_at: number | null;
  received_at: number;
  url: string;
  thumb: string | null;
}

export const clock = (t: number | null | undefined) =>
  t ? new Date(t * 1000).toLocaleString('sv-SE', { dateStyle: 'short', timeStyle: 'medium' }) : '–';
