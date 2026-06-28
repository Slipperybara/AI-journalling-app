import { useEffect, useState } from 'react';
import { Platform, View } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { getOrCreateWelcome, listConversations } from '../lib/chat';
import { syncNotificationPrefs } from '../lib/notificationPrefs';
import { registerForPushNotifications } from '../lib/notifications';
import { syncOnboardingProfile } from '../lib/profile';
import { AmbientBackground } from './AmbientBackground';
import { ChatScreen } from './ChatScreen';
import { ConversationsDrawer } from './ConversationsDrawer';
import { DashboardScreen } from './DashboardScreen';
import { TopBar } from './TopBar';

type MainView = 'chat' | 'dashboard';

export function MainScreen() {
  const [view, setView] = useState<MainView>('chat');
  const [convId, setConvId] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [booting, setBooting] = useState(true);
  // 'cool' while the analytical (GraphRAG) path runs — ChatScreen reports the
  // retrieval phase from the stream. Mirrors the web app's tint.
  const [bgMode, setBgMode] = useState<'warm' | 'cool'>('warm');
  const insets = useSafeAreaInsets();

  const today = new Date()
    .toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
    .toUpperCase();

  // Boot: open the most recent conversation (mirrors the web app). For a brand-new
  // user with no conversations, seed a personalized greeting as their first message
  // (profile must sync FIRST so the greeting can reference their onboarding answers).
  useEffect(() => {
    (async () => {
      // One-time: push onboarding answers to the backend so the welcome greeting
      // and the bot's first replies already know the user. Best-effort, idempotent.
      // Awaited so the greeting below is generated with the profile already present.
      await syncOnboardingProfile();
      const convs = await listConversations();
      if (convs.length) {
        setConvId(convs[0].id);
      } else {
        const welcomeId = await getOrCreateWelcome();
        if (welcomeId) setConvId(welcomeId);
      }
      setBooting(false);
    })();
    // Register for push (morning-brief notifications). No-op on simulator /
    // when denied; only effective in a real build with push entitlements.
    registerForPushNotifications();
    // Sync the morning-notification time chosen during onboarding (PUT needs
    // auth, which we now have). Idempotent; the drawer setting keeps it current.
    syncNotificationPrefs();
  }, []);

  return (
    <View className="flex-1">
      <AmbientBackground mode={bgMode} />
      <TopBar date={today} onMenu={() => setDrawerOpen(true)} />
      {/* Keyboard avoidance wraps ONLY the content — the TopBar stays fixed so
          opening the keyboard no longer lurches the whole screen. The offset
          cancels the input's bottom safe-area inset so there's no extra gap
          above the keyboard (the classic SafeAreaView + padding double-count). */}
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={insets.bottom}
        className="flex-1"
      >
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
          {/* Soft fade where content scrolls up under the header (Gemini/ChatGPT
              style). Paper → transparent; matches both warm and cool tints. */}
          <LinearGradient
            colors={['#ECE9E4', 'rgba(236,233,228,0)']}
            style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 28 }}
            pointerEvents="none"
          />
        </View>
      </KeyboardAvoidingView>
      <ConversationsDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        activeConvId={convId}
        view={view}
        onSelect={(id) => {
          setConvId(id);
          setView('chat');
          setDrawerOpen(false);
        }}
        onNew={() => {
          setConvId(null);
          setView('chat');
          setDrawerOpen(false);
        }}
        onOpenDashboard={() => {
          setView('dashboard');
          setDrawerOpen(false);
        }}
      />
    </View>
  );
}
