import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Animated, FlatList, Platform, Pressable, Text, TextInput, View } from 'react-native';
import { KeyboardAvoidingView } from 'react-native-keyboard-controller';
import { SafeAreaView } from 'react-native-safe-area-context';

import {
  createConversation,
  getMessages,
  listConversations,
  streamReply,
  type Message,
} from '../lib/chat';
import { fonts } from '../lib/theme';
import { Markdown } from './Markdown';

const userText = {
  fontFamily: fonts.serifItalic,
  fontSize: 18,
  lineHeight: 31,
  color: '#5C5850',
  textAlign: 'right' as const,
};
const aiText = {
  fontFamily: fonts.serif,
  fontSize: 19,
  lineHeight: 31,
  color: '#6E6B64',
};

// Messages are written onto the canvas — no bubbles. User entries sit right,
// italic; the companion's voice flows left, like the web app.
function JournalMessage({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <View className="mb-8 mt-1 items-end">
        <Text style={userText}>{message.content}</Text>
      </View>
    );
  }
  return (
    <View className="mb-6">
      <Markdown content={message.content} style={aiText} />
    </View>
  );
}

function PulsingDot() {
  const op = useRef(new Animated.Value(0.25)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(op, { toValue: 1, duration: 700, useNativeDriver: true }),
        Animated.timing(op, { toValue: 0.25, duration: 700, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [op]);
  return <Animated.View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: '#8E8B83', opacity: op }} />;
}

export function ChatScreen() {
  const [convId, setConvId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [streamText, setStreamText] = useState('');
  const listRef = useRef<FlatList<Message>>(null);

  useEffect(() => {
    (async () => {
      const convs = await listConversations();
      if (convs.length) {
        setConvId(convs[0].id);
        setMessages(await getMessages(convs[0].id));
      }
      setLoading(false);
    })();
  }, []);

  const scrollToEnd = useCallback(() => {
    requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
  }, []);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');

    let id = convId;
    if (!id) {
      id = await createConversation();
      setConvId(id);
    }

    setMessages((m) => [
      ...m,
      { id: `tmp-${Date.now()}`, role: 'user', content: text, created_at: new Date().toISOString() },
    ]);
    setSending(true);
    setStreamText('');
    scrollToEnd();

    await streamReply(id, text, {
      onDelta: (t) => {
        setStreamText((s) => s + t);
        scrollToEnd();
      },
      onDone: () => {
        setStreamText((s) => {
          if (s.trim()) {
            setMessages((m) => [
              ...m,
              { id: `a-${Date.now()}`, role: 'assistant', content: s, created_at: new Date().toISOString() },
            ]);
          }
          return '';
        });
        setSending(false);
        scrollToEnd();
      },
      onError: (msg) => {
        setSending(false);
        setStreamText('');
        setMessages((m) => [
          ...m,
          { id: `e-${Date.now()}`, role: 'assistant', content: `(${msg})`, created_at: new Date().toISOString() },
        ]);
      },
    });
  }, [input, sending, convId, scrollToEnd]);

  if (loading) {
    return (
      <View className="flex-1 items-center justify-center bg-paper">
        <ActivityIndicator color="#8E8B84" />
      </View>
    );
  }

  const today = new Date()
    .toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
    .toUpperCase();

  const canSend = input.trim().length > 0 && !sending;

  return (
    <KeyboardAvoidingView
      className="flex-1 bg-paper"
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <FlatList
        ref={listRef}
        className="flex-1"
        contentContainerStyle={{ paddingHorizontal: 22, paddingTop: 4, paddingBottom: 16 }}
        data={messages}
        keyExtractor={(m) => String(m.id)}
        renderItem={({ item }) => <JournalMessage message={item} />}
        onContentSizeChange={scrollToEnd}
        ListHeaderComponent={
          <Text
            style={{ fontFamily: fonts.sans, fontSize: 11, letterSpacing: 1.4, color: '#9C998F' }}
            className="pb-6 pt-1"
          >
            {today}
          </Text>
        }
        ListEmptyComponent={
          !sending ? (
            <Text style={{ ...aiText, marginTop: 8 }}>What&apos;s on your mind?</Text>
          ) : null
        }
        ListFooterComponent={
          sending ? (
            <View className="mb-6 mt-1">
              {streamText ? <Markdown content={streamText} style={aiText} /> : <PulsingDot />}
            </View>
          ) : null
        }
      />

      <SafeAreaView edges={['bottom']} className="bg-paper">
        <View className="flex-row items-end px-5 pb-2 pt-2" style={{ gap: 10 }}>
          <TextInput
            className="flex-1"
            style={{
              fontFamily: fonts.serif,
              fontSize: 19,
              lineHeight: 28,
              color: '#38342F',
              maxHeight: 160,
              paddingVertical: 6,
            }}
            placeholder="Write here…"
            placeholderTextColor="#B4B1A9"
            value={input}
            onChangeText={setInput}
            multiline
          />
          {canSend && (
            <Pressable
              onPress={send}
              className="mb-1 h-9 w-9 items-center justify-center rounded-full bg-ink active:opacity-80"
            >
              <Text className="text-base text-white">↑</Text>
            </Pressable>
          )}
        </View>
      </SafeAreaView>
    </KeyboardAvoidingView>
  );
}
