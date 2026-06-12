import './global.css';
import { StatusBar } from 'expo-status-bar';
import { Text, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';

export default function App() {
  return (
    <SafeAreaProvider>
      <SafeAreaView className="flex-1 bg-paper">
        <View className="flex-1 items-center justify-center px-8">
          <Text className="text-3xl font-semibold text-ink">MindForge</Text>
          <Text className="mt-3 text-center text-base leading-6 text-muted">
            Your warm journaling companion. The native foundation is up — auth,
            chat, and the dashboard come next.
          </Text>
        </View>
        <StatusBar style="dark" />
      </SafeAreaView>
    </SafeAreaProvider>
  );
}
