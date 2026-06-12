import './global.css';
import { ActivityIndicator, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AuthProvider, useAuth } from './lib/auth';
import { LoginScreen } from './components/LoginScreen';
import { HomeScreen } from './components/HomeScreen';

function Root() {
  const { session, loading } = useAuth();
  if (loading) {
    return (
      <View className="flex-1 items-center justify-center bg-paper">
        <ActivityIndicator color="#8E8B84" />
      </View>
    );
  }
  return session ? <HomeScreen /> : <LoginScreen />;
}

export default function App() {
  return (
    <SafeAreaProvider>
      <AuthProvider>
        <Root />
        <StatusBar style="dark" />
      </AuthProvider>
    </SafeAreaProvider>
  );
}
