import { Pressable, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useAuth } from '../lib/auth';
import { fonts } from '../lib/theme';

export type MainView = 'chat' | 'dashboard';

function TabText({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <Pressable onPress={onPress} hitSlop={8}>
      <Text
        style={{
          fontFamily: active ? fonts.sansMedium : fonts.sans,
          fontSize: 15,
          color: active ? '#2A2825' : '#9A9790',
        }}
      >
        {label}
      </Text>
    </Pressable>
  );
}

export function TopBar({ view, onChange }: { view: MainView; onChange: (v: MainView) => void }) {
  const { signOut } = useAuth();
  return (
    <SafeAreaView edges={['top']} className="bg-paper">
      <View className="flex-row items-center justify-between px-5 pb-3 pt-1">
        <View className="flex-row" style={{ gap: 18 }}>
          <TabText label="Chat" active={view === 'chat'} onPress={() => onChange('chat')} />
          <TabText label="Dashboard" active={view === 'dashboard'} onPress={() => onChange('dashboard')} />
        </View>
        <Pressable onPress={signOut} hitSlop={8}>
          <Text style={{ fontFamily: fonts.sans, fontSize: 12, color: '#9A9790' }}>Sign out</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}
