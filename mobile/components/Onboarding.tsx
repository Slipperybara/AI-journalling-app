import { useRef, useState } from 'react';
import { Dimensions, NativeScrollEvent, NativeSyntheticEvent, Pressable, ScrollView, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { fonts } from '../lib/theme';

const { width: SCREEN_W } = Dimensions.get('window');

const SLIDES = [
  {
    title: 'Meet JAI',
    body: 'A companion that listens — and helps you make sense of your days.',
  },
  {
    title: 'Just write',
    body: 'Say whatever’s on your mind. JAI listens closely, asks gently, and never judges.',
  },
  {
    title: 'Wake to reflection',
    body: 'Each morning, a warm recap of your yesterday and a gentle nudge toward where you’re heading.',
  },
];

export function Onboarding({ onDone }: { onDone: () => void }) {
  const [index, setIndex] = useState(0);
  const scrollRef = useRef<ScrollView>(null);

  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const i = Math.round(e.nativeEvent.contentOffset.x / SCREEN_W);
    if (i !== index) setIndex(i);
  };

  const next = () => {
    if (index < SLIDES.length - 1) {
      scrollRef.current?.scrollTo({ x: (index + 1) * SCREEN_W, animated: true });
    } else {
      onDone();
    }
  };

  const last = index === SLIDES.length - 1;

  return (
    <SafeAreaView className="flex-1 bg-paper">
      <View className="items-end px-6 pt-2">
        <Pressable onPress={onDone} hitSlop={10}>
          <Text style={{ fontFamily: fonts.sans, fontSize: 13, color: '#9A9790' }}>Skip</Text>
        </Pressable>
      </View>

      <ScrollView
        ref={scrollRef}
        horizontal
        pagingEnabled
        showsHorizontalScrollIndicator={false}
        onMomentumScrollEnd={onScroll}
        className="flex-1"
      >
        {SLIDES.map((s) => (
          <View key={s.title} style={{ width: SCREEN_W }} className="flex-1 justify-center px-10">
            <Text style={{ fontFamily: fonts.serifMedium, fontSize: 34, lineHeight: 42, color: '#2A2825' }}>
              {s.title}
            </Text>
            <Text style={{ fontFamily: fonts.serif, fontSize: 19, lineHeight: 30, color: '#6E6B64', marginTop: 16 }}>
              {s.body}
            </Text>
          </View>
        ))}
      </ScrollView>

      <View className="px-10 pb-6">
        <View className="mb-7 flex-row justify-center" style={{ gap: 7 }}>
          {SLIDES.map((s, i) => (
            <View
              key={s.title}
              style={{
                width: i === index ? 18 : 7,
                height: 7,
                borderRadius: 4,
                backgroundColor: i === index ? '#6E6B64' : '#CFCBC3',
              }}
            />
          ))}
        </View>
        <Pressable
          onPress={next}
          className="h-12 items-center justify-center rounded-2xl bg-ink active:opacity-80"
        >
          <Text style={{ fontFamily: fonts.sansMedium, fontSize: 16, color: '#fff' }}>
            {last ? 'Get started' : 'Continue'}
          </Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}
