import { useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as AppleAuthentication from 'expo-apple-authentication';

import { useAuth } from '../lib/auth';
import { SUPABASE_CONFIGURED } from '../lib/supabase';
import { fonts } from '../lib/theme';
import { GoogleLogo } from './GoogleLogo';
import { Mascot } from './Mascot';

export function LoginScreen() {
  const { signInWithGoogle, signInWithApple, signInWithEmail } = useAuth();
  const [busy, setBusy] = useState<null | 'google' | 'apple' | 'email'>(null);
  const [appleAvailable, setAppleAvailable] = useState(false);
  const [emailMode, setEmailMode] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  useEffect(() => {
    AppleAuthentication.isAvailableAsync()
      .then(setAppleAvailable)
      .catch(() => setAppleAvailable(false));
  }, []);

  const run = async (which: 'google' | 'apple', fn: () => Promise<{ error?: string }>) => {
    if (!SUPABASE_CONFIGURED) {
      Alert.alert('Not configured', 'Set EXPO_PUBLIC_SUPABASE_URL and _ANON_KEY in mobile/.env.');
      return;
    }
    setBusy(which);
    const { error } = await fn();
    setBusy(null);
    if (error && error !== 'cancelled') Alert.alert('Sign-in failed', error);
  };

  const runEmail = async () => {
    if (!SUPABASE_CONFIGURED) {
      Alert.alert('Not configured', 'Set EXPO_PUBLIC_SUPABASE_URL and _ANON_KEY in mobile/.env.');
      return;
    }
    if (!email.trim() || !password) {
      Alert.alert('Missing details', 'Enter both your email and password.');
      return;
    }
    setBusy('email');
    const { error } = await signInWithEmail(email, password);
    setBusy(null);
    if (error) Alert.alert('Sign-in failed', error);
  };

  return (
    <SafeAreaView className="flex-1 bg-paper">
      <View className="flex-1 items-center justify-center px-8">
        <View className="mb-12 items-center">
          <Mascot mood="happy" size={128} style={{ marginBottom: 8 }} />
          <Text className="text-4xl font-semibold text-ink">JAI</Text>
          <Text className="mt-3 text-center text-base leading-6 text-muted">
            A warm companion for your day. Sign in to pick up where you left off.
          </Text>
        </View>

        <View className="w-full max-w-sm gap-3">
          <Pressable
            onPress={() => run('google', signInWithGoogle)}
            disabled={busy !== null}
            className="w-full flex-row items-center justify-center active:opacity-80"
            style={{ height: 48, borderRadius: 24, backgroundColor: '#fff', borderWidth: 1, borderColor: '#DADCE0' }}
          >
            {busy === 'google' ? (
              <ActivityIndicator color="#3C4043" />
            ) : (
              <>
                <GoogleLogo size={18} />
                <Text style={{ fontFamily: fonts.sansMedium, fontSize: 16, color: '#3C4043', marginLeft: 10 }}>
                  Continue with Google
                </Text>
              </>
            )}
          </Pressable>

          {appleAvailable && (
            <AppleAuthentication.AppleAuthenticationButton
              buttonType={AppleAuthentication.AppleAuthenticationButtonType.CONTINUE}
              buttonStyle={AppleAuthentication.AppleAuthenticationButtonStyle.BLACK}
              cornerRadius={24}
              style={{ height: 48, width: '100%' }}
              onPress={() => run('apple', signInWithApple)}
            />
          )}

          {emailMode ? (
            <View className="mt-2 gap-3">
              <TextInput
                value={email}
                onChangeText={setEmail}
                placeholder="Email"
                placeholderTextColor="#B4B1A9"
                autoCapitalize="none"
                keyboardType="email-address"
                autoComplete="email"
                style={{
                  height: 48,
                  borderRadius: 14,
                  borderWidth: 1,
                  borderColor: '#DDD8D0',
                  paddingHorizontal: 16,
                  fontFamily: fonts.sans,
                  fontSize: 15,
                  color: '#2A2825',
                  backgroundColor: '#fff',
                }}
              />
              <TextInput
                value={password}
                onChangeText={setPassword}
                placeholder="Password"
                placeholderTextColor="#B4B1A9"
                secureTextEntry
                autoCapitalize="none"
                style={{
                  height: 48,
                  borderRadius: 14,
                  borderWidth: 1,
                  borderColor: '#DDD8D0',
                  paddingHorizontal: 16,
                  fontFamily: fonts.sans,
                  fontSize: 15,
                  color: '#2A2825',
                  backgroundColor: '#fff',
                }}
              />
              <Pressable
                onPress={runEmail}
                disabled={busy !== null}
                className="w-full flex-row items-center justify-center active:opacity-80"
                style={{ height: 48, borderRadius: 24, backgroundColor: '#2A2825' }}
              >
                {busy === 'email' ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={{ fontFamily: fonts.sansMedium, fontSize: 16, color: '#fff' }}>Sign in</Text>
                )}
              </Pressable>
            </View>
          ) : null}

          <Pressable
            onPress={() => setEmailMode((v) => !v)}
            hitSlop={8}
            className="mt-2 items-center"
          >
            <Text style={{ fontFamily: fonts.sans, fontSize: 13, color: '#9A9790' }}>
              {emailMode ? 'Back to social sign-in' : 'Sign in with email'}
            </Text>
          </Pressable>
        </View>
      </View>
    </SafeAreaView>
  );
}
