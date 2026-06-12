import { useState } from 'react';
import { View } from 'react-native';

import { ChatScreen } from './ChatScreen';
import { DashboardScreen } from './DashboardScreen';
import { TopBar, type MainView } from './TopBar';

export function MainScreen() {
  const [view, setView] = useState<MainView>('chat');
  return (
    <View className="flex-1 bg-paper">
      <TopBar view={view} onChange={setView} />
      <View className="flex-1">{view === 'chat' ? <ChatScreen /> : <DashboardScreen />}</View>
    </View>
  );
}
