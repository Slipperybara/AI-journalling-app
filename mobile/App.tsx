import './global.css';
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { ActivityIndicator, View } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { StatusBar } from 'expo-status-bar';
import { useFonts } from 'expo-font';
import {
  Lora_400Regular,
  Lora_500Medium,
  Lora_600SemiBold,
  Lora_400Regular_Italic,
} from '@expo-google-fonts/lora';
import { DMSans_300Light, DMSans_400Regular, DMSans_500Medium } from '@expo-google-fonts/dm-sans';
import { KeyboardProvider } from 'react-native-keyboard-controller';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AuthProvider, useAuth } from './lib/auth';
import { LoginScreen } from './components/LoginScreen';
import { MainScreen } from './components/MainScreen';
import { Onboarding } from './components/Onboarding';
import { Paywall } from './components/Paywall';
import { PURCHASES_ENABLED, configurePurchases, isEntitled } from './lib/purchases';

const ONBOARDED_KEY = 'jai_onboarded';

function Spinner() {
  return (
    <View className="flex-1 items-center justify-center bg-paper">
      <ActivityIndicator color="#8E8B84" />
    </View>
  );
}

// Hard paywall (free-trial model). No-op when RevenueCat isn't configured —
// isEntitled() fails open, so an unconfigured build never gates anyone.
function RequireSubscription({ userId, children }: { userId: string; children: ReactNode }) {
  const [state, setState] = useState<'loading' | 'ok' | 'paywall'>('loading');

  const check = useCallback(async () => {
    if (!PURCHASES_ENABLED) {
      setState('ok');
      return;
    }
    await configurePurchases(userId);
    setState((await isEntitled()) ? 'ok' : 'paywall');
  }, [userId]);

  useEffect(() => {
    check();
  }, [check]);

  if (state === 'loading') return <Spinner />;
  if (state === 'paywall') return <Paywall onPurchased={() => setState('ok')} />;
  return <>{children}</>;
}

function Root() {
  const { session, loading } = useAuth();
  const [onboarded, setOnboarded] = useState<boolean | null>(null);

  useEffect(() => {
    AsyncStorage.getItem(ONBOARDED_KEY).then((v) => setOnboarded(v === '1'));
  }, []);

  if (loading || onboarded === null) {
    return <Spinner />;
  }
  if (session) {
    return (
      <RequireSubscription userId={session.user.id}>
        <MainScreen />
      </RequireSubscription>
    );
  }
  if (!onboarded) {
    return (
      <Onboarding
        onDone={() => {
          AsyncStorage.setItem(ONBOARDED_KEY, '1');
          setOnboarded(true);
        }}
      />
    );
  }
  return <LoginScreen />;
}

export default function App() {
  const [fontsLoaded] = useFonts({
    Lora_400Regular,
    Lora_500Medium,
    Lora_600SemiBold,
    Lora_400Regular_Italic,
    DMSans_300Light,
    DMSans_400Regular,
    DMSans_500Medium,
  });

  if (!fontsLoaded) {
    return (
      <View className="flex-1 items-center justify-center bg-paper">
        <ActivityIndicator color="#8E8B84" />
      </View>
    );
  }

  return (
    <KeyboardProvider>
      <SafeAreaProvider>
        <AuthProvider>
          <Root />
          <StatusBar style="dark" />
        </AuthProvider>
      </SafeAreaProvider>
    </KeyboardProvider>
  );
}
