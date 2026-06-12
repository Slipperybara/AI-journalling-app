import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import {
  createConversation,
  getMessages,
  listConversations,
  streamReply,
  type Message,
} from '../lib/chat';

function Bubble({ message }: { message: Message }) {
  const isUser = message.role === 'user';
  return (
    <View className={`my-1.5 max-w-[85%] ${isUser ? 'self-end' : 'self-start'}`}>
      <View className={`rounded-2xl px-4 py-2.5 ${isUser ? 'bg-ink' : 'bg-white/70'}`}>
        <Text className={`text-base leading-6 ${isUser ? 'text-white' : 'text-ink-soft'}`}>
          {message.content}
        </Text>
      </View>
    </View>
  );
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

    const optimistic: Message = {
      id: `tmp-${Date.now()}`,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages((m) => [...m, optimistic]);
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

  // The in-flight assistant reply rides at the end of the list so it scrolls
  // naturally with the conversation.
  const data: Message[] = sending
    ? [...messages, { id: '__stream__', role: 'assistant', content: streamText || '…', created_at: '' }]
    : messages;

  const canSend = input.trim().length > 0 && !sending;

  return (
    <KeyboardAvoidingView
      className="flex-1 bg-paper"
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <FlatList
        ref={listRef}
        className="flex-1"
        contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 8, paddingBottom: 12 }}
        data={data}
        keyExtractor={(m) => String(m.id)}
        renderItem={({ item }) => <Bubble message={item} />}
        onContentSizeChange={scrollToEnd}
        ListEmptyComponent={
          <Text className="mt-16 text-center text-lg text-muted">What&apos;s on your mind?</Text>
        }
      />

      <SafeAreaView edges={['bottom']} className="bg-paper">
        <View className="flex-row items-end gap-2 border-t border-black/5 px-4 pb-2 pt-3">
          <TextInput
            className="max-h-32 flex-1 rounded-2xl bg-white/70 px-4 py-3 text-base text-ink-soft"
            placeholder="Write here…"
            placeholderTextColor="#B4B1A9"
            value={input}
            onChangeText={setInput}
            multiline
          />
          <Pressable
            onPress={send}
            disabled={!canSend}
            className={`h-11 w-11 items-center justify-center rounded-full bg-ink ${canSend ? 'active:opacity-80' : 'opacity-40'}`}
          >
            <Text className="text-lg text-white">↑</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    </KeyboardAvoidingView>
  );
}
