import PostHog from 'posthog-react-native';

// Client-side PostHog, gated behind the key (mirrors the server pattern). When
// EXPO_PUBLIC_POSTHOG_KEY is unset, every call is a no-op. Every event carries
// an `environment` super-property so staging traffic can be filtered out.
const KEY = process.env.EXPO_PUBLIC_POSTHOG_KEY ?? '';
const HOST = process.env.EXPO_PUBLIC_POSTHOG_HOST ?? 'https://us.i.posthog.com';
const APP_ENV = process.env.EXPO_PUBLIC_APP_ENV ?? 'production';

export const ANALYTICS_ENABLED = Boolean(KEY);

let client: PostHog | null = null;
if (ANALYTICS_ENABLED) {
  try {
    client = new PostHog(KEY, { host: HOST });
    client.register({ environment: APP_ENV });
  } catch {
    client = null;
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function track(event: string, properties?: Record<string, any>): void {
  try {
    client?.capture(event, properties);
  } catch {
    // analytics must never break a flow
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function identify(distinctId: string, properties?: Record<string, any>): void {
  try {
    client?.identify(distinctId, properties);
  } catch {
    // ignore
  }
}
