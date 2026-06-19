import * as React from 'react';
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import * as AppleAuthentication from 'expo-apple-authentication';
import { makeRedirectUri } from 'expo-auth-session';
import * as WebBrowser from 'expo-web-browser';
import type { Session } from '@supabase/supabase-js';

import { supabase } from './supabase';

// Finishes any auth session that was pending when the app was backgrounded.
WebBrowser.maybeCompleteAuthSession();

// Native deep link the OAuth provider redirects back to. Must be added to the
// Supabase redirect allowlist (Authentication → URL Configuration).
const redirectTo = makeRedirectUri({ scheme: 'mindforge', path: 'auth-callback' });

type AuthResult = { error?: string };

type AuthContextValue = {
  session: Session | null;
  loading: boolean;
  signInWithGoogle: () => Promise<AuthResult>;
  signInWithApple: () => Promise<AuthResult>;
  signInWithEmail: (email: string, password: string) => Promise<AuthResult>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

async function exchangeCodeFromUrl(url: string): Promise<AuthResult> {
  // PKCE: the provider returns ?code=... — exchange it for a Supabase session.
  const parsed = new URL(url);
  const errorDesc = parsed.searchParams.get('error_description');
  if (errorDesc) return { error: errorDesc };
  const code = parsed.searchParams.get('code');
  if (!code) return { error: 'No authorization code returned' };
  const { error } = await supabase.auth.exchangeCodeForSession(code);
  return error ? { error: error.message } : {};
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => setSession(s));
    return () => sub.subscription.unsubscribe();
  }, []);

  const signInWithGoogle = useCallback(async (): Promise<AuthResult> => {
    const { data, error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo, skipBrowserRedirect: true },
    });
    if (error) return { error: error.message };
    if (!data?.url) return { error: 'Could not start Google sign-in' };

    const result = await WebBrowser.openAuthSessionAsync(data.url, redirectTo);
    if (result.type === 'success' && result.url) return exchangeCodeFromUrl(result.url);
    if (result.type === 'cancel' || result.type === 'dismiss') return { error: 'cancelled' };
    return { error: 'Google sign-in did not complete' };
  }, []);

  const signInWithApple = useCallback(async (): Promise<AuthResult> => {
    try {
      const credential = await AppleAuthentication.signInAsync({
        requestedScopes: [
          AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
          AppleAuthentication.AppleAuthenticationScope.EMAIL,
        ],
      });
      if (!credential.identityToken) return { error: 'No Apple identity token' };
      const { error } = await supabase.auth.signInWithIdToken({
        provider: 'apple',
        token: credential.identityToken,
      });
      return error ? { error: error.message } : {};
    } catch (e) {
      const err = e as { code?: string; message?: string };
      if (err?.code === 'ERR_REQUEST_CANCELED') return { error: 'cancelled' };
      return { error: err?.message ?? 'Apple sign-in failed' };
    }
  }, []);

  // Email/password sign-in. Primarily for the App Review demo account (OAuth is
  // awkward for Apple's reviewers), but a valid path for any user. Sign-up is
  // intentionally not exposed in the app — the demo account is provisioned in
  // Supabase directly.
  const signInWithEmail = useCallback(async (email: string, password: string): Promise<AuthResult> => {
    const { error } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    });
    return error ? { error: error.message } : {};
  }, []);

  const signOut = useCallback(async () => {
    await supabase.auth.signOut();
  }, []);

  return (
    <AuthContext.Provider
      value={{ session, loading, signInWithGoogle, signInWithApple, signInWithEmail, signOut }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
