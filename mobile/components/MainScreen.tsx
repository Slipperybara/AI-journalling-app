import { useEffect, useState } from 'react';
import { Platform, View } from 'react-native';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';

import { listConversations } from '../lib/chat';
import { registerForPushNotifications } from '../lib/notifications';
import { AmbientBackground } from './AmbientBackground';
import { ChatScreen } from './ChatScreen';
import { ConversationsDrawer } from './ConversationsDrawer';
import { DashboardScreen } from './DashboardScreen';
import { TopBar, type MainView } from './TopBar';

export function MainScreen() {
  const [view, setView] = useState<MainView>('chat');
  const [convId, setConvId] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [booting, setBooting] = useState(true);
  // 'cool' while the analytical (GraphRAG) path runs — ChatScreen reports the
  // retrieval phase from the stream. Mirrors the web app's tint.
  const [bgMode, setBgMode] = useState<'warm' | 'cool'>('warm');

  // Boot: open the most recent conversation (mirrors the web app). If there are
  // none, convId stays null and a conversation is created lazily on first send.
  useEffect(() => {
    (async () => {
      const convs = await listConversations();
      if (convs.length) setConvId(convs[0].id);
      setBooting(false);
    })();
    // Register for push (morning-brief notifications). No-op on simulator /
    // when denied; only effective in a real build with push entitlements.
    registerForPushNotifications();
  }, []);

  return (
    // Full-screen keyboard avoidance lives here (above the tab content) so the
    // chat input is never undershot by the TopBar's height — see the
    // react-native-keyboard-controller "header offset" caveat.
    <KeyboardAvoidingView
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      className="flex-1"
    >
      <AmbientBackground mode={bgMode} />
      <TopBar view={view} onChange={setView} onMenu={view === 'chat' ? () => setDrawerOpen(true) : undefined} />
      <View className="flex-1">
        {view === 'chat' ? (
          <ChatScreen
            convId={convId}
            booting={booting}
            onConvCreated={setConvId}
            onRetrieval={(phase) => setBgMode(phase === 'start' ? 'cool' : 'warm')}
          />
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
    </KeyboardAvoidingView>
  );
}
