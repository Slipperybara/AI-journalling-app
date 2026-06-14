import { useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as AppleAuthentication from 'expo-apple-authentication';

import { useAuth } from '../lib/auth';
import { SUPABASE_CONFIGURED } from '../lib/supabase';
import { fonts } from '../lib/theme';
import { GoogleLogo } from './GoogleLogo';

export function LoginScreen() {
  const { signInWithGoogle, signInWithApple } = useAuth();
  const [busy, setBusy] = useState<null | 'google' | 'apple'>(null);
  const [appleAvailable, setAppleAvailable] = useState(false);

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

  return (
    <SafeAreaView className="flex-1 bg-paper">
      <View className="flex-1 items-center justify-center px-8">
        <View className="mb-12 items-center">
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
            style={{ height: 48, borderRadius: 16, backgroundColor: '#fff', borderWidth: 1, borderColor: '#DADCE0' }}
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
              cornerRadius={16}
              style={{ height: 48, width: '100%' }}
              onPress={() => run('apple', signInWithApple)}
            />
          )}
        </View>
      </View>
    </SafeAreaView>
  );
}
