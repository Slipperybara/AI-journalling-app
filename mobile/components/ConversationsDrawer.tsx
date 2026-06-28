import { Feather } from '@expo/vector-icons';
import { useEffect, useRef, useState } from 'react';
import { Alert, Animated, Dimensions, FlatList, PanResponder, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { deleteConversation, listConversations, renameConversation, type Conversation } from '../lib/chat';
import { colors, fonts } from '../lib/theme';
import { AccountSheet } from './AccountSheet';
import { NotificationSettings } from './NotificationSettings';

const { width: SCREEN_W } = Dimensions.get('window');
const DRAWER_W = Math.min(330, Math.round(SCREEN_W * 0.82));

function preview(c: Conversation): string {
  if (c.title) return c.title;
  if (c.first_user_message) {
    const s = c.first_user_message.trim().replace(/\s+/g, ' ');
    return s.length > 42 ? s.slice(0, 42) + '…' : s;
  }
  return 'New conversation';
}

// Compact relative time for the conversation list (now / 12m / 3h / Yesterday / 4d / Mar 2).
function relTime(iso: string | null): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${days}d`;
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export function ConversationsDrawer({
  open,
  onClose,
  activeConvId,
  view,
  onSelect,
  onNew,
  onOpenDashboard,
}: {
  open: boolean;
  onClose: () => void;
  activeConvId: number | null;
  view: 'chat' | 'dashboard';
  onSelect: (id: number) => void;
  onNew: () => void;
  onOpenDashboard: () => void;
}) {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [notifOpen, setNotifOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [mounted, setMounted] = useState(open);
  const tx = useRef(new Animated.Value(-DRAWER_W)).current;
  const fade = useRef(new Animated.Value(0)).current;

  // Leftward swipe on the open panel closes it (latest onClose via ref so the
  // once-created responder never goes stale).
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const closePan = useRef(
    PanResponder.create({
      onMoveShouldSetPanResponder: (_e, g) => g.dx < -12 && Math.abs(g.dx) > Math.abs(g.dy) * 1.5,
      onPanResponderRelease: (_e, g) => {
        if (g.dx < -45) onCloseRef.current();
      },
    }),
  ).current;

  useEffect(() => {
    if (open) {
      setMounted(true);
      listConversations().then(setConvs);
      Animated.parallel([
        Animated.timing(tx, { toValue: 0, duration: 220, useNativeDriver: true }),
        Animated.timing(fade, { toValue: 1, duration: 220, useNativeDriver: true }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.timing(tx, { toValue: -DRAWER_W, duration: 200, useNativeDriver: true }),
        Animated.timing(fade, { toValue: 0, duration: 200, useNativeDriver: true }),
      ]).start(({ finished }) => {
        if (finished) setMounted(false);
      });
    }
  }, [open, tx, fade]);

  if (!mounted) return null;

  const refresh = () => listConversations().then(setConvs);

  const promptRename = (c: Conversation) => {
    // Alert.prompt is iOS-only — fine for the iOS-first MVP.
    Alert.prompt?.(
      'Rename chat',
      undefined,
      async (title?: string) => {
        if (title && title.trim()) {
          await renameConversation(c.id, title.trim());
          refresh();
        }
      },
      'plain-text',
      c.title ?? '',
    );
  };

  const confirmDelete = (c: Conversation) => {
    Alert.alert('Delete chat?', 'It disappears from your list. Past entries still inform your reflections.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          await deleteConversation(c.id);
          if (activeConvId === c.id) onNew();
          refresh();
        },
      },
    ]);
  };

  const onLongPress = (c: Conversation) => {
    Alert.alert(preview(c), undefined, [
      { text: 'Rename', onPress: () => promptRename(c) },
      { text: 'Delete', style: 'destructive', onPress: () => confirmDelete(c) },
      { text: 'Cancel', style: 'cancel' },
    ]);
  };

  return (
    <View style={StyleSheet.absoluteFill} pointerEvents="box-none">
      <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(20,18,15,0.28)', opacity: fade }]}>
        <Pressable style={{ flex: 1 }} onPress={onClose} />
      </Animated.View>

      <Animated.View
        {...closePan.panHandlers}
        style={{
          position: 'absolute',
          top: 0,
          bottom: 0,
          left: 0,
          width: DRAWER_W,
          transform: [{ translateX: tx }],
          backgroundColor: colors.paper,
          borderRightWidth: 1,
          borderRightColor: colors.line,
        }}
      >
        <SafeAreaView edges={['top', 'bottom']} style={{ flex: 1 }}>
          <Pressable
            onPress={onOpenDashboard}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 18, paddingTop: 10, paddingVertical: 12 }}
            android_ripple={{ color: colors.line }}
          >
            <Feather name="grid" size={18} color={view === 'dashboard' ? colors.ink : colors.inkSoft} />
            <Text
              style={{
                fontFamily: fonts.serifMedium,
                fontSize: 18,
                color: view === 'dashboard' ? colors.ink : colors.inkSoft,
              }}
            >
              Dashboard
            </Text>
          </Pressable>

          <Pressable
            onPress={onNew}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 18, paddingVertical: 12 }}
            android_ripple={{ color: colors.line }}
          >
            <Feather name="edit" size={18} color={colors.inkSoft} />
            <Text style={{ fontFamily: fonts.serifMedium, fontSize: 18, color: colors.inkSoft }}>New chat</Text>
          </Pressable>

          <View style={{ paddingHorizontal: 18, paddingTop: 12, paddingBottom: 4 }}>
            <Text
              style={{
                fontFamily: fonts.sans,
                fontSize: 11,
                letterSpacing: 1.4,
                color: colors.mutedSoft,
                textTransform: 'uppercase',
              }}
            >
              Chats
            </Text>
          </View>

          <FlatList
            style={{ flex: 1 }}
            data={convs}
            keyExtractor={(c) => String(c.id)}
            contentContainerStyle={{ paddingBottom: 16 }}
            renderItem={({ item }) => {
              const active = view === 'chat' && item.id === activeConvId;
              return (
                <Pressable
                  onPress={() => onSelect(item.id)}
                  onLongPress={() => onLongPress(item)}
                  delayLongPress={300}
                  style={{
                    paddingHorizontal: 18,
                    paddingVertical: 11,
                    backgroundColor: active ? colors.card : 'transparent',
                  }}
                >
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Text
                      numberOfLines={1}
                      style={{
                        flex: 1,
                        fontFamily: fonts.serif,
                        fontSize: 16,
                        color: active ? colors.ink : '#5C5850',
                      }}
                    >
                      {preview(item)}
                    </Text>
                    <Text style={{ fontFamily: fonts.sans, fontSize: 11, color: colors.faint, marginLeft: 10 }}>
                      {relTime(item.last_message_at ?? item.started_at)}
                    </Text>
                  </View>
                </Pressable>
              );
            }}
            ListEmptyComponent={
              <Text style={{ paddingHorizontal: 18, paddingTop: 6, fontFamily: fonts.serif, fontSize: 15, color: colors.faint }}>
                No chats yet — start writing.
              </Text>
            }
          />

          <Pressable
            onPress={() => setNotifOpen(true)}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 18, paddingVertical: 14, borderTopWidth: 1, borderTopColor: colors.line }}
            android_ripple={{ color: colors.line }}
          >
            <Feather name="clock" size={16} color={colors.mutedSoft} />
            <Text style={{ fontFamily: fonts.sans, fontSize: 14, color: colors.mutedSoft }}>
              Morning reflection
            </Text>
          </Pressable>

          <Pressable
            onPress={() =>
              Alert.alert(
                'Safety & support',
                "JAI is a journaling companion for reflection — not a medical or mental-health service, " +
                  'and not a substitute for professional care.\n\n' +
                  "If you're in crisis or thinking about harming yourself, please reach your local emergency " +
                  'services or a crisis line right away. In the US, call or text 988 (Suicide & Crisis Lifeline).',
                [{ text: 'Close', style: 'cancel' }],
              )
            }
            style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 18, paddingVertical: 14 }}
            android_ripple={{ color: colors.line }}
          >
            <Feather name="life-buoy" size={16} color={colors.mutedSoft} />
            <Text style={{ fontFamily: fonts.sans, fontSize: 14, color: colors.mutedSoft }}>Safety &amp; support</Text>
          </Pressable>

          <Pressable
            onPress={() => setAccountOpen(true)}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 18, paddingVertical: 14 }}
            android_ripple={{ color: colors.line }}
          >
            <Feather name="user" size={16} color={colors.mutedSoft} />
            <Text style={{ fontFamily: fonts.sans, fontSize: 14, color: colors.mutedSoft }}>Account</Text>
          </Pressable>
        </SafeAreaView>

        <NotificationSettings open={notifOpen} onClose={() => setNotifOpen(false)} />
        <AccountSheet open={accountOpen} onClose={() => setAccountOpen(false)} />
      </Animated.View>
    </View>
  );
}
