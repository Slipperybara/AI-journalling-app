import { Pressable, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useAuth } from '../lib/auth';

// Placeholder signed-in screen. Replaced by the chat + dashboard navigator
// in tasks #5–#6.
export function HomeScreen() {
  const { session, signOut } = useAuth();
  return (
    <SafeAreaView className="flex-1 bg-paper">
      <View className="flex-1 items-center justify-center px-8">
        <Text className="text-2xl font-semibold text-ink">You're in.</Text>
        <Text className="mt-2 text-center text-base leading-6 text-muted">
          Signed in as {session?.user?.email ?? 'your account'}. Chat and the
          dashboard are coming next.
        </Text>
        <Pressable
          onPress={signOut}
          className="mt-8 rounded-2xl bg-ink px-5 py-3 active:opacity-80"
        >
          <Text className="font-medium text-white">Sign out</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}
