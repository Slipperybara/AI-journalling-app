import { Feather } from '@expo/vector-icons';
import { Pressable, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { fonts } from '../lib/theme';
import { Mascot } from './Mascot';

// Slim top bar: hamburger (opens the left panel) + today's date + a small duck.
// Navigation (chats, dashboard, sign out) all live in the left panel now.
export function TopBar({ date, onMenu }: { date: string; onMenu: () => void }) {
  return (
    <SafeAreaView edges={['top']}>
      <View className="flex-row items-center justify-between px-4 pb-2 pt-1">
        <Pressable onPress={onMenu} hitSlop={12} style={{ width: 28 }}>
          <Feather name="menu" size={20} color="#6E6B64" />
        </Pressable>
        <Text style={{ fontFamily: fonts.sans, fontSize: 11, letterSpacing: 1.4, color: '#9C998F' }}>{date}</Text>
        <Mascot mood="base" size={34} style={{ width: 28, height: 28 }} />
      </View>
    </SafeAreaView>
  );
}
