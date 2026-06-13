import { useEffect, useState } from 'react';
import { View } from 'react-native';

import { listConversations } from '../lib/chat';
import { ChatScreen } from './ChatScreen';
import { ConversationsDrawer } from './ConversationsDrawer';
import { DashboardScreen } from './DashboardScreen';
import { TopBar, type MainView } from './TopBar';

export function MainScreen() {
  const [view, setView] = useState<MainView>('chat');
  const [convId, setConvId] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [booting, setBooting] = useState(true);

  // Boot: open the most recent conversation (mirrors the web app). If there are
  // none, convId stays null and a conversation is created lazily on first send.
  useEffect(() => {
    (async () => {
      const convs = await listConversations();
      if (convs.length) setConvId(convs[0].id);
      setBooting(false);
    })();
  }, []);

  return (
    <View className="flex-1 bg-paper">
      <TopBar view={view} onChange={setView} onMenu={view === 'chat' ? () => setDrawerOpen(true) : undefined} />
      <View className="flex-1">
        {view === 'chat' ? (
          <ChatScreen convId={convId} booting={booting} onConvCreated={setConvId} />
        ) : (
          <DashboardScreen />
        )}
      </View>
      <ConversationsDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        activeConvId={convId}
        onSelect={(id) => {
          setConvId(id);
          setDrawerOpen(false);
        }}
        onNew={() => {
          setConvId(null);
          setDrawerOpen(false);
        }}
      />
    </View>
  );
}
