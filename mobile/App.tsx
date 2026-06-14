import './global.css';
import { useEffect, useState } from 'react';
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

const ONBOARDED_KEY = 'jai_onboarded';

function Root() {
  const { session, loading } = useAuth();
  const [onboarded, setOnboarded] = useState<boolean | null>(null);

  useEffect(() => {
    AsyncStorage.getItem(ONBOARDED_KEY).then((v) => setOnboarded(v === '1'));
  }, []);

  if (loading || onboarded === null) {
    return (
      <View className="flex-1 items-center justify-center bg-paper">
        <ActivityIndicator color="#8E8B84" />
      </View>
    );
  }
  if (session) return <MainScreen />;
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
