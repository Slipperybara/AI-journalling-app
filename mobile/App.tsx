import './global.css';
import { ActivityIndicator, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { KeyboardProvider } from 'react-native-keyboard-controller';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AuthProvider, useAuth } from './lib/auth';
import { LoginScreen } from './components/LoginScreen';
import { MainScreen } from './components/MainScreen';

function Root() {
  const { session, loading } = useAuth();
  if (loading) {
    return (
      <View className="flex-1 items-center justify-center bg-paper">
        <ActivityIndicator color="#8E8B84" />
      </View>
    );
  }
  return session ? <MainScreen /> : <LoginScreen />;
}

export default function App() {
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
