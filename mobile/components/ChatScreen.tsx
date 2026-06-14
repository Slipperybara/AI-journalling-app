import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Animated, FlatList, Keyboard, Pressable, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { createConversation, getMessages, streamReply, type Message } from '../lib/chat';
import { tryGoalCommand } from '../lib/goals';
import { fonts } from '../lib/theme';
import { Mascot } from './Mascot';
import { Markdown } from './Markdown';

const userText = {
  fontFamily: fonts.serifItalic,
  fontSize: 16,
  lineHeight: 27,
  color: '#5C5850',
  textAlign: 'right' as const,
};
const aiText = {
  fontFamily: fonts.serif,
  fontSize: 17,
  lineHeight: 28,
  color: '#6E6B64',
};

// Messages are written onto the canvas — no bubbles. User entries sit right,
// italic; the companion's voice flows left, like the web app.
function JournalMessage({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <View className="mb-10 mt-2 items-end">
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

// While JAI is thinking (before the first token streams), a small duck softly
// flashes in place of the old typing dot.
function ThinkingDuck() {
  const op = useRef(new Animated.Value(0.4)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(op, { toValue: 1, duration: 650, useNativeDriver: true }),
        Animated.timing(op, { toValue: 0.4, duration: 650, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [op]);
  return (
    <Animated.View style={{ opacity: op }}>
      <Mascot mood="thinkExcited" size={64} />
    </Animated.View>
  );
}

export function ChatScreen({
  convId,
  booting,
  onConvCreated,
  onRetrieval,
}: {
  convId: number | null;
  booting: boolean;
  onConvCreated: (id: number) => void;
  onRetrieval?: (phase: 'start' | 'end') => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [streamText, setStreamText] = useState('');
  const listRef = useRef<FlatList<Message>>(null);
  const loadedConv = useRef<number | null>(null);

  // Load messages when the selected conversation changes (from the drawer).
  // loadedConv guards against reloading right after we create a conversation
  // ourselves on first send — which would otherwise wipe the optimistic message.
  useEffect(() => {
    if (convId === loadedConv.current) return;
    loadedConv.current = convId;
    if (convId == null) {
      setMessages([]);
      return;
    }
    setLoadingMsgs(true);
    getMessages(convId).then((m) => {
      setMessages(m);
      setLoadingMsgs(false);
    });
  }, [convId]);

  const scrollToEnd = useCallback(() => {
    requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
  }, []);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    Keyboard.dismiss(); // drop the keyboard on submit so the reply has the canvas

    // /goal slash-commands run before touching a conversation. `list` and
    // errors inject a local-only message and stop; a successful mutation falls
    // through so the bot can acknowledge it naturally (mirrors the web app).
    const goal = await tryGoalCommand(text);
    if (goal) {
      if (!goal.ok) {
        setMessages((m) => [
          ...m,
          { id: `local-${Date.now()}`, role: 'assistant', content: `(${goal.message})`, created_at: new Date().toISOString() },
        ]);
        scrollToEnd();
        return;
      }
      if (goal.listMessage) {
        const listMessage = goal.listMessage;
        setMessages((m) => [
          ...m,
          { id: `local-${Date.now()}`, role: 'assistant', content: listMessage, created_at: new Date().toISOString() },
        ]);
        scrollToEnd();
        return;
      }
      // Mutation succeeded — fall through so the bot acknowledges naturally.
    }

    let id = convId;
    if (!id) {
      id = await createConversation();
      loadedConv.current = id;
      onConvCreated(id);
    }

    setMessages((m) => [
      ...m,
      { id: `tmp-${Date.now()}`, role: 'user', content: text, created_at: new Date().toISOString() },
    ]);
    setSending(true);
    setStreamText('');
    scrollToEnd();

    await streamReply(id, text, {
      onRetrieval: (phase) => onRetrieval?.(phase),
      onDelta: (t) => {
        setStreamText((s) => s + t);
        scrollToEnd();
      },
      onDone: () => {
        onRetrieval?.('end'); // ensure the tint resets even if the graph path skipped retrieval_end
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
        onRetrieval?.('end');
        setSending(false);
        setStreamText('');
        setMessages((m) => [
          ...m,
          { id: `e-${Date.now()}`, role: 'assistant', content: `(${msg})`, created_at: new Date().toISOString() },
        ]);
      },
    });
  }, [input, sending, convId, scrollToEnd, onConvCreated, onRetrieval]);

  if (booting || loadingMsgs) {
    return (
      <View className="flex-1 items-center justify-center bg-paper">
        <ActivityIndicator color="#8E8B84" />
      </View>
    );
  }

  const canSend = input.trim().length > 0 && !sending;

  return (
    <View className="flex-1">
      <FlatList
        ref={listRef}
        className="flex-1"
        contentContainerStyle={{ paddingHorizontal: 22, paddingTop: 8, paddingBottom: 16 }}
        data={messages}
        keyExtractor={(m) => String(m.id)}
        renderItem={({ item }) => <JournalMessage message={item} />}
        onContentSizeChange={scrollToEnd}
        ListEmptyComponent={
          !sending ? (
            <View style={{ alignItems: 'center', marginTop: 36 }}>
              <Mascot mood="base" size={132} />
              <Text style={{ ...aiText, marginTop: 14, textAlign: 'center' }}>What&apos;s on your mind?</Text>
            </View>
          ) : null
        }
        ListFooterComponent={
          sending ? (
            <View className="mb-6 mt-1">
              {streamText ? <Markdown content={streamText} style={aiText} /> : <ThinkingDuck />}
            </View>
          ) : null
        }
      />

      <SafeAreaView edges={['bottom']}>
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
    </View>
  );
}
